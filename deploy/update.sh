#!/usr/bin/env bash
# Pull the deploy ref and apply it: preflight, dependency hook, system overlay,
# service restart, health check, and automatic rollback if the result is not
# healthy.
#
# Run by protmon-deploy.timer through /usr/local/bin/protmon-update. By hand:
#
#   sudo /usr/local/bin/protmon-update            # normal check
#   sudo /usr/local/bin/protmon-update --force    # ignore HOLD and live alerts
#
# Design notes, because the failure mode here is a dark sign nobody notices:
#   - A commit is validated in a scratch extract before the live tree moves.
#   - Deploys defer while an alert is on screen. Nothing restarts under an
#     operator who is mid-response.
#   - A deploy that does not come back healthy is rolled back to the previous
#     commit automatically, and the rollback is logged loudly.
#   - A commit that failed is remembered and not retried until a newer commit
#     appears. Without that, a bad commit would restart the display every
#     minute forever.
set -euo pipefail

DEPLOY_ENV=${DEPLOY_ENV:-/etc/protmon/deploy.env}
# shellcheck source=/dev/null
[ -r "$DEPLOY_ENV" ] && . "$DEPLOY_ENV"

APP_DIR=${APP_DIR:-/opt/protmon}
DEPLOY_REMOTE=${DEPLOY_REMOTE:-origin}
DEPLOY_REF=${DEPLOY_REF:-release}
STATE_DIR=${STATE_DIR:-/var/lib/protmon}
LOG_DIR=${LOG_DIR:-/var/log/protmon}
DEPLOY_LOG=${DEPLOY_LOG:-$LOG_DIR/deploy.jsonl}
HOLD_FILE=${HOLD_FILE:-/etc/protmon/HOLD}
LOCK_FILE=${LOCK_FILE:-/run/protmon-deploy.lock}
HEALTH_URL=${HEALTH_URL-http://127.0.0.1:8080/healthz}
STATE_URL=${STATE_URL-http://127.0.0.1:8080/api/state}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-30}
DEFER_WARN_AFTER=${DEFER_WARN_AFTER:-1800}
SERVER_UNITS=${SERVER_UNITS:-protmon-server.service}
GO2RTC_UNITS=${GO2RTC_UNITS:-protmon-go2rtc.service}
KIOSK_UNITS=${KIOSK_UNITS:-protmon-kiosk.service}
SYSTEMCTL=${SYSTEMCTL:-systemctl}
export APP_DIR STATE_DIR SYSTEMCTL

DEFER_MARKER=$STATE_DIR/deploy-deferred-since
KNOWN_BAD=$STATE_DIR/failed-sha
RESTARTED=0

log() { # log <event> [key=value ...]
  local event=$1 kv json
  shift
  json=$(printf '{"ts":"%s","src":"deploy","event":"%s"' "$(date -Is)" "$event")
  for kv in "$@"; do
    json+=$(printf ',"%s":"%s"' "${kv%%=*}" "${kv#*=}")
  done
  json+='}'
  mkdir -p "$(dirname "$DEPLOY_LOG")"
  printf '%s\n' "$json" >>"$DEPLOY_LOG"
  printf '%s\n' "$json" >&2
}

git_app() { git -C "$APP_DIR" "$@"; }

unit_exists() { "$SYSTEMCTL" cat "$1" >/dev/null 2>&1; }

restart_units() {
  local u
  for u in "$@"; do
    [ -n "$u" ] || continue
    if ! unit_exists "$u"; then
      log unit-absent unit="$u"
      continue
    fi
    if "$SYSTEMCTL" restart "$u"; then
      log restarted unit="$u"
      RESTARTED=1
    else
      log restart-failed unit="$u"
      return 1
    fi
  done
  return 0
}

# Number of alerts currently on screen. Unreachable server counts as zero: if
# the server is down there is nothing to interrupt and the update may well be
# the fix.
active_alerts() {
  local body out
  if [ -z "$STATE_URL" ]; then printf 0; return 0; fi
  if ! body=$(curl -fsS --max-time 2 "$STATE_URL" 2>/dev/null); then printf 0; return 0; fi
  if ! out=$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    print(len(json.load(sys.stdin).get("alerts") or []))
except Exception:
    print(0)' 2>/dev/null); then printf 0; return 0; fi
  printf '%s' "$out"
}

healthy() {
  local deadline
  if [ -z "$HEALTH_URL" ]; then return 0; fi
  deadline=$(($(date +%s) + HEALTH_TIMEOUT))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS --max-time 2 -o /dev/null "$HEALTH_URL" 2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}

changed_matches() { # changed_matches <from> <to> <regex>
  local changed
  changed=$(git_app diff --name-only "$1" "$2" 2>/dev/null) || return 0
  printf '%s\n' "$changed" | grep -qE "$3"
}

# Restart the server on every deploy. Only disturb go2rtc and the browser when
# something they own actually changed, so a docs commit never blanks the wall.
units_to_restart() { # units_to_restart <from> <to>
  local units=$SERVER_UNITS
  if changed_matches "$1" "$2" '^(deploy/go2rtc/|deploy/system/etc/systemd/system/protmon-go2rtc)'; then
    units="$units $GO2RTC_UNITS"
  fi
  if changed_matches "$1" "$2" '^(web/|deploy/kiosk/|deploy/system/etc/systemd/system/protmon-kiosk)'; then
    units="$units $KIOSK_UNITS"
  fi
  printf '%s' "$units"
}

apply() { # apply <sha> <units...>
  local sha=$1
  shift
  git_app reset --hard --quiet "$sha"
  git_app clean -fdq
  if [ -x "$APP_DIR/deploy/postfetch.sh" ]; then
    log postfetch-start
    if ! "$APP_DIR/deploy/postfetch.sh"; then
      log postfetch-failed
      return 1
    fi
  fi
  "$APP_DIR/deploy/install-system.sh"
  RESTARTED=0
  restart_units "$@" || return 1
  if [ "$RESTARTED" = 0 ]; then
    log health-skipped reason=no-units-installed sha="$sha"
    return 0
  fi
  healthy || return 1
  return 0
}

main() {
  local force=0 local_sha remote_sha waited units
  [ "${1:-}" = "--force" ] && force=1

  cd "$APP_DIR" 2>/dev/null || { log fatal reason=missing-app-dir dir="$APP_DIR"; exit 1; }
  mkdir -p "$STATE_DIR" "$LOG_DIR"

  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log skipped reason=already-running
    exit 0
  fi

  if [ -e "$HOLD_FILE" ] && [ "$force" = 0 ]; then
    log held file="$HOLD_FILE"
    exit 0
  fi

  local_sha=$(git_app rev-parse HEAD)
  if ! remote_sha=$(git_app ls-remote --exit-code "$DEPLOY_REMOTE" "refs/heads/$DEPLOY_REF" | awk 'NR==1{print $1}'); then
    log remote-unreachable remote="$DEPLOY_REMOTE" ref="$DEPLOY_REF"
    exit 1
  fi

  if [ "$remote_sha" = "$local_sha" ]; then
    rm -f "$DEFER_MARKER"
    exit 0
  fi

  # Already tried this commit and it failed. Wait for a newer one rather than
  # restarting the display on a loop.
  if [ "$force" = 0 ] && [ -f "$KNOWN_BAD" ] && [ "$(cat "$KNOWN_BAD")" = "$remote_sha" ]; then
    log skipped reason=known-bad sha="$remote_sha"
    exit 0
  fi

  if [ "$force" = 0 ] && [ "$(active_alerts)" -gt 0 ]; then
    [ -e "$DEFER_MARKER" ] || date +%s >"$DEFER_MARKER"
    waited=$(($(date +%s) - $(cat "$DEFER_MARKER")))
    if [ "$waited" -ge "$DEFER_WARN_AFTER" ]; then
      log defer-overdue target="$remote_sha" waited_s="$waited"
    else
      log deferred reason=alert-active target="$remote_sha" waited_s="$waited"
    fi
    exit 0
  fi
  rm -f "$DEFER_MARKER"

  log start ref="$DEPLOY_REF" from="$local_sha" to="$remote_sha"

  if ! git_app fetch --prune --quiet "$DEPLOY_REMOTE" "$DEPLOY_REF"; then
    log fetch-failed remote="$DEPLOY_REMOTE" ref="$DEPLOY_REF"
    exit 1
  fi

  # Validate the candidate in a scratch extract. The live tree has not moved.
  scratch=$(mktemp -d)
  trap 'rm -rf "$scratch"' EXIT
  git_app archive "$remote_sha" | tar -x -C "$scratch"
  if [ -x "$scratch/deploy/preflight.sh" ] && ! "$scratch/deploy/preflight.sh" >>"$DEPLOY_LOG" 2>&1; then
    printf '%s' "$remote_sha" >"$KNOWN_BAD"
    log preflight-failed sha="$remote_sha" note=live-tree-untouched
    exit 1
  fi

  units=$(units_to_restart "$local_sha" "$remote_sha")
  # shellcheck disable=SC2086
  if apply "$remote_sha" $units; then
    printf '{"commit":"%s","previous":"%s","ref":"%s","deployed_at":"%s"}\n' \
      "$remote_sha" "$local_sha" "$DEPLOY_REF" "$(date -Is)" >"$STATE_DIR/deployed.json"
    rm -f "$KNOWN_BAD"
    log deployed sha="$remote_sha" previous="$local_sha"
    exit 0
  fi

  printf '%s' "$remote_sha" >"$KNOWN_BAD"
  log unhealthy sha="$remote_sha" action=rolling-back to="$local_sha"
  # shellcheck disable=SC2086
  if apply "$local_sha" $SERVER_UNITS $GO2RTC_UNITS $KIOSK_UNITS; then
    log rolled-back sha="$local_sha" failed="$remote_sha"
  else
    # The commit we were already running is not healthy either, so the health
    # check itself is suspect. Stay here and shout rather than flip-flopping.
    log rollback-unhealthy sha="$local_sha" failed="$remote_sha" severity=critical \
      note=health-check-may-be-misconfigured
  fi
  exit 1
}

main "$@"

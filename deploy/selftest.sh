#!/usr/bin/env bash
# Exercise update.sh end to end against a throwaway git remote, a stub
# systemctl and a relocated filesystem root. Touches nothing real.
#
#   ./deploy/selftest.sh
#
# The deployer runs unattended on a safety display, so the paths that matter
# most are the ones nobody watches: a rejected commit, a deferred deploy, and a
# rollback. Those are the cases covered here.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

pass=0
fail=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail + 1)); }
check()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi; }
assert() { local label=$1; shift; if "$@"; then ok "$label"; else bad "$label"; fi; }
refute() { local label=$1; shift; if "$@"; then bad "$label"; else ok "$label"; fi; }
case_()  { printf '\n%s\n' "$*"; }

# A stub systemctl that records its calls. Units listed in units.txt "exist".
mkdir -p "$T/bin"
cat >"$T/bin/systemctl" <<'STUB'
#!/bin/sh
# Records calls, and models a server that publishes a health file when it
# starts. Touching $SELFTEST_T/fail_next makes the next restart come up dead,
# which is how the rollback path gets exercised.
printf '%s\n' "$*" >>"$SELFTEST_T/systemctl.log"
case $1 in
  cat) grep -Fxq "$2" "$SELFTEST_T/units.txt" 2>/dev/null || exit 1 ;;
  restart)
    if [ -e "$SELFTEST_T/fail_next" ]; then
      rm -f "$SELFTEST_T/fail_next" "$SELFTEST_T/health"
    else
      : >"$SELFTEST_T/health"
    fi
    ;;
esac
exit 0
STUB
chmod +x "$T/bin/systemctl"
printf 'protmon-server.service\nprotmon-go2rtc.service\nprotmon-kiosk.service\n' >"$T/units.txt"

export SELFTEST_T=$T
export DESTDIR=$T/root
export SYSTEMCTL=$T/bin/systemctl
export DEPLOY_ENV=$T/deploy.env

# Build an origin from the real repo so the test runs against real content.
git init -q --bare "$T/origin.git"
git clone -q "$REPO_ROOT" "$T/work"
git -C "$T/work" remote set-url origin "$T/origin.git"
# Local identity for the throwaway fixture only, so this runs on a CI box with
# no git identity configured. Never touches a real repository.
git -C "$T/work" config user.name selftest
git -C "$T/work" config user.email selftest@invalid
git -C "$T/work" push -q origin HEAD:refs/heads/main
git clone -q --branch main "$T/origin.git" "$T/app"

write_env() { # write_env [KEY=VAL ...]
  cat >"$DEPLOY_ENV" <<ENV
APP_DIR=$T/app
DEPLOY_REMOTE=origin
DEPLOY_REF=main
STATE_DIR=$T/state
LOG_DIR=$T/log
HOLD_FILE=$T/HOLD
LOCK_FILE=$T/lock
HEALTH_URL=file://$T/health
STATE_URL=
HEALTH_TIMEOUT=2
ENV
  for kv in "$@"; do printf '%s\n' "$kv" >>"$DEPLOY_ENV"; done
}

commit_push() { # commit_push <message>
  git -C "$T/work" add -A
  git -C "$T/work" commit -qm "$1"
  git -C "$T/work" push -q origin HEAD:refs/heads/main
}

run() { # run [args...] -> exit code on stdout, output in $T/out
  set +e
  "$REPO_ROOT/deploy/update.sh" "$@" >"$T/out" 2>&1
  local rc=$?
  set -e
  printf '%s' "$rc"
}

head_sha()   { git -C "$T/app" rev-parse HEAD; }
origin_sha() { git -C "$T/origin.git" rev-parse main; }
logged() {
  local f=$T/log/deploy.jsonl
  if [ ! -f "$f" ]; then printf 0; return 0; fi
  grep -c "\"event\":\"$1\"" "$f" || true
}

: >"$T/health"
write_env

case_ "1. up to date: no work, no noise"
rc=$(run)
check "exit 0" "$rc" "0"
check "no deploy started" "$(logged start)" "0"

case_ "2. HOLD pauses deploys"
date >"$T/work/docs/.touch1"
commit_push "test: first change"
target=$(origin_sha)
before=$(head_sha)
touch "$T/HOLD"
rc=$(run)
check "exit 0" "$rc" "0"
check "logged held" "$(logged held)" "1"
check "tree not moved" "$(head_sha)" "$before"
rm -f "$T/HOLD"

case_ "3. normal deploy applies the commit and the overlay"
rc=$(run)
check "exit 0" "$rc" "0"
check "tree at target" "$(head_sha)" "$target"
check "logged deployed" "$(logged deployed)" "1"
assert "overlay unit installed" [ -f "$DESTDIR/etc/systemd/system/protmon-deploy.timer" ]
assert "overlay wrapper installed executable" [ -x "$DESTDIR/usr/local/bin/protmon-update" ]
assert "deployed.json records the commit" grep -q "\"commit\":\"$target\"" "$T/state/deployed.json"
assert "daemon-reload after unit install" grep -q 'daemon-reload' "$T/systemctl.log"
assert "server restarted" grep -q 'restart protmon-server.service' "$T/systemctl.log"

case_ "4. docs-only change leaves the screen alone"
: >"$T/systemctl.log"
date >"$T/work/docs/.touch2"
commit_push "test: docs only"
rc=$(run)
check "exit 0" "$rc" "0"
assert "server restarted" grep -q 'restart protmon-server.service' "$T/systemctl.log"
refute "kiosk left running" grep -q 'restart protmon-kiosk.service' "$T/systemctl.log"

case_ "5. overlay file deleted in git is removed from the host"
cp "$REPO_ROOT/deploy/system/etc/logrotate.d/protmon" "$T/work/deploy/system/etc/logrotate.d/scratch-rule"
commit_push "test: add overlay file"
rc=$(run)
check "exit 0" "$rc" "0"
assert "new overlay file installed" [ -f "$DESTDIR/etc/logrotate.d/scratch-rule" ]
rm "$T/work/deploy/system/etc/logrotate.d/scratch-rule"
commit_push "test: remove overlay file"
rc=$(run)
check "exit 0" "$rc" "0"
refute "stale overlay file removed" [ -f "$DESTDIR/etc/logrotate.d/scratch-rule" ]

case_ "6. a commit that fails preflight never reaches the live tree"
before=$(head_sha)
printf '{ this is not json' >"$T/work/broken.json"
commit_push "test: broken config"
rc=$(run)
check "exit 1" "$rc" "1"
check "logged preflight-failed" "$(logged preflight-failed)" "1"
check "tree not moved" "$(head_sha)" "$before"
git -C "$T/work" rm -q broken.json
commit_push "test: fix config"

case_ "7. deploy defers while an alert is on screen"
printf '{"alerts":[{"key":"front-desk-panic"}]}' >"$T/alerts.json"
write_env "STATE_URL=file://$T/alerts.json"
before=$(head_sha)
rc=$(run)
check "exit 0" "$rc" "0"
check "logged deferred" "$(logged deferred)" "1"
check "tree not moved" "$(head_sha)" "$before"

case_ "8. --force overrides the alert gate"
rc=$(run --force)
check "exit 0" "$rc" "0"
check "tree at target" "$(head_sha)" "$(origin_sha)"

case_ "9. an unhealthy deploy rolls itself back"
write_env
before=$(head_sha)
date >"$T/work/docs/.touch3"
commit_push "test: change that will not come up"
bad_sha=$(origin_sha)
touch "$T/fail_next"
rc=$(run)
check "exit 1" "$rc" "1"
check "logged unhealthy" "$(logged unhealthy)" "1"
check "logged rolled-back" "$(logged rolled-back)" "1"
check "tree back at previous commit" "$(head_sha)" "$before"

case_ "10. the failed commit is not retried on a loop"
: >"$T/systemctl.log"
rc=$(run)
check "exit 0" "$rc" "0"
check "logged known-bad skip" "$(logged skipped)" "1"
refute "nothing restarted" grep -q restart "$T/systemctl.log"

case_ "11. a newer commit clears the block and deploys"
date >"$T/work/docs/.touch4"
commit_push "test: the fix"
rc=$(run)
check "exit 0" "$rc" "0"
check "tree at newest commit" "$(head_sha)" "$(origin_sha)"
assert "known-bad marker cleared" [ ! -f "$T/state/failed-sha" ]
assert "failed commit is behind us" [ "$(head_sha)" != "$bad_sha" ]

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]

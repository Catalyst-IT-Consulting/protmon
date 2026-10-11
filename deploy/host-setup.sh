#!/usr/bin/env bash
# Converge packages and system users with the tracked state. Idempotent. Packages
# are only checked when deploy/packages.txt changed since the last successful run.
#
# APT_GET, DPKG_QUERY, GETENT, USERADD and USERMOD are overridable for selftest.sh.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/protmon}
STATE_DIR=${STATE_DIR:-/var/lib/protmon}
APT_GET=${APT_GET:-apt-get}
DPKG_QUERY=${DPKG_QUERY:-dpkg-query}
GETENT=${GETENT:-getent}
USERADD=${USERADD:-useradd}
USERMOD=${USERMOD:-usermod}

LIST=$APP_DIR/deploy/packages.txt
STAMP=$STATE_DIR/packages.sha256

log() { printf '%s host-setup: %s\n' "$(date -Is)" "$*" >&2; }

mkdir -p "$STATE_DIR"
want=$(sha256sum "$LIST" | cut -d' ' -f1)
if [ "$(cat "$STAMP" 2>/dev/null || true)" != "$want" ]; then
  mapfile -t pkgs < <(sed 's/#.*//' "$LIST" | xargs -n1)
  missing=()
  for p in "${pkgs[@]}"; do
    # shellcheck disable=SC2016
    "$DPKG_QUERY" -W -f='${Status}' "$p" 2>/dev/null | grep -q '^install ok installed$' || missing+=("$p")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    log "installing ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    "$APT_GET" update -qq
    "$APT_GET" install -y -qq "${missing[@]}"
  fi
  printf '%s\n' "$want" >"$STAMP"
fi

if "$GETENT" passwd protmon-kiosk >/dev/null; then
  "$USERMOD" -aG video,render,input protmon-kiosk
else
  log "creating protmon-kiosk"
  "$USERADD" --system --create-home --home-dir /var/lib/protmon-kiosk \
    --shell /usr/sbin/nologin --groups video,render,input protmon-kiosk
fi

#!/usr/bin/env bash
# Install the deploy/system overlay onto this host.
#
# Every file under deploy/system/ maps to the same path under / , so adding a
# systemd unit or a logrotate rule to the repo is enough to get it onto the Pi.
# Files this script installed previously that have since left the overlay are
# removed, so a deleted unit does not linger on the box forever.
#
# DESTDIR relocates the whole install, which is how selftest.sh exercises this
# without touching the real /etc.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/protmon}
STATE_DIR=${STATE_DIR:-/var/lib/protmon}
DESTDIR=${DESTDIR:-}
SYSTEMCTL=${SYSTEMCTL:-systemctl}

OVERLAY=$APP_DIR/deploy/system
MANIFEST=$STATE_DIR/installed-files

log() { printf '%s install-system: %s\n' "$(date -Is)" "$*" >&2; }

if [ ! -d "$OVERLAY" ]; then
  log "no overlay at $OVERLAY, nothing to do"
  exit 0
fi

mkdir -p "$STATE_DIR"
wanted=$(mktemp)
trap 'rm -f "$wanted"' EXIT
reload=0

while IFS= read -r -d '' src; do
  rel=${src#"$OVERLAY"/}
  dest=$DESTDIR/$rel
  printf '/%s\n' "$rel" >>"$wanted"
  if ! cmp -s "$src" "$dest"; then
    install -D -m "$(stat -c %a "$src")" "$src" "$dest"
    log "installed /$rel"
    case $rel in etc/systemd/system/*) reload=1 ;; esac
  fi
done < <(find "$OVERLAY" -type f -print0)

# Anything we installed on a previous run and that is no longer in the overlay.
if [ -f "$MANIFEST" ]; then
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if ! grep -Fxq "$path" "$wanted"; then
      case $path in /etc/systemd/system/*) "$SYSTEMCTL" disable "${path##*/}" >/dev/null 2>&1 || true ;; esac
      rm -f "$DESTDIR$path"
      log "removed $path"
      case $path in /etc/systemd/system/*) reload=1 ;; esac
    fi
  done <"$MANIFEST"
fi

sort -u "$wanted" >"$MANIFEST"

if [ "$reload" = 1 ]; then
  log "systemd daemon-reload"
  "$SYSTEMCTL" daemon-reload
fi

# Enable units that declare [Install], every run, so a disabled unit self-heals.
# A failure is logged, not fatal: it must not roll back an otherwise good deploy.
for f in "$OVERLAY"/etc/systemd/system/*; do
  grep -qs '^\[Install\]' "$f" || continue
  unit=${f##*/}
  "$SYSTEMCTL" is-enabled -q "$unit" >/dev/null 2>&1 && continue
  if "$SYSTEMCTL" enable "$unit" >/dev/null 2>&1; then
    log "enabled $unit"
  else
    log "enable failed: $unit"
  fi
done
exit 0

#!/usr/bin/env bash
# Sync packages, system users, the pinned go2rtc binary and the server virtualenv after update.sh moves
# the live tree. The venv lives in STATE_DIR so git reset --hard leaves it alone,
# and pip only runs when requirements.txt changed. Non-zero fails the deploy and
# rolls back.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/protmon}
STATE_DIR=${STATE_DIR:-/var/lib/protmon}
VENV=$STATE_DIR/venv
STAMP=$STATE_DIR/venv.requirements.sha256

# Lets selftest.sh run the real deployer without installing anything.
[ -z "${PROTMON_SKIP_VENV:-}" ] || exit 0

# Packages and system users, before anything below needs them.
"$APP_DIR/deploy/host-setup.sh"

# Pinned go2rtc binary, reinstalled only when its checksum does not match.
# shellcheck source=/dev/null
. "$APP_DIR/deploy/go2rtc/pin.env"
BIN=${GO2RTC_BIN:-/usr/local/bin/go2rtc}
if ! { [ -x "$BIN" ] && echo "$GO2RTC_SHA256  $BIN" | sha256sum -c --status; }; then
  tmp=$(mktemp "$BIN.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL -o "$tmp" "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VERSION/go2rtc_linux_arm64"
  echo "$GO2RTC_SHA256  $tmp" | sha256sum -c --status
  chmod 0755 "$tmp"
  mv "$tmp" "$BIN"
fi

want=$(sha256sum "$APP_DIR/requirements.txt" | cut -d' ' -f1)
if [ -x "$VENV/bin/python" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$want" ]; then
  exit 0
fi

mkdir -p "$STATE_DIR"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
rm -f "$STAMP"
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
printf '%s\n' "$want" >"$STAMP"

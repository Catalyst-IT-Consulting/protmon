#!/usr/bin/env bash
# Sync the server's virtualenv after update.sh moves the live tree.
# The venv lives in STATE_DIR so git reset --hard leaves it alone, and pip only
# runs when requirements.txt changed. Non-zero fails the deploy and rolls back.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/protmon}
STATE_DIR=${STATE_DIR:-/var/lib/protmon}
VENV=$STATE_DIR/venv
STAMP=$STATE_DIR/venv.requirements.sha256

# Lets selftest.sh run the real deployer without installing packages.
[ -z "${PROTMON_SKIP_VENV:-}" ] || exit 0

want=$(sha256sum "$APP_DIR/requirements.txt" | cut -d' ' -f1)
if [ -x "$VENV/bin/python" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$want" ]; then
  exit 0
fi

mkdir -p "$STATE_DIR"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
rm -f "$STAMP"
"$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
printf '%s\n' "$want" >"$STAMP"

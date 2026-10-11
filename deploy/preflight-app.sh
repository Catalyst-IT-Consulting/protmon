#!/usr/bin/env bash
# Stack-specific preflight, called by preflight.sh from CI and on the Pi.
# System python3 only: the app's dependencies are not installed here.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m compileall -q server

# Live-config validation against the incoming code hooks in here.

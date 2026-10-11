#!/usr/bin/env bash
# Stack-specific preflight, called by preflight.sh from CI and on the Pi.
# System python3 only: the app's dependencies are not installed here.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m compileall -q server

# Validate the live config against the incoming code, when there is one.
cfg="${PROTMON_CONFIG:-/etc/protmon/config.json}"
if [ -f "$cfg" ]; then
  PYTHONPATH=server python3 -m protmon.config "$cfg"
fi

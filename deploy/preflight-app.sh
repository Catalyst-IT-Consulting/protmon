#!/usr/bin/env bash
# Stack-specific preflight, called by preflight.sh from CI and on the Pi.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests

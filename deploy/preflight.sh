#!/usr/bin/env bash
# Validate a candidate commit before it is allowed near the live tree.
#
# update.sh extracts the incoming commit to a scratch directory and runs this
# copy of the script from there, so a commit that fails here never touches the
# running display. Also run in CI on every push.
#
# Keep this fast and self-contained: it must not need the app's dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
note() { printf 'preflight: %s\n' "$*" >&2; }

while IFS= read -r f; do
  if python3 -m json.tool "$f" >/dev/null 2>&1; then
    note "ok   json $f"
  else
    note "FAIL json $f does not parse"
    fail=1
  fi
done < <(find . -name '*.json' -not -path './.git/*' -not -path './node_modules/*')

while IFS= read -r script; do
  if bash -n "$script" 2>/dev/null; then
    note "ok   syntax $script"
  else
    note "FAIL syntax $script"
    fail=1
  fi
done < <(find deploy -type f -name '*.sh'; find deploy/system/usr/local/bin -type f 2>/dev/null)

for unit in deploy/system/etc/systemd/system/*; do
  [ -e "$unit" ] || continue
  if grep -q '^\[Unit\]' "$unit"; then
    note "ok   unit $(basename "$unit")"
  else
    note "FAIL unit $(basename "$unit") has no [Unit] section"
    fail=1
  fi
done

# Hook for stack-specific checks once the server exists: validate the live
# config against the incoming code, import the package, run unit tests.
if [ -x deploy/preflight-app.sh ]; then
  note "running deploy/preflight-app.sh"
  ./deploy/preflight-app.sh || fail=1
fi

if [ "$fail" = 0 ]; then note "passed"; else note "FAILED"; fi
exit "$fail"

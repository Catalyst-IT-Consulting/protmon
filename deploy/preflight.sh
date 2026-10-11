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

# The firewall must render from sample secrets and pass nft's own check. nft -c
# needs CAP_NET_ADMIN, so try a user+net namespace before giving up.
fwtmp=$(mktemp -d)
trap 'rm -rf "$fwtmp"' EXIT
cat >"$fwtmp/secrets.env" <<'ENV'
PROTMON_ALLOWED_IPS=192.0.2.10,2001:db8::10
PROTMON_ADMIN_NETWORKS=198.51.100.0/24,2001:db8:1::/64
ENV
if PROTMON_SECRETS=$fwtmp/secrets.env deploy/system/usr/local/bin/protmon-firewall --print >"$fwtmp/rules.nft"; then
  runner=
  if ! command -v nft >/dev/null 2>&1; then
    note "skip firewall nft -c (nft not installed)"
  else
    printf 'table inet protmon_probe\n' >"$fwtmp/probe.nft"
    if nft -c -f "$fwtmp/probe.nft" >/dev/null 2>&1; then runner='env'
    elif unshare -rn nft -c -f "$fwtmp/probe.nft" >/dev/null 2>&1; then runner="unshare -rn"
    fi
    if [ -z "$runner" ]; then
      note "skip firewall nft -c (not permitted here)"
    elif $runner nft -c -f "$fwtmp/rules.nft"; then
      note "ok   firewall ruleset"
    else
      note "FAIL firewall ruleset rejected by nft"
      fail=1
    fi
  fi
else
  note "FAIL firewall ruleset does not render"
  fail=1
fi

# Hook for stack-specific checks once the server exists: validate the live
# config against the incoming code, import the package, run unit tests.
if [ -x deploy/preflight-app.sh ]; then
  note "running deploy/preflight-app.sh"
  ./deploy/preflight-app.sh || fail=1
fi

if [ "$fail" = 0 ]; then note "passed"; else note "FAILED"; fi
exit "$fail"

#!/usr/bin/env bash
# Apply deploy/boot to the firmware partition: the video= arguments in
# cmdline.txt and a marked block in config.txt. Changes take effect on the next
# reboot; this script only logs that one is pending and never reboots.
#
# PROTMON_BOOT_DIR relocates the firmware partition for selftest.sh.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/protmon}
BOOT=${PROTMON_BOOT_DIR:-/boot/firmware}
SRC=$APP_DIR/deploy/boot

log() { printf '%s boot-config: %s\n' "$(date -Is)" "$*" >&2; }

if [ ! -d "$BOOT" ]; then
  log "no $BOOT, skipping"
  exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
changed=()

# replace <file> <new>: swap in <new> atomically when it differs.
replace() {
  cmp -s "$2" "$1" && return 0
  local t
  t=$(mktemp "$1.XXXXXX")
  cat "$2" >"$t"
  chmod --reference="$1" "$t" 2>/dev/null || true
  mv "$t" "$1"
  changed+=("${1##*/}")
}

if [ -f "$BOOT/cmdline.txt" ]; then
  read -r -a old <"$BOOT/cmdline.txt" || true
  new=()
  for t in "${old[@]}"; do
    case $t in video=*) ;; *) new+=("$t") ;; esac
  done
  read -r -a want < <(grep -v '^#' "$SRC/cmdline.args")
  new+=("${want[@]}")
  printf '%s\n' "${new[*]}" >"$tmp/cmdline.txt"
  if [ "${#old[@]}" -gt 0 ]; then replace "$BOOT/cmdline.txt" "$tmp/cmdline.txt"; fi
else
  log "no $BOOT/cmdline.txt, skipping"
fi

if [ -f "$BOOT/config.txt" ]; then
  if grep -qx '# BEGIN protmon' "$BOOT/config.txt" && ! grep -qx '# END protmon' "$BOOT/config.txt"; then
    log "config.txt has BEGIN without END, leaving it alone"
  else
    { echo '# BEGIN protmon'; echo '[all]'; cat "$SRC/config.txt"; echo '# END protmon'; } >"$tmp/block"
    awk -v blk="$tmp/block" '
      function emit(  l) { while ((getline l < blk) > 0) print l; close(blk) }
      $0 == "# BEGIN protmon" { emit(); skip = 1; seen = 1; next }
      $0 == "# END protmon" { skip = 0; next }
      !skip { print }
      END { if (!seen) { print ""; emit() } }' "$BOOT/config.txt" >"$tmp/config.txt"
    replace "$BOOT/config.txt" "$tmp/config.txt"
  fi
else
  log "no $BOOT/config.txt, skipping"
fi

if [ "${#changed[@]}" -gt 0 ]; then
  log "updated ${changed[*]}, reboot pending"
fi

#!/bin/sh
# Patch-specific verification: GPS setZoneTime must branch to setSystemTime.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
PATCHED="$HERE/libsvp_basic.so.patched"

if ! command -v aarch64-linux-gnu-objdump >/dev/null 2>&1; then
    echo "[i] GPS disassembly skipped (aarch64 objdump not found)"
    exit 0
fi

DIS=$(mktemp) || exit 1
trap 'rm -f "$DIS"' EXIT INT TERM HUP

aarch64-linux-gnu-objdump -d --start-address=0x4A70 --stop-address=0x4A80 \
    "$PATCHED" >"$DIS" || exit 1
grep -E 'b[[:space:]]+4490' "$DIS" || {
    echo "ERROR: GPS patch does not branch to setSystemTime (0x4490)"
    exit 1
}
echo "[OK] GPS patch: setZoneTime -> setSystemTime (0x4490)"

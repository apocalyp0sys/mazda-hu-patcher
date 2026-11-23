#!/bin/sh
# ---------------------------------------------------------------------------
# build.sh -- reproduce all patch payloads from an official update.bin.
#
# Pipeline:
#   1. extract the fs4 (/svp) ext4 image from update.bin at 0x4947FA37;
#   2. for every patches/*/patch.json with a "build" section:
#        - extract the original file with debugfs;
#        - verify sha256.original;
#        - run the patch tool with --out;
#        - verify sha256.patched;
#        - write .orig/.patched next to the manifest.
#
# Usage (inside the mazda-tools container, from the patcher root):
#   sh patches/build.sh [UPDATE_BIN] [OUT_DIR]
#
# Defaults: UPDATE_BIN = <patcher>/firmware/update.bin, OUT_DIR = this directory.
# Requires: python3, debugfs (e2fsprogs).
# ---------------------------------------------------------------------------
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
PATCHER=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
UPDATE=${1:-"$PATCHER/firmware/update.bin"}
OUT=${2:-"$HERE"}

for tool in python3 debugfs; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool not found: $tool" >&2
        echo "       run this script in the mazda-tools container (see Dockerfile)." >&2
        exit 1
    fi
done

if [ ! -f "$UPDATE" ]; then
    echo "ERROR: update image not found: $UPDATE" >&2
    echo "Usage: sh patches/build.sh [UPDATE_BIN] [OUT_DIR]" >&2
    exit 1
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/hu_patcher_build.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT INT TERM HUP

echo "== [1/2] extract fs4 (/svp) from $UPDATE =="
python3 "$HERE/extract_update.py" "$UPDATE" "$TMP/fs4_1GB.img"

echo
echo "== [2/2] build manifest-driven payloads =="
python3 "$HERE/build_patches.py" \
    --image "$TMP/fs4_1GB.img" \
    --patches-dir "$HERE" \
    --out-dir "$OUT"

echo
echo "PATCHES_BUILD: OK"

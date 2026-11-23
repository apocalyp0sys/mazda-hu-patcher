#!/bin/sh
# ---------------------------------------------------------------------------
# build-release.sh -- build a distributable zip for GitHub Releases.
#
# Required for a full rebuild:
#   firmware/update.bin  (official Mazda update container)
#
# The script:
#   1. rebuilds patches/*.orig / *.patched from firmware/update.bin;
#   2. validates the payloads with hu_patcher.py --self-check;
#   3. packages the patcher, bootstrap payload and patch binaries into
#      dist/hu-patcher-<version>.zip.
#
# Usage (from the patcher root, inside mazda-tools):
#   sh build-release.sh [UPDATE_BIN]
#
# Version priority:
#   VERSION=v1.0.0 sh build-release.sh
#   VERSION file
#   git describe, then the commit hash, then the current date
#
# If firmware/update.bin is absent but the patch binaries already exist, the
# script packages them as-is and prints a warning.
# ---------------------------------------------------------------------------
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
UPDATE=${1:-${UPDATE_BIN:-"$HERE/firmware/update.bin"}}
VERSION=${VERSION:-}

if [ -z "$VERSION" ] && [ -f "$HERE/VERSION" ]; then
    VERSION=$(head -n1 "$HERE/VERSION" | tr -d '[:space:]')
fi
if [ -z "$VERSION" ] && git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    VERSION=$(git -C "$HERE" describe --tags --always --dirty 2>/dev/null || true)
fi
if [ -z "$VERSION" ]; then
    VERSION=$(date +%Y%m%d)
fi

echo "== validate release metadata =="
set -- --root "$HERE" --version "$VERSION"
if [ "${ALLOW_UNRELEASED:-0}" = "1" ]; then
    set -- "$@" --allow-unreleased
fi
if [ "${ALLOW_VERSION_MISMATCH:-0}" = "1" ]; then
    set -- "$@" --allow-version-mismatch
fi
python3 "$HERE/check_release.py" "$@"

if [ -f "$UPDATE" ]; then
    echo "== rebuild payloads from $UPDATE =="
    sh "$HERE/patches/build.sh" "$UPDATE"
else
    echo "WARNING: update image not found: $UPDATE"
    echo "         validating and packaging the existing payloads in patches/"
fi

echo
echo "== validate payloads =="
python3 "$HERE/hu_patcher.py" --self-check

echo
echo "== package release =="
python3 "$HERE/package_release.py" \
    --root "$HERE" \
    --version "$VERSION" \
    --out-dir "$HERE/dist"

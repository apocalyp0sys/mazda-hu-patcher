#!/bin/sh
# ---------------------------------------------------------------------------
# build-patches.sh -- prove that all manifest payloads can be rebuilt.
#
# Runs patches/build.sh into a temporary directory and compares every produced
# file with the payloads in patches/.
#
# Run from the patcher root in the mazda-tools container:
#   docker run --rm -v "<patcher>:/work" -w /work mazda-tools \
#       sh test/build-patches.sh [UPDATE_BIN]
# ---------------------------------------------------------------------------
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
UPDATE=${1:-"$ROOT/firmware/update.bin"}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/hu_patcher_rebuild.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT INT TERM HUP

sh "$ROOT/patches/build.sh" "$UPDATE" "$TMP" || exit 1

echo
echo "== compare rebuilt payloads with patches/ =="
python3 - "$ROOT" "$TMP" <<'PY' || exit 1
import filecmp
import sys
from pathlib import Path

root = Path(sys.argv[1])
tmp = Path(sys.argv[2])
sys.path.insert(0, str(root))
from patch_loader import load_patches  # noqa: E402

failed = False
for patch in load_patches(root / "patches"):
    patch_dir = Path(patch["_dir"]).name
    for index, file_spec in enumerate(patch["files"], 1):
        if file_spec["op"] == "symlink":
            print("skip  - %s file %d (symlink, no payload)" %
                  (patch["id"], index))
            continue
        if not file_spec.get("build"):
            print("skip  - %s file %d (no build section)" %
                  (patch["id"], index))
            continue
        for key, label in (("local_original", "original"),
                           ("local_patched", "patched")):
            if file_spec[key] is None:
                continue
            source = Path(file_spec[key])
            rebuilt = tmp / patch_dir / source.name
            if not rebuilt.is_file():
                print("FAIL  - %s file %d %s (missing rebuilt file)" %
                      (patch["id"], index, label))
                failed = True
                continue
            if filecmp.cmp(str(source), str(rebuilt), shallow=False):
                print("ok    - %s file %d %s: %s" %
                      (patch["id"], index, label, source.name))
            else:
                print("FAIL  - %s file %d %s: %s" %
                      (patch["id"], index, label, source.name))
                failed = True

if failed:
    sys.exit(1)
PY

echo
echo "PATCHES_REBUILD: OK"

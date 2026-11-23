#!/bin/sh
# ---------------------------------------------------------------------------
# validate.sh -- offline validation of the manifest-driven patcher payloads.
#
# Run from the patcher root in a container:
#   docker run --rm -v "<patcher>:/work" -w /work mazda-tools \
#       sh test/validate.sh
# ---------------------------------------------------------------------------
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd) || exit 1
cd "$ROOT" || exit 1

TMPD=
cleanup() {
    [ -z "$TMPD" ] || rm -rf "$TMPD"
}
trap cleanup EXIT INT TERM HUP

echo "== local payload / remote-script self-check =="
python3 "$ROOT/hu_patcher.py" --self-check || exit 1

echo
echo "== operation log =="
LOGFILE=$(mktemp) || exit 1
if ! python3 "$ROOT/hu_patcher.py" --self-check --log "$LOGFILE" \
    >/dev/null 2>&1; then
    echo "FAIL - self-check with --log"
    rm -f "$LOGFILE"
    exit 1
fi
if ! grep -q "=== HU patcher" "$LOGFILE"; then
    echo "FAIL - log header missing"
    rm -f "$LOGFILE"
    exit 1
fi
if ! grep -q "SELF-CHECK: OK" "$LOGFILE"; then
    echo "FAIL - self-check output missing from log"
    rm -f "$LOGFILE"
    exit 1
fi
rm -f "$LOGFILE"
echo "ok   - detailed operation log"

echo
echo "== patch tools reproduce payloads =="
TMPD=$(mktemp -d) || exit 1
python3 - "$ROOT" "$TMPD" <<'PY' || exit 1
import filecmp
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
tmp = Path(sys.argv[2])
sys.path.insert(0, str(root))
from patch_loader import load_patches  # noqa: E402

for patch in load_patches(root / "patches"):
    for index, file_spec in enumerate(patch["files"], 1):
        build = file_spec.get("build")
        if not build:
            continue
        tool = build["tool"]
        out = tmp / ("%s_%02d_%s" %
                     (patch["_dir"].name, index,
                      Path(file_spec["local_patched"]).name))
        cmd = [sys.executable, str(tool)]
        if build.get("source"):
            cmd.append(str(file_spec["local_original"]))
        cmd += ["--out", str(out)]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.STDOUT)
        if result.returncode != 0:
            sys.exit("ERROR: %s file %d tool failed for %s" %
                     (patch["id"], index, tool))
        if not filecmp.cmp(str(out), str(file_spec["local_patched"]),
                           shallow=False):
            sys.exit("ERROR: %s file %d tool does not reproduce %s" %
                     (patch["id"], index, file_spec["local_patched"]))
        print("[OK] %s file %d: %s reproduces %s" %
              (patch["id"], index, Path(tool).name,
               Path(file_spec["local_patched"]).name))
PY

echo
echo "== per-patch verification scripts =="
for verify in "$ROOT"/patches/*/verify.sh; do
    [ -f "$verify" ] || continue
    echo "-- $verify"
    sh "$verify" || exit 1
done

echo
echo "VALIDATION: OK"

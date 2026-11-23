#!/bin/sh
# ---------------------------------------------------------------------------
# verify.sh -- Fast Login manifest check (no HU required).
# ---------------------------------------------------------------------------
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1

python3 - "$HERE/patch.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

files = data["files"]
assert len(files) == 1, "Fast Login must have exactly one file"
spec = files[0]
assert spec["op"] == "symlink", spec
assert spec["target"] == \
    "/home/root/.config/systemd/user/lightmediascanner.service", spec
assert spec["link"] == "/dev/null", spec
assert spec.get("create_dirs") is True, spec
print("[OK] Fast Login: root mask -> /dev/null")
PY

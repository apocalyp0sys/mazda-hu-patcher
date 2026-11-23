#!/bin/sh
# ---------------------------------------------------------------------------
# verify.sh -- Callfix manifest and unit sanity check (no HU required).
# ---------------------------------------------------------------------------
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1

python3 - "$HERE" <<'PY'
import json
import sys
from pathlib import Path

here = Path(sys.argv[1])
with open(here / "patch.json", encoding="utf-8") as f:
    data = json.load(f)

files = {item["target"]: item for item in data["files"]}
assert set(files) == {
    "/svp/bin/svp-callfix",
    "/etc/systemd/system/svp-callfix.service",
    "/etc/systemd/system/multi-user.target.wants/svp-callfix.service",
}, sorted(files)

daemon = files["/svp/bin/svp-callfix"]
assert daemon["op"] == "create" and daemon["mode"] == "0755", daemon

unit = files["/etc/systemd/system/svp-callfix.service"]
assert unit["op"] == "create" and unit["mode"] == "0644", unit

enable = files["/etc/systemd/system/multi-user.target.wants/svp-callfix.service"]
assert enable["op"] == "symlink", enable
assert enable["link"] == "/etc/systemd/system/svp-callfix.service", enable

binary = (here / "svp-callfix").read_bytes()
assert binary[:4] == b"\x7fELF", "svp-callfix is not an ELF file"
assert int.from_bytes(binary[18:20], "little") == 0xB7, "svp-callfix is not aarch64"

unit_text = (here / "svp-callfix.service").read_text(encoding="utf-8")
for line in (
    "ExecStart=/svp/bin/svp-callfix",
    "Environment=LD_LIBRARY_PATH=/svp/lib/someip:/svp/lib:/usr/lib64",
    "Environment=COMMONAPI_DEFAULT_BINDING=someip",
    "Environment=COMMONAPI_CONFIG=/svp/etc/someip/commonapi.ini",
    "Environment=VSOMEIP_CONFIGURATION=/svp/etc/vsomeip.json",
    "Restart=on-failure",
    "TimeoutStopSec=5",
):
    assert line in unit_text, line

print("[OK] Callfix: daemon, systemd unit and enable symlink are consistent")
PY

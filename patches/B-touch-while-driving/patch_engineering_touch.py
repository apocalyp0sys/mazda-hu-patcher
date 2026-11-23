#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Byte-patch for svp.svc.engineering
#
# The REAL "disable touch while driving" source.  svp.svc.engineering receives
# the vehicle speed from the MCU (Remote/Diag message) and toggles the kernel
# misc attribute /sys/devices/virtual/misc/alpine_display/disable_touch by
# running  system("echo 0|1 > /sys/devices/virtual/misc/alpine_display/disable_touch").
#
#   function : SVPRmtEngineering::msgDecode(std::vector<unsigned char> const&)
#   file off : 0x630A8   (== .text VA)
#   logic    : speed = be16(bytes[5],bytes[6]);  frame byte[0]==0x03
#              speed >  0x383 (899) -> echo 1 (DISABLE touch)
#              speed <= 0x2bc (700) -> echo 0 (ENABLE  touch)
#              700..899 = hysteresis, no change; state flag at [this+10]
#
# We make the disable branch unreachable: the conditional "b.ls" (speed<=899 ->
# enable path) becomes an unconditional branch to the enable path.  For the
# speed>899 case the enable path immediately does "cmp #0x2bc ; b.hi <ret>",
# i.e. it returns WITHOUT writing sysfs and WITHOUT logging anything -- so this
# patch is log-free (no "Set touch screen disable/enable" spam) and touch is
# simply never disabled by speed.
#
#   before : a9 18 00 54   b.ls 0x633bc   (speed<=899 -> enable branch)
#   after  : c5 00 00 14   b    0x633bc   (always -> enable branch; >899 bails)
#
# Usage:
#   patch_engineering_touch.py <file>            # check only
#   patch_engineering_touch.py <file> --apply    # in-place, <file>.orig backup
#   patch_engineering_touch.py <file> --revert
#   patch_engineering_touch.py <file> --out X    # write patched copy
# ---------------------------------------------------------------------------
import argparse
import hashlib
import os
import shutil
import sys

OFF = 0x630A8
ORIG = bytes.fromhex("a9180054")   # b.ls 0x633bc
PATCH = bytes.fromhex("c5000014")  # b    0x633bc

KNOWN_SHA256 = "c46bc285bd20813ca7374b10069cda9f5f76bc7edd53f5b1e759d056087e61ed"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read(p):
    with open(p, "rb") as f:
        return f.read()


def write(p, d):
    with open(p, "wb") as f:
        f.write(d)


def status(d):
    if d[OFF:OFF + 4] == ORIG:
        return "ORIGINAL (touch disabled by vehicle speed)"
    if d[OFF:OFF + 4] == PATCH:
        return "PATCHED (touch never disabled by speed; log-free)"
    return "UNKNOWN bytes at 0x%X: %s" % (OFF, d[OFF:OFF + 4].hex())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    path = args.file
    if not os.path.isfile(path):
        sys.exit("[-] not found: %s" % path)

    data = read(path)
    print("[i] file   : %s" % path)
    print("[i] size   : %d" % len(data))
    print("[i] sha256 : %s" % sha256(data))
    print("[i] state  : %s" % status(data))

    if args.revert:
        bak = path + ".orig"
        if not os.path.isfile(bak):
            sys.exit("[-] no backup: %s" % bak)
        shutil.copy2(bak, path)
        print("[+] reverted from %s" % bak)
        return

    if args.apply:
        if data[OFF:OFF + 4] == PATCH:
            print("[=] already patched")
            return
        if data[OFF:OFF + 4] != ORIG:
            sys.exit("[-] unexpected bytes at 0x%X: %s" % (OFF, data[OFF:OFF + 4].hex()))
        if not args.force and sha256(data) != KNOWN_SHA256:
            print("[!] sha256 differs from known build; use --force")
            sys.exit(1)
        bak = path + ".orig"
        if not os.path.isfile(bak):
            shutil.copy2(path, bak)
            print("[i] backup : %s" % bak)
        write(path, data[:OFF] + PATCH + data[OFF + 4:])
        print("[+] patched %s" % path)
        return

    if args.out:
        out = data if data[OFF:OFF + 4] == PATCH else data[:OFF] + PATCH + data[OFF + 4:]
        write(args.out, out)
        print("[+] wrote %s (sha256 %s)" % (args.out, sha256(out)))
        return

    if data[OFF:OFF + 4] == ORIG:
        print("[i] patchable: 0x%X %s -> %s" % (OFF, ORIG.hex(), PATCH.hex()))
    else:
        print("[!] not patchable by this tool")


if __name__ == "__main__":
    main()

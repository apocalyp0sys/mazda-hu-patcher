#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Byte-patch for libsvp_basic.so (patch A: GPS / timezone).
#
# The factory firmware applies a hardcoded +8 h offset to GPS UTC time:
#
#   SVPSatSync::SyncSatTime() -> SVPTime::setZoneTime(satUTC, 8)
#                             -> SVPTime::setLocalTime() -> mktime() -> stime()
#
# That keeps the system clock wrong outside UTC+8.  This patch rewrites the
# first instruction of SVPTime::setZoneTime() into a tail branch to
# SVPTime::setSystemTime(), which writes GPS UTC directly to the system clock.
# Local time is then derived from /etc/localtime by the OS, so the configured
# zone actually takes effect.
#
#   function : SVPTime::setZoneTime(SVPTypeSystemTime const&, int)
#   file off : 0x4A70   (== .text VA for this ELF)
#   before   : fd 7b bd a9   (a9bd7bfd  stp x29, x30, [sp, #-48]!)
#   after    : 88 fe ff 17   (17fffe88  b 0x4490 <SVPTime::setSystemTime>)
#
# Usage:
#   patch_gps_timezone.py <file>              # check only
#   patch_gps_timezone.py <file> --apply      # in-place, <file>.orig backup
#   patch_gps_timezone.py <file> --revert     # restore from <file>.orig
#   patch_gps_timezone.py <file> --out X      # write patched copy to X
# ---------------------------------------------------------------------------
import argparse
import hashlib
import os
import shutil
import sys

OFF = 0x4A70
ORIG = bytes.fromhex("fd7bbda9")   # a9bd7bfd  stp x29, x30, [sp, #-48]!
PATCH = bytes.fromhex("88feff17")  # 17fffe88  b 0x4490 (setSystemTime)

KNOWN_SHA256 = "d7ae14f24fc631d2e23ab5fc745ff8248660b1b62253b1ec3e48c21d5097367a"
PATCHED_SHA256 = "bf26789698fd224ed6e99537d17756568fd48ad1cc854111f9ba9ff4787625c4"


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
        return "ORIGINAL (GPS time gets a hardcoded local offset)"
    if d[OFF:OFF + 4] == PATCH:
        return "PATCHED (GPS UTC -> system clock; local zone from /etc/localtime)"
    return "UNKNOWN bytes at 0x%X: %s" % (OFF, d[OFF:OFF + 4].hex())


def main():
    ap = argparse.ArgumentParser(
        description="Patch libsvp_basic.so: setZoneTime -> setSystemTime")
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
            sys.exit("[-] unexpected bytes at 0x%X: %s" %
                     (OFF, data[OFF:OFF + 4].hex()))
        if not args.force and sha256(data) != KNOWN_SHA256:
            print("[!] sha256 differs from the known build; use --force")
            sys.exit(1)
        bak = path + ".orig"
        if not os.path.isfile(bak):
            shutil.copy2(path, bak)
            print("[i] backup : %s" % bak)
        write(path, data[:OFF] + PATCH + data[OFF + 4:])
        print("[+] patched %s" % path)
        return

    if args.out:
        out = data if data[OFF:OFF + 4] == PATCH else \
            data[:OFF] + PATCH + data[OFF + 4:]
        write(args.out, out)
        got = sha256(out)
        print("[+] wrote %s (sha256 %s)" % (args.out, got))
        if got != PATCHED_SHA256:
            print("[!] unexpected patched sha256 (expected %s)" % PATCHED_SHA256)
            sys.exit(1)
        return

    if data[OFF:OFF + 4] == ORIG:
        print("[i] patchable: 0x%X %s -> %s" %
              (OFF, ORIG.hex(), PATCH.hex()))
    else:
        print("[!] not patchable by this tool")


if __name__ == "__main__":
    main()

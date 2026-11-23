#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# extract_update.py -- extract an ext4 filesystem image from update.bin.
#
# update.bin is a Mazda CMU update container.  The four real ext4 filesystems
# start at fixed offsets; we only need fs4 (the /svp partition), which holds
# libsvp_basic.so and svp.svc.engineering.
#
# The script reads the ext4 superblock at the expected offset, derives the
# real image size from the block count, and copies exactly that many bytes.
# It does not mount anything and does not modify update.bin.
#
# Usage:
#   python3 extract_update.py <update.bin> <out.img> [--fs fs4]
#
# Exit codes: 0 = OK, 1 = file/IO error, 2 = bad arguments/superblock.
# ---------------------------------------------------------------------------
import argparse
import hashlib
import os
import struct
import sys

# (offset in update.bin, human name)
FILESYSTEMS = {
    "fs1": (0xB2E9ED, "fs1_754MB.img"),
    "fs2": (0x2DAE8BED, "fs2_134MB.img"),
    "fs3": (0x35AE8DED, "fs3_314MB.img"),
    "fs4": (0x4947FA37, "fs4_1GB.img"),
}

EXT4_MAGIC = 0xEF53


def parse_superblock(f, pos):
    """Return ext4 geometry for an image starting at pos, or None."""
    f.seek(pos + 1024)
    sb = f.read(1024)
    if len(sb) < 1024:
        return None
    magic = struct.unpack_from("<H", sb, 56)[0]
    if magic != EXT4_MAGIC:
        return None
    log_bsize = struct.unpack_from("<I", sb, 24)[0]
    if log_bsize > 2:
        return None
    bsize = 1024 << log_bsize
    blocks_lo = struct.unpack_from("<I", sb, 4)[0]
    blocks_hi = struct.unpack_from("<I", sb, 336)[0]
    blocks = blocks_lo | (blocks_hi << 32)
    inodes = struct.unpack_from("<I", sb, 0)[0]
    if not blocks or not inodes:
        return None
    return {
        "pos": pos,
        "block_size": bsize,
        "blocks": blocks,
        "size": blocks * bsize,
        "inodes": inodes,
        "label": sb[120:136].split(b"\0")[0].decode("utf-8", "replace"),
        "uuid": format_uuid(sb[104:120]),
    }


def format_uuid(raw):
    if len(raw) != 16:
        return "?"
    h = raw.hex()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def copy_range(src_path, dst_path, pos, size):
    h = hashlib.sha256()
    with open(src_path, "rb") as src:
        src.seek(pos)
        with open(dst_path, "wb") as dst:
            left = size
            while left:
                chunk = src.read(min(1024 * 1024, left))
                if not chunk:
                    raise IOError("unexpected EOF while reading %s" % src_path)
                dst.write(chunk)
                h.update(chunk)
                left -= len(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(
        description="Extract an ext4 image from Mazda update.bin")
    ap.add_argument("update", help="path to update.bin")
    ap.add_argument("out", help="output image path")
    ap.add_argument("--fs", default="fs4", choices=sorted(FILESYSTEMS),
                    help="filesystem to extract (default: fs4)")
    args = ap.parse_args()

    if not os.path.isfile(args.update):
        print("ERROR: update image not found: %s" % args.update, file=sys.stderr)
        return 1

    offset, default_name = FILESYSTEMS[args.fs]
    upd_size = os.path.getsize(args.update)

    with open(args.update, "rb") as f:
        info = parse_superblock(f, offset)
    if not info:
        print("ERROR: no ext4 superblock at 0x%X in %s" %
              (offset, args.update), file=sys.stderr)
        return 2
    if info["pos"] + info["size"] > upd_size:
        print("ERROR: %s ends at 0x%X, beyond the end of %s (0x%X)" %
              (args.fs, info["pos"] + info["size"], args.update, upd_size),
              file=sys.stderr)
        return 2

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    print("[i] source : %s (%d bytes)" % (args.update, upd_size))
    print("[i] image  : %s @ 0x%X, %d bytes (%.1f MiB), label=%r, uuid=%s" %
          (args.fs, info["pos"], info["size"], info["size"] / 1048576.0,
           info["label"], info["uuid"]))
    if args.out.endswith(("/", os.sep)) or os.path.isdir(args.out):
        args.out = os.path.join(args.out, default_name)

    sha = copy_range(args.update, args.out, info["pos"], info["size"])
    print("[+] wrote  : %s" % args.out)
    print("[+] sha256 : %s" % sha)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (IOError, OSError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)

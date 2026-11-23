#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# build_patches.py -- rebuild all manifest-driven patch payloads.
#
# Reads patch manifests, extracts the original file from the fs4 image with
# debugfs, runs each patch tool with "--out", and verifies the known hashes.
#
# Usage:
#   python3 patches/build_patches.py --image fs4_1GB.img \
#       --patches-dir patches [--out-dir DIR] [--only KEY]
# ---------------------------------------------------------------------------
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHER = HERE.parent
sys.path.insert(0, str(PATCHER))

from patch_loader import PatchConfigError, load_patches  # noqa: E402


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_with_debugfs(image, source, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = "dump %s %s" % (source, dest)
    result = subprocess.run(["debugfs", "-R", cmd, str(image)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    if not dest.is_file():
        raise SystemExit("ERROR: debugfs could not extract %s from %s:\n%s" %
                         (source, image, (result.stdout or "").strip()))


def build_file(patch, file_spec, index, image, out_root, work):
    build = file_spec.get("build")
    if not build:
        return
    if build.get("fs", "fs4") != "fs4":
        raise SystemExit("ERROR: patch %s file %d uses unsupported fs %r" %
                         (patch["id"], index, build.get("fs")))

    source = build.get("source")
    tool = Path(build["tool"])
    if not tool.is_file():
        raise SystemExit("ERROR: patch %s file %d build tool not found: %s" %
                         (patch["id"], index, tool))

    patch_out = Path(out_root) / patch["_dir"].name
    patch_out.mkdir(parents=True, exist_ok=True)
    patched_out = patch_out / Path(file_spec["local_patched"]).name

    tmp_original = None
    if source:
        tmp_original = Path(work) / ("%s_%02d.original" % (patch["key"], index))
        extract_with_debugfs(image, source, tmp_original)
        got = sha256_file(tmp_original)
        if got != file_spec["hash_original"]:
            raise SystemExit(
                "ERROR: patch %s file %d original hash mismatch: %s (expected %s)" %
                (patch["id"], index, got, file_spec["hash_original"]))

        cmd = [sys.executable, str(tool), str(tmp_original),
               "--out", str(patched_out)]
    else:
        cmd = [sys.executable, str(tool), "--out", str(patched_out)]

    result = subprocess.run(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        raise SystemExit("ERROR: patch %s file %d tool failed (exit %d)" %
                         (patch["id"], index, result.returncode))

    if tmp_original is not None and file_spec["local_original"] is not None:
        original_out = patch_out / Path(file_spec["local_original"]).name
        shutil.copy2(tmp_original, original_out)
        got = sha256_file(original_out)
        if got != file_spec["hash_original"]:
            raise SystemExit(
                "ERROR: patch %s file %d original payload hash mismatch: %s" %
                (patch["id"], index, got))

    got = sha256_file(patched_out)
    if got != file_spec["hash_patched"]:
        raise SystemExit("ERROR: patch %s file %d patched hash mismatch: %s (expected %s)" %
                         (patch["id"], index, got, file_spec["hash_patched"]))

    print("[OK] %s file %d %s -> %s" %
          (patch["id"], index, file_spec["target"], patched_out))


def main():
    ap = argparse.ArgumentParser(description="Rebuild manifest-driven payloads")
    ap.add_argument("--image", required=True, help="fs4 ext4 image")
    ap.add_argument("--patches-dir", default=str(HERE),
                    help="directory containing patch subdirectories")
    ap.add_argument("--out-dir", default=None,
                    help="output root (default: patches directory)")
    ap.add_argument("--only", default=None,
                    help="build only the patch with this key or id")
    args = ap.parse_args()

    image = Path(args.image)
    if not image.is_file():
        raise SystemExit("ERROR: image not found: %s" % image)

    patches_dir = Path(args.patches_dir).resolve()
    out_root = Path(args.out_dir).resolve() if args.out_dir else patches_dir

    try:
        patches = load_patches(patches_dir)
    except PatchConfigError as e:
        raise SystemExit("ERROR: invalid patch configuration: %s" % e)

    work = tempfile.mkdtemp(prefix="hu_patcher_build_")
    try:
        for patch in patches:
            if args.only and args.only not in (patch["key"], patch["id"]):
                continue
            for index, file_spec in enumerate(patch["files"], 1):
                if file_spec["op"] == "symlink":
                    print("[i] %s file %d (%s) is a symlink; no payload" %
                          (patch["id"], index, file_spec["target"]))
                    continue
                if file_spec.get("build"):
                    build_file(patch, file_spec, index, image,
                               out_root, work)
                else:
                    print("[i] %s file %d (%s) has no build section; "
                          "keeping existing payload" %
                          (patch["id"], index, file_spec["target"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (IOError, OSError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)

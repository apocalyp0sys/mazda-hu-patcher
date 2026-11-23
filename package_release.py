#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# package_release.py -- build the distributable HU patcher zip.
#
# The zip contains the patcher, the bootstrap payload and every enabled patch
# (manifest, tool, hook and generated .orig/.patched binaries), so an end user
# does not need update.bin or any build tools.
#
# Usage:
#   python3 package_release.py --version v1.0.0 [--out-dir dist]
#   python3 package_release.py --root . --version dev --out-dir dist
#
# Exits non-zero if any required file is missing (for example patch binaries
# that have not been built yet).
# ---------------------------------------------------------------------------
import argparse
import hashlib
import re
import stat
import sys
import time
import zipfile
from pathlib import Path

from patch_loader import PatchConfigError, load_patches

CORE_FILES = [
    "Dockerfile",
    "hu_patcher.py",
    "mock_hu_patcher.py",
    "hu-patcher.cmd",
    "hu-patcher.sh",
    "run.cmd",
    "run.ps1",
    "check_release.py",
    "patch_loader.py",
    "README.md",
    "ROOT-PASSWORD.md",
    "BOOT-SPEED.md",
    "LICENSE",
    "CHANGELOG.md",
    "firmware/PLACE_update.bin_HERE",
    "patches/README.md",
    "patches/build.sh",
    "patches/build_patches.py",
    "patches/extract_update.py",
    "bootstrap/README.md",
    "bootstrap/v2/install.sh",
    "bootstrap/v2/preflight.sh",
    "bootstrap/v2/boot.sh",
    "bootstrap/v2/sysctl-shim.sh",
    "bootstrap/v2/status.sh",
    "bootstrap/v2/uninstall.sh",
]


def safe_version(value):
    value = re.sub(r"[^A-Za-z0-9._+-]+", "-", value).strip("-")
    return value or "dev"


def patch_files(root):
    """Return release-relative files for every enabled patch manifest."""
    patches_dir = root / "patches"
    if not patches_dir.is_dir():
        raise SystemExit("ERROR: patches directory not found: %s" % patches_dir)

    try:
        patches = load_patches(patches_dir)
    except PatchConfigError as e:
        raise SystemExit("ERROR: invalid patch configuration: %s" % e)

    def add(files, path):
        files.append(Path(path).relative_to(root).as_posix())

    files = []
    for patch in patches:
        rel_dir = patch["_dir"].relative_to(root).as_posix()
        files.append(rel_dir + "/patch.json")

        for file_spec in patch["files"]:
            if file_spec["local_patched"] is not None:
                add(files, file_spec["local_patched"])
            if file_spec["local_original"] is not None:
                add(files, file_spec["local_original"])
            build = file_spec.get("build")
            if build and build.get("tool"):
                add(files, build["tool"])

        if patch.get("hook_path"):
            add(files, patch["hook_path"])

        for optional in ("README.md", "verify.sh"):
            candidate = patch["_dir"] / optional
            if candidate.is_file():
                files.append(rel_dir + "/" + optional)
    return files


def add_file(zf, root, rel, prefix):
    src = root / rel
    if not src.is_file():
        raise SystemExit("ERROR: missing release file: %s" % src)
    arc = prefix + rel
    info = zipfile.ZipInfo(arc)
    info.date_time = time.localtime(src.stat().st_mtime)[:6]
    executable = src.suffix in (".py", ".sh") or src.name.endswith(".cmd")
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as f:
        zf.writestr(info, f.read())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Package the HU patcher release")
    ap.add_argument("--root", default=".", help="patcher source root")
    ap.add_argument("--version", required=True, help="release version tag")
    ap.add_argument("--out-dir", default="dist", help="output directory")
    ap.add_argument("--name", default=None, help="override zip base name")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    version = safe_version(args.version)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    base = args.name or ("hu-patcher-%s" % version)
    out_path = out_dir / (base + ".zip")
    prefix = base + "/"

    files = []
    for rel in CORE_FILES + patch_files(root):
        if rel not in files:
            files.append(rel)

    for rel in files:
        if not (root / rel).is_file():
            raise SystemExit("ERROR: missing release file: %s" % (root / rel))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            add_file(zf, root, rel, prefix)

        info = zipfile.ZipInfo(prefix + "VERSION")
        info.date_time = time.localtime()[:6]
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, version + "\n")

    print("RELEASE_ZIP: %s" % out_path)
    print("RELEASE_VERSION: %s" % version)
    print("RELEASE_FILES: %d" % (len(files) + 1))
    print("RELEASE_SHA256: %s" % sha256_file(out_path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (IOError, OSError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)

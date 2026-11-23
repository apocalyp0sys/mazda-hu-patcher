#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# hu_patcher.py -- universal patcher for Mazda CMU (KBST-41-47-M5 / DS04L).
#
# Usage (from the patcher root):
#   python3 hu_patcher.py <HU_IP>
#   python3 hu_patcher.py <HU_IP> --key path/to/root_key
#   python3 hu_patcher.py --self-check
#
# Patches:
#   A) GPS/timezone: /svp/lib/libsvp_basic.so
#      original sha256 d7ae14f2...
#      patched  sha256 bf2678...
#      Patch: SVPTime::setZoneTime -> setSystemTime (GPS UTC; local time via
#      /etc/localtime).  Install also asks for a UTC offset / IANA zone and
#      sets /etc/localtime + /etc/timezone (with backup).
#   B) touch while driving: /svp/bin/svp.svc.engineering
#      original sha256 c46bc2...
#      patched  sha256 4419a2...
#      Patch: makes the speed>899 "disable_touch" branch unreachable.
#
# The script never stops services and never flashes boot/kernel/system
# partitions.  Patch A additionally changes /etc/localtime and /etc/timezone
# (with a backup); file replacement is atomic (temp + mv), with a verified
# backup and a post-write hash check.
# ---------------------------------------------------------------------------

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from patch_loader import PatchConfigError, load_patches

HERE = Path(__file__).resolve().parent
PATCH_DIR = HERE / "patches"
V2_DIR = HERE / "bootstrap" / "v2"


def read_version():
    try:
        value = (HERE / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return value or "dev"

try:
    PATCHES = load_patches(PATCH_DIR)
except PatchConfigError as e:
    raise SystemExit("ERROR: invalid patch configuration: %s" % e)

PAUSE_ON_EXIT = True

# ---------------------------------------------------------------------------
# operation log
# ---------------------------------------------------------------------------
LOG_FILE = None
LOG_PATH = None
LOG_ORIG_STDOUT = None
LOG_ORIG_STDERR = None


class _Tee:
    """Duplicate stdout/stderr writes into the operation log."""

    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, data):
        if self.stream is not None:
            self.stream.write(data)
        if self.log is not None:
            self.log.write(data)
        return len(data)

    def flush(self):
        for stream in (self.stream, self.log):
            if stream is not None:
                try:
                    stream.flush()
                except Exception:
                    pass

    def isatty(self):
        return bool(getattr(self.stream, "isatty", lambda: False)())

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self.stream, "errors", "replace")


def log_only(message):
    """Write a timestamped line to the log without printing it."""
    if LOG_FILE is None:
        return
    try:
        LOG_FILE.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                    message))
        LOG_FILE.flush()
    except Exception:
        pass


def log_block(tag, text):
    """Write a multi-line block with a tag prefix."""
    lines = (text or "").splitlines()
    if not lines:
        lines = [""]
    for line in lines:
        log_only("[%s] %s" % (tag, line))


def choose_log_path(args):
    """Return the log path, or None when logging is disabled."""
    if getattr(args, "no_log", False) or \
            os.environ.get("HU_PATCHER_NO_LOG") == "1":
        return None
    if getattr(args, "log", None):
        path = Path(args.log).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    if getattr(args, "self_check", False):
        return None
    return HERE / "logs" / ("hu_patcher-%s.log" % time.strftime("%Y%m%d-%H%M%S"))


def start_logging(path, args):
    global LOG_FILE, LOG_PATH, LOG_ORIG_STDOUT, LOG_ORIG_STDERR
    if path is None:
        return
    stream = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "w", encoding="utf-8", newline="\n")
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / path.name
        try:
            stream = open(fallback, "w", encoding="utf-8", newline="\n")
            path = fallback
        except OSError:
            print("WARNING: cannot open log file %s: %s" % (path, exc),
                  file=sys.stderr)
            return
    LOG_FILE = stream
    LOG_PATH = path
    LOG_ORIG_STDOUT = sys.stdout
    LOG_ORIG_STDERR = sys.stderr
    sys.stdout = _Tee(sys.stdout, stream)
    sys.stderr = _Tee(sys.stderr, stream)

    log_only("=== HU patcher %s ===" % read_version())
    log_only("started: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log_only("python: %s" % sys.version.replace("\n", " "))
    log_only("os: %s" % platform.platform())
    log_only("host: %s" % (getattr(args, "host", None) or "(prompted)"))
    log_only("port: %s" % getattr(args, "port", 22))
    log_only("key: %s" % (getattr(args, "key", None) or "(auto)"))
    log_only("self-check: %s" % getattr(args, "self_check", False))
    log_only("===")


def stop_logging(rc):
    global LOG_FILE, LOG_PATH, LOG_ORIG_STDOUT, LOG_ORIG_STDERR
    if LOG_FILE is None:
        return
    log_only("finished: %s exit=%s" %
             (time.strftime("%Y-%m-%d %H:%M:%S"), rc))
    try:
        LOG_FILE.flush()
    except Exception:
        pass
    sys.stdout = LOG_ORIG_STDOUT or sys.__stdout__
    sys.stderr = LOG_ORIG_STDERR or sys.__stderr__
    try:
        LOG_FILE.close()
    except Exception:
        pass
    LOG_FILE = None
    LOG_ORIG_STDOUT = None
    LOG_ORIG_STDERR = None


def log_exception():
    if LOG_FILE is not None:
        log_block("traceback", traceback.format_exc())



STATUS_INSTALLED = "Installed"
STATUS_NOT_INSTALLED = "Not installed"
STATUS_CONFLICT = "Conflict (unknown file version)"
STATUS_PARTIAL = "Partial (mixed file states)"
STATUS_OUTDATED = "Outdated (update available)"

# ANSI colors for patch statuses.
STATUS_COLORS = {
    STATUS_INSTALLED: "\033[32m",      # green
    STATUS_NOT_INSTALLED: "\033[33m",  # yellow
    STATUS_CONFLICT: "\033[31m",       # red
    STATUS_PARTIAL: "\033[36m",        # cyan
    STATUS_OUTDATED: "\033[35m",       # magenta
}
COLOR_RESET = "\033[0m"
USE_COLOR = False

STATE_DIR = "/svp/patcher/state"
PATCH_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")


def version_tuple(value):
    text = str(value or "").strip()
    if not PATCH_VERSION_RE.match(text):
        return None
    parts = [int(x) for x in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def compare_versions(left, right):
    a = version_tuple(left)
    b = version_tuple(right)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def _enable_windows_ansi():
    """Enable ANSI escape processing in the Windows console (Win10+)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def setup_color(mode):
    global USE_COLOR
    if mode == "never":
        USE_COLOR = False
    elif mode == "always":
        USE_COLOR = True
    else:
        if os.environ.get("NO_COLOR"):
            USE_COLOR = False
        elif os.environ.get("FORCE_COLOR"):
            USE_COLOR = True
        else:
            USE_COLOR = sys.stdout.isatty()
    if USE_COLOR:
        _enable_windows_ansi()


def colorize_status(status):
    if not USE_COLOR:
        return status
    color = STATUS_COLORS.get(status)
    if not color:
        return status
    return color + status + COLOR_RESET


# ---------------------------------------------------------------------------
# patch hooks
# ---------------------------------------------------------------------------
def patch_hook(patch, name):
    hook = patch.get("_hook")
    fn = getattr(hook, name, None) if hook is not None else None
    return fn if callable(fn) else None


def call_patch_hook(patch, name, *args):
    fn = patch_hook(patch, name)
    if fn is None:
        return None
    try:
        return fn(PATCH_API, *args)
    except Exception as e:
        print("WARNING: patch %s hook %s failed: %s" %
              (patch["id"], name, e))
        return False


def print_patch_details(patch, ssh, indent):
    lines = call_patch_hook(patch, "describe", ssh, patch)
    if isinstance(lines, (list, tuple)):
        for line in lines:
            print("%s%s" % (indent, line))


def run_hook_self_checks():
    ok = True
    for patch in PATCHES:
        fn = patch_hook(patch, "self_check")
        if fn is None:
            continue
        try:
            result = fn(PATCH_API)
        except Exception as e:
            print("ERROR: patch %s self-check failed: %s" %
                  (patch["id"], e), file=sys.stderr)
            result = False
        if result is False:
            ok = False
    return ok


# ---------------------------------------------------------------------------
# local validation
# ---------------------------------------------------------------------------
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_local(verbose=True):
    """Verify that the repository payloads are exactly the known patches."""
    ok = True
    for p in PATCHES:
        multi = len(p["files"]) > 1
        for file_spec in p["files"]:
            label = "%s %s" % (p["id"], p["name"])
            if multi:
                label = "%s [%s]" % (label, file_spec["target"])

            if file_spec["op"] == "symlink":
                if verbose:
                    print("[OK] %-42s symlink -> %s" %
                          (label, file_spec["link"]))
                continue

            missing = False
            paths = [file_spec["local_patched"]]
            if file_spec["local_original"] is not None:
                paths.append(file_spec["local_original"])
            for path in paths:
                if not Path(path).is_file():
                    print("ERROR: missing local file: %s" % path, file=sys.stderr)
                    print("       put update.bin into firmware/ and run patches/build.sh,",
                          file=sys.stderr)
                    print("       or use a prebuilt release zip.", file=sys.stderr)
                    ok = False
                    missing = True
            if missing:
                continue

            patched = Path(file_spec["local_patched"]).read_bytes()
            hp = sha256_bytes(patched)
            if hp != file_spec["hash_patched"]:
                print("ERROR: %s patched hash mismatch: %s (expected %s)" %
                      (label, hp, file_spec["hash_patched"]), file=sys.stderr)
                ok = False

            orig = None
            ho = None
            if file_spec["local_original"] is not None:
                orig = Path(file_spec["local_original"]).read_bytes()
                ho = sha256_bytes(orig)
                if ho != file_spec["hash_original"]:
                    print("ERROR: %s original hash mismatch: %s (expected %s)" %
                          (label, ho, file_spec["hash_original"]), file=sys.stderr)
                    ok = False

            verify = file_spec.get("verify")
            if verify:
                if len(orig) != len(patched):
                    print("ERROR: %s size differs: %d vs %d" %
                          (label, len(orig), len(patched)), file=sys.stderr)
                    ok = False
                    continue
                off = verify["offset"]
                orig_bytes = verify["orig_bytes"]
                patch_bytes = verify["patch_bytes"]
                width = len(orig_bytes)
                if orig[off:off + width] != orig_bytes:
                    print("ERROR: %s original bytes at 0x%X: %s (expected %s)" %
                          (label, off, orig[off:off + width].hex(),
                           orig_bytes.hex()), file=sys.stderr)
                    ok = False
                if patched[off:off + width] != patch_bytes:
                    print("ERROR: %s patched bytes at 0x%X: %s (expected %s)" %
                          (label, off, patched[off:off + width].hex(),
                           patch_bytes.hex()), file=sys.stderr)
                    ok = False

                diffs = [i for i, (a, b) in enumerate(zip(orig, patched))
                         if a != b]
                if not diffs or any(i < off or i >= off + width for i in diffs):
                    print("ERROR: %s differs outside the expected %d-byte window: %s" %
                          (label, width, diffs[:16]), file=sys.stderr)
                    ok = False
                elif verbose:
                    print("[OK] %-42s %d-byte window @ 0x%X  %s -> %s" %
                          (label, width, off, orig_bytes.hex(),
                           patch_bytes.hex()))
                    print("     original %s" % ho)
                    print("     patched  %s" % hp)
            elif verbose:
                print("[OK] %-42s sha256 verified" % label)
                if ho is not None:
                    print("     original %s" % ho)
                print("     patched  %s" % hp)

    return ok


def self_test_zone_map():
    cases = {
        "+3": "Etc/GMT-3",
        "-5": "Etc/GMT+5",
        "0": "Etc/UTC",
        "+14": "Etc/GMT-14",
        "-12": "Etc/GMT+12",
        "UTC": "Etc/UTC",
    }
    bad = ["+15", "-13", "abc", "+3:30"]
    ok = True
    for src, want in cases.items():
        got = map_offset(src)
        if got != want:
            print("ERROR: timezone map %r -> %r (expected %r)" % (src, got, want))
            ok = False
    for src in bad:
        if map_offset(src) is not None:
            print("ERROR: timezone map accepted invalid %r" % src)
            ok = False
    if ok:
        print("[OK] timezone offset mapping (Etc/GMT sign inversion, range)")
    return ok


def self_test_versioning():
    ok = True
    checks = [
        ("1.0.0", "1.0.1", -1),
        ("1.2", "1.2.0", 0),
        ("2", "1.9.9", 1),
        ("1.0.0", "bad", None),
    ]
    for left, right, want in checks:
        got = compare_versions(left, right)
        if got != want:
            print("ERROR: version compare %r vs %r -> %r (expected %r)" %
                  (left, right, got, want))
            ok = False

    patch = {"version": "1.1.0"}
    probes = {"/x": ("F", "oldhash")}
    old_state = {
        "version": "1.0.0",
        "files": [{"target": "/x", "op": "create",
                   "hash_patched": "oldhash"}],
    }
    status, installed = classify_patch_status(
        patch, STATUS_CONFLICT, probes, old_state)
    if status != STATUS_OUTDATED or installed != "1.0.0":
        print("ERROR: old installed version was not classified as Outdated: "
              "%r / %r" % (status, installed))
        ok = False

    newer_state = {"version": "9.9.9", "files": []}
    status, _installed = classify_patch_status(
        patch, STATUS_INSTALLED, probes, newer_state)
    if status != STATUS_CONFLICT:
        print("ERROR: newer installed version was not classified as Conflict: "
              "%r" % status)
        ok = False

    status, _installed = classify_patch_status(
        patch, STATUS_INSTALLED, probes, None)
    if status != STATUS_INSTALLED:
        print("ERROR: legacy install without state was not Installed: %r" %
              status)
        ok = False

    if ok:
        print("[OK] patch version classification")
    return ok


def _run_script(args, script, timeout=None, env=None):
    """Run a command with a POSIX shell script on stdin.

    The script is sent as UTF-8 bytes: text mode would translate "\\n" into
    "\\r\\n" on Windows, and the remote shell would then choke on the CRs.
    """
    r = subprocess.run(args, input=script.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, env=env)
    return subprocess.CompletedProcess(
        args, r.returncode,
        r.stdout.decode("utf-8", "replace") if r.stdout else "", "")


def _run_sh(script):
    r = _run_script(["sh", "-s"], script)
    return r.returncode, (r.stdout or "")


def self_test_script_transport():
    """Guard against CRLF translation of scripts sent over stdin."""
    script = "a\nb\n"
    probe = _run_script(
        [sys.executable, "-c",
         "import sys; sys.stdout.write(repr(sys.stdin.buffer.read()))"],
        script)
    if probe.returncode != 0 or "\\r" in probe.stdout:
        print("ERROR: script stdin is not LF-clean: %r" % (probe.stdout,))
        return False
    print("[OK] script transport: stdin stays LF-only")
    return True


def self_test_probe_scripts():
    """Local test for the batched status/hash scripts."""
    if os.name == "nt":
        print("[SKIP] batched status/hash self-test (Windows path semantics)")
        return True
    if not shutil.which("sh"):
        print("[SKIP] batched status/hash self-test (sh not found)")
        return True
    tmp = Path(tempfile.mkdtemp(prefix="hu_patcher_probe_"))
    ok = True
    try:
        reg = tmp / "regular.bin"
        reg.write_bytes(b"probe-data")
        absent = tmp / "absent.bin"
        paths = [str(reg), str(absent)]
        link = tmp / "link"
        try:
            link.symlink_to("/dev/null")
            paths.append(str(link))
        except OSError:
            link = None

        expected = sha256_file(reg)
        rc, out = _run_sh(build_status_script(paths, want_tz=True))
        probes, tz_state, _states = parse_status_output(out)
        if rc != 0:
            print("ERROR: batched status script failed:")
            print(out)
            ok = False
        if tz_state is None:
            print("ERROR: batched status: timezone line missing")
            ok = False
        if probes.get(str(reg)) != ("F", expected):
            print("ERROR: batched status: regular file -> %r" %
                  (probes.get(str(reg)),))
            ok = False
        if probes.get(str(absent)) != ("A", None):
            print("ERROR: batched status: absent file -> %r" %
                  (probes.get(str(absent)),))
            ok = False
        if link is not None and probes.get(str(link)) != ("L", "/dev/null"):
            print("ERROR: batched status: symlink -> %r" %
                  (probes.get(str(link)),))
            ok = False

        replace_spec = {
            "op": "replace",
            "target": str(reg),
            "hash_patched": expected,
            "hash_original": "0" * 64,
        }
        if probe_to_status(replace_spec, probes.get(str(reg))) != \
                (STATUS_INSTALLED, expected):
            print("ERROR: batched status: replace mapping failed")
            ok = False
        create_spec = {
            "op": "create",
            "target": str(absent),
            "hash_patched": expected,
        }
        if probe_to_status(create_spec, probes.get(str(absent))) != \
                (STATUS_NOT_INSTALLED, None):
            print("ERROR: batched status: create mapping failed")
            ok = False
        if link is not None:
            link_spec = {
                "op": "symlink",
                "target": str(link),
                "link": "/dev/null",
            }
            if probe_to_status(link_spec, probes.get(str(link))) != \
                    (STATUS_INSTALLED, "/dev/null"):
                print("ERROR: batched status: symlink mapping failed")
                ok = False

        rc, out = _run_sh(build_hash_script([str(reg), str(absent)]))
        hashes = parse_hash_output(out)
        if rc != 0 or hashes.get(str(reg)) != expected or \
                hashes.get(str(absent)) is not None:
            print("ERROR: batched hash script failed:")
            print(out)
            ok = False
        if ok:
            print("[OK] batched status/hash script")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def self_test_remote():
    """Exercise the remote apply/rollback/timezone scripts locally (needs sh)."""
    if not shutil.which("sh"):
        print("[SKIP] remote apply/timezone self-test (sh not found)")
        return True
    if not PATCHES:
        print("[SKIP] remote apply self-test (no patches)")
        return True
    tmp = Path(tempfile.mkdtemp(prefix="hu_patcher_selftest_"))
    ok = True
    try:
        replace_spec = None
        for patch in PATCHES:
            for file_spec in patch["files"]:
                if file_spec["op"] == "replace":
                    replace_spec = file_spec
                    break
            if replace_spec:
                break

        if replace_spec:
            orig = Path(replace_spec["local_original"]).read_bytes()
            patched = Path(replace_spec["local_patched"]).read_bytes()

            target = tmp / "target.bin"
            new = tmp / "new.bin"
            origf = tmp / "orig.bin"
            backup = tmp / "backup"
            target.write_bytes(orig)
            new.write_bytes(patched)
            origf.write_bytes(orig)

            # install
            rc, out = _run_sh(build_apply_script(
                replace_spec, "install", str(new), str(origf),
                str(backup), target_path=str(target)))
            if rc != 0 or "PATCHER_OK:" not in out or \
                    sha256_file(target) != replace_spec["hash_patched"]:
                print("ERROR: remote apply self-test failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote apply: original -> patched, backup verified")

            # idempotent install
            rc, out = _run_sh(build_apply_script(
                replace_spec, "install", str(new), str(origf),
                str(backup), target_path=str(target)))
            if "PATCHER_ALREADY:" not in out:
                print("ERROR: remote apply idempotence failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote apply: idempotent (already patched)")

            # rollback
            rc, out = _run_sh(build_apply_script(
                replace_spec, "rollback", str(new), str(origf),
                str(backup), target_path=str(target)))
            if rc != 0 or "PATCHER_OK:" not in out or \
                    sha256_file(target) != replace_spec["hash_original"]:
                print("ERROR: remote rollback self-test failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote rollback: patched -> original")

            # rollback idempotent
            rc, out = _run_sh(build_apply_script(
                replace_spec, "rollback", str(new), str(origf),
                str(backup), target_path=str(target)))
            if "PATCHER_ALREADY:" not in out:
                print("ERROR: remote rollback idempotence failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote rollback: idempotent (already original)")

            # conflict
            target.write_bytes(b"unknown-version")
            rc, out = _run_sh(build_apply_script(
                replace_spec, "install", str(new), str(origf),
                str(backup), target_path=str(target)))
            if "PATCHER_CONFLICT:" not in out or target.read_bytes() != b"unknown-version":
                print("ERROR: remote conflict handling failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote apply: unknown version -> conflict, no change")

            # corrupt upload
            target.write_bytes(orig)
            bad = tmp / "bad.bin"
            bad_bytes = bytearray(patched)
            bad_bytes[0] ^= 0xFF
            bad.write_bytes(bytes(bad_bytes))
            rc, out = _run_sh(build_apply_script(
                replace_spec, "install", str(bad), str(origf),
                str(backup), target_path=str(target)))
            if "PATCHER_ERROR:" not in out or sha256_file(target) != replace_spec["hash_original"]:
                print("ERROR: remote corrupt-upload handling failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote apply: corrupt upload rejected, target unchanged")

            # create operation
            create_target = tmp / "created.conf"
            create_new = tmp / "created.new"
            create_new.write_bytes(patched)
            create_spec = {
                "op": "create",
                "target": str(create_target),
                "hash_patched": sha256_file(create_new),
                "mode": "0644",
            }
            rc, out = _run_sh(build_apply_script(
                create_spec, "install", str(create_new), None,
                str(backup), target_path=str(create_target)))
            if rc != 0 or "PATCHER_OK:" not in out or \
                    create_target.read_bytes() != patched:
                print("ERROR: remote create self-test failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: new file created")

            rc, out = _run_sh(build_apply_script(
                create_spec, "install", str(create_new), None,
                str(backup), target_path=str(create_target)))
            if "PATCHER_ALREADY:" not in out:
                print("ERROR: remote create idempotence failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: idempotent (already present)")

            rc, out = _run_sh(build_apply_script(
                create_spec, "rollback", str(create_new), None,
                str(backup), target_path=str(create_target)))
            if rc != 0 or create_target.exists():
                print("ERROR: remote create rollback failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: rollback removed the file")

            rc, out = _run_sh(build_apply_script(
                create_spec, "rollback", str(create_new), None,
                str(backup), target_path=str(create_target)))
            if "PATCHER_ALREADY:" not in out:
                print("ERROR: remote create rollback idempotence failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: rollback idempotent (already absent)")

            create_target.write_bytes(b"unknown-version")
            rc, out = _run_sh(build_apply_script(
                create_spec, "install", str(create_new), None,
                str(backup), target_path=str(create_target)))
            if "PATCHER_CONFLICT:" not in out or \
                    create_target.read_bytes() != b"unknown-version":
                print("ERROR: remote create conflict handling failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: unknown version -> conflict, no change")
            create_target.unlink()

            # create with parent directories
            nested_target = tmp / "nested" / "dir" / "new.conf"
            nested_new = tmp / "nested.new"
            nested_new.write_bytes(patched)
            nested_spec = {
                "op": "create",
                "target": str(nested_target),
                "hash_patched": sha256_file(nested_new),
                "mode": "0644",
                "create_dirs": True,
            }
            rc, out = _run_sh(build_apply_script(
                nested_spec, "install", str(nested_new), None,
                str(backup), target_path=str(nested_target)))
            if rc != 0 or "PATCHER_OK:" not in out or \
                    not nested_target.is_file():
                print("ERROR: remote create_dirs self-test failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: parent directories created")

            rc, out = _run_sh(build_apply_script(
                nested_spec, "rollback", str(nested_new), None,
                str(backup), target_path=str(nested_target)))
            if rc != 0 or nested_target.exists():
                print("ERROR: remote create_dirs rollback failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: rollback removed the file")

            # create without create_dirs must refuse a missing directory
            blocked_target = tmp / "blocked" / "dir" / "new.conf"
            blocked_spec = {
                "op": "create",
                "target": str(blocked_target),
                "hash_patched": sha256_file(nested_new),
                "mode": "0644",
                "create_dirs": False,
            }
            rc, out = _run_sh(build_apply_script(
                blocked_spec, "install", str(nested_new), None,
                str(backup), target_path=str(blocked_target)))
            if "PATCHER_ERROR:" not in out or blocked_target.exists():
                print("ERROR: remote create without create_dirs failed:")
                print(out)
                ok = False
            else:
                print("[OK] remote create: missing directory rejected by default")
        else:
            print("[SKIP] remote replace self-test (no replace patch)")

        # symlink operation (independent of the bundled payloads)
        link_target = tmp / "masked.service"
        link_spec = {
            "op": "symlink",
            "target": str(link_target),
            "link": "/dev/null",
            "create_dirs": True,
        }
        rc, out = _run_sh(build_apply_script(
            link_spec, "install", None, None,
            str(backup), target_path=str(link_target)))
        if rc != 0 or "PATCHER_OK:" not in out or \
                not link_target.is_symlink() or \
                os.readlink(str(link_target)) != "/dev/null":
            print("ERROR: remote symlink self-test failed:")
            print(out)
            ok = False
        else:
            print("[OK] remote symlink: created -> /dev/null")

        rc, out = _run_sh(build_apply_script(
            link_spec, "install", None, None,
            str(backup), target_path=str(link_target)))
        if "PATCHER_ALREADY:" not in out:
            print("ERROR: remote symlink idempotence failed:")
            print(out)
            ok = False
        else:
            print("[OK] remote symlink: idempotent (already linked)")

        rc, out = _run_sh(build_apply_script(
            link_spec, "rollback", None, None,
            str(backup), target_path=str(link_target)))
        if rc != 0 or link_target.exists() or link_target.is_symlink():
            print("ERROR: remote symlink rollback failed:")
            print(out)
            ok = False
        else:
            print("[OK] remote symlink: rollback removed the link")

        rc, out = _run_sh(build_apply_script(
            link_spec, "rollback", None, None,
            str(backup), target_path=str(link_target)))
        if "PATCHER_ALREADY:" not in out:
            print("ERROR: remote symlink rollback idempotence failed:")
            print(out)
            ok = False
        else:
            print("[OK] remote symlink: rollback idempotent (already absent)")

        link_target.write_text("not a symlink\n")
        rc, out = _run_sh(build_apply_script(
            link_spec, "install", None, None,
            str(backup), target_path=str(link_target)))
        if "PATCHER_CONFLICT:" not in out or link_target.is_symlink():
            print("ERROR: remote symlink conflict handling failed:")
            print(out)
            ok = False
        else:
            print("[OK] remote symlink: regular file -> conflict, no change")
        link_target.unlink()

        # old-version rollback without a new payload
        old_create_target = tmp / "old-created.conf"
        old_create_new = tmp / "old-created.new"
        old_create_new.write_bytes(b"old-create")
        old_create_spec = {
            "op": "create",
            "target": str(old_create_target),
            "hash_patched": sha256_file(old_create_new),
            "mode": "0644",
        }
        _run_sh(build_apply_script(
            old_create_spec, "install", str(old_create_new), None,
            str(backup), target_path=str(old_create_target)))
        rc, out = _run_sh(build_apply_script(
            old_create_spec, "rollback", None, None,
            str(backup), target_path=str(old_create_target)))
        if rc != 0 or old_create_target.exists() or "PATCHER_OK:" not in out:
            print("ERROR: old create rollback self-test failed:")
            print(out)
            ok = False
        else:
            print("[OK] old create rollback without a new payload")

        old_target = tmp / "old-target.bin"
        old_orig = tmp / "old-orig.bin"
        old_patched = tmp / "old-patched.bin"
        old_orig.write_bytes(b"old original")
        old_patched.write_bytes(b"old patched")
        old_target.write_bytes(b"old patched")
        old_replace_spec = {
            "op": "replace",
            "target": str(old_target),
            "backup_name": "old-target.bin",
            "hash_original": sha256_file(old_orig),
            "hash_patched": sha256_file(old_patched),
        }
        rc, out = _run_sh(build_apply_script(
            old_replace_spec, "rollback", None, str(old_orig),
            str(backup), target_path=str(old_target)))
        if rc != 0 or old_target.read_bytes() != b"old original" or \
                "PATCHER_OK:" not in out:
            print("ERROR: old replace rollback self-test failed:")
            print(out)
            ok = False
        else:
            print("[OK] old replace rollback without a new payload")

        # timezone install + restore
        zi = tmp / "zoneinfo"
        etc = tmp / "etc"
        (zi / "Etc").mkdir(parents=True)
        (zi / "Asia").mkdir(parents=True)
        etc.mkdir()
        (zi / "Etc" / "GMT-3").write_bytes(b"zone")
        old_zone_file = zi / "Asia" / "Shanghai"
        old_zone_file.write_bytes(b"zone")
        lt = etc / "localtime"
        lt.symlink_to(old_zone_file)
        tzf = etc / "timezone"
        tzf.write_text("Asia/Shanghai\n")
        tzb = tmp / "backup_tz"

        rc, out = _run_sh(build_tz_script("Etc/GMT-3", backup_dir=str(tzb),
                                          localtime=str(lt), timezone=str(tzf),
                                          zoneinfo=str(zi)))
        if rc != 0 or "TZ_OK:" not in out or \
                Path(os.path.realpath(lt)) != (zi / "Etc" / "GMT-3") or \
                tzf.read_text() != "Etc/GMT-3\n":
            print("ERROR: timezone install self-test failed:")
            print(out)
            ok = False
        else:
            print("[OK] timezone install: localtime + /etc/timezone updated")

        rc, out = _run_sh(build_tz_restore_script(backup_dir=str(tzb),
                                                  localtime=str(lt),
                                                  timezone=str(tzf)))
        if rc != 0 or "TZ_RESTORED" not in out or \
                Path(os.path.realpath(lt)) != old_zone_file or \
                tzf.read_text() != "Asia/Shanghai\n":
            print("ERROR: timezone restore self-test failed:")
            print(out)
            ok = False
        else:
            print("[OK] timezone restore: original localtime + timezone restored")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


# ---------------------------------------------------------------------------
# SSH / SCP helpers (system OpenSSH; works on Windows/Linux/macOS)
# ---------------------------------------------------------------------------
class Ssh:
    def __init__(self, host, port, key, media_password="", tmpdir=None):
        self.host = host
        self.port = int(port)
        self.key = Path(key) if key else None
        self.media_password = media_password
        self.tmpdir = Path(tmpdir or tempfile.mkdtemp(prefix="hu_patcher_"))
        # Do not rely on a path with backslashes in -o UserKnownHostsFile
        # (OpenSSH config parsing on Windows); use the null device.
        self.known_hosts = os.devnull
        self.sshpass = shutil.which("sshpass")

    def cleanup(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _common_opts(self):
        return [
            "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=%s" % self.known_hosts,
            "-o", "LogLevel=ERROR",
        ]

    def _ssh_args(self, user, batch):
        args = ["ssh", "-p", str(self.port)] + self._common_opts()
        if batch:
            args += ["-o", "BatchMode=yes"]
        if user == "root" and self.key:
            args += ["-o", "IdentitiesOnly=yes", "-i", str(self.key)]
        args.append("%s@%s" % (user, self.host))
        return args

    def _media_prefix(self, args):
        """Return (args, env) for media commands. sshpass if available."""
        if self.sshpass and self.media_password is not None:
            env = os.environ.copy()
            env["SSHPASS"] = self.media_password
            return ["sshpass", "-e"] + args, env
        return args, None

    def run(self, user, command, input_text=None, batch=True, timeout=60):
        started = time.time()
        log_only("[ssh] %s@%s$ %s (timeout=%ss)" %
                 (user, self.host, command, timeout))
        if input_text is not None:
            log_block("ssh-script", input_text)
        args = self._ssh_args(user, batch)
        args.append(command)
        env = None
        if user == "media":
            args, env = self._media_prefix(args)
        try:
            if input_text is not None:
                result = _run_script(args, input_text, timeout=timeout, env=env)
            else:
                result = subprocess.run(
                    args, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=timeout, env=env,
                )
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            result = subprocess.CompletedProcess(args, 124,
                                                 out + "\nTIMEOUT", "")
        log_only("[ssh] rc=%s elapsed=%.1fs" %
                 (result.returncode, time.time() - started))
        log_block("ssh-out", result.stdout or "")
        return result

    def scp(self, user, sources, remote, batch=True, timeout=180):
        started = time.time()
        names = ", ".join(Path(s).name for s in sources)
        log_only("[scp] %s -> %s@%s:%s (timeout=%ss)" %
                 (names, user, self.host, remote, timeout))
        args = ["scp", "-O", "-P", str(self.port)] + self._common_opts()
        if batch:
            args += ["-o", "BatchMode=yes"]
        if user == "root" and self.key:
            args += ["-o", "IdentitiesOnly=yes", "-i", str(self.key)]
        args += [str(s) for s in sources]
        args.append("%s@%s:%s" % (user, self.host, remote))
        env = None
        if user == "media":
            args, env = self._media_prefix(args)
        try:
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    env=env)
        except subprocess.TimeoutExpired as e:
            result = subprocess.CompletedProcess(
                args, 124, (e.stdout or "") + "\nTIMEOUT", "")
        log_only("[scp] rc=%s elapsed=%.1fs" %
                 (result.returncode, time.time() - started))
        log_block("scp-out", result.stdout or "")
        return result

    def run_retry(self, user, command, input_text=None, batch=True,
                  timeout=60, attempts=3):
        last = None
        for i in range(attempts):
            r = self.run(user, command, input_text=input_text, batch=batch,
                         timeout=timeout)
            last = r
            if r.returncode != 255:
                return r
            if i + 1 < attempts:
                print("  SSH connection failed, retry %d/%d ..." %
                      (i + 2, attempts))
                time.sleep(2)
        return last


# ---------------------------------------------------------------------------
# prompts / output
# ---------------------------------------------------------------------------
def ask_yes(question, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            ans = input("%s %s: " % (question, suffix)).strip().lower()
        except EOFError:
            # No terminal input: never assume "yes" for a change/reboot.
            log_only("[prompt] %s -> EOF (no)" % question)
            print()
            return False
        if not ans:
            log_only("[prompt] %s -> default %s" %
                     (question, "yes" if default else "no"))
            return default
        if ans in ("y", "yes"):
            log_only("[prompt] %s -> yes" % question)
            return True
        if ans in ("n", "no"):
            log_only("[prompt] %s -> no" % question)
            return False
        print("  Please answer y/n.")


def ask_text(question, default=""):
    try:
        ans = input("%s%s: " % (question, (" [%s]" % default) if default else ""))
    except EOFError:
        log_only("[prompt] %s -> EOF (default %r)" % (question, default))
        return default
    ans = ans.strip() or default
    log_only("[prompt] %s -> %r" % (question, ans))
    return ans


def pause_before_exit():
    """Keep an interactive console open so the user can read the output."""
    if not PAUSE_ON_EXIT:
        return
    if os.environ.get("HU_PATCHER_NO_PAUSE"):
        return
    if any(arg in ("-h", "--help", "--no-pause") for arg in sys.argv[1:]):
        return
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
    except (AttributeError, ValueError):
        return

    print()
    try:
        if os.name == "nt":
            import msvcrt
            print("Press any key to continue . . .", end="", flush=True)
            msvcrt.getch()
            print()
        else:
            input("Press Enter to continue . . .")
    except (EOFError, KeyboardInterrupt):
        print()


class RootProbeUnverified(Exception):
    """Root login could not be verified (as opposed to being denied)."""


def root_probe(ssh, timeout=120):
    """Probe root login; return (ok, state) with state ok/timeout/denied.

    Some units need tens of seconds to open an SSH session (for example
    pam_systemd waiting for a broken user manager), so a timeout must not be
    reported as a wrong key.
    """
    r = ssh.run("root", "id -u", timeout=timeout)
    if r.returncode == 124:
        return False, "timeout"
    if r.returncode == 0 and (r.stdout or "").strip() == "0":
        return True, "ok"
    return False, "denied"


def root_ok(ssh, timeout=120):
    ok, _ = root_probe(ssh, timeout=timeout)
    return ok


def media_ok(ssh):
    r = ssh.run("media", "id", batch=False, timeout=120)
    out = r.stdout or ""
    return r.returncode == 0 and "uid=" in out


def media_boot_id(ssh):
    r = ssh.run("media", "cat /proc/sys/kernel/random/boot_id 2>/dev/null",
                batch=False, timeout=120)
    if r.returncode != 0:
        return None
    value = (r.stdout or "").strip()
    return value or None


def print_reboot_instruction():
    print()
    print("Reboot the HU:")
    print("  To reboot, hold Music, Favorite (Star) and Volume On/Off")
    print("  buttons for 5 seconds, then select 'End Diag Mode'.")
    print()
    log_only("[reboot] instruction: hold Music + Favorite + Volume "
             "On/Off for 5 s, then End Diag Mode")


def wait_for_reboot(ssh, old_boot_id, timeout=90, interval=3,
                    source="media"):
    print("Waiting for the reboot", end="", flush=True)
    log_only("[reboot] wait for boot change via %s (old=%s, timeout=%ss)" %
             (source, old_boot_id, timeout))
    deadline = time.time() + timeout
    saw_down = False
    while time.time() < deadline:
        if source == "root":
            ok, new_id = root_probe_boot_id(ssh)
            if not ok:
                saw_down = True
            elif old_boot_id:
                if new_id and new_id != old_boot_id:
                    print(" reboot detected")
                    log_only("[reboot] detected (new=%r, saw_down=%s)" %
                             (new_id, saw_down))
                    return True
            elif saw_down:
                print(" reboot detected")
                log_only("[reboot] detected (new=%r, saw_down=%s)" %
                         (new_id, saw_down))
                return True
        else:
            new_id = media_boot_id(ssh)
            if new_id is None or (old_boot_id and new_id != old_boot_id):
                print(" reboot detected")
                log_only("[reboot] detected (new=%r)" % new_id)
                return True
        print(".", end="", flush=True)
        time.sleep(interval)
    print(" not detected")
    log_only("[reboot] not detected within %ss" % timeout)
    return False


def wait_root(ssh, timeout=300, interval=5):
    print("Waiting for root SSH", end="", flush=True)
    log_only("[root] wait for root SSH (timeout=%ss)" % timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, state = root_probe(ssh, timeout=45)
        if ok:
            print(" OK")
            log_only("[root] root SSH is back")
            return True
        if state == "denied":
            print(".", end="", flush=True)
        else:
            print("+", end="", flush=True)
        time.sleep(interval)
    print(" timeout")
    log_only("[root] root SSH did not return within %ss" % timeout)
    return False


def print_banner():
    print("=" * 74)
    print(" Mazda CMU patcher %s (KBST-41-47-M5 / DS04L)" % read_version())
    if PATCHES:
        print(" Patches: %s" %
              ", ".join("%s %s" % (p["id"], p["key"]) for p in PATCHES))
    else:
        print(" Patches: none")
    print("=" * 74)


def check_tools():
    missing = [t for t in ("ssh", "scp", "ssh-keygen") if not shutil.which(t)]
    if missing:
        print("ERROR: missing required OpenSSH tools: %s" % ", ".join(missing),
              file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# remote helpers
# ---------------------------------------------------------------------------
REMOTE_HASH_FUNC = r'''
hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" 2>/dev/null | awk '{print $1}'
    elif command -v busybox >/dev/null 2>&1; then
        busybox sha256sum "$1" 2>/dev/null | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" 2>/dev/null | awk '{print $NF}'
    else
        return 1
    fi
}
die() { echo "PATCHER_ERROR:$*"; exit 1; }
'''


REMOTE_PROBE_FUNC = r'''
status_path() {
    p=$1
    if [ -L "$p" ]; then
        v=$(readlink "$p" 2>/dev/null) || v=PATCHER_UNREADABLE
        [ -n "$v" ] || v=PATCHER_UNREADABLE
        printf 'S\tL\t%s\t%s\n' "$p" "$v"
    elif [ -e "$p" ]; then
        v=$(hash_file "$p" 2>/dev/null) || v=PATCHER_UNREADABLE
        [ -n "$v" ] || v=PATCHER_UNREADABLE
        printf 'S\tF\t%s\t%s\n' "$p" "$v"
    else
        printf 'S\tA\t%s\t-\n' "$p"
    fi
}
hash_path() {
    p=$1
    v=$(hash_file "$p" 2>/dev/null) || v=PATCHER_UNREADABLE
    [ -n "$v" ] || v=PATCHER_UNREADABLE
    printf 'H\t%s\t%s\n' "$p" "$v"
}
'''


def build_hash_script(paths):
    lines = [REMOTE_HASH_FUNC, REMOTE_PROBE_FUNC]
    for path in paths:
        lines.append("hash_path %s" % shlex.quote(str(path)))
    return "\n".join(lines) + "\n"


def parse_hash_output(out):
    result = {}
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] == "H":
            value = parts[2]
            result[parts[1]] = None if value == "PATCHER_UNREADABLE" else value
    return result


def remote_hashes(ssh, paths, timeout=60):
    paths = [str(path) for path in paths]
    if not paths:
        return {}
    r = ssh.run_retry("root", "sh -s",
                      input_text=build_hash_script(paths), timeout=timeout)
    if r.returncode != 0:
        return {}
    return parse_hash_output(r.stdout or "")


def remote_hash(ssh, path):
    return remote_hashes(ssh, [path]).get(str(path))


def patch_state_path(key):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(key))
    return "%s/%s.json" % (STATE_DIR, safe)


def patch_state_payload(patch):
    files = []
    for file_spec in patch["files"]:
        files.append({
            "target": file_spec["target"],
            "op": file_spec["op"],
            "hash_patched": file_spec["hash_patched"],
            "hash_original": file_spec["hash_original"],
            "link": file_spec["link"],
        })
    data = {"version": patch["version"], "files": files}
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def write_patch_state(ssh, patch):
    payload = shlex.quote(patch_state_payload(patch))
    path = shlex.quote(patch_state_path(patch["key"]))
    log_only("[state] write %s (version %s)" %
             (patch_state_path(patch["key"]), patch["version"]))
    script = ("mkdir -p %s\nprintf '%%s\\n' %s > %s\n" %
              (shlex.quote(STATE_DIR), payload, path))
    r = ssh.run_retry("root", "sh -s", input_text=script, timeout=60)
    if r.returncode != 0:
        print("WARNING: cannot save patch state:")
        print((r.stdout or "").strip())
        return False
    return True


def remove_patch_state(ssh, patch):
    log_only("[state] remove %s" % patch_state_path(patch["key"]))
    r = ssh.run_retry(
        "root", "rm -f %s" % shlex.quote(patch_state_path(patch["key"])),
        timeout=60)
    if r.returncode != 0:
        print("WARNING: cannot remove patch state:")
        print((r.stdout or "").strip())
    return r.returncode == 0


def remote_get(ssh, command, timeout=60):
    r = ssh.run_retry("root", command, timeout=timeout)
    return r.returncode, (r.stdout or "")


def build_status_script(paths, want_tz=False, state_keys=None):
    lines = [REMOTE_HASH_FUNC, REMOTE_PROBE_FUNC]
    for path in paths:
        lines.append("status_path %s" % shlex.quote(str(path)))
    if want_tz:
        lines.append("printf 'T\\t%s\\t%s\\n' "
                     '"$(readlink -f /etc/localtime 2>/dev/null)" '
                     '"$(cat /etc/timezone 2>/dev/null)"')
    for key in (state_keys or []):
        safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", str(key))
        path = shlex.quote("%s/%s.json" % (STATE_DIR, safe_key))
        lines.append("if [ -f %s ]; then printf 'K\\t%s\\t%%s\\n' "
                     "\"$(cat %s)\"; fi" % (path, safe_key, path))
    return "\n".join(lines) + "\n"


def parse_status_output(out):
    probes = {}
    tz_state = None
    states = {}
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        if parts[0] == "S" and len(parts) >= 4:
            kind, path, value = parts[1], parts[2], parts[3]
            if kind == "A":
                probes[path] = ("A", None)
            elif value == "PATCHER_UNREADABLE":
                probes[path] = (kind, None if kind == "F" else value)
            else:
                probes[path] = (kind, value)
        elif parts[0] == "T" and len(parts) >= 3:
            tz_state = (parts[1] or "(none)", parts[2] or "(none)")
        elif parts[0] == "K" and len(parts) >= 3:
            states[parts[1]] = parts[2]
    return probes, tz_state, states


def remote_status(ssh, paths, want_tz=False, state_keys=None, timeout=120):
    paths = [str(path) for path in paths]
    script = build_status_script(paths, want_tz=want_tz,
                                 state_keys=state_keys)
    r = ssh.run_retry("root", "sh -s", input_text=script, timeout=timeout)
    if r.returncode != 0:
        return None, None, None
    return parse_status_output(r.stdout or "")


def remote_readlink(ssh, path):
    """Return a symlink target, None if absent, or 'PATCHER_REGULAR'."""
    probes, _tz, _states = remote_status(ssh, [path])
    if probes is None:
        return None
    kind, value = probes.get(str(path), (None, None))
    if kind == "L":
        return value if value is not None else "PATCHER_UNREADABLE"
    if kind == "F":
        return "PATCHER_REGULAR"
    if kind == "A":
        return "PATCHER_ABSENT"
    return None


def remote_tz_state(ssh):
    cached = getattr(ssh, "_tz_state", None)
    if cached is not None:
        return cached
    rc, out = remote_get(
        ssh, "readlink -f /etc/localtime 2>/dev/null; cat /etc/timezone 2>/dev/null")
    lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
    lt = lines[0] if len(lines) > 0 else "(none)"
    tz = lines[1] if len(lines) > 1 else "(none)"
    state = (lt, tz)
    try:
        ssh._tz_state = state
    except Exception:
        pass
    return state


def probe_to_status(file_spec, probe):
    kind, value = probe if probe else (None, None)
    if file_spec["op"] == "symlink":
        if kind == "L" and value == file_spec["link"]:
            return STATUS_INSTALLED, value
        if kind in (None, "A"):
            return STATUS_NOT_INSTALLED, None
        if kind == "F":
            return STATUS_CONFLICT, "PATCHER_REGULAR"
        return STATUS_CONFLICT, value
    if kind == "F" and value == file_spec["hash_patched"]:
        return STATUS_INSTALLED, value
    if file_spec["op"] == "create":
        if kind in (None, "A"):
            return STATUS_NOT_INSTALLED, None
        return STATUS_CONFLICT, value
    if kind == "F" and value == file_spec["hash_original"]:
        return STATUS_NOT_INSTALLED, value
    return STATUS_CONFLICT, value if kind == "F" else None


def summarize_details(details):
    statuses = [item[1] for item in details]
    if STATUS_CONFLICT in statuses:
        return STATUS_CONFLICT
    if all(s == STATUS_INSTALLED for s in statuses):
        return STATUS_INSTALLED
    if all(s == STATUS_NOT_INSTALLED for s in statuses):
        return STATUS_NOT_INSTALLED
    return STATUS_PARTIAL


def patch_file_status(ssh, file_spec):
    probes, _tz, _states = remote_status(ssh, [file_spec["target"]])
    if probes is None:
        return STATUS_CONFLICT, None
    return probe_to_status(file_spec, probes.get(file_spec["target"]))


def patch_status(ssh, patch):
    probes, _tz, _states = remote_status(ssh, [f["target"] for f in patch["files"]])
    details = []
    for file_spec in patch["files"]:
        if probes is None:
            status, value = STATUS_CONFLICT, None
        else:
            status, value = probe_to_status(
                file_spec, probes.get(file_spec["target"]))
        details.append((file_spec, status, value))
    return summarize_details(details), details


def parse_patch_state(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("version"), str):
        return None
    if not isinstance(data.get("files"), list):
        return None
    return data


def state_files_match(state, probes):
    files = state.get("files") if state else None
    if not isinstance(files, list):
        return False
    for item in files:
        if not isinstance(item, dict):
            return False
        target = item.get("target")
        op = item.get("op")
        probe = probes.get(target)
        kind, value = probe if probe else (None, None)
        if kind == "A":
            continue
        if op == "symlink":
            if kind != "L" or value != item.get("link"):
                return False
        elif op in ("replace", "create"):
            if kind != "F" or value != item.get("hash_patched"):
                return False
        else:
            return False
    return True


def classify_patch_status(patch, file_status, probes, state):
    installed_version = state.get("version") if state else None
    if file_status == STATUS_NOT_INSTALLED:
        return STATUS_NOT_INSTALLED, installed_version
    if state is None:
        return file_status, None
    cmp = compare_versions(installed_version, patch["version"])
    if cmp is None:
        # A corrupt/unknown state version must not block a matching file state.
        return file_status, installed_version
    if cmp > 0:
        return STATUS_CONFLICT, installed_version
    if cmp < 0:
        if file_status == STATUS_INSTALLED or state_files_match(state, probes):
            return STATUS_OUTDATED, installed_version
        return STATUS_CONFLICT, installed_version
    return file_status, installed_version


def snapshot_status(ssh):
    """Read all targets and version state in one SSH session."""
    specs = [f for p in PATCHES for f in p["files"]]
    probes, tz_state, raw_states = remote_status(
        ssh, [f["target"] for f in specs], want_tz=True,
        state_keys=[p["key"] for p in PATCHES])
    if probes is None:
        return None
    if tz_state is not None:
        try:
            ssh._tz_state = tz_state
        except Exception:
            pass
    result = {}
    for patch in PATCHES:
        details = []
        for file_spec in patch["files"]:
            status, value = probe_to_status(
                file_spec, probes.get(file_spec["target"]))
            details.append((file_spec, status, value))
        file_status = summarize_details(details)
        state = parse_patch_state(raw_states.get(patch["key"]))
        status, installed_version = classify_patch_status(
            patch, file_status, probes, state)
        result[patch["id"]] = {
            "patch": patch,
            "status": status,
            "details": details,
            "state": state,
            "installed_version": installed_version,
        }
    for patch in PATCHES:
        entry = result[patch["id"]]
        log_only("[status] %s) %s: %s installed=%s current=%s" %
                 (patch["id"], patch["name"], entry["status"],
                  entry["installed_version"] or "-", patch["version"]))
        for file_spec, file_status, value in entry["details"]:
            log_only("[status]   %s: %s %s" %
                     (file_spec["target"], file_status, value or ""))
        if entry["state"]:
            log_only("[status]   saved state version: %s" %
                     entry["state"].get("version"))
    return result


def status_display(file_spec, status, value):
    """Human-readable second line for a file state."""
    if file_spec["op"] == "symlink":
        if status in (STATUS_INSTALLED, STATUS_OUTDATED):
            return "-> %s" % file_spec["link"]
        if value == "PATCHER_REGULAR":
            return "(regular file, not a link)"
        if value and value != "PATCHER_ABSENT":
            return "(unexpected: %s)" % value
        return "(absent)"
    if value:
        return value
    if file_spec["op"] == "create":
        return "(absent)"
    return "(cannot read/hash)"


def print_file_status(file_spec, status, h, indent):
    print("%s%s  %s" % (indent, file_spec["target"],
                        colorize_status(status)))
    print("%s  %s" % (indent, status_display(file_spec, status, h)))


def version_line(entry, patch):
    current = patch["version"]
    installed = entry.get("installed_version")
    status = entry["status"]
    if status == STATUS_OUTDATED:
        return "version: %s -> %s" % (installed, current)
    if status == STATUS_INSTALLED and installed is None:
        return "version: %s (state unknown)" % current
    if status == STATUS_CONFLICT and installed and installed != current:
        return "version: %s (installed) / %s (current)" % (installed, current)
    return "version: %s" % current


def display_file_status(patch_status, file_status):
    if patch_status == STATUS_OUTDATED and file_status == STATUS_CONFLICT:
        return STATUS_OUTDATED
    return file_status


def print_status(ssh, snapshot=None):
    if snapshot is None:
        snapshot = snapshot_status(ssh)
    if snapshot is None:
        print("ERROR: cannot read the patch status.")
        return
    print()
    print("Patch status:")
    for p in PATCHES:
        entry = snapshot[p["id"]]
        status = entry["status"]
        details = entry["details"]
        print("  %s) %-42s %s" % (p["id"], p["name"], colorize_status(status)))
        if len(details) == 1:
            file_spec, file_status, h = details[0]
            print("     %s" % status_display(
                file_spec, display_file_status(status, file_status), h))
        else:
            for file_spec, file_status, h in details:
                print_file_status(
                    file_spec, display_file_status(status, file_status), h,
                    "     ")
        print("     %s" % version_line(entry, p))
        print_patch_details(p, ssh, "     ")


# ---------------------------------------------------------------------------
# remote patch application
# ---------------------------------------------------------------------------
def build_replace_script(file_spec, mode, remote_new, remote_orig,
                         backup_dir="/svp/patcher/backup", target_path=None):
    target = shlex.quote(target_path or file_spec["target"])
    new = shlex.quote(remote_new or "")
    orig = shlex.quote(remote_orig or "")
    backup = shlex.quote(backup_dir)
    ho = shlex.quote(file_spec["hash_original"])
    hp = shlex.quote(file_spec["hash_patched"])
    name = shlex.quote(file_spec["backup_name"])
    action = shlex.quote(mode)

    return r'''set -u
TARGET=%s
NEW=%s
ORIG=%s
BACKUP_DIR=%s
H_ORIG=%s
H_PATCH=%s
NAME=%s
ACTION=%s
%s

[ -f "$TARGET" ] || die "target not found: $TARGET"
[ ! -L "$TARGET" ] || die "target is a symlink: $TARGET"
[ -f "$ORIG" ] || die "original file not found: $ORIG"
if [ "$ACTION" = "install" ]; then
    [ -f "$NEW" ] || die "new file not found: $NEW"
fi

TARGET_DIR=$(dirname "$TARGET")
AVAIL=$(df -k "$TARGET_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
case "$AVAIL" in
    ''|*[!0-9]*) ;;
    *) [ "$AVAIL" -ge 8192 ] || die "not enough free space on $TARGET_DIR ($AVAIL KB)" ;;
esac

CUR=$(hash_file "$TARGET") || die "cannot hash target"
[ -n "$CUR" ] || die "empty target hash"

if [ "$ACTION" = "install" ]; then
    if [ "$CUR" = "$H_PATCH" ]; then
        echo "PATCHER_ALREADY:already patched"
        exit 0
    fi
    if [ "$CUR" != "$H_ORIG" ]; then
        echo "PATCHER_CONFLICT:$CUR"
        exit 3
    fi
else
    if [ "$CUR" = "$H_ORIG" ]; then
        echo "PATCHER_ALREADY:already original"
        exit 0
    fi
    if [ "$CUR" != "$H_PATCH" ]; then
        echo "PATCHER_CONFLICT:$CUR"
        exit 3
    fi
fi

if [ "$ACTION" = "install" ]; then
    [ "$(hash_file "$NEW")" = "$H_PATCH" ] || die "uploaded patched file hash mismatch"
fi
if [ "$(hash_file "$ORIG")" != "$H_ORIG" ]; then
    die "uploaded original file hash mismatch"
fi

mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR"
STAMP=$(date +%%Y%%m%%d-%%H%%M%%S 2>/dev/null || echo unknown)-$$
B="$BACKUP_DIR/$NAME.$STAMP"
cp -a "$TARGET" "$B" || die "backup failed"
[ "$(hash_file "$B")" = "$CUR" ] || die "backup verify failed"

TMP="$TARGET.patcher.$$"
cp -a "$TARGET" "$TMP" || die "temp copy failed"

if [ "$ACTION" = "install" ]; then
    cat "$NEW" > "$TMP" || { rm -f "$TMP"; die "cannot write temp"; }
    WANT="$H_PATCH"
else
    # rollback: restore the known original from the uploaded file
    cat "$ORIG" > "$TMP" || { rm -f "$TMP"; die "cannot write temp"; }
    WANT="$H_ORIG"
fi

GOT=$(hash_file "$TMP") || { rm -f "$TMP"; die "cannot hash temp"; }
if [ "$GOT" != "$WANT" ]; then
    rm -f "$TMP"
    die "temp verification failed ($GOT != $WANT)"
fi

# preserve mode/owner as copied from target (cp -a), then replace atomically
sync
mv -f "$TMP" "$TARGET" || die "replace failed"
sync

AFTER=$(hash_file "$TARGET") || AFTER=""
if [ "$AFTER" != "$WANT" ]; then
    # try to restore the backup
    R="$TARGET.restore.$$"
    if cp -a "$B" "$R" 2>/dev/null && mv -f "$R" "$TARGET" 2>/dev/null; then
        sync
        die "final verification failed; backup restored"
    fi
    die "CRITICAL: final verification failed and backup restore failed"
fi

echo "PATCHER_OK:backup=$B"
''' % (target, new, orig, backup, ho, hp, name, action, REMOTE_HASH_FUNC)


def build_create_script(file_spec, mode, remote_new,
                        backup_dir="/svp/patcher/backup", target_path=None):
    target = shlex.quote(target_path or file_spec["target"])
    new = shlex.quote(remote_new or "")
    hp = shlex.quote(file_spec["hash_patched"])
    file_mode = shlex.quote(file_spec.get("mode") or "0644")
    action = shlex.quote(mode)
    create_dirs = "1" if file_spec.get("create_dirs") else "0"

    return r'''set -u
TARGET=%s
NEW=%s
H_PATCH=%s
FILE_MODE=%s
CREATE_DIRS=%s
ACTION=%s
%s

if [ "$ACTION" = "install" ]; then
    [ -f "$NEW" ] || die "new file not found: $NEW"
fi

TARGET_DIR=$(dirname "$TARGET")
CHECK_DIR=$TARGET_DIR
while [ ! -d "$CHECK_DIR" ]; do
    CHECK_DIR=$(dirname "$CHECK_DIR")
done
AVAIL=$(df -k "$CHECK_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
case "$AVAIL" in
    ''|*[!0-9]*) ;;
    *) [ "$AVAIL" -ge 8192 ] || die "not enough free space on $CHECK_DIR ($AVAIL KB)" ;;
esac

if [ "$ACTION" = "install" ]; then
    if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
        CUR=$(hash_file "$TARGET") || die "cannot hash target"
        if [ "$CUR" = "$H_PATCH" ]; then
            echo "PATCHER_ALREADY:already patched"
            exit 0
        fi
        echo "PATCHER_CONFLICT:$CUR"
        exit 3
    fi
    if [ ! -d "$TARGET_DIR" ]; then
        [ "$CREATE_DIRS" = "1" ] || die "target directory not found: $TARGET_DIR (set create_dirs=true to create it)"
        mkdir -p "$TARGET_DIR" || die "cannot create target directory: $TARGET_DIR"
    fi
    [ "$(hash_file "$NEW")" = "$H_PATCH" ] || die "uploaded patched file hash mismatch"

    TMP="$TARGET.patcher.$$"
    cat "$NEW" > "$TMP" || { rm -f "$TMP"; die "cannot write temp"; }
    chmod "$FILE_MODE" "$TMP" || { rm -f "$TMP"; die "cannot chmod temp"; }
    sync
    mv -f "$TMP" "$TARGET" || { rm -f "$TMP"; die "create failed"; }
    sync

    AFTER=$(hash_file "$TARGET") || AFTER=""
    if [ "$AFTER" != "$H_PATCH" ]; then
        rm -f "$TARGET"
        die "final verification failed; created file removed"
    fi
    echo "PATCHER_OK:created"
    exit 0
fi

# rollback: remove the file only if it is still the one we created
if [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    echo "PATCHER_ALREADY:already absent"
    exit 0
fi
CUR=$(hash_file "$TARGET") || die "cannot hash target"
if [ "$CUR" != "$H_PATCH" ]; then
    echo "PATCHER_CONFLICT:$CUR"
    exit 3
fi
rm -f "$TARGET" || die "remove failed"
sync
echo "PATCHER_OK:removed"
''' % (target, new, hp, file_mode, create_dirs, action, REMOTE_HASH_FUNC)


def build_symlink_script(file_spec, mode,
                         backup_dir="/svp/patcher/backup", target_path=None):
    target = shlex.quote(target_path or file_spec["target"])
    link = shlex.quote(file_spec["link"])
    action = shlex.quote(mode)
    create_dirs = "1" if file_spec.get("create_dirs") else "0"

    return r'''set -u
TARGET=%s
LINK=%s
CREATE_DIRS=%s
ACTION=%s
die() { echo "PATCHER_ERROR:$*"; exit 1; }

if [ "$ACTION" = "install" ]; then
    if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
        CUR=$(readlink "$TARGET" 2>/dev/null || true)
        if [ "$CUR" = "$LINK" ]; then
            echo "PATCHER_ALREADY:already linked"
            exit 0
        fi
        echo "PATCHER_CONFLICT:$CUR"
        exit 3
    fi
    TARGET_DIR=$(dirname "$TARGET")
    if [ ! -d "$TARGET_DIR" ]; then
        [ "$CREATE_DIRS" = "1" ] || die "target directory not found: $TARGET_DIR (set create_dirs=true to create it)"
        mkdir -p "$TARGET_DIR" || die "cannot create target directory: $TARGET_DIR"
    fi
    TMP="$TARGET.patcher.$$"
    rm -f "$TMP"
    ln -s "$LINK" "$TMP" || die "cannot create symlink"
    sync
    mv -f "$TMP" "$TARGET" || { rm -f "$TMP"; die "cannot install symlink"; }
    sync
    AFTER=$(readlink "$TARGET" 2>/dev/null || true)
    if [ "$AFTER" != "$LINK" ]; then
        rm -f "$TARGET"
        die "final verification failed; symlink removed"
    fi
    echo "PATCHER_OK:linked"
    exit 0
fi

# rollback: remove the link only if it is still ours
if [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    echo "PATCHER_ALREADY:already absent"
    exit 0
fi
CUR=$(readlink "$TARGET" 2>/dev/null || true)
if [ ! -L "$TARGET" ] || [ "$CUR" != "$LINK" ]; then
    echo "PATCHER_CONFLICT:$CUR"
    exit 3
fi
rm -f "$TARGET" || die "remove failed"
sync
echo "PATCHER_OK:removed"
''' % (target, link, create_dirs, action)


def build_apply_script(file_spec, mode, remote_new, remote_orig,
                       backup_dir="/svp/patcher/backup", target_path=None):
    if file_spec["op"] == "create":
        return build_create_script(file_spec, mode, remote_new,
                                   backup_dir=backup_dir,
                                   target_path=target_path)
    if file_spec["op"] == "symlink":
        return build_symlink_script(file_spec, mode,
                                    backup_dir=backup_dir,
                                    target_path=target_path)
    return build_replace_script(file_spec, mode, remote_new, remote_orig,
                                backup_dir=backup_dir,
                                target_path=target_path)


def build_tz_script(zone, backup_dir="/svp/patcher/backup",
                    localtime="/etc/localtime", timezone="/etc/timezone",
                    zoneinfo="/usr/share/zoneinfo"):
    script = r'''set -u
Z=__ZONE__
ZDIR=__ZDIR__
B=__BACKUP__
LT=__LT__
TZF=__TZF__
[ -f "$ZDIR/$Z" ] || { echo "TZ_NOZONE:$Z"; exit 2; }
mkdir -p "$B" || { echo "TZ_ERROR:backup dir"; exit 3; }

# Save the original only once (first install).
if [ ! -f "$B/localtime.link" ] && [ ! -f "$B/localtime.file" ]; then
    if [ -L "$LT" ]; then
        readlink "$LT" > "$B/localtime.link" 2>/dev/null || { echo "TZ_ERROR:backup localtime"; exit 3; }
    elif [ -f "$LT" ]; then
        cp -a "$LT" "$B/localtime.file" 2>/dev/null || { echo "TZ_ERROR:backup localtime"; exit 3; }
    fi
fi
if [ ! -f "$B/timezone" ] && [ -f "$TZF" ]; then
    cp -a "$TZF" "$B/timezone" 2>/dev/null || { echo "TZ_ERROR:backup timezone"; exit 3; }
fi

ln -sfn "$ZDIR/$Z" "$LT" || { echo "TZ_ERROR:localtime"; exit 4; }
if ! printf '%s\n' "$Z" > "$TZF"; then
    # best-effort restore of localtime
    if [ -f "$B/localtime.link" ]; then
        ln -sfn "$(cat "$B/localtime.link")" "$LT" 2>/dev/null || true
    elif [ -f "$B/localtime.file" ]; then
        cp -a "$B/localtime.file" "$LT.tmp" 2>/dev/null && \
            mv -f "$LT.tmp" "$LT" 2>/dev/null || true
    fi
    echo "TZ_ERROR:timezone file"
    exit 5
fi
if [ "$(readlink -f "$LT")" != "$ZDIR/$Z" ]; then
    echo "TZ_ERROR:verify"
    exit 6
fi
echo "TZ_OK:$Z"
'''
    return (script.replace("__ZONE__", shlex.quote(zone))
                  .replace("__ZDIR__", shlex.quote(zoneinfo))
                  .replace("__BACKUP__", shlex.quote(backup_dir))
                  .replace("__LT__", shlex.quote(localtime))
                  .replace("__TZF__", shlex.quote(timezone)))


def build_tz_restore_script(backup_dir="/svp/patcher/backup",
                            localtime="/etc/localtime",
                            timezone="/etc/timezone"):
    script = r'''set -u
B=__BACKUP__
LT=__LT__
TZF=__TZF__
restored=0
if [ -f "$B/localtime.link" ]; then
    ln -sfn "$(cat "$B/localtime.link")" "$LT" || { echo "TZ_RESTORE_ERROR:localtime"; exit 4; }
    restored=1
elif [ -f "$B/localtime.file" ]; then
    cp -a "$B/localtime.file" "$LT.tmp" && \
        mv -f "$LT.tmp" "$LT" || { echo "TZ_RESTORE_ERROR:localtime"; exit 4; }
    restored=1
fi
if [ -f "$B/timezone" ]; then
    cat "$B/timezone" > "$TZF" || { echo "TZ_RESTORE_ERROR:timezone"; exit 5; }
    restored=1
fi
if [ "$restored" = "0" ]; then
    echo "TZ_NOBACKUP"
else
    echo "TZ_RESTORED"
fi
'''
    return (script.replace("__BACKUP__", shlex.quote(backup_dir))
                  .replace("__LT__", shlex.quote(localtime))
                  .replace("__TZF__", shlex.quote(timezone)))


def upload_patch_files(ssh, patch, remote_dir):
    stage = Path(ssh.tmpdir) / ("upload_%s" % patch["key"])
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    uploaded = []
    local_paths = []
    key = re.sub(r"[^A-Za-z0-9._-]", "_", patch["key"])
    for index, file_spec in enumerate(patch["files"], 1):
        base = "hu_%d_%s_%02d_%s" % (
            os.getpid(), key, index, file_spec["backup_name"])
        if file_spec["op"] == "symlink":
            uploaded.append({
                "file": file_spec,
                "remote_new": None,
                "remote_orig": None,
            })
            continue
        local_new = stage / (base + ".patched")
        shutil.copy2(file_spec["local_patched"], local_new)
        local_paths.append(local_new)
        local_orig = None
        remote_orig = None
        if file_spec["local_original"] is not None:
            local_orig = stage / (base + ".orig")
            shutil.copy2(file_spec["local_original"], local_orig)
            local_paths.append(local_orig)
            remote_orig = "%s/%s" % (remote_dir, local_orig.name)
        uploaded.append({
            "file": file_spec,
            "remote_new": "%s/%s" % (remote_dir, local_new.name),
            "remote_orig": remote_orig,
        })

    if not local_paths:
        # A symlink-only patch has no payload to upload.
        return uploaded

    for attempt in range(1, 4):
        r = ssh.scp("root", local_paths, remote_dir + "/")
        if r.returncode != 0:
            print("SCP failed (attempt %d/3):" % attempt)
            print(r.stdout)
            time.sleep(2)
            continue

        verify_paths = []
        for item in uploaded:
            if item["remote_new"]:
                verify_paths.append(item["remote_new"])
            if item["remote_orig"]:
                verify_paths.append(item["remote_orig"])
        hashes = remote_hashes(ssh, verify_paths)

        good = True
        for item in uploaded:
            file_spec = item["file"]
            if item["remote_new"] is None:
                continue
            hn = hashes.get(item["remote_new"])
            if hn != file_spec["hash_patched"]:
                print("Remote patched hash mismatch for %s: %s" %
                      (file_spec["target"], hn))
                good = False
                break
            if item["remote_orig"]:
                ho = hashes.get(item["remote_orig"])
                if ho != file_spec["hash_original"]:
                    print("Remote original hash mismatch for %s: %s" %
                          (file_spec["target"], ho))
                    good = False
                    break
        if good:
            return uploaded
        print("Remote hash verification failed (attempt %d/3)" % attempt)
    return None


def run_apply(ssh, file_spec, mode, remote_new, remote_orig):
    script = build_apply_script(file_spec, mode, remote_new, remote_orig)
    r = ssh.run_retry("root", "sh -s", input_text=script, timeout=180)
    out = r.stdout or ""
    print(out.strip())
    if "PATCHER_OK:" in out:
        return "OK", out
    if "PATCHER_ALREADY:" in out:
        return "ALREADY", out
    if "PATCHER_CONFLICT:" in out:
        return "CONFLICT", out
    return "ERROR", out


def set_timezone(ssh, zone):
    r = ssh.run_retry("root", "sh -s", input_text=build_tz_script(zone), timeout=60)
    out = r.stdout or ""
    print(out.strip())
    ok = "TZ_OK:" in out
    if ok:
        try:
            ssh._tz_state = ("/usr/share/zoneinfo/" + zone, zone)
        except Exception:
            pass
    return ok, out


def restore_timezone(ssh):
    r = ssh.run_retry("root", "sh -s", input_text=build_tz_restore_script(), timeout=60)
    out = r.stdout or ""
    print(out.strip())
    ok = ("TZ_RESTORED" in out or "TZ_NOBACKUP" in out)
    if ok:
        try:
            ssh._tz_state = None
        except Exception:
            pass
    return ok, out


# ---------------------------------------------------------------------------
# timezone prompt
# ---------------------------------------------------------------------------
def map_offset(s):
    s = s.strip()
    if s.upper() in ("UTC", "GMT", "Z", "ETC/UTC"):
        return "Etc/UTC"
    m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?00)?", s)
    if not m:
        return None
    sign, num = m.group(1), int(m.group(2))
    if num < 0 or num > 14:
        return None
    if sign == "-":
        if num < 1 or num > 12:
            return None
        return "Etc/GMT+%d" % num
    if num == 0:
        return "Etc/UTC"
    return "Etc/GMT-%d" % num


def prompt_zone(ssh):
    lt, tz = remote_tz_state(ssh)
    print("Current timezone: %s ; /etc/timezone=%s" % (lt, tz))
    print("Enter UTC offset (-12..+14, e.g. +3, -5, 0) or an IANA zone")
    print("(e.g. Europe/Moscow). Empty = keep current timezone.")
    for _ in range(5):
        s = ask_text("Timezone")
        if not s:
            return None
        if "/" in s:
            zone = s
            if not re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*", zone):
                print("  Invalid zone name.")
                continue
        else:
            zone = map_offset(s)
            if not zone:
                print("  Invalid offset. Use a whole-hour offset -12..+14.")
                continue
        rc, out = remote_get(ssh, "test -f %s && echo YES || echo NO" %
                            shlex.quote("/usr/share/zoneinfo/" + zone))
        if "YES" in out:
            return zone
        print("  No such zone in /usr/share/zoneinfo: %s" % zone)
    print("  Timezone not changed.")
    return None


# ---------------------------------------------------------------------------
# root access / bootstrap
# ---------------------------------------------------------------------------
def candidate_keys(explicit):
    if explicit:
        return [Path(explicit)]
    out = []
    for p in (Path.home() / ".ssh" / "hu_patcher_ed25519",
              Path.home() / ".ssh" / "id_ed25519",
              Path.home() / ".ssh" / "id_rsa",
              Path.home() / ".ssh" / "id_ecdsa"):
        if p.is_file():
            out.append(p)
    return out


def find_root(ssh, args, keys=None):
    if keys is None:
        keys = candidate_keys(args.key)
    log_only("[root] key candidates: %s" %
             (", ".join(str(k) for k in keys) or "(none)"))
    if not keys and not args.key:
        print("  no SSH keys found in %s" % (Path.home() / ".ssh"))
    last_state = None
    for key in keys:
        if not key.is_file():
            continue
        ssh.key = key
        print("  trying root key: %s" % key)
        ok, state = root_probe(ssh)
        log_only("[root] probe %s -> %s" % (key, state))
        if ok:
            print("Root access OK with %s" % key)
            return key
        last_state = state
        if state == "timeout":
            print("  no answer in 120 s; the key was not rejected")

    if last_state == "timeout":
        # A slow unit must not silently trigger a bootstrap: the key may be
        # valid, the SSH session just needs longer than usual.
        print("Root access could not be verified: the SSH session timed out.")
        print("The key was not rejected; the HU may just be slow to open SSH.")
        if ask_yes("Retry the root probe with a longer timeout?", True):
            ok, state = root_probe(ssh, timeout=180)
            log_only("[root] retry probe %s -> %s" % (ssh.key, state))
            if ok:
                print("Root access OK with %s" % ssh.key)
                return ssh.key
            last_state = state
        if last_state == "timeout":
            if not ask_yes("Bootstrap via media anyway?", False):
                raise RootProbeUnverified()
            return None

    if args.key:
        if last_state == "timeout":
            print("Root access could not be verified: the SSH session timed out.")
        else:
            print("Root access failed with the specified key: %s" % args.key)
        return None

    # No local keys: the caller goes straight to the media bootstrap.
    if not keys:
        return None

    # allow the operator to point at another key instead of bootstrapping
    for _ in range(3):
        p = ask_text("Path to a root key (empty = bootstrap via media)")
        if not p:
            return None
        path = Path(p).expanduser()
        if not path.is_file():
            print("  Not found: %s" % path)
            continue
        ssh.key = path
        ok, state = root_probe(ssh)
        if ok:
            print("Root access OK with %s" % path)
            return path
        if state == "timeout":
            print("  no answer in 120 s; the key was not rejected")
        else:
            print("  This key does not give root access.")
    return None


def public_key_body(text):
    """Return 'type body' from the first OpenSSH public key line."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and (parts[0].startswith("ssh-")
                                or parts[0].startswith("ecdsa-")
                                or parts[0].startswith("sk-")):
            return parts[0] + " " + parts[1]
    return ""


def key_fingerprint(path):
    r = subprocess.run(["ssh-keygen", "-lf", str(path)],
                       stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()


def derive_public_key(key):
    """Derive a public key from the private key; None on failure.

    A protected private key makes ssh-keygen fail, which also tells us that
    BatchMode SSH could not use it without an agent.  ``-P ""`` prevents an
    interactive passphrase prompt (Windows may otherwise wait on the console).
    """
    try:
        r = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(key)],
                           stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()


def prepare_bootstrap_key(args):
    if args.key:
        key = Path(args.key).expanduser()
        if not key.is_file():
            print("ERROR: key not found: %s" % key)
            return None, None
    else:
        key = Path.home() / ".ssh" / "hu_patcher_ed25519"
        if not key.is_file():
            key.parent.mkdir(parents=True, exist_ok=True)
            print("Generating bootstrap key: %s" % key)
            r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "",
                                "-C", "hu-patcher", "-f", str(key)],
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
            if r.returncode != 0:
                print("ERROR: ssh-keygen failed:")
                print(r.stdout)
                return None, None

    # The public key is derived from the private key, never trusted from a
    # possibly stale .pub file.  A mismatch here is the usual reason the key
    # installed on the HU does not work.
    derived = derive_public_key(key)
    if not derived:
        print("ERROR: cannot read the private key: %s" % key)
        print("       It may be passphrase-protected; BatchMode SSH cannot")
        print("       use it without an agent. Use a key without a passphrase.")
        return None, None

    pub = Path(str(key) + ".pub")
    existing = pub.read_text(encoding="utf-8", errors="replace") \
        if pub.is_file() else ""
    if public_key_body(existing) != public_key_body(derived):
        if existing.strip():
            print("WARNING: %s does not match the private key; rewriting it."
                  % pub.name)
        pub.write_text(derived + "\n", encoding="utf-8")

    fp = key_fingerprint(pub)
    if fp:
        print("Bootstrap key: %s" % fp)
    return key, pub


def normalize_tree(root):
    for p in Path(root).rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in (".sh", ".pub") or p.name in ("install.sh", "preflight.sh"):
            data = p.read_bytes().replace(b"\r\n", b"\n")
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            p.write_bytes(data)


def verify_bootstrap(ssh, pub):
    """Check the staged key and report where root's home is mounted."""
    print()
    print("Verifying the staged key and root home layout ...")
    command = ("cat /svp/unlock/authorized_key.pub 2>/dev/null; "
               "echo '---MOUNTS---'; cat /proc/mounts 2>/dev/null")
    r = ssh.run("media", command, batch=False, timeout=120)
    if r.returncode != 0:
        print("ERROR: cannot query the bootstrap state as media:")
        print((r.stdout or "").strip())
        return False

    text = r.stdout or ""
    key_text, _sep, mounts = text.partition("---MOUNTS---")
    local_body = public_key_body(pub.read_text(encoding="utf-8",
                                               errors="replace"))
    remote_body = public_key_body(key_text)
    if not remote_body or remote_body != local_body:
        print("ERROR: the staged key does not match the local public key.")
        print("       local : %s" % ((local_body or "(empty)")[:70]))
        print("       remote: %s" % ((remote_body or "(empty)")[:70]))
        print("       The key on the HU would not allow this machine to log in.")
        return False
    print("staged key matches the local public key")

    home_mount = None
    userdata = False
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mount = parts[1].replace("\\040", " ")
        if mount == "/home":
            home_mount = parts[0]
        elif mount == "/mnt/userdata":
            userdata = True
    if home_mount:
        print("Root home layout: /home is mounted from %s" % home_mount)
    elif userdata:
        print("WARNING: /home is not a separate mount; /mnt/userdata exists.")
        print("         The key is staged in userdata; /home must be mounted")
        print("         before root SSH can use it.")
    else:
        print("WARNING: /home is not a separate mount and /mnt/userdata is")
        print("         missing; boot.sh will defer until root's home is mounted.")
    return True


def bootstrap_root(ssh, args, assume_media=False):
    print()
    print("Root access is not available.")
    if assume_media:
        print("No SSH keys found; using the media bootstrap.")
    elif not ask_yes("Bootstrap root via the media account?", True):
        return False
    if not V2_DIR.is_dir():
        print("ERROR: missing bootstrap payload: %s" % V2_DIR)
        return False
    for name in ("install.sh", "preflight.sh", "boot.sh", "sysctl-shim.sh"):
        if not (V2_DIR / name).is_file():
            print("ERROR: incomplete bootstrap payload: missing %s" % (V2_DIR / name))
            return False

    key, pub = prepare_bootstrap_key(args)
    if not key:
        return False

    # media login
    print()
    print("Checking media login (password is usually empty; press Enter).")
    if not media_ok(ssh):
        if ssh.sshpass:
            from getpass import getpass
            pw = getpass("media password: ")
            ssh.media_password = pw
            if not media_ok(ssh):
                print("ERROR: media login failed.")
                return False
        else:
            print("ERROR: media login failed. If media has a password, install")
            print("       sshpass or use an empty media password.")
            return False
    print("media access OK")

    # stage v2 + public key
    staging = Path(ssh.tmpdir) / "bootstrap"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(V2_DIR, staging)
    normalize_tree(staging)
    (staging / "bootstrap_key.pub").write_text(pub.read_text().replace("\r\n", "\n"))

    remote_dir = "/tmp/hu_patcher_v2"
    r = ssh.run("media", "rm -rf %s && mkdir -p %s" % (remote_dir, remote_dir),
                batch=False, timeout=60)
    if r.returncode != 0:
        print("ERROR: cannot prepare %s as media:" % remote_dir)
        print(r.stdout)
        return False

    files = [str(p) for p in staging.iterdir() if p.is_file()]
    r = ssh.scp("media", files, remote_dir + "/", batch=False)
    if r.returncode != 0:
        print("ERROR: scp to media failed:")
        print(r.stdout)
        return False

    for attempt in range(1, 4):
        print("Running preflight (attempt %d/3) ..." % attempt)
        r = ssh.run("media", "sh %s/preflight.sh" % remote_dir,
                    batch=False, timeout=120)
        print((r.stdout or "").strip())
        if r.returncode != 0:
            print("ERROR: preflight failed; nothing installed.")
            if attempt < 3 and ask_yes("Retry preflight?", True):
                continue
            return False

        print("Installing bootstrap (media, attempt %d/3) ..." % attempt)
        r = ssh.run("media", "sh %s/install.sh --key %s/bootstrap_key.pub" %
                    (remote_dir, remote_dir), batch=False, timeout=180)
        print((r.stdout or "").strip())
        if r.returncode != 0:
            print("ERROR: install failed. The v2 installer is fail-safe (it")
            print("       never changes boot/kernel files); you can retry.")
            if attempt < 3 and ask_yes("Retry install?", True):
                continue
            return False
        break

    if not verify_bootstrap(ssh, pub):
        return False

    print()
    print("Bootstrap installed. A reboot is required.")
    ssh.key = key
    old_boot_id = media_boot_id(ssh)
    print_reboot_instruction()
    ans = ask_text("Press Enter after starting the reboot (or type 'skip')", "")
    if ans.lower() == "skip":
        print("Reboot later, then run this script again.")
        return False
    if old_boot_id and not wait_for_reboot(
            ssh, old_boot_id, timeout=600, source="media"):
        print("WARNING: no reboot detected yet; waiting for root anyway.")

    ok = wait_root(ssh, timeout=600)
    if not ok:
        if ask_yes("Root still not available. Wait another 300 s?", True):
            ok = wait_root(ssh, timeout=300)
    if not ok:
        print("ERROR: root access did not come up. Check the HU and rerun.")
        return False
    return True


# ---------------------------------------------------------------------------
# patch operations
# ---------------------------------------------------------------------------
def undo_applied_files(ssh, applied, mode):
    """Undo files changed by a partial install/rollback attempt."""
    undo_mode = "rollback" if mode == "install" else "install"
    for item in reversed(applied):
        file_spec = item["file"]
        print("  undoing %s: %s" % (file_spec["target"],
                                    "rollback" if undo_mode == "rollback" else "install"))
        result, _out = run_apply(ssh, file_spec, undo_mode,
                                 item["remote_new"], item["remote_orig"])
        if result not in ("OK", "ALREADY"):
            print("WARNING: could not undo %s" % file_spec["target"])


def perform_patch(ssh, patch, mode):
    print()
    label = "%s) %s" % (patch["id"], patch["name"])
    log_only("[patch] %s: %s (version %s)" %
             (label, "install" if mode == "install" else "rollback",
              patch["version"]))
    for file_spec in patch["files"]:
        log_only("[patch]   %s %s" % (file_spec["op"], file_spec["target"]))
    print("== %s: %s ==" % (label, "install" if mode == "install" else "rollback"))

    remote_dir = "/tmp"
    uploaded = upload_patch_files(ssh, patch, remote_dir)
    if not uploaded:
        print("ERROR: could not upload/verify files.")
        return False
    log_only("[patch] uploaded %d file(s)" % len(uploaded))

    state = {}
    if mode == "install":
        state = call_patch_hook(patch, "pre_install", ssh, patch)
    else:
        state = call_patch_hook(patch, "pre_rollback", ssh, patch)
    if state is False:
        print("Patch cancelled.")
        return False
    if not isinstance(state, dict):
        state = {}

    applied = []
    for item in uploaded:
        file_spec = item["file"]
        result, out = run_apply(ssh, file_spec, mode,
                                item["remote_new"], item["remote_orig"])
        log_only("[patch] %s %s -> %s" %
                 (mode, file_spec["target"], result))
        if result == "CONFLICT":
            print("CONFLICT: %s: unknown file version. No changes left in place." %
                  file_spec["target"])
            undo_applied_files(ssh, applied, mode)
            return False
        if result == "ERROR":
            print("ERROR: remote apply failed for %s." % file_spec["target"])
            print("       The remote script restores the file backup on failure.")
            undo_applied_files(ssh, applied, mode)
            return False
        if result == "OK":
            applied.append(item)

    state["already"] = not applied
    if not applied:
        print("Already in the requested state.")

    post_name = "post_install" if mode == "install" else "post_rollback"
    if call_patch_hook(patch, post_name, ssh, patch, state) is False:
        if mode == "install" and applied:
            print("ERROR: patch post-install step failed; rolling the patch back.")
            undo_applied_files(ssh, applied, mode)
            call_patch_hook(patch, "post_rollback", ssh, patch, state)
            return False
        if mode == "rollback":
            print("WARNING: patch post-rollback step failed.")
            return True
        return False
    if mode == "install":
        write_patch_state(ssh, patch)
    else:
        remove_patch_state(ssh, patch)
    log_only("[patch] %s done" % mode)
    return True


def build_old_specs(patch, state):
    """Reconstruct rollback specs from the saved installed-version state."""
    if not state or not isinstance(state.get("files"), list):
        return None
    current = {f["target"]: f for f in patch["files"]}
    specs = []
    for index, item in enumerate(state["files"], 1):
        if not isinstance(item, dict):
            return None
        target = item.get("target")
        op = item.get("op")
        if not target or op not in ("replace", "create", "symlink"):
            print("ERROR: invalid saved state entry for %r." % target)
            return None
        spec = {
            "op": op,
            "target": target,
            "backup_name": "old_%02d_%s" % (index, Path(target).name),
            "local_original": None,
            "local_patched": None,
            "hash_original": item.get("hash_original"),
            "hash_patched": item.get("hash_patched"),
            "link": item.get("link"),
            "verify": None,
            "mode": None,
            "create_dirs": False,
            "build": None,
        }
        if op == "replace":
            if not spec["hash_original"] or not spec["hash_patched"]:
                print("ERROR: saved state is incomplete for %s." % target)
                return None
            cur = current.get(target)
            if cur is None or cur.get("local_original") is None:
                print("ERROR: no original payload for %s in the new manifest." %
                      target)
                return None
            if sha256_file(cur["local_original"]) != spec["hash_original"]:
                print("ERROR: firmware original changed for %s; refusing "
                      "to update." % target)
                return None
            spec["local_original"] = cur["local_original"]
        elif op == "create":
            if not spec["hash_patched"]:
                print("ERROR: saved state is incomplete for %s." % target)
                return None
        elif op == "symlink":
            if not spec["link"]:
                print("ERROR: saved state is incomplete for %s." % target)
                return None
        specs.append(spec)
    if not specs:
        print("ERROR: saved state has no files.")
        return None
    return specs


def old_specs_match(ssh, specs):
    probes, _tz, _states = remote_status(
        ssh, [spec["target"] for spec in specs])
    if probes is None:
        return False
    for spec in specs:
        kind, value = probes.get(spec["target"], (None, None))
        if kind == "A":
            continue
        if spec["op"] == "symlink":
            if kind != "L" or value != spec["link"]:
                return False
        elif kind != "F" or value != spec["hash_patched"]:
            return False
    return True


def upload_old_originals(ssh, patch, specs):
    stage = Path(ssh.tmpdir) / ("update_%s" % re.sub(
        r"[^A-Za-z0-9._-]", "_", patch["key"]))
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    mapping = {}
    local_paths = []
    for index, spec in enumerate(specs, 1):
        if spec["op"] != "replace":
            continue
        name = "hu_old_%d_%02d_%s" % (
            os.getpid(), index, Path(spec["target"]).name)
        local = stage / name
        shutil.copy2(spec["local_original"], local)
        local_paths.append(local)
        mapping[spec["target"]] = "/tmp/" + name
    if not local_paths:
        return mapping
    r = ssh.scp("root", local_paths, "/tmp/")
    if r.returncode != 0:
        print("SCP failed for the old original:")
        print(r.stdout)
        return None
    return mapping


def perform_patch_update(ssh, patch, state):
    print("Updating %s) %s ..." % (patch["id"], patch["name"]))
    log_only("[update] %s: %s -> %s" %
             (patch["id"], state.get("version") if state else "?",
              patch["version"]))
    if not state:
        print("ERROR: no saved state for the installed version; cannot update "
              "safely.")
        return False
    specs = build_old_specs(patch, state)
    if specs is None:
        return False

    probes, _tz, _states = remote_status(
        ssh, [f["target"] for f in patch["files"]])
    if probes is not None:
        installed_now = all(
            probe_to_status(f, probes.get(f["target"]))[0] == STATUS_INSTALLED
            for f in patch["files"])
        if installed_now:
            print("Current files already match the new version; updating "
                  "state only.")
            log_only("[update] files already match; refreshing state")
            write_patch_state(ssh, patch)
            return True

    if not old_specs_match(ssh, specs):
        print("ERROR: installed files do not match the saved old version.")
        print("       Refusing to update; remove the patch manually and "
              "install it again.")
        log_only("[update] old files do not match the saved state; refused")
        return False

    originals = upload_old_originals(ssh, patch, specs)
    if originals is None:
        return False

    for spec in specs:
        result, _out = run_apply(
            ssh, spec, "rollback", None, originals.get(spec["target"]))
        log_only("[update] remove old %s -> %s" %
                 (spec["target"], result))
        if result not in ("OK", "ALREADY"):
            print("ERROR: cannot remove the old version of %s." % spec["target"])
            print("       The update was stopped; check the patch status.")
            return False

    if not perform_patch(ssh, patch, "install"):
        print("ERROR: old version removed but the new install failed.")
        print("       Run the patcher again to install the new version.")
        return False
    log_only("[update] %s updated to %s" % (patch["id"], patch["version"]))
    return True


def build_patch_actions(snapshot):
    """Turn the status snapshot into selectable actions."""
    modes = {
        STATUS_NOT_INSTALLED: "install",
        STATUS_INSTALLED: "rollback",
        STATUS_PARTIAL: "install",
        STATUS_OUTDATED: "update",
    }
    actions = []
    for patch in PATCHES:
        entry = snapshot[patch["id"]]
        status = entry["status"]
        actions.append({
            "patch": patch,
            "status": status,
            "details": entry["details"],
            "state": entry.get("state"),
            "installed_version": entry.get("installed_version"),
            "mode": modes.get(status),
            "selectable": status != STATUS_CONFLICT,
        })
    return actions


def action_text(action):
    if not action["selectable"]:
        return "conflict; resolve manually"
    if action["status"] == STATUS_OUTDATED:
        return "update (uninstall old + install new)"
    if action["status"] == STATUS_PARTIAL:
        if action["mode"] == "rollback":
            return "roll back installed files"
        return "install missing files"
    if action["mode"] == "install":
        return "install"
    return "remove"


def print_conflict_details(action):
    for file_spec, file_status, _h in action["details"]:
        if file_status != STATUS_CONFLICT:
            continue
        print("          %s: conflict" % file_spec["target"])
        if file_spec["op"] == "replace":
            print("            expected original: %s" %
                  file_spec["hash_original"])
            print("            expected patched : %s" %
                  file_spec["hash_patched"])
        elif file_spec["op"] == "symlink":
            print("            expected symlink: %s" % file_spec["link"])
        else:
            print("            expected absent or patched: %s" %
                  file_spec["hash_patched"])


def print_action_menu(actions, selected):
    has_conflict = False
    print()
    print("Select patches (numbers toggle, a=all, n=none, Enter=apply, q=quit):")
    for index, action in enumerate(actions, 1):
        patch = action["patch"]
        if not action["selectable"]:
            mark = "[-]"
            has_conflict = True
        elif (index - 1) in selected:
            mark = "[x]"
        else:
            mark = "[ ]"
        print("  %s %d) %s) %-34s %s" % (
            mark, index, patch["id"], patch["name"],
            colorize_status(action["status"])))
        print("        %s" % version_line(action, patch))
        print("        %s" % action_text(action))
        if not action["selectable"]:
            print_conflict_details(action)
    if has_conflict:
        print()
        print("Skipping: unknown file version.")


def menu_input(prompt):
    try:
        value = input("%s: " % prompt).strip()
    except EOFError:
        log_only("[menu] %s -> EOF (quit)" % prompt)
        print()
        return "q"
    log_only("[menu] %s -> %r" % (prompt, value))
    return value


def select_patch_actions(actions):
    selected = set()
    while True:
        print_action_menu(actions, selected)
        raw = menu_input("Toggle")
        if raw == "":
            if not selected:
                print("  Nothing selected. Enter numbers, a, or q.")
                continue
            log_only("[menu] selected: %s" %
                     ", ".join(actions[i]["patch"]["id"]
                               for i in sorted(selected)))
            return selected
        command = raw.lower()
        if command in ("q", "quit"):
            log_only("[menu] quit without changes")
            return None
        if command == "a":
            selected = {i for i, action in enumerate(actions)
                        if action["selectable"]}
            log_only("[menu] select all")
            continue
        if command == "n":
            selected = set()
            log_only("[menu] select none")
            continue
        for token in re.split(r"[\s,]+", raw):
            if not token:
                continue
            if not token.isdigit():
                print("  Unknown selection: %s" % token)
                continue
            number = int(token)
            index = number - 1
            if index < 0 or index >= len(actions):
                print("  No patch %d." % number)
                continue
            action = actions[index]
            if not action["selectable"]:
                print("  %d) %s: %s" %
                      (number, action["patch"]["name"], action_text(action)))
                continue
            if index in selected:
                selected.remove(index)
                continue
            if action["status"] == STATUS_PARTIAL:
                choice = ask_text(
                    "%s is partial: [i]nstall missing or [r]oll back installed?"
                    % action["patch"]["name"], "i").lower()
                action["mode"] = "rollback" if choice.startswith("r") \
                    else "install"
            selected.add(index)


def print_selected_actions(actions, selected):
    print()
    print("Selected actions:")
    for index in sorted(selected):
        action = actions[index]
        print("  [x] %s) %s: %s" % (
            action["patch"]["id"], action["patch"]["name"],
            action_text(action)))


def apply_patch_actions(ssh, actions, selected):
    changed = []
    failed = []
    for index in sorted(selected):
        action = actions[index]
        patch = action["patch"]
        mode = action["mode"]
        log_only("[apply] %s) %s: %s" % (patch["id"], patch["name"],
                                          mode or "?"))
        ok = False
        for attempt in range(1, 4):
            if mode == "update":
                success = perform_patch_update(
                    ssh, patch, action.get("state"))
            else:
                success = perform_patch(ssh, patch, mode)
            if success:
                ok = True
                break
            if attempt < 3 and ask_yes("%s) %s failed. Retry?" %
                                       (patch["id"], patch["name"]), True):
                continue
            break
        if ok:
            verb = {
                "install": "installed",
                "rollback": "rolled back",
                "update": "updated",
            }.get(mode, "changed")
            changed.append("%s: %s" % (patch["id"], verb))
            print("Patch %s. Reboot is required to apply it." % verb)
        else:
            failed.append("%s: %s" % (patch["id"], action_text(action)))
    return changed, failed


def remote_boot_id(ssh):
    rc, out = remote_get(ssh, "cat /proc/sys/kernel/random/boot_id 2>/dev/null")
    if rc != 0 or not out:
        return None
    return out.strip() or None


def root_probe_boot_id(ssh, timeout=45):
    """One SSH session: root check plus boot id for reboot detection."""
    r = ssh.run("root",
                "id -u; cat /proc/sys/kernel/random/boot_id 2>/dev/null",
                timeout=timeout)
    if r.returncode == 124:
        return False, None
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if r.returncode != 0 or not lines or lines[0] != "0":
        return False, None
    return True, (lines[1] if len(lines) > 1 else None)


# ---------------------------------------------------------------------------
# patch hook API
# ---------------------------------------------------------------------------
class PatchApi:
    """Stable API for patch hooks.

    Methods resolve module-level functions at call time, so tests and the
    mock patcher can replace them with monkeypatching.
    """

    def remote_tz_state(self, ssh):
        return remote_tz_state(ssh)

    def prompt_zone(self, ssh):
        return prompt_zone(ssh)

    def set_timezone(self, ssh, zone):
        return set_timezone(ssh, zone)

    def restore_timezone(self, ssh):
        return restore_timezone(ssh)

    def self_test_zone_map(self):
        return self_test_zone_map()

    def run_apply(self, ssh, file_spec, mode, remote_new, remote_orig):
        return run_apply(ssh, file_spec, mode, remote_new, remote_orig)


PATCH_API = PatchApi()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Universal patcher for Mazda CMU (KBST-41-47-M5)")
    ap.add_argument("host", nargs="?",
                    help="HU IP/hostname (prompted if omitted)")
    ap.add_argument("--key", help="private SSH key for root")
    ap.add_argument("--port", type=int, default=22, help="SSH port (default 22)")
    ap.add_argument("--self-check", action="store_true",
                    help="validate local patched/original payloads and exit")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="colorize patch status (default: auto)")
    ap.add_argument("--no-pause", action="store_true",
                    help="do not wait for input before exit")
    log_group = ap.add_mutually_exclusive_group()
    log_group.add_argument("--log", metavar="FILE",
                           help="write a detailed operation log to FILE")
    log_group.add_argument("--no-log", action="store_true",
                           help="disable the automatic operation log")
    args = ap.parse_args()

    global PAUSE_ON_EXIT
    PAUSE_ON_EXIT = not args.no_pause

    start_logging(choose_log_path(args), args)
    setup_color(args.color)
    print_banner()
    if LOG_PATH is not None:
        print("[i] log: %s" % LOG_PATH)

    if args.self_check:
        ok = validate_local(verbose=True)
        if ok:
            print()
            ok = self_test_script_transport()
        if ok:
            print()
            ok = self_test_probe_scripts()
        if ok:
            print()
            ok = run_hook_self_checks()
        if ok:
            print()
            ok = self_test_versioning()
        if ok:
            print()
            ok = self_test_remote()
        print()
        print("SELF-CHECK: %s" % ("OK" if ok else "FAILED"))
        return 0 if ok else 1

    if not args.host:
        args.host = ask_text("HU IP/hostname")
    if not args.host:
        ap.error("host is required (or use --self-check)")
    log_only("[info] target: %s:%s" % (args.host, args.port))
    if not check_tools():
        return 1
    if not validate_local(verbose=False):
        print("ERROR: local payload validation failed; aborting.", file=sys.stderr)
        return 1

    ssh = Ssh(args.host, args.port, args.key)
    try:
        # 1-2. root access
        print()
        print("Checking root access to %s ..." % args.host)
        keys = candidate_keys(args.key)
        try:
            key = find_root(ssh, args, keys)
        except RootProbeUnverified:
            print()
            print("Root access could not be verified; nothing was changed.")
            print("Check the network/the HU and run the patcher again.")
            return 1
        if not key:
            # No local keys on first start: go straight to media bootstrap.
            assume_media = (not args.key and not keys)
            if not bootstrap_root(ssh, args, assume_media=assume_media):
                return 1
            key = ssh.key
            if not key:
                print("ERROR: no key after bootstrap.")
                return 1
            if not wait_root(ssh, timeout=120):
                print("ERROR: root SSH is not available.")
                return 1

        # 5. status (one SSH session for all targets)
        snapshot = snapshot_status(ssh)
        if snapshot is None:
            print("ERROR: cannot read the patch status; aborting.")
            return 1
        print_status(ssh, snapshot)

        # 6-8. select and apply patches
        actions = build_patch_actions(snapshot)
        selected = select_patch_actions(actions)
        if selected is None:
            print("No patch changes selected.")
            return 0
        print_selected_actions(actions, selected)
        changed, failed = apply_patch_actions(ssh, actions, selected)

        # 9. manual reboot
        print()
        if changed:
            print("Changes:")
            for x in changed:
                print("  - %s" % x)
        if failed:
            print("Failed:")
            for x in failed:
                print("  - %s" % x)
        if not changed:
            if failed:
                print("No patch changes were applied.")
            else:
                print("No patch changes were made.")
            return 0

        old_id = remote_boot_id(ssh)
        print_reboot_instruction()
        ans = ask_text("Press Enter after starting the reboot (or type 'skip')", "")
        if ans.lower() == "skip":
            print("Reboot later to apply the changes.")
            return 0
        if old_id and not wait_for_reboot(
                ssh, old_id, timeout=600, source="root"):
            print("WARNING: no reboot detected yet; waiting for root anyway.")

        ok = wait_root(ssh, timeout=600)
        if not ok:
            if ask_yes("Root still not available. Wait another 300 s?", True):
                ok = wait_root(ssh, timeout=300)
        if not ok:
            print("WARNING: root SSH did not come back within the timeout.")
            print("         The HU may need more time; check it manually.")
            return 1

        print("Root is back. Final status:")
        final = snapshot_status(ssh)
        if final is None:
            print("WARNING: cannot read the final patch status.")
        else:
            print_status(ssh, final)
        return 0
    finally:
        ssh.cleanup()


def _run():
    rc = 1
    try:
        rc = main()
        return rc
    except KeyboardInterrupt:
        print("\nInterrupted.")
        rc = 130
        return rc
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
        raise
    except Exception:
        log_exception()
        raise
    finally:
        pause_before_exit()
        stop_logging(rc)


if __name__ == "__main__":
    sys.exit(_run())

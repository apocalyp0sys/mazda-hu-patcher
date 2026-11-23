#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# mock_hu_patcher.py -- offline mock of patcher UI/interaction.
#
# It reuses the real hu_patcher.main() and all of its prompts, status tables,
# patch sequencing and reboot questions, but:
#   * never opens an SSH connection;
#   * never uploads or modifies files on a HU;
#   * simulates root/media login, patch hashes, apply/rollback, timezone and
#     reboot with an in-memory state.
#
# Usage (from the patcher root):
#   python3 mock_hu_patcher.py --list
#   python3 mock_hu_patcher.py --scenario no-root
#   python3 mock_hu_patcher.py --scenario root-one
#   python3 mock_hu_patcher.py --scenario conflict
# ---------------------------------------------------------------------------

import argparse
import builtins
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hu_patcher as hp  # noqa: E402


SCENARIOS = {
    "no-root": {
        "title": "no root, no patches installed",
        "root": False,
        "gps": "original",
        "touch": "original",
        "localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "timezone": "Asia/Shanghai",
        "answers": [
            "y",        # bootstrap via media?
            "y",        # reboot now?
            "1 2 3 4",  # select all patches
            "",         # apply the selection
            "+3",       # timezone for A
            "y",        # final reboot
        ],
    },
    "root-one": {
        "title": "root already available, patch A installed",
        "root": True,
        "gps": "patched",
        "touch": "original",
        "localtime": "/usr/share/zoneinfo/Etc/GMT-3",
        "timezone": "Etc/GMT-3",
        "orig_localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "orig_timezone": "Asia/Shanghai",
        "answers": [
            "1 2 3 4",  # A rollback, B/C/D install
            "",         # apply the selection
            "y",        # final reboot
        ],
    },
    "conflict": {
        "title": "no root, patch B has an unknown file version",
        "root": False,
        "gps": "original",
        "touch": "unknown",
        "localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "timezone": "Asia/Shanghai",
        "answers": [
            "y",        # bootstrap via media?
            "y",        # reboot now?
            "1 2 3 4",  # select all; B is skipped as conflict
            "",         # apply the selection
            "+3",       # timezone for A
            "y",        # final reboot
        ],
    },
    "manual-reboot": {
        "title": "media cannot reboot; the user reboots manually",
        "root": False,
        "manual_reboot": True,
        "gps": "original",
        "touch": "original",
        "localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "timezone": "Asia/Shanghai",
        "answers": [
            "y",        # bootstrap via media?
            "y",        # reboot now?
            "",         # press Enter after the manual reboot
            "1 2 3 4",  # select all patches
            "",         # apply the selection
            "+3",       # timezone for A
            "y",        # final reboot
        ],
    },
    "outdated": {
        "title": "Callfix installed with an older version",
        "root": True,
        "gps": "patched",
        "touch": "patched",
        "callfix": "patched",
        "login": "patched",
        "old_hashes": {
            "callfix": {
                "/svp/bin/svp-callfix": "a" * 64,
                "/etc/systemd/system/svp-callfix.service": "b" * 64,
            },
        },
        "state_versions": {"callfix": "0.9.0"},
        "localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "timezone": "Asia/Shanghai",
        "answers": [
            "3",        # select C) Callfix for update
            "",         # apply the selection
            "y",        # final reboot
        ],
    },
    "newer": {
        "title": "Callfix installed with a newer unknown version",
        "root": True,
        "gps": "patched",
        "touch": "patched",
        "callfix": "patched",
        "login": "patched",
        "state_versions": {"callfix": "9.9.9"},
        "localtime": "/usr/share/zoneinfo/Asia/Shanghai",
        "timezone": "Asia/Shanghai",
        "answers": [
            "q",        # leave without changes
        ],
    },
}

ALIASES = {
    "no-root-no-patches": "no-root",
    "root-one-patch": "root-one",
    "no-root-conflict": "conflict",
    "manual": "manual-reboot",
}


class MockState:
    def __init__(self, spec):
        self.title = spec["title"]
        self.root = spec["root"]
        self.media = True
        self.manual_reboot = spec.get("manual_reboot", False)
        self.patches = {
            p["key"]: {f["target"]: "original" for f in p["files"]}
            for p in hp.PATCHES
        }
        self.target_patch = {
            f["target"]: p["key"] for p in hp.PATCHES for f in p["files"]
        }
        self.specs = {
            f["target"]: (p, f) for p in hp.PATCHES for f in p["files"]
        }
        self.ssh_calls = 0
        for key in self.patches:
            if key in spec:
                for target in self.patches[key]:
                    self.patches[key][target] = spec[key]
        self.old_hashes = {}
        for key, mapping in spec.get("old_hashes", {}).items():
            if key not in self.patches:
                continue
            for target, value in mapping.items():
                if target in self.patches[key]:
                    self.patches[key][target] = "old"
                    self.old_hashes[target] = value
        self.states = {}
        for key, version in spec.get("state_versions", {}).items():
            patch = next((p for p in hp.PATCHES if p["key"] == key), None)
            if patch is None:
                continue
            old = spec.get("old_hashes", {}).get(key, {})
            files = [{
                "target": f["target"],
                "op": f["op"],
                "hash_patched": old.get(f["target"], f["hash_patched"]),
                "hash_original": f["hash_original"],
                "link": f["link"],
            } for f in patch["files"]]
            self.states[key] = json.dumps(
                {"version": version, "files": files},
                separators=(",", ":"), sort_keys=True)
        self.localtime = spec["localtime"]
        self.timezone = spec["timezone"]
        self.orig_localtime = spec.get("orig_localtime", spec["localtime"])
        self.orig_timezone = spec.get("orig_timezone", spec["timezone"])
        self.answers = list(spec["answers"])
        self.boot_id = "mock-boot-0"
        self.reboot_seq = 0
        self.root_ready_in = None
        self.no_answers = 0

    def start_reboot(self, why):
        self.reboot_seq += 1
        self.boot_id = "mock-boot-%d" % self.reboot_seq
        self.root = False
        self.root_ready_in = 2
        print("[mock] reboot (%s); boot_id=%s" % (why, self.boot_id))

    def root_uid(self):
        if self.root:
            return 0, "0\n"
        if self.root_ready_in is not None:
            if self.root_ready_in <= 0:
                self.root = True
                self.root_ready_in = None
                return 0, "0\n"
            self.root_ready_in -= 1
        return 1, "Permission denied\n"


STATE = None


def mock_input(prompt=""):
    print(prompt, end="", flush=True)
    if STATE.answers:
        ans = STATE.answers.pop(0)
    else:
        STATE.no_answers += 1
        print()
        print("[mock] no scripted answer left; using default empty")
        ans = ""
    print(ans)
    if "after starting the reboot" in prompt and STATE.manual_reboot:
        STATE.start_reboot("manual")
    return ans


def mock_remote_hash(ssh, path):
    for patch in hp.PATCHES:
        for file_spec in patch["files"]:
            if path != file_spec["target"]:
                continue
            if file_spec["op"] == "symlink":
                return None
            state = STATE.patches[patch["key"]][path]
            if state == "patched":
                return file_spec["hash_patched"]
            if state == "original":
                if file_spec["op"] == "create":
                    return None
                return file_spec["hash_original"]
            if state == "unknown":
                return "f" * 64
            return None
    return None


def mock_remote_readlink(ssh, path):
    for patch in hp.PATCHES:
        for file_spec in patch["files"]:
            if path != file_spec["target"] or file_spec["op"] != "symlink":
                continue
            state = STATE.patches[patch["key"]][path]
            if state == "patched":
                return file_spec["link"]
            if state == "original":
                return None
            return "/some/other-target"
    return None


def mock_remote_status(ssh, paths, want_tz=False, state_keys=None,
                       timeout=120):
    STATE.ssh_calls += 1
    probes = {}
    for path in paths:
        entry = STATE.specs.get(path)
        if entry is None:
            probes[path] = ("A", None)
            continue
        patch, spec = entry
        state = STATE.patches[patch["key"]][path]
        if state == "old":
            if spec["op"] == "symlink":
                probes[path] = ("L", spec["link"])
            else:
                probes[path] = ("F", STATE.old_hashes.get(path, "e" * 64))
        elif spec["op"] == "symlink":
            if state == "patched":
                probes[path] = ("L", spec["link"])
            elif state == "original":
                probes[path] = ("A", None)
            else:
                probes[path] = ("L", "/some/other-target")
        elif spec["op"] == "create":
            if state == "patched":
                probes[path] = ("F", spec["hash_patched"])
            elif state == "original":
                probes[path] = ("A", None)
            else:
                probes[path] = ("F", "f" * 64)
        else:
            if state == "patched":
                probes[path] = ("F", spec["hash_patched"])
            elif state == "original":
                probes[path] = ("F", spec["hash_original"])
            else:
                probes[path] = ("F", "f" * 64)
    tz = (STATE.localtime, STATE.timezone) if want_tz else None
    states = {key: STATE.states[key] for key in (state_keys or [])
              if key in STATE.states}
    return probes, tz, states


def mock_remote_tz_state(ssh):
    return STATE.localtime, STATE.timezone


def mock_write_patch_state(ssh, patch):
    STATE.states[patch["key"]] = hp.patch_state_payload(patch)
    return True


def mock_remove_patch_state(ssh, patch):
    STATE.states.pop(patch["key"], None)
    return True


def mock_upload(ssh, patch, remote_dir):
    has_payload = any(f["op"] != "symlink" for f in patch["files"])
    if has_payload:
        # scp + one batched hash verification
        STATE.ssh_calls += 2
    print("[mock] upload %s (%d file(s))" %
          (patch["name"], len(patch["files"])))
    uploaded = []
    for index, file_spec in enumerate(patch["files"], 1):
        base = "%02d_%s" % (index, file_spec["backup_name"])
        if file_spec["op"] == "symlink":
            uploaded.append({
                "file": file_spec,
                "remote_new": None,
                "remote_orig": None,
            })
            continue
        remote_new = "%s/%s.patched" % (remote_dir, base)
        remote_orig = None
        if file_spec["op"] == "replace":
            remote_orig = "%s/%s.orig" % (remote_dir, base)
        uploaded.append({
            "file": file_spec,
            "remote_new": remote_new,
            "remote_orig": remote_orig,
        })
    return uploaded


def mock_apply(ssh, file_spec, mode, remote_new, remote_orig):
    STATE.ssh_calls += 1
    patch_key = STATE.target_patch[file_spec["target"]]
    print("[mock] apply %s: %s" % (file_spec["target"], mode))
    STATE.patches[patch_key][file_spec["target"]] = (
        "patched" if mode == "install" else "original")
    return "OK", "PATCHER_OK:mock"


def mock_set_timezone(ssh, zone):
    print("[mock] set timezone: %s" % zone)
    STATE.localtime = "/usr/share/zoneinfo/" + zone
    STATE.timezone = zone
    return True, "TZ_OK:%s" % zone


def mock_restore_timezone(ssh):
    print("[mock] restore timezone: %s" % STATE.orig_timezone)
    STATE.localtime = STATE.orig_localtime
    STATE.timezone = STATE.orig_timezone
    return True, "TZ_RESTORED"


class MockSsh:
    def __init__(self, host, port, key, media_password="", tmpdir=None):
        self.host = host
        self.port = port
        self.key = Path(key) if key else None
        self.media_password = media_password
        self.sshpass = None
        self.tmpdir = Path(tmpdir or tempfile.mkdtemp(prefix="hu_patcher_mock_"))

    def cleanup(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _res(rc=0, out=""):
        return subprocess.CompletedProcess([], rc, out, "")

    def run(self, user, command, input_text=None, batch=True, timeout=60):
        STATE.ssh_calls += 1
        cmd = command.strip()

        if user == "root" and cmd == "id -u":
            rc, out = STATE.root_uid()
            return self._res(rc, out)

        if user == "root" and cmd.startswith("id -u;"):
            rc, out = STATE.root_uid()
            if rc == 0:
                return self._res(0, out + STATE.boot_id + "\n")
            return self._res(rc, out)

        if user == "media" and cmd == "id":
            if STATE.media:
                return self._res(0, "uid=100(media) gid=100(users) groups=100(users)\n")
            return self._res(1, "Permission denied\n")

        if "cat /proc/sys/kernel/random/boot_id" in cmd:
            return self._res(0, STATE.boot_id + "\n")

        if user == "media" and "/svp/unlock/authorized_key.pub" in cmd:
            pub = Path(str(self.key) + ".pub") if self.key else None
            if pub and pub.is_file():
                return self._res(
                    0, pub.read_text(encoding="utf-8")
                       + "---MOUNTS---\n"
                         "/dev/mmcblk0p4 / ext4 rw 0 0\n"
                         "/dev/mmcblk0p6 /home ext4 rw 0 0\n")
            return self._res(1, "No such file or directory\n")

        if user == "root" and "/etc/passwd" in cmd and "root" in cmd:
            return self._res(0, "/home/root\n")

        if user == "root" and "command -v systemctl" in cmd:
            return self._res(0, "YES\n")

        if "test -f /usr/share/zoneinfo" in cmd:
            return self._res(0, "YES\n")

        if user == "media" and ("svp.util.powerctrl" in cmd
                                or "svp.util.remote.reboot" in cmd):
            # These do not work from media on this firmware; the patcher must
            # not use them.
            return self._res(1, "not available to media\n")

        if user == "media" and "systemctl reboot" in cmd:
            if STATE.manual_reboot and STATE.reboot_seq == 0:
                return self._res(
                    1, "Failed to reboot system via logind: Access denied\n")
            STATE.start_reboot("systemd")
            return self._res(0, "")

        if user == "media" and cmd == "reboot":
            return self._res(1, "reboot: must be root\n")

        if user == "root" and "reboot" in cmd:
            STATE.start_reboot("root")
            return self._res(0, "")

        if user == "media" and "preflight.sh" in cmd:
            print("[mock] media preflight OK")
            return self._res(0, "[mock] preflight OK\n")

        if user == "media" and "install.sh" in cmd:
            print("[mock] media install OK")
            return self._res(0, "[mock] install OK\n")

        if "rm -rf" in cmd or "mkdir" in cmd:
            return self._res(0, "")

        if cmd == "sh -s" and input_text:
            # Apply/hash/timezone scripts are mocked above; this is only a
            # fallback for unexpected calls.
            print("[mock] sh -s (%d bytes)" % len(input_text))
            return self._res(0, "")

        print("[mock] ssh %s: %s" % (user, cmd[:90]))
        return self._res(0, "")

    def scp(self, user, sources, remote, batch=True, timeout=180):
        STATE.ssh_calls += 1
        print("[mock] scp %s -> %s@%s:%s" %
              (", ".join(str(Path(s).name) for s in sources), user, self.host, remote))
        return self._res(0, "")

    def run_retry(self, user, command, input_text=None, batch=True,
                  timeout=60, attempts=3):
        return self.run(user, command, input_text=input_text,
                        batch=batch, timeout=timeout)


def make_mock_key(tmpdir):
    key = Path(tmpdir) / "mock_id_ed25519"
    pub = Path(str(key) + ".pub")
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nMOCK\n"
                   "-----END OPENSSH PRIVATE KEY-----\n")
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE6Wwe06A8FR1sRTvhrM9u6rbXa3PsLSImt8CNQySxtM mock\n")
    return key


def main():
    ap = argparse.ArgumentParser(
        description="Offline mock of the HU patcher UI (no HU interaction)")
    ap.add_argument("--scenario", help="scenario name (see --list)")
    ap.add_argument("--list", action="store_true", help="list scenarios")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="colorize patch status (default: auto)")
    args = ap.parse_args()

    if args.list:
        print("Scenarios:")
        for name, spec in SCENARIOS.items():
            print("  %-10s %s" % (name, spec["title"]))
        return 0

    name = ALIASES.get(args.scenario, args.scenario)
    if name not in SCENARIOS:
        print("ERROR: unknown scenario %r" % args.scenario, file=sys.stderr)
        print("Use --list to see available scenarios.", file=sys.stderr)
        return 2

    global STATE
    STATE = MockState(SCENARIOS[name])

    print("[mock] scenario: %s (%s)" % (name, STATE.title))
    print("[mock] no SSH, no SCP, no HU files will be touched")

    tmpdir = Path(tempfile.mkdtemp(prefix="hu_patcher_mock_key_"))
    try:
        key = make_mock_key(tmpdir)

        # Replace the real transport and file operations with the mock.
        hp.Ssh = MockSsh
        hp.remote_hash = mock_remote_hash
        hp.remote_readlink = mock_remote_readlink
        hp.remote_status = mock_remote_status
        hp.remote_tz_state = mock_remote_tz_state
        hp.write_patch_state = mock_write_patch_state
        hp.remove_patch_state = mock_remove_patch_state
        hp.upload_patch_files = mock_upload
        hp.run_apply = mock_apply
        hp.set_timezone = mock_set_timezone
        hp.restore_timezone = mock_restore_timezone
        hp.check_tools = lambda: True
        hp.validate_local = lambda verbose=True: True
        hp.prepare_bootstrap_key = lambda _args: (key, Path(str(key) + ".pub"))
        hp.time.sleep = lambda _seconds: None
        builtins.input = mock_input

        # The real patcher main(); all stages and prompts are reused.
        sys.argv = [sys.argv[0], "mock-hu", "--key", str(key),
                    "--color", args.color]
        rc = hp.main()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("[mock] final state:")
    print("  root      : %s" % ("yes" if STATE.root else "no"))
    def state_summary(key):
        values = list(STATE.patches.get(key, {}).values())
        if not values:
            return "?"
        if len(set(values)) == 1:
            return values[0]
        return "mixed"

    if "gps" in STATE.patches:
        print("  A (GPS)   : %s" % state_summary("gps"))
    if "touch" in STATE.patches:
        print("  B (touch) : %s" % state_summary("touch"))
    for patch in hp.PATCHES:
        if patch["key"] not in ("gps", "touch"):
            print("  %-9s : %s" % (patch["id"], state_summary(patch["key"])))
    print("  localtime : %s" % STATE.localtime)
    print("  timezone  : %s" % STATE.timezone)
    if STATE.no_answers:
        print("[mock] WARNING: %d prompt(s) had no scripted answer" % STATE.no_answers)
    print("[mock] ssh calls: %d" % STATE.ssh_calls)
    print("[mock] exit: %d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())

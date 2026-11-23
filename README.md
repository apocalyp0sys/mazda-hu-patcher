# HU patcher (Mazda CMU KBST-41-47-M5 / DS04L)

Universal interactive patcher for your own Mazda head unit. Runs on
Windows / Linux / macOS: Python 3.8+ and the system OpenSSH (`ssh`, `scp`,
`ssh-keygen`).

## Repository layout

This Git repository contains the patcher source and the scripts that build the
patch payloads. The generated `.orig`/`.patched` binaries are **not** committed
(`.gitignore`), because they are reproduced from the official `update.bin`.

- Put the official firmware image into `firmware/update.bin`
  (the folder contains an empty `PLACE_update.bin_HERE` placeholder);
- build the payloads with `patches/build.sh`;
- or download a ready release zip, which already contains the binaries.

Nothing else needs to be downloaded or unpacked.

The reproducible build/test environment is the bundled Dockerfile:

```sh
docker build -t mazda-tools .
```

## What it does

1. Connects to the HU over SSH and checks **root** access with a key.
2. If root is unavailable, offers bootstrap via the `media` account using the
   bundled bootstrap payload (`bootstrap/v2`), asks for a reboot and waits for
   root.
3. Shows the status of all discovered patches, for example:
   - **A) GPS/timezone** — `/svp/lib/libsvp_basic.so`;
   - **B) touch while driving** — `/svp/bin/svp.svc.engineering`;
   - **C) Callfix (loudness)** — re-asserts loudness OFF after a phone call;
   - **D) Fast Login** — masks `lightmediascanner.service` for root.
4. Shows a checkbox menu:
   - `[ ]` / `[x]` — not selected / selected; `[-]` — conflict;
   - numbers toggle a patch, `a` selects all, `n` clears, Enter applies,
     `q` quits;
   - `Not installed` → install; `Installed` → remove; `Outdated` →
     update (uninstall old + install new); `Partial` → asks
     whether to install the missing files or roll back the installed ones;
   - `Conflict (unknown file version)` is not selectable and is skipped.
5. For A, during installation, asks for a UTC offset or an IANA zone and
   configures `/etc/localtime` + `/etc/timezone` (with a backup).
6. Applies all selected patches, then offers to reboot the HU and waits for
   root to come back.

## Running

From the patcher root:

```sh
python3 hu_patcher.py 192.168.50.1
# or the wrappers:
hu-patcher.cmd 192.168.50.1      # Windows
sh hu-patcher.sh 192.168.50.1    # Unix
```

If the host is omitted, the patcher asks for the HU IP/hostname
interactively:

```sh
python3 hu_patcher.py
# HU IP/hostname: 192.168.50.1
```

The patch selector uses checkboxes:

```text
Select patches (numbers toggle, a=all, n=none, Enter=apply, q=quit):
  [ ] 1) A) GPS/timezone (libsvp_basic.so)     Not installed
        install
  [ ] 2) B) touch while driving (svp.svc.engineering) Not installed
        install
  [ ] 3) C) Callfix (loudness)                 Not installed
        install
  [ ] 4) D) Fast Login                         Not installed
        install
Toggle: 1 3
...
Toggle:
Selected actions:
  [x] A) GPS/timezone (libsvp_basic.so): install
  [x] C) Callfix (loudness): install
```

`Installed` rows show `remove`, `Outdated` rows show `update`, `Partial` rows
ask whether to install the missing files or roll back the installed ones, and
`Conflict` rows are `[-]` and cannot be selected.

Useful options:

```sh
python3 hu_patcher.py 192.168.50.1 --key ~/.ssh/hu_patcher_ed25519
python3 hu_patcher.py 192.168.50.1 --port 22
python3 hu_patcher.py 192.168.50.1 --color never
python3 hu_patcher.py --self-check
python3 hu_patcher.py 192.168.50.1 --no-pause
python3 hu_patcher.py 192.168.50.1 --log my-run.log
python3 hu_patcher.py 192.168.50.1 --no-log
```

In an interactive terminal the patcher waits for input before exiting, so the
console window does not close before you can read the output. On Windows any
key continues; on Unix press Enter. Use `--no-pause` or
`HU_PATCHER_NO_PAUSE=1` for automation.

`--color` controls status highlighting:

| Value | Behavior |
|---|---|
| `auto` (default) | color only in a terminal (TTY); no color when output is redirected |
| `always` | always use ANSI color |
| `never` | no color |

`NO_COLOR=1` disables color; `FORCE_COLOR=1` enables it in `auto` mode. On
Windows 10+ ANSI is enabled automatically.

### SSH performance

On units without **Fast Login**, every successful SSH session costs about 25 s
(`pam_systemd`/`user@0`). The patcher therefore batches remote work:

- all patch statuses are read in **one** `sh -s` session;
- the status snapshot is reused by the patch loop (no second query per patch);
- the timezone state is cached;
- uploaded files are hash-verified in one session;
- no `mkdir` session is used before `scp` (files go to `/tmp`);
- the final reboot check uses one combined `id -u` + `boot_id` request.

Install/rollback still needs one `scp` per patch and one apply session per
file; multi-file patches are applied file by file.

### Windows wrapper

Before launching, `hu-patcher.cmd`:

- looks for Python 3.8+ in `PATH` and the standard installation directories;
- if Python is missing, prints diagnostics and offers to install it:
  - via `winget` (`Python.Python.3.12`, falling back to 3.11);
  - or via the official python.org installer;
- warns if the OpenSSH Client is not found;
- runs `hu_patcher.py` with the Python it found.

Diagnostics without installing anything:

```powershell
hu-patcher.cmd --diagnose
```

`--self-check` does not connect to the HU: it verifies the local files,
hashes and patch bytes, and runs the apply/rollback/timezone scripts against
temporary files.

### Root password for UART

The patcher does **not** change the root password. If you need local recovery
over UART `ttyS0`, use the separate guide:

- [`ROOT-PASSWORD.md`](ROOT-PASSWORD.md) — how to set your own root password,
  restrict it to UART (`PermitRootLogin prohibit-password`), verify it and
  roll it back.

### Keys

The repository does not contain a private key. By default the script tries:

1. `~/.ssh/id_ed25519`, `~/.ssh/id_rsa`, `~/.ssh/id_ecdsa`;
2. `~/.ssh/hu_patcher_ed25519`.

If root access is unavailable, you can enter the path to your own key at the
prompt or start bootstrap. During bootstrap:

- if `--key` is given, its public part is used;
- otherwise `~/.ssh/hu_patcher_ed25519` is created or reused.

On the first run, if no SSH keys are found and `--key` was not given, the
patcher skips the root-key prompt and goes straight to the `media` bootstrap.
It still asks before rebooting the HU.

The private key is never copied anywhere.

## Bootstrap via `media`

This is needed when the root key has not been installed yet. If no local SSH
keys exist on the first run, this path starts automatically (without the
root-key prompt). The patcher uses the bundled payload in `bootstrap/v2`:

1. The `media` login is checked (the password is usually empty — just press
   Enter at the `ssh` prompt).
2. A key is generated or reused, and the bootstrap payload is copied to
   `/tmp/hu_patcher_v2`.
3. `preflight.sh` and `install.sh --key ...` are run as `media`.
4. The staged key is read back from `/svp/unlock/authorized_key.pub` and
   compared with the local public key; the `/home` mount layout is reported.
5. The script offers a reboot and tries `/bin/systemctl reboot` once. The
   `media` account cannot use `svp.util.powerctrl` (no `svp.power` proxy) or
   `svp.util.remote.reboot` (`/dev/mcu_spi` is root-only), so on this firmware
   a manual reboot is the normal path: use the engineering menu or power-cycle
   the HU.
   The patcher verifies that a reboot actually started (boot id), then waits
   for root.

If `media` has a non-empty password:

- with `sshpass` available, the script asks for the password and uses it;
- without `sshpass`, `ssh`/`scp` will prompt for the password interactively.

## Patches

### A) GPS/timezone

- File: `/svp/lib/libsvp_basic.so`
- Original: `d7ae14f24fc631d2e23ab5fc745ff8248660b1b62253b1ec3e48c21d5097367a`
- Patch: `bf26789698fd224ed6e99537d17756568fd48ad1cc854111f9ba9ff4787625c4`
- Change: 4 bytes in `SVPTime::setZoneTime` (`0x4A70`):
  `a9bd7bfd` (`stp ...`) → `17fffe88` (`b setSystemTime`).
  GPS time is now written as UTC, and local time is derived from
  `/etc/localtime`.
- On install: asks for `+3`, `-5`, `0` or `Europe/Moscow`; checks that the
  zone exists in `/usr/share/zoneinfo`; creates the `/etc/localtime` symlink,
  writes `/etc/timezone`; saves the original values in
  `/svp/patcher/backup/`. A numeric offset is mapped to `Etc/GMT`
  (IANA inverts the sign: `+3` → `Etc/GMT-3`).
- Rollback: restores `libsvp_basic.so` and, if a backup exists,
  `/etc/localtime` + `/etc/timezone`.

### B) touch while driving

- File: `/svp/bin/svp.svc.engineering`
- Original: `c46bc285bd20813ca7374b10069cda9f5f76bc7edd53f5b1e759d056087e61ed`
- Patch: `4419a2aa049cbda00b7b1dd0f4a72e6c9a991e70334d6d7546d641c5525131a3`
- Change: 4 bytes at `0x630A8`:
  `a9 18 00 54` (`b.ls`) → `c5 00 00 14` (`b`), making the
  speed-based `disable_touch` branch unreachable.
- No other files are touched.

### C) Callfix (loudness)

- Files:
  - `/svp/bin/svp-callfix` (aarch64 daemon, mode `0755`);
  - `/etc/systemd/system/svp-callfix.service` (mode `0644`);
  - `/etc/systemd/system/multi-user.target.wants/svp-callfix.service`
    (enable symlink).
- Change: subscribes to `v1.svp.source.Source::SourceStatusChanged` and, when a
  phone source becomes inactive, sends the MCU sequence
  `05 7D 61 02 00 01` → `05 7D 61 02 00 00` (twice). This re-asserts
  loudness OFF after a BT/phone call.
- Why: the MCU/DSP re-applies the loudness profile after a call, which makes the
  rear speakers sound worse. No firmware binary is patched.
- Install: the files are created by the manifest, then the hook runs
  `systemctl daemon-reload` and `systemctl restart svp-callfix.service`.
- Rollback: the hook stops the service, the files are removed and systemd is
  reloaded.
- The patch uses the systemd mode only; it does not modify
  `/svp/etc/svp.startup.sh`.
- If a manual `install-callfix.sh` installation is present, remove it first;
  mismatched files are reported as a conflict.

### D) Fast Login

- File: `/home/root/.config/systemd/user/lightmediascanner.service`
  (symlink → `/dev/null`).
- Change: masks the global user unit `lightmediascanner.service` for `root`
  only. No global unit or service file is modified.
- Why: `lightmediascannerd` cannot open
  `/usr/lib64/lightmediascanner/plugins`, so `user@0.service` waits for its
  startup timeout and every root SSH/SCP session spends ~25 s in
  `pam_systemd`.
- Rollback: removes the symlink. Parent directories are left in place.
- Takes effect on the next root login, or after a reboot.

## Payload files and rebuild

Every patch is a self-contained directory with a `patch.json` manifest.
`hu_patcher.py` discovers all `patches/*/patch.json` files at startup, so
adding or removing a patch does not require editing the main script:

```text
patches/
├── A-gps-timezone/
│   ├── patch.json
│   ├── hook.py
│   ├── verify.sh
│   ├── patch_gps_timezone.py
│   ├── libsvp_basic.so.orig
│   └── libsvp_basic.so.patched
├── B-touch-while-driving/
│   ├── patch.json
│   ├── patch_engineering_touch.py
│   ├── svp.svc.engineering.orig
│   └── svp.svc.engineering.patched
├── C-callfix/
│   ├── patch.json
│   ├── hook.py
│   ├── README.md
│   ├── verify.sh
│   ├── svp-callfix
│   └── svp-callfix.service
└── D-fast-login/
    ├── patch.json
    ├── hook.py
    ├── README.md
    └── verify.sh
```

Manifests drive the status table, install/rollback flow, local validation and
`--self-check`. A manifest can contain multiple `files[]` entries with
`op: "replace"`, `op: "create"` or `op: "symlink"`. A `create` entry can set
`create_dirs: true` to create missing parent directories; a `symlink` entry
uses the same flag for its parent directories. A patch can
optionally provide `hook.py` for extra behavior (the timezone logic is
implemented this way) and `verify.sh` for patch-specific tests.

Payloads are reproduced from the official `update.bin` (fs4 `/svp`, offset
`0x4947FA37`) by `patches/build.sh`. See
[`patches/README.md`](patches/README.md) for the manifest format and the
"Adding a patch" guide.

Put the firmware image into `firmware/update.bin` and run:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh patches/build.sh
# PATCHES_BUILD: OK
```

## Release build

`build-release.sh` rebuilds the payloads, validates them and creates a zip
ready for GitHub Releases:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh build-release.sh
# dist/hu-patcher-v0.2.0.zip
```

The version comes from the `VERSION` file (`v0.2.0`), then from `git describe`
as a fallback. Override it with `VERSION`, for example:

```sh
docker run --rm -e VERSION=v1.0.0 -v "$PWD:/work" -w /work mazda-tools \
    sh build-release.sh
# dist/hu-patcher-v1.0.0.zip
```

The zip contains the patcher, the bootstrap payload and the generated patch
binaries. It does **not** contain `update.bin` and does not require it at
runtime. Upload the resulting `dist/hu-patcher-<version>.zip` to a GitHub
Release.

When preparing a release, update both `VERSION` and
[`CHANGELOG.md`](CHANGELOG.md).

`build-release.sh` validates the release metadata before packaging:

- `[Unreleased]` in `CHANGELOG.md` must be empty;
- `VERSION` must match the newest released changelog entry.

For development builds that intentionally skip these checks:

```sh
docker run --rm -e ALLOW_UNRELEASED=1 -e ALLOW_VERSION_MISMATCH=1 \
    -v "$PWD:/work" -w /work mazda-tools sh build-release.sh
```

The repository ignores `firmware/update.bin`, the generated patch binaries,
`dist/` and zip archives. Only source files and scripts are committed.

### Windows build scripts

`run.cmd`/`run.ps1` is a single Docker dispatcher for the existing `.sh`
scripts, so no Unix tools are needed on Windows. Docker Desktop must be
running; the image is built automatically on first use.

```powershell
# payloads only
.\run.cmd build

# release zip (VERSION file is used by default)
.\run.cmd build-release
.\run.cmd build-release -Version v1.0.0
.\run.cmd build-release -UpdateBin ..\update.bin

# tests
.\run.cmd validate
.\run.cmd mock
.\run.cmd bootstrap
.\run.cmd build-patches
```

Common options: `-UpdateBin <path>`, `-OutDir <dir>` (for `build`),
`-Version <tag>` (for `build-release`), `-Image <tag>` and `-BuildImage`.
For development releases, `build-release` also accepts `-AllowUnreleased`
and `-AllowVersionMismatch`.

## Operation log

By default the patcher writes a detailed log to:

```text
logs/hu_patcher-YYYYMMDD-HHMMSS.log
```

If `logs/` cannot be created, it falls back to the system temp directory. The
log is also written when `--log FILE` is given explicitly. `logs/` is ignored
by Git.

The log contains:

- patcher version, OS, Python version, target/port, key path, options;
- every SSH/SCP command, its duration, exit code and full output;
- remote scripts sent over `sh -s`;
- status snapshot, patch versions, installed state;
- every install/rollback/update decision and per-file result;
- reboot/boot-id waits and errors.

Disable it with:

```sh
python3 hu_patcher.py <HU> --no-log
# or
HU_PATCHER_NO_LOG=1 python3 hu_patcher.py <HU>
```

`--self-check` does not create the automatic log; combine it with
`--log FILE` if you want one. Passwords and private key contents are never
written. The log may contain hostnames, key paths and command output; review it
before sharing publicly.

## Statuses

| Status | Meaning |
|---|---|
| `Installed` | every file matches the known patched state and the installed version is current |
| `Not installed` | every file matches the known original state (or is absent for `create`/`symlink`) |
| `Outdated (update available)` | an older patch version is installed and its files match the saved old state |
| `Conflict (unknown file version)` | at least one file is unknown, or a newer version is installed; the script **changes nothing** |
| `Partial (mixed file states)` | some files are installed and some are not |

Statuses are highlighted in the terminal: `Installed` is green,
`Not installed` is yellow, `Outdated` is magenta,
`Conflict (unknown file version)` is red, `Partial (mixed file states)` is
cyan.

For a multi-file patch, the status is aggregated across all `files[]`.
`Partial` offers to complete the installation or roll back the installed
files. A conflict always takes precedence and blocks all changes.

## Patch versions

Every `patch.json` has a `version` field (`1`, `1.2` or `1.2.3`). When a patch
is installed, the patcher writes its version and file hashes to:

```text
/svp/patcher/state/<patch-key>.json
```

On the next run the installed version is compared with the manifest:

- equal → `Installed`;
- installed version older → `Outdated (update available)`;
- installed version newer or unparsable → `Conflict (unknown file version)`;
- no state file → the file hashes decide. A matching current install is
  treated as `Installed` (legacy state unknown); an unknown file is a
  `Conflict`.

Selecting an `Outdated` patch runs **update**:

1. verify every installed file still matches the saved old version;
2. remove the old version (`replace` restores the firmware original,
   `create` removes the created file, `symlink` removes the link);
3. install the new version and write the new state.

If the old files do not match the saved state, the update is refused and
nothing is removed. An update is not atomic: if the new install fails after
the old version was removed, the patch is left `Not installed` — run the
patcher again to install it. An OTA may remove `/svp` and therefore the state
file; reinstall the patch to recreate it.

## Safety / brick and soft-lock protection

- Before connecting, the hashes of the **local** files and the bytes at the
  expected offsets are verified; on a mismatch the script does not connect to
  the HU.
- On the HU, the hash of the target file is checked: an unknown version means
  `conflict`, with no writes at all.
- Before replacing a file, a verified backup is created in
  `/svp/patcher/backup/`.
- The new file is uploaded to `/tmp`, verified by sha256, and then replaced
  via `temp + mv` (atomically; a running process keeps the old inode).
- After replacement the hash is checked again; on failure the backup is
  restored.
- No services are stopped, and no images or partitions are rewritten. The
  only writes outside the target files are `/etc/localtime` and
  `/etc/timezone` for patch A (with a backup), `/svp/patcher/backup/` and the
  patch version state under `/svp/patcher/state/`.
  Bootstrap (if root is not available yet) additionally writes
  `/svp/bin/sysctl`, its payload files under `/svp` and the root key in root's
  home (`/home/root`, or `/mnt/userdata/home/root` when `/home` is a bind
  mount) — that is the fail-safe bundled bootstrap method.
- The bootstrap does not modify existing scripts and is fail-safe (a shim
  error does not break boot).
- Reboots only happen after confirmation.
- If root does not come back after a reboot, the script reports it and offers
  to wait longer; by that point all data has already been fully written to
  disk.

## What has been validated in the container

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    python3 hu_patcher.py --self-check
```

`SELF-CHECK: OK` verifies:

- every manifest: file hashes and the optional byte-window (`verify`) check;
- remote apply: original → patched, backup created and verified;
- repeated apply is idempotent;
- rollback: patched → original, idempotent;
- unknown version → `PATCHER_CONFLICT`, file unchanged;
- corrupt uploaded file → `PATCHER_ERROR`, file unchanged;
- create: new file created, idempotent, rollback removes it, unknown
  content → conflict;
- timezone install/restore against temporary `/etc` files;
- script transport: the script sent to `sh -s` stays LF-only on Windows.

All of it in one command:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/validate.sh
# VALIDATION: OK
```

It additionally runs each optional `patches/*/verify.sh` (for example the
GPS `setZoneTime -> setSystemTime` disassembly check) and verifies that every
build tool reproduces its payload files.

To verify a full rebuild from `update.bin` (may take a while: it reads
1 GiB of fs4):

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/build-patches.sh
# PATCHES_REBUILD: OK
```

To test the bundled root bootstrap payload (needs `--privileged` for the
tmpfs mounts; use a fresh container):

```sh
docker run --rm --privileged -v "$PWD:/work" -w /work mazda-tools \
    sh test/bootstrap.sh
# BOOTSTRAP_TEST: OK
```

## Mock mode (no HU)

`mock_hu_patcher.py` mirrors the interface and sequence of the real
`hu_patcher.main()`, but **does not connect to the HU**:

- root/media login, patch statuses, apply/rollback, timezone and reboot are
  emulated in memory;
- no real SSH/SCP commands are executed;
- no real files on the HU are changed;
- `[mock]` lines are added to the output.

Running:

```powershell
python mock_hu_patcher.py --list
python mock_hu_patcher.py --scenario no-root
python mock_hu_patcher.py --scenario root-one
python mock_hu_patcher.py --scenario conflict
python mock_hu_patcher.py --scenario manual-reboot
python mock_hu_patcher.py --scenario outdated
python mock_hu_patcher.py --scenario newer
```

Scenarios:

| Scenario | Initial state | What it does | Result |
|---|---|---|---|
| `no-root` | no root, no patches installed | bootstrap, select all, timezone `+3`, reboot | root present, A-D installed |
| `root-one` | root present, A installed, B-D not installed | select all (A remove, B-D install), reboot | A rolled back, B-D installed |
| `conflict` | no root, B has an unknown version | bootstrap, select A/C/D, reboot | root present, B conflict |
| `manual-reboot` | no root, media cannot reboot | bootstrap, systemd reboot denied, manual reboot, select all | root present, A-D installed |
| `outdated` | C installed with version 0.9.0 | select C update, reboot | C updated to 1.0.0 |
| `newer` | C installed with version 9.9.9 | menu shows conflict, quit | no changes |

Aliases: `no-root-no-patches`, `root-one-patch`, `no-root-conflict`, `manual`.

Status colors can be forced on or off with
`--color always` / `--color never` (default: `auto`).

Run all scenarios and verify the final state:

```sh
sh test/mock-scenarios.sh
# MOCK_SCENARIOS: OK
```

Mock mode is only for UI/scenario testing; on a real HU use the normal
`hu_patcher.py`/`hu-patcher.cmd`.

> If output characters look garbled:
> in `cmd` use `set PYTHONUTF8=1`; in PowerShell use
> `$env:PYTHONUTF8="1"`; in Git Bash use
> `export PYTHONIOENCODING=utf-8`.

## Boot speed diagnostics

If the HU takes a long time to come back after a patcher reboot (for example a
black screen for several minutes), see [`BOOT-SPEED.md`](BOOT-SPEED.md). It
explains how to separate systemd shutdown time from boot time, which firmware
timeouts are involved, and how to check whether Fast Login has taken effect.

## Limitations

- `/svp` must be mounted `rw` and `exec`; for bootstrap, `media` must have
  access to `/svp`.
- If the root key is protected by a passphrase, use `ssh-agent` (the script
  invokes `ssh` with `BatchMode=yes` for root). The patcher derives the public
  key from the private key, so a stale `.pub` file cannot install the wrong
  key.
- `libsvp_basic.so` and `svp.svc.engineering` must be **known** versions;
  otherwise the script reports a conflict.
- Patch A changes the time model: after installation, local time depends on
  `/etc/localtime`. Check `date` after rebooting.
- An OTA overwrites `/svp` and rootfs: patches and timezone are reset, and
  the script must be run again.
- Patch B takes effect after `svp.svc.engineering` is restarted or the HU is
  rebooted. The script does not kill the service, to avoid interfering with
  the UI.
- `/svp` with `0777` and `media` with an empty password are a fundamental
  risk; after provisioning, consider closing `media`/SSH.

## License

MIT. See [`LICENSE`](LICENSE).

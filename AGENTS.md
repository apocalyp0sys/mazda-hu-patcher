# AGENTS.md

Guidance for coding agents working on this repository.

## What this project is

`hu-patcher` is a standalone, cross-platform patcher for the Mazda CMU
head unit (KBST-41-47-M5 / DS04L). It connects over SSH, optionally
bootstraps root via the `media` account, and installs or rolls back the
patches listed in `patches/*/patch.json`.

The repository is self-contained. Do not reference directories outside
`patcher/` in source, scripts, or documentation.

## Repository layout

```text
hu_patcher.py              main interactive patcher
mock_hu_patcher.py         offline UI/scenario mock (no HU, no SSH)
patch_loader.py            discovers/validates patches/*/patch.json
package_release.py         builds dist/hu-patcher-<version>.zip
build-release.sh           rebuild payloads + validate + package
run.ps1 / run.cmd          Windows Docker dispatcher for all .sh scripts
VERSION                    current release version (v0.2.0)
CHANGELOG.md               release history
BOOT-SPEED.md              shutdown/boot timing diagnostics
Dockerfile                 reproducible build/test image
patches/
  build.sh                 update.bin -> all payloads
  build_patches.py         manifest-driven payload builder
  extract_update.py        extract fs4 from update.bin
  <patch>/
    patch.json             patch manifest
    hook.py                optional per-patch behavior
    verify.sh              optional patch-specific test
    <tool>.py              build tool: <original> --out <patched>
    *.orig / *.patched     generated binaries (ignored by git)
bootstrap/v2/              bundled fail-safe root bootstrap payload
firmware/                  user puts update.bin here (ignored)
test/                      validation and scenario scripts
```

## Build and test environment

Use the bundled Dockerfile; do not rely on host tools:

```sh
docker build -t mazda-tools .
```

The image contains Python 3, `debugfs`, `git`, `gawk`, `diffutils` and
aarch64 binutils.

Run commands from the repository root.

## Required checks before finishing

Run all of these when changing code, manifests, payloads or build scripts:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    python3 hu_patcher.py --self-check
# SELF-CHECK: OK

docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/validate.sh
# VALIDATION: OK

docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/mock-scenarios.sh
# MOCK_SCENARIOS: OK
```

When changing `bootstrap/v2`, also run:

```sh
docker run --rm --privileged -v "$PWD:/work" -w /work mazda-tools \
    sh test/bootstrap.sh
# BOOTSTRAP_TEST: OK
```

If `firmware/update.bin` is available, also run:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/build-patches.sh
# PATCHES_REBUILD: OK
```

For release changes, verify the packaged archive itself:

```sh
docker run --rm -e VERSION=test -v "$PWD:/work" -w /work mazda-tools \
    sh build-release.sh
# extract dist/hu-patcher-test.zip and run:
# python3 hu_patcher.py --self-check
```

## Patch architecture

- `hu_patcher.py` has no hardcoded patch list. It calls
  `patch_loader.load_patches()`.
- A patch is a directory under `patches/` with `patch.json`.
- `order` controls display order; `enabled: false` disables without deleting.
- `version` is required in spirit (`1`, `1.2` or `1.2.3`; default `1.0.0`).
  Bump it whenever the patch payload or installed files change.
- `files[]` is the list of operations. Each entry has `op: "replace"`
  (default), `op: "create"` or `op: "symlink"`.
- `replace` requires `original`, `patched`, `sha256.original` and
  `sha256.patched`.
- `create` requires `patched`, `sha256.patched`, an optional octal `mode`,
  and optional `create_dirs: true`; the target must be absent (unknown
  content is a conflict). Missing parent directories are an error unless
  `create_dirs` is enabled. Rollback removes the file, not the directories.
- A `create` payload may be a prebuilt executable (for example the Callfix
  daemon). Mark binary files as `binary` in `.gitattributes` so Git does not
  normalize them.
- `symlink` requires an absolute `link` target and optional
  `create_dirs: true`; it has no payload and no `sha256`. The target must be
  absent or already the same symlink (anything else is a conflict). Rollback
  removes the symlink only if it still points at the expected target.
- `verify` is an optional single byte-window check on `replace`.
- `build` is per file and optional: `fs`, optional `source`, and a tool that
  accepts `<original> --out <patched>` or `--out <patched>`.
- `hook` is optional; `hu_patcher.py` calls only hook functions that exist.
- Duplicate `id`, `key` or `target` values are rejected by the loader.
- Manifest paths must stay inside the patch directory.
- Statuses are aggregated: all patched -> `Installed`, all original/absent ->
  `Not installed`, any unknown -> `Conflict`, mixed -> `Partial`.
- Successful installs write `/svp/patcher/state/<key>.json` with the patch
  version and installed hashes; rollback removes it. Older state + matching
  old files -> `Outdated`; newer/unknown version -> `Conflict`. Updates must
  verify the old files against the saved state before removing anything.
- Multi-file apply is sequential. If a file fails, files already changed in
  that attempt are undone; created files are removed on rollback.

### Hook API

Hooks receive a stable `api` object; do not import `hu_patcher` from a hook.

Available functions:

- `describe(api, ssh, patch)` -> list of extra status lines;
- `pre_install(api, ssh, patch)` -> dict state, None, or False to abort;
- `post_install(api, ssh, patch, state)` -> False on failure;
- `pre_rollback(api, ssh, patch)`;
- `post_rollback(api, ssh, patch, state)`;
- `self_check(api)` -> False on failure.

`api` exposes `remote_tz_state`, `prompt_zone`, `set_timezone`,
`restore_timezone`, `self_test_zone_map` and `run_apply`.

`PatchApi` methods resolve module-level functions at call time. Keep this
property: `mock_hu_patcher.py` monkeypatches `hu_patcher` functions.

### Adding a patch

1. Create `patches/C-name/`.
2. Add `patch.json` with unique identifiers and hashes.
3. Add a build tool with the `<original> --out <patched>` interface.
4. Optionally add `hook.py` and `verify.sh`.
5. Run `sh patches/build.sh` (with `firmware/update.bin`).

Do not edit `hu_patcher.py`, `patches/build.sh`,
`patches/build_patches.py` or `package_release.py` just to add a normal
manifest-driven patch. If the new patch has prompts, update
`mock_hu_patcher.py` scenario answers as well.

## Generated files and git hygiene

Never commit:

- `firmware/update.bin` or other firmware images;
- `patches/*/*.orig` / `patches/*/*.patched`;
- `dist/` or release zips;
- `__pycache__/`, `.pyc`;
- private keys (`id_ed25519`, `id_rsa`, `id_ecdsa`, `*.pem`).

No SSH key is bundled. `bootstrap/v2/install.sh` requires `--key FILE`;
`hu_patcher.py` always passes its own generated/selected public key.

Keep scripts LF. `.cmd` files are checked out as CRLF; `.gitattributes`
handles this.

## Safety and behavior rules

- Do not stop services or rewrite images/partitions.
- Patch writes are limited to manifest target files, the optional timezone
  files (`/etc/localtime`, `/etc/timezone`), `/svp/patcher/backup/` and the
  patch version state under `/svp/patcher/state/`.
- Remote replacement must stay atomic (`temp + mv`) with pre/post hash
  verification and a verified backup.
- An unknown file version must produce `Conflict (unknown file version)` and
  no writes.
- If no local SSH keys exist, the patcher goes directly to the `media`
  bootstrap without the root-key prompt; keep the reboot confirmation.
- For a media reboot, try `/bin/systemctl reboot` once, then ask the user to
  use the engineering menu or power-cycle. Do not use
  `svp.util.powerctrl --reboot` (no `svp.power` proxy) or
  `/svp/bin/svp.util.remote.reboot` (it cannot open `/dev/mcu_spi`) from
  media. Verify the reboot actually started via the boot id.
- An SSH timeout is not a wrong key: `root_probe()` distinguishes
  `ok`/`timeout`/`denied`. On `timeout`, ask before bootstrapping; never
  fall through to the media bootstrap silently.
- Prefer one batched `sh -s` session over one `ssh` call per file. On units
  without Fast Login every successful SSH session costs ~25 s. Keep the status
  snapshot, timezone cache and batched hash verification intact.
- Derive the bootstrap public key from the private key and verify the staged
  `/svp/unlock/authorized_key.pub` before rebooting.
- `bootstrap/v2/boot.sh` resolves root's home from `/etc/passwd` and writes
  the key only to a mounted filesystem; it exits 75 when the home is not
  mounted yet. Keep the `/home` and `/mnt/userdata/home` layouts working.
- Keep the interactive pause before exit unless `--no-pause` or
  `HU_PATCHER_NO_PAUSE=1` is set; tests and mock mode must not block.
- Keep user-facing messages and status strings in English.
- Keep the operation log detailed: SSH/SCP commands and outputs, patch state,
  updates and reboot waits. Never write passwords or private key contents.
  Tests and mock runs must use `--no-log` or `HU_PATCHER_NO_LOG=1`.
- Status colors: `Installed` green, `Not installed` yellow, `Conflict` red,
  `Partial` cyan.
- Bootstrap must remain fail-safe and must not modify boot/kernel files.
- `/svp/unlock`, `operator-unlock` and `mazda-unlock` inside
  `bootstrap/v2/*.sh` are on-device runtime paths/identifiers, not repository
  directory references. Do not rename them without a device-compatibility
  review.

## Style

- Python 3.8+, standard library only.
- Shell scripts must be POSIX `sh` compatible (`sh`, not `bash`).
- Prefer small, explicit changes; preserve existing public CLI behavior.
- Update `README.md` / `patches/README.md` when behavior or layout changes.
- Keep the patcher self-contained under this repository.

## Releases

The version is stored in `VERSION`; `build-release.sh` uses it by default.
`VERSION` environment variable overrides it.

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh build-release.sh
```

Output: `dist/hu-patcher-<version>.zip`. It contains the patcher, bootstrap,
patch manifests/tools/hooks and generated binaries. It does not contain
`update.bin`.

Before publishing:

- update `VERSION`;
- add an entry to `CHANGELOG.md`;
- ensure `git status` is clean;
- extract the zip and verify `python3 hu_patcher.py --self-check`.

`build-release.sh` enforces release metadata:

- `[Unreleased]` must be empty;
- `VERSION` must match the newest changelog version.

Use `ALLOW_UNRELEASED=1` / `ALLOW_VERSION_MISMATCH=1` only for development
builds, never for a real release. Any user-visible change must be reflected
in `CHANGELOG.md`.

## Useful commands

```sh
# validate manifests, payloads and hooks
python3 hu_patcher.py --self-check

# run the patcher; the host is prompted if omitted
python3 hu_patcher.py

# rebuild only
sh patches/build.sh [UPDATE_BIN] [OUT_DIR]

# package without rebuilding, if payloads already exist
python3 package_release.py --version dev --out-dir dist

# single patch tool check
python3 patches/A-gps-timezone/patch_gps_timezone.py \
    patches/A-gps-timezone/libsvp_basic.so.orig
```

On Windows, use the Docker dispatcher (Docker Desktop required):

```powershell
.\run.cmd build
.\run.cmd build-release -Version v1.0.0
.\run.cmd build-release -UpdateBin ..\update.bin -Version v1.0.0
.\run.cmd validate
.\run.cmd mock
.\run.cmd bootstrap
.\run.cmd build-patches
```

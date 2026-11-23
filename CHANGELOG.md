# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses semantic versioning (`MAJOR.MINOR.PATCH`).

## [Unreleased]

### Added

- Detailed operation log. By default the patcher writes
  `logs/hu_patcher-YYYYMMDD-HHMMSS.log` with the patcher environment, every
  SSH/SCP command and output, patch status/versions, install/rollback/update
  decisions and reboot waits. Use `--log FILE`, `--no-log` or
  `HU_PATCHER_NO_LOG=1` to control it. Passwords and private key contents are
  never logged.

## [v0.2.0] - 2026-11-23

### Added

- Patch versioning. Each manifest has a `version`; successful installs save
  the version and installed hashes to `/svp/patcher/state/<key>.json`.
  An older installed version is reported as `Outdated (update available)` and
  can be updated (uninstall old + install new). A newer/unknown installed
  version is a `Conflict`. Legacy installs without a state file are treated
  as current when their files match the manifest.
- MIT license (`LICENSE`, also included in the release zip).

## [v0.1.1] - 2026-11-20

### Changed

- Patch selection is now a checkbox menu: numbers toggle patches, `a` selects
  all, `n` clears, Enter applies, `q` quits. `Not installed` rows install,
  `Installed` rows remove, `Partial` rows ask whether to install missing files
  or roll back installed files, and `Conflict` rows are not selectable.
- Patch letters were reordered: **C) Callfix (loudness)** is now third and
  **D) Fast Login** is now fourth (previously D/C).

## [v0.1.0] - 2026-11-20

### Added

- New patch **D) Callfix (loudness)**: installs the `svp-callfix` daemon and
  its systemd unit, re-asserting loudness OFF after a phone call. It uses the
  systemd mode from `loudness/install-callfix.sh`; rollback stops the service
  and removes the daemon, unit and enable symlink.
- Prebuilt binary payloads can now live in a patch directory; the Callfix
  daemon is marked as binary in `.gitattributes` so Git does not normalize it.

### Changed

- Reduced the number of SSH sessions. Patch status is read in one batched
  `sh -s` session and reused by the patch loop; the timezone state is cached;
  uploaded files are hash-verified in one session; the staging `mkdir` is
  gone; the final reboot check combines `id -u` and `boot_id`. This matters on
  units without Fast Login, where each successful SSH session costs ~25 s.

## [v0.0.2] - 2026-11-12

### Added

- Automatic release metadata checks in `build-release.sh`: `[Unreleased]`
  must be empty and `VERSION` must match the newest changelog entry.
  Development builds can override these checks.
- `BOOT-SPEED.md`: shutdown/boot timing diagnostics for slow reboots,
  including the systemd stop timeouts, `user@0`/`svp-launcher`, Fast Login
  behavior and the evidence to collect.
- First run with no SSH keys now goes directly to the `media` bootstrap,
  without the root-key prompt.
- The patcher waits for input before exiting in an interactive terminal so
  the output can be read. Use `--no-pause` or `HU_PATCHER_NO_PAUSE=1` for
  automation.
- `test/bootstrap.sh` and `run.cmd bootstrap`: a container test for the
  bundled bootstrap payload (root-home layouts, deferred install, shim).
- The bootstrap resolves root's home from `/etc/passwd` and supports both a
  dedicated `/home` partition and a `/mnt/userdata/home` bind mount. When
  the home is not mounted, `boot.sh` exits 75 and the shim retries later.
- The patcher derives the bootstrap public key from the private key, prints
  its fingerprint and verifies the staged key on the HU before rebooting.
- Bootstrap reports whether `/home` is a separate mount and warns when the
  key was staged in userdata but `/home` is not mounted.
- New patch **C) Fast Login**: masks `lightmediascanner.service` for root
  (`~root/.config/systemd/user/lightmediascanner.service -> /dev/null`),
  removing the ~25 s SSH login delay caused by `user@0.service`.
- Manifest support for `op: "symlink"` with atomic install and verified
  rollback (used by Fast Login).

### Changed

- Media reboot now tries `/bin/systemctl reboot` once, then asks for a manual
  reboot via the engineering menu or power-cycle, and verifies the boot id.
  `svp.util.powerctrl --reboot` was removed: it fails with
  `buildProxy() - svp.power null!` / `wait power proxy failed` on this
  firmware. `svp.util.remote.reboot` cannot open `/dev/mcu_spi` as media.
- An SSH timeout is now reported as “not verified” instead of a wrong key.
  The patcher asks whether to retry with a longer timeout and does not fall
  through to the bootstrap silently.
- The initial bootstrap confirmation is skipped when no local SSH keys are
  found; the reboot still requires confirmation.
- `hu_patcher_ed25519` is tried before the generic `id_*` keys, and the
  searched `.ssh` directory is printed when no keys are found.
- Root SSH and SCP use `IdentitiesOnly=yes` so an ssh-agent cannot offer
  unrelated keys.
- SSH timeouts were raised for slow units (`pam_systemd`/`user@0.service`
  can delay every session by ~25 s).

### Fixed

- Scripts sent to the HU over `ssh ... sh -s` were translated to CRLF on
  Windows, which caused `Conflict (unknown file version)` and broken
  apply/rollback/timezone scripts. Scripts are now sent as UTF-8 bytes.
- A stale `hu_patcher_ed25519.pub` could install a key that does not match
  the local private key; the `.pub` file is now derived and checked.

## [v0.0.1] - 2026-11-10

### Added

- Manifest-driven patch discovery from `patches/*/patch.json`.
- Multi-file patches with `files[]` and per-file `replace` / `create`
  operations.
- `create` patches with optional `create_dirs` for missing parent
  directories.
- `Partial (mixed file states)` status for multi-file patches.
- Automatic per-patch hooks (`hook.py`) with timezone support for the GPS
  patch.
- Generic payload build from `update.bin` via `patches/build.sh` and
  `patches/build_patches.py`.
- Release packaging with `build-release.sh` and `package_release.py`.
- Windows Docker dispatcher `run.cmd` / `run.ps1`.
- Offline mock mode and scenario tests.
- `--self-check` validation for payloads, remote apply/rollback and create
  operations.
- Bundled fail-safe bootstrap payload in `bootstrap/v2`.

### Changed

- Patch statuses are shown as `Installed`, `Not installed`,
  `Conflict (unknown file version)` and `Partial (mixed file states)`.
- Windows build/test scripts now run the existing `.sh` scripts through one
  Docker dispatcher.
- The patcher prompts for the HU host when it is omitted.
- Bootstrap no longer bundles an SSH public key; `install.sh` requires
  `--key`.

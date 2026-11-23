# Root bootstrap payload

`v2/` contains the fail-safe bootstrap payload used by `hu_patcher.py` when
root access is not available yet:

- `install.sh` — installs the sysctl shim and the public key (run as `media`);
- `preflight.sh` — checks that the current firmware is compatible;
- `boot.sh` — root payload executed by the shim on the next boot;
- `sysctl-shim.sh` — the `/svp/bin/sysctl` shim itself;
- `status.sh` / `uninstall.sh` — optional manual status and removal helpers.

`install.sh` requires an explicit `--key FILE`. The patcher always passes its
own public key (`bootstrap_key.pub`); no key is bundled with the repository.

`boot.sh` derives root's home from `/etc/passwd` (normally `/home/root`). It
writes the key only when that home is on a mounted filesystem:

- a dedicated `/home` partition (for example `/dev/mmcblk0p6`), or
- a userdata source `/mnt/userdata/home/root` used while `/home` is not
  mounted yet.

If neither is mounted, `boot.sh` exits with code 75 and does not write
anything; `sysctl-shim.sh` then allows another attempt during the same boot.
The key is installed with mode `600` and a stable `mazda-unlock` comment.

Do not edit these files in place: they are part of the bundled bootstrap
payload. The bootstrap is fail-safe and never changes boot or kernel files.

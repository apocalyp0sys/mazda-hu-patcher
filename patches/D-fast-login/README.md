# Fast Login

Masks the global user unit `lightmediascanner.service` for `root` only:

```text
/home/root/.config/systemd/user/lightmediascanner.service -> /dev/null
```

## Why

On this firmware the root user manager starts
`/etc/systemd/user/default.target.wants/lightmediascanner.service`.
`lightmediascannerd` cannot open `/usr/lib64/lightmediascanner/plugins`, so
`user@0.service` waits for its startup timeout. Every root SSH/SCP session
spends ~25 s in `pam_systemd` before the command runs.

The mask is per-user (`~root/.config/systemd/user/`); other users are not
affected, and no global unit or service file is changed.

## Install and rollback

- Install: create the symlink (and the parent directories if needed).
- Rollback: remove the symlink. The directories are left in place.
- Target takes effect on the next root login, or after a reboot.

## Apply order

`patch.json` sets `"apply_first": true`, so the patcher applies the mask before
other selected patches and removes it after them. Once the symlink exists, the
next root user-manager start skips `lightmediascanner`; the remaining
SSH/SCP-heavy patch operations should be faster.

If the target already exists as a regular file or points somewhere else, the
patcher reports a conflict and changes nothing. The hook also refuses to
install when `/etc/passwd` gives root a home other than `/home/root`.

## Verify

After the next root login:

```sh
systemctl --user status lightmediascanner.service
# Loaded: masked (/dev/null)
time ssh root@<HU> id -u
```

The second command should stop spending ~25 s before returning `0`.

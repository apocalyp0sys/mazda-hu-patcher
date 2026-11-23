# Patcher payloads and patch manifests

Every patch is a self-contained directory under `patches/`. `hu_patcher.py`
discovers all `patches/*/patch.json` files at startup, so adding or removing a
patch does **not** require editing the main script.

The generated `.orig`/`.patched` binaries are not committed to Git; they are
produced by `patches/build.sh` (or shipped in a release zip).

```text
patcher/
├── patch_loader.py                 # manifest discovery and validation
└── patches/
    ├── build.sh                    # update.bin -> all payloads
    ├── build_patches.py            # generic manifest-driven builder
    ├── extract_update.py           # extract fs4 (/svp) from update.bin
    ├── A-gps-timezone/
    │   ├── patch.json              # manifest
    │   ├── hook.py                 # optional behavior
    │   ├── verify.sh               # optional patch-specific test
    │   ├── patch_gps_timezone.py   # build tool
    │   ├── libsvp_basic.so.orig    # generated
    │   └── libsvp_basic.so.patched # generated
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

## Manifest format

`patches/<name>/patch.json` uses `files[]`. Each entry is one operation:
`replace` an existing file, `create` a new file, or `symlink` a new link.

```json
{
  "schema": 2,
  "id": "A",
  "key": "gps",
  "name": "GPS/timezone (libsvp_basic.so)",
  "version": "1.0.0",
  "order": 10,
  "enabled": true,
  "hook": "hook.py",
  "files": [
    {
      "op": "replace",
      "target": "/svp/lib/libsvp_basic.so",
      "backup_name": "libsvp_basic.so",
      "original": "libsvp_basic.so.orig",
      "patched": "libsvp_basic.so.patched",
      "sha256": {
        "original": "d7ae14f2...7367a",
        "patched": "bf267896...7625c4"
      },
      "verify": {
        "offset": "0x4A70",
        "from": "fd7bbda9",
        "to": "88feff17"
      },
      "build": {
        "fs": "fs4",
        "source": "/lib/libsvp_basic.so",
        "tool": "patch_gps_timezone.py"
      }
    }
  ]
}
```

A new file:

```json
{
  "op": "create",
  "target": "/svp/etc/example/example.conf",
  "patched": "example.conf",
  "sha256": { "patched": "..." },
  "mode": "0644",
  "create_dirs": true
}
```

A new symlink (no payload, no hash):

```json
{
  "op": "symlink",
  "target": "/home/root/.config/systemd/user/lightmediascanner.service",
  "link": "/dev/null",
  "create_dirs": true
}
```

The old single-file form (top-level `target`, `original`, `patched`,
`sha256`, `verify`, `build`) is still accepted and is normalized to a
one-element `files[]` list.

Fields:

| Field | Meaning |
|---|---|
| `id`, `key`, `name` | display and internal identifiers |
| `version` | patch version (`1`, `1.2` or `1.2.3`; default `1.0.0`) |
| `order` | display order (lower first) |
| `apply_first` | if `true`, apply before other selected patches; remove last |
| `enabled` | `false` hides the patch without deleting it |
| `files` | non-empty array of file operations |
| `hook` | optional Python hook |

Per file:

| Field | Meaning |
|---|---|
| `op` | `replace` (default), `create` or `symlink` |
| `target` | absolute path on the head unit |
| `backup_name` | filename used for the on-device backup |
| `original`, `patched` | payload filenames inside the patch directory (`original` only for `replace`) |
| `sha256` | expected hashes (`original` + `patched`, or `patched` only for `create`) |
| `link` | absolute link target for `symlink` |
| `verify` | optional byte-window check for `replace` (`offset`, `from`, `to`) |
| `mode` | optional permissions for `create`, for example `"0644"` |
| `create_dirs` | if `true`, create missing parent directories for `create`/`symlink` (default: `false`) |
| `build` | optional rule for rebuilding from `fs4` |

Paths inside the manifest must be relative and stay inside the patch directory.
Duplicate `id`, `key` or `target` values are rejected.

### Multi-file behavior

- All files in one manifest are presented as one patch.
- `replace` creates a verified backup and replaces the file atomically.
- `create` requires the target to be absent; if it exists with unknown
  content, the patch reports a conflict.
- `symlink` requires the target to be absent or already the same link; a
  regular file or a different link is a conflict. Install is atomic
  (`ln -s` to a temporary name, then `mv`), and rollback removes the link
  only if it still points at the expected target.
- `create_dirs: true` creates missing parent directories. Without it, a
  missing parent directory is an error. Directories are not removed on
  rollback (only the created file or link is removed).
- Rollback restores every replaced file and removes every created file/link.
- If a later file fails, files already changed in this attempt are undone.
- A mix of installed and not-installed files is reported as
  `Partial (mixed file states)`. An unknown file always wins and is reported
  as `Conflict (unknown file version)`; no changes are made in that case.
- For `Partial`, the patcher offers to complete the installation or roll back
  the installed files.

### Patch versions

Each manifest has a `version`. On a successful install the patcher writes the
version and the installed file hashes to
`/svp/patcher/state/<key>.json`; rollback removes that file. On the next run:

- same version → `Installed`;
- older installed version → `Outdated (update available)`;
- newer/unknown installed version → `Conflict (unknown file version)`;
- missing state file → the current file hashes decide (legacy installs).

Selecting an `Outdated` patch performs **update**: verify the old files against
the saved state, remove the old version, install the new one and write the new
state. If the old files no longer match the saved state, the update is refused
before any removal.

Bump `version` whenever a patch payload or its installed files change.

`apply_first: true` makes the patcher apply the patch before the other selected
patches (and remove it after them). Fast Login uses this to enable fast root
logins before the remaining SSH-heavy work.

### Building multi-file patches

Each file can have its own `build` rule. `build.source` is optional:

- with `source`: `debugfs` extracts it and the tool is called as
  `<tool> <original> --out <patched>`;
- without `source`: the tool is called as `<tool> --out <patched>` (useful
  for generated/new files).

Files without a `build` rule must already exist in the patch directory.
For `create`, use a normal filename such as `example.conf` (not
`*.patched`), so Git tracks it. `symlink` entries have no payload file.
Prebuilt executables are allowed as `create` payloads; mark them as `binary`
in `.gitattributes`.

## How discovery works

`hu_patcher.py` calls `patch_loader.load_patches(patches/)`:

- reads every `*/patch.json`;
- skips `"enabled": false`;
- validates the schema, paths, hashes and optional byte window;
- loads `hook.py` if specified;
- sorts by `order`, then by `id`.

The resulting list drives the status table, install/rollback prompts, local
validation and `--self-check`.

## Building payloads

Put the firmware image into `firmware/update.bin` at the patcher root, then
run the build inside the `mazda-tools` Docker image:

```sh
docker build -t mazda-tools .

docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh patches/build.sh
```

On Windows, run the same script through the Docker dispatcher from the
patcher root:

```powershell
.\run.cmd build
```

For every manifest with a `build` section, `build_patches.py`:

1. extracts the source file from `fs4` with `debugfs`;
2. verifies `sha256.original` (when `source` is present);
3. runs `build.tool` as `python3 <tool> <original> --out <patched>`, or
   `python3 <tool> --out <patched>` when there is no source;
4. writes `.orig`/`.patched` next to the manifest;
5. verifies `sha256.patched`.

Custom `update.bin` and output directory:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh patches/build.sh /work/update.bin /tmp/payloads
```

To build the complete release zip (payloads + patcher + bootstrap), use
`build-release.sh` from the patcher root.

## Adding a patch

1. Create `patches/C-my-patch/`.
2. Add `patch.json` with a unique `id`, `key`, and a non-empty `files[]`.
3. Add a build tool for every `build` rule that accepts
   `<original> --out <patched>` or `--out <patched>`.
4. Optional: add `hook.py` for extra behavior.
5. Optional: add `verify.sh` for patch-specific tests.
6. Build:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh patches/build.sh
```

The patcher now lists the new patch automatically. No changes to
`hu_patcher.py`, `patches/build.sh` or `package_release.py` are needed.

To disable a patch temporarily, set `"enabled": false`. To remove it, delete
its directory.

## Hook API

`hook.py` is optional. `hu_patcher.py` calls only the functions that exist:

```python
def describe(api, ssh, patch):
    """Return extra status lines."""
    return ["key: value"]

def pre_install(api, ssh, patch):
    """Return a dict with state, None, or False to abort."""
    return {"zone": api.prompt_zone(ssh)}

def post_install(api, ssh, patch, state):
    """Return False on failure."""
    return True

def pre_rollback(api, ssh, patch):
    return None

def post_rollback(api, ssh, patch, state):
    return True

def self_check(api):
    """Called by --self-check; return False on failure."""
    return True
```

The `api` object exposes:

| Method | Purpose |
|---|---|
| `remote_tz_state(ssh)` | read `/etc/localtime` and `/etc/timezone` |
| `prompt_zone(ssh)` | prompt for a UTC offset or IANA zone |
| `set_timezone(ssh, zone)` | apply a timezone on the HU |
| `restore_timezone(ssh)` | restore the timezone backup |
| `self_test_zone_map()` | validate offset-to-zone mapping |
| `run_apply(...)` | run a remote apply/rollback script |

If a hook function raises an exception, the patcher reports a warning and
treats the step as failed.

## Verify reproducibility

Rebuild from `update.bin` into a temporary directory and compare the result
with the files in `patches/`:

```sh
docker run --rm -v "$PWD:/work" -w /work mazda-tools \
    sh test/build-patches.sh
# PATCHES_REBUILD: OK
```

`test/validate.sh` also runs each optional `patches/*/verify.sh` and checks
that every build tool reproduces its checked-in payload.

## Checking a single file without update.bin

```sh
python3 patches/A-gps-timezone/patch_gps_timezone.py \
    patches/A-gps-timezone/libsvp_basic.so.orig
# [i] state : ORIGINAL ...
# [i] patchable: 0x4A70 fd7bbda9 -> 88feff17
```

`--out <file>` writes a patched copy; `--apply`/`--revert` work in place
(with an `.orig` backup).

## Why the binaries are not committed

`update.bin` is 2.3 GB, so it makes no sense to process it on every patcher
run. The generated `.orig`/`.patched` files allow you to:

- run `--self-check` and the patcher without unpacking the firmware;
- roll back using the known original;
- reproduce any file from `update.bin` with the scripts above.

They are build artifacts: Git ignores them, `build-release.sh` regenerates
them and puts them into the release zip.

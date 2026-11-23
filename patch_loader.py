#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# patch_loader.py -- discover and validate patch manifests.
#
# Two manifest forms are supported:
#
#   schema 2 (multi-file):
#     {
#       "schema": 2, "id": "...", "key": "...", "name": "...",
#       "files": [
#         {"op": "replace", "target": "/svp/lib/a.so", "original": "...",
#          "patched": "...", "sha256": {"original": "...", "patched": "..."}},
#         {"op": "create", "target": "/svp/etc/new.conf",
#          "patched": "new.conf", "sha256": {"patched": "..."}, "mode": "0644"},
#         {"op": "symlink", "target": "/home/root/.config/systemd/user/x.service",
#          "link": "/dev/null", "create_dirs": true}
#       ]
#     }
#
#   legacy single-file:
#     {"target": "...", "original": "...", "patched": "...",
#      "sha256": {"original": "...", "patched": "..."}, ...}
#
# Adding or removing a patch means adding or removing its directory.
# ---------------------------------------------------------------------------
import importlib.util
import json
import re
from pathlib import Path

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MODE_RE = re.compile(r"^[0-7]{3,4}$")
VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")


class PatchConfigError(Exception):
    pass


def _error(patch_dir, message):
    name = patch_dir.name if hasattr(patch_dir, "name") else str(patch_dir)
    raise PatchConfigError("%s: %s" % (name, message))


def _required(data, key, patch_dir):
    value = data.get(key)
    if value is None or value == "":
        _error(patch_dir, "missing required field %r" % key)
    return value


def _safe_path(patch_dir, value, field):
    if not isinstance(value, str) or not value:
        _error(patch_dir, "%s must be a non-empty string" % field)
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        _error(patch_dir, "%s must be a relative path inside the patch directory" % field)
    resolved = (patch_dir / rel).resolve()
    try:
        resolved.relative_to(patch_dir.resolve())
    except ValueError:
        _error(patch_dir, "%s escapes the patch directory" % field)
    return resolved


def _parse_int(patch_dir, value, field):
    if isinstance(value, bool):
        _error(patch_dir, "%s must be an integer or hex string" % field)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            pass
    _error(patch_dir, "%s must be an integer or hex string" % field)


def _parse_hex(patch_dir, value, field):
    if not isinstance(value, str):
        _error(patch_dir, "%s must be a hex string" % field)
    text = "".join(value.split())
    if len(text) % 2:
        _error(patch_dir, "%s has an odd number of hex digits" % field)
    try:
        return bytes.fromhex(text)
    except ValueError:
        _error(patch_dir, "%s is not valid hex" % field)


def _check_sha(value, patch_dir, field):
    value = str(value)
    if not SHA256_RE.match(value):
        _error(patch_dir, "%s must be a 64-character sha256" % field)
    return value.lower()


def _load_hook(patch_dir, hook_path, key):
    mod_name = "hu_patch_hook_%s" % re.sub(r"[^A-Za-z0-9_]", "_", key)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(hook_path))
        if spec is None or spec.loader is None:
            _error(patch_dir, "cannot load hook %s" % hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except PatchConfigError:
        raise
    except Exception as e:
        _error(patch_dir, "cannot import hook %s: %s" % (hook_path.name, e))


def _load_build(patch_dir, raw_build, field):
    if raw_build is None:
        return None
    if not isinstance(raw_build, dict):
        _error(patch_dir, "%s must be an object" % field)
    tool_name = _required(raw_build, "tool", patch_dir)
    tool = _safe_path(patch_dir, tool_name, field + ".tool")
    source = raw_build.get("source")
    return {
        "fs": str(raw_build.get("fs", "fs4")),
        "source": str(source) if source else None,
        "tool": tool,
    }


def _load_file(patch_dir, raw, default_build, index, multi):
    if not isinstance(raw, dict):
        _error(patch_dir, "files[%d] must be an object" % index)

    op = str(raw.get("op", "replace"))
    if op not in ("replace", "create", "symlink"):
        _error(patch_dir,
               "files[%d].op must be 'replace', 'create' or 'symlink'" % index)

    target = str(_required(raw, "target", patch_dir))
    basename = Path(target).name or "target"
    backup_name = raw.get("backup_name")
    if not backup_name:
        backup_name = ("%02d_%s" % (index + 1, basename)) if multi else basename
    backup_name = str(backup_name)
    if not re.match(r"^[A-Za-z0-9._+-]+$", backup_name):
        _error(patch_dir,
               "files[%d].backup_name must be a plain filename (no paths)" % index)

    patched = None
    hash_patched = None
    original = None
    hash_original = None
    link = None

    if op == "symlink":
        for forbidden in ("patched", "original", "sha256", "mode", "verify",
                          "build"):
            if forbidden in raw:
                _error(patch_dir,
                       "files[%d]: op=symlink does not accept %r" %
                       (index, forbidden))
        link = str(_required(raw, "link", patch_dir))
        if not link.startswith("/"):
            _error(patch_dir, "files[%d].link must be an absolute path" % index)
    else:
        patched_name = _required(raw, "patched", patch_dir)
        patched = _safe_path(patch_dir, patched_name, "files[%d].patched" % index)

        hashes = raw.get("sha256")
        if not isinstance(hashes, dict):
            _error(patch_dir, "files[%d].sha256 must be an object" % index)
        hash_patched = _check_sha(hashes.get("patched"), patch_dir,
                                  "files[%d].sha256.patched" % index)

        if op == "replace":
            original_name = _required(raw, "original", patch_dir)
            original = _safe_path(patch_dir, original_name,
                                  "files[%d].original" % index)
            hash_original = _check_sha(hashes.get("original"), patch_dir,
                                       "files[%d].sha256.original" % index)

    verify = None
    raw_verify = raw.get("verify")
    if raw_verify is not None:
        if op != "replace":
            _error(patch_dir, "files[%d].verify is only valid for op=replace" % index)
        if not isinstance(raw_verify, dict):
            _error(patch_dir, "files[%d].verify must be an object" % index)
        offset = _parse_int(patch_dir, _required(raw_verify, "offset", patch_dir),
                            "files[%d].verify.offset" % index)
        orig_bytes = _parse_hex(patch_dir, _required(raw_verify, "from", patch_dir),
                                "files[%d].verify.from" % index)
        patch_bytes = _parse_hex(patch_dir, _required(raw_verify, "to", patch_dir),
                                 "files[%d].verify.to" % index)
        if not orig_bytes or len(orig_bytes) != len(patch_bytes):
            _error(patch_dir,
                   "files[%d].verify.from and verify.to must have the same "
                   "non-zero length" % index)
        verify = {
            "offset": offset,
            "orig_bytes": orig_bytes,
            "patch_bytes": patch_bytes,
        }

    mode = None
    create_dirs = False
    if op in ("create", "symlink"):
        raw_dirs = raw.get("create_dirs", False)
        if not isinstance(raw_dirs, bool):
            _error(patch_dir, "files[%d].create_dirs must be true or false" % index)
        create_dirs = raw_dirs
        if op == "create":
            mode = str(raw.get("mode", "0644"))
            if not MODE_RE.match(mode):
                _error(patch_dir,
                       "files[%d].mode must be octal, for example '0644'" % index)
    elif raw.get("create_dirs"):
        _error(patch_dir, "files[%d].create_dirs is only valid for op=create" % index)

    build = _load_build(patch_dir, raw.get("build") or default_build,
                        "files[%d].build" % index)
    if op in ("create", "symlink") and build and build.get("source"):
        _error(patch_dir, "files[%d].build.source is not valid for op=%s" %
               (index, op))
    if op == "symlink" and build:
        _error(patch_dir, "files[%d].build is not valid for op=symlink" % index)

    return {
        "op": op,
        "target": target,
        "backup_name": backup_name,
        "local_original": original,
        "local_patched": patched,
        "hash_original": hash_original,
        "hash_patched": hash_patched,
        "link": link,
        "verify": verify,
        "mode": mode,
        "create_dirs": create_dirs,
        "build": build,
    }


def _load_manifest(patch_dir, manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError) as e:
        _error(patch_dir, "cannot read patch.json: %s" % e)
    except ValueError as e:
        _error(patch_dir, "invalid patch.json: %s" % e)

    if not isinstance(data, dict):
        _error(patch_dir, "patch.json must contain an object")
    schema = data.get("schema", 1)
    if schema not in (1, 2):
        _error(patch_dir, "unsupported schema version %r" % schema)
    if data.get("enabled", True) is False:
        return None

    pid = str(_required(data, "id", patch_dir))
    key = str(_required(data, "key", patch_dir))
    name = str(_required(data, "name", patch_dir))
    version = str(data.get("version", "1.0.0")).strip()
    if not VERSION_RE.match(version):
        _error(patch_dir,
               "version must look like 1, 1.2 or 1.2.3 (got %r)" % version)
    order = _parse_int(patch_dir, data.get("order", 100), "order")

    default_build = data.get("build")
    if "files" in data:
        raw_files = data.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            _error(patch_dir, "files must be a non-empty array")
        multi = True
    else:
        raw_files = [data]
        multi = False

    files = []
    for index, raw in enumerate(raw_files):
        files.append(_load_file(patch_dir, raw, default_build, index, multi))

    targets = [f["target"] for f in files]
    if len(set(targets)) != len(targets):
        _error(patch_dir, "duplicate target inside files")

    for field, label in (("backup_name", "backup_name"),
                         ("local_patched", "patched file"),
                         ("local_original", "original file")):
        values = [str(f[field]) for f in files if f[field] is not None]
        if len(set(values)) != len(values):
            _error(patch_dir, "duplicate %s inside files" % label)

    hook = None
    hook_path = None
    if data.get("hook"):
        hook_path = _safe_path(patch_dir, data["hook"], "hook")
        if not hook_path.is_file():
            _error(patch_dir, "hook file not found: %s" % hook_path)
        hook = _load_hook(patch_dir, hook_path, key)

    patch = {
        "id": pid,
        "key": key,
        "name": name,
        "version": version,
        "description": str(data.get("description", "")),
        "order": order,
        "files": files,
        "hook_path": hook_path,
        "_hook": hook,
        "_dir": patch_dir,
    }

    # Keep single-file shorthands for old hooks/tests that still read them.
    if len(files) == 1:
        first = files[0]
        patch.update({
            "target": first["target"],
            "backup_name": first["backup_name"],
            "local_original": first["local_original"],
            "local_patched": first["local_patched"],
            "hash_original": first["hash_original"],
            "hash_patched": first["hash_patched"],
        })
    return patch


def load_patches(patch_root):
    """Return enabled patches sorted by (order, id)."""
    root = Path(patch_root)
    if not root.is_dir():
        raise PatchConfigError("patch directory not found: %s" % root)

    patches = []
    for patch_dir in sorted(root.iterdir()):
        if not patch_dir.is_dir():
            continue
        manifest = patch_dir / "patch.json"
        if not manifest.is_file():
            continue
        patch = _load_manifest(patch_dir, manifest)
        if patch is not None:
            patches.append(patch)

    patches.sort(key=lambda p: (p["order"], p["id"]))

    seen_ids = {}
    seen_keys = {}
    seen_targets = {}
    for patch in patches:
        for field, seen, label in (("id", seen_ids, "id"),
                                   ("key", seen_keys, "key")):
            value = patch[field]
            if value in seen:
                raise PatchConfigError(
                    "duplicate patch %s %r (%s and %s)" %
                    (label, value, seen[value], patch["_dir"].name))
            seen[value] = patch["_dir"].name
        for file_spec in patch["files"]:
            target = file_spec["target"]
            if target in seen_targets:
                raise PatchConfigError(
                    "duplicate patch target %r (%s and %s)" %
                    (target, seen_targets[target], patch["_dir"].name))
            seen_targets[target] = patch["_dir"].name
    return patches

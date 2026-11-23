#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# check_release.py -- validate VERSION and CHANGELOG.md before packaging.
#
# Checks:
#   * CHANGELOG.md exists and has a released version with an ISO date;
#   * the newest released version matches VERSION;
#   * version headings are unique;
#   * the [Unreleased] section has no pending entries (unless explicitly
#     allowed for a development build).
#
# Usage:
#   python3 check_release.py --version v0.0.1
#   python3 check_release.py --version v0.0.1 --allow-unreleased
#   python3 check_release.py --version v9.9.9 --allow-version-mismatch
# ---------------------------------------------------------------------------
import argparse
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(
    r"^##\s+\[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def normalize_version(value):
    return value.strip().lstrip("v")


def parse_changelog(path):
    versions = []
    unreleased = []
    in_unreleased = False

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            match = HEADING_RE.match(line.strip())
            if match:
                label, date = match.group(1), match.group(2)
                if label.strip().lower() == "unreleased":
                    in_unreleased = True
                    continue
                in_unreleased = False
                if date and VERSION_RE.match(normalize_version(label)):
                    versions.append((label, date))
                continue

            if in_unreleased:
                text = line.strip()
                if (text and not text.startswith("#")
                        and not text.startswith("<!--")):
                    unreleased.append(text)

    return versions, unreleased


def main():
    ap = argparse.ArgumentParser(description="Validate release metadata")
    ap.add_argument("--root", default=".", help="patcher root")
    ap.add_argument("--version", required=True, help="release version")
    ap.add_argument("--changelog", default=None,
                    help="path to CHANGELOG.md (default: <root>/CHANGELOG.md)")
    ap.add_argument("--allow-unreleased", action="store_true",
                    help="allow pending [Unreleased] entries")
    ap.add_argument("--allow-version-mismatch", action="store_true",
                    help="do not require VERSION to match the newest entry")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    changelog = Path(args.changelog) if args.changelog else root / "CHANGELOG.md"
    version = args.version.strip()

    if not VERSION_RE.match(normalize_version(version)):
        print("ERROR: VERSION must look like vMAJOR.MINOR.PATCH or "
              "MAJOR.MINOR.PATCH, got %r" % version, file=sys.stderr)
        return 1

    if not changelog.is_file():
        print("ERROR: changelog not found: %s" % changelog, file=sys.stderr)
        return 1

    versions, unreleased = parse_changelog(changelog)

    if not versions:
        print("ERROR: no released versions found in %s" % changelog,
              file=sys.stderr)
        return 1

    latest_label, latest_date = versions[0]
    if not args.allow_version_mismatch:
        if normalize_version(latest_label) != normalize_version(version):
            print("ERROR: VERSION %r does not match the newest changelog "
                  "entry %r" % (version, latest_label), file=sys.stderr)
            print("       update VERSION/CHANGELOG, or use "
                  "ALLOW_VERSION_MISMATCH=1 for a development build.",
                  file=sys.stderr)
            return 1
        print("[OK] VERSION %s matches changelog (%s)" %
              (version, latest_date))

    seen = {}
    for label, _date in versions:
        key = normalize_version(label)
        if key in seen:
            print("ERROR: duplicate changelog version %r (%s and %s)" %
                  (label, seen[key], label), file=sys.stderr)
            return 1
        seen[key] = label

    if unreleased and not args.allow_unreleased:
        print("ERROR: [Unreleased] contains pending changelog entries:",
              file=sys.stderr)
        for line in unreleased[:8]:
            print("  " + line, file=sys.stderr)
        print("       move them to a released version, or use "
              "ALLOW_UNRELEASED=1 for a development build.", file=sys.stderr)
        return 1
    if not unreleased:
        print("[OK] CHANGELOG: [Unreleased] is empty")
    else:
        print("[i] CHANGELOG: [Unreleased] pending entries allowed")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (IOError, OSError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)

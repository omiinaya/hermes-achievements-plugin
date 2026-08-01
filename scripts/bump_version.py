#!/usr/bin/env python3
"""Bump the plugin version across all files that carry it.

The version lives in 4 places — pyproject.toml, plugin.yaml, setup.sh,
and this repo's git tag convention. This script updates all of them in
one shot so they can never drift.

Usage:
    python3 scripts/bump_version.py 2.5.0
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
PLUGIN_YAML = os.path.join(ROOT, "plugin.yaml")
SETUP_SH = os.path.join(ROOT, "setup.sh")

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def bump(path, pattern, replacement, count=1):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new_text, n = pattern.subn(replacement, text, count=count)
    if n == 0:
        raise SystemExit(f"ERROR: pattern not found in {path}: {pattern.pattern}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"  {os.path.relpath(path, ROOT)}: updated ({n} replacement(s))")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/bump_version.py 2.5.0")
    version = sys.argv[1]
    if not VERSION_RE.match(version):
        raise SystemExit(f"ERROR: '{version}' is not a valid semver (want X.Y.Z)")

    print(f"Bumping version to {version}")
    # pyproject.toml: version = "x.y.z"
    bump(PYPROJECT, re.compile(r'^version = "\d+\.\d+\.\d+"', re.MULTILINE),
         f'version = "{version}"')
    # plugin.yaml: version: x.y.z
    bump(PLUGIN_YAML, re.compile(r"^version: \d+\.\d+\.\d+$", re.MULTILINE),
         f"version: {version}")
    # setup.sh: two banner lines — vX.Y.Z (both occurrences)
    bump(SETUP_SH, re.compile(r"v\d+\.\d+\.\d+"), f"v{version}", count=0)
    print("Done. Update CHANGELOG.md and run the test suite.")


if __name__ == "__main__":
    sys.exit(main())

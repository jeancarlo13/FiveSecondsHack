#!/usr/bin/env python3
"""Validate that the PR version is a strict semver increment over the base branch
and that the resulting tag does not already exist in the repository.

Usage:
    python validate_version.py <path/to/base/pyproject.toml>

Reads the current version from pyproject.toml in the working directory and
compares it against the base TOML passed as a positional argument.
Exits with a non-zero status and an explanatory message on any validation failure.
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _read_version(path):
    """Parse and return the (major, minor, patch) tuple from a pyproject.toml file."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version", "").strip()
    if not version:
        raise SystemExit(f"Missing [project].version in {path}")
    if not _SEMVER.match(version):
        raise SystemExit(f"Invalid version format '{version}' in {path}. Expected X.Y.Z.")
    return tuple(map(int, version.split(".")))


def main():
    """Entry point: parse arguments, validate version increment, check tag absence."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: validate_version.py <path/to/base/pyproject.toml>")

    base_tuple = _read_version(Path(sys.argv[1]))
    current_tuple = _read_version(Path("pyproject.toml"))

    current_str = ".".join(map(str, current_tuple))
    base_str = ".".join(map(str, base_tuple))

    if current_tuple <= base_tuple:
        raise SystemExit(f"Version must be incremented. Base={base_str}, current={current_str}.")

    result = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{current_str}"],
        capture_output=True,
    )
    if result.returncode == 0:
        raise SystemExit(f"Tag {current_str} already exists. Pick a new version before merge.")

    print(f"Version check passed: {base_str} -> {current_str}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Check PyPI for newer versions of project dependencies.

Parses requirements.txt and requirements-dev.txt, queries the PyPI JSON API for
each package, and writes a Markdown comment body to the path given by the
OUTPUT_FILE environment variable when newer versions are available.

Always exits 0 — never fails the build.
"""

import os
import re
import tempfile
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version

REQ_FILES = ["requirements.txt", "requirements-dev.txt"]
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", str(Path(tempfile.gettempdir()) / "outdated_deps.md"))
PYPI_URL = "https://pypi.org/pypi/{}/json"


def _parse_requirements():
    """Return (filename, package_name, specifier) for every non-comment line."""
    results = []
    for fname in REQ_FILES:
        path = Path(fname)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9_\-\.]+)(.*)$", line)
            if m:
                results.append((fname, m.group(1), m.group(2).strip()))
    return results


def _get_latest_version(package):
    """Return the latest version string for *package* from PyPI, or None on any error."""
    try:
        resp = requests.get(PYPI_URL.format(package), timeout=10)
        resp.raise_for_status()
        return resp.json()["info"]["version"]
    except Exception:  # noqa: BLE001
        return None


def _extract_floor_version(specifier):
    """Return the version number from ==X or >=X specifiers, else None."""
    m = re.match(r"^[><=!]+(.+)$", specifier)
    return m.group(1).strip() if m else None


def _write_output(content):
    """Write *content* to OUTPUT_FILE using an exclusive open to prevent symlink attacks."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(OUTPUT_FILE, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)


def main():
    """Query PyPI for each requirement and write the outdated-deps comment body to OUTPUT_FILE."""
    outdated = []

    for fname, package, specifier in _parse_requirements():
        latest_str = _get_latest_version(package)
        if latest_str is None:
            continue
        floor_str = _extract_floor_version(specifier)
        if floor_str is None:
            continue
        try:
            if Version(latest_str) > Version(floor_str):
                outdated.append((fname, package, specifier, latest_str))
        except InvalidVersion:
            continue

    if not outdated:
        _write_output("")
        return

    lines = [
        "## :package: Newer dependency versions available",
        "",
        "The following dependencies have newer versions available on PyPI.",
        "",
        "| File | Package | Current specifier | Latest version |",
        "| --- | --- | --- | --- |",
    ]
    for fname, pkg, spec, latest in outdated:
        lines.append(f"| `{fname}` | `{pkg}` | `{spec or '(unconstrained)'}` | `{latest}` |")
    lines += [
        "",
        "> This message is informational — it does not block the merge.",
    ]

    _write_output("\n".join(lines))


if __name__ == "__main__":
    main()

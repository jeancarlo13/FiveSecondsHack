#!/usr/bin/env python3
"""Read [project].version from pyproject.toml and append it to the GITHUB_OUTPUT file
so subsequent workflow steps can reference it via ${{ steps.<id>.outputs.version }}.

Requires the GITHUB_OUTPUT environment variable to be set (provided automatically
by the GitHub Actions runner).
"""
import os
import tomllib
from pathlib import Path


def main():
    """Read version from pyproject.toml and write it to GITHUB_OUTPUT."""
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version", "").strip()
    if not version:
        raise SystemExit("Missing [project].version in pyproject.toml")

    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        raise SystemExit("GITHUB_OUTPUT environment variable not set.")

    with open(output_file, "a", encoding="utf-8") as fh:
        fh.write(f"version={version}\n")

    print(f"Version: {version}")


if __name__ == "__main__":
    main()

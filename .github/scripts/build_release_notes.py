#!/usr/bin/env python3
"""Build the Markdown body for a GitHub release and write it to a file.

Expected environment variables:
    PR_NUMBER   — Pull request number (e.g. "42").
    PR_TITLE    — Title of the merged PR.
    PR_BODY     — Body text of the merged PR (may be empty or multiline).
    VERSION     — Project version string (e.g. "1.2.1").
    IMAGE       — Full GHCR image name without tag (e.g. "ghcr.io/owner/repo").
    OUTPUT_FILE — Destination path for the generated Markdown file
                  (default: /tmp/release_body.md).

The generated body uses ``#<number> <title>`` as section header so it mirrors
the GitHub release title format ``#<number> — <title>`` at a glance.
"""

import os
from pathlib import Path


def main():
    """Render release notes from PR metadata and write them to OUTPUT_FILE."""
    pr_number = os.environ.get("PR_NUMBER", "?")
    pr_title = (os.environ.get("PR_TITLE") or "").strip()
    pr_body = (os.environ.get("PR_BODY") or "").strip() or "Sin resumen en la PR."
    version = os.environ.get("VERSION", "?")
    image = os.environ.get("IMAGE", "?")
    output = os.environ.get("OUTPUT_FILE", "/tmp/release_body.md")

    header = f"## #{pr_number} {pr_title}".rstrip()
    body = "\n".join(
        [
            header,
            "",
            pr_body,
            "",
            "---",
            "",
            f"- Versión: `{version}`",
            f"- Docker image: `{image}:{version}`",
            "",
        ]
    )
    Path(output).write_text(body, encoding="utf-8")
    print(f"Release notes written to {output}")


if __name__ == "__main__":
    main()

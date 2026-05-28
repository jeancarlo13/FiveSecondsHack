---
description: "Generate a PR description for the current branch. Follows .github/pull_request_template.md and outputs to tmp/. Use when: writing a PR, opening a pull request, documenting changes, summarizing commits."
agent: "agent"
argument-hint: "Optional: extra context or focus area (e.g. 'focus on breaking changes')"
tools: ["get_changed_files", "read_file", "run_in_terminal", "create_file"]
---

You are generating a pull request description for the current branch of the **fiveSecondsHack** project.

## Steps

1. Run `git log --oneline main..HEAD` to list commits on the current branch.
2. Run `git diff main..HEAD --stat` to see which files changed.
3. Read [.github/pull_request_template.md](.github/pull_request_template.md) for the required sections.
4. Read [.github/prompts/style-prompt.prompt.md](.github/prompts/style-prompt.prompt.md) for project conventions.
5. For any changed file you need context on, read it.

## Output requirements

- Language: **English (en-US)**.
- Strictly follow the sections in `pull_request_template.md`. Expand **Overview** with a concise but complete summary when the change is non-trivial (multi-feature or architectural).
- Propose **3 alternative PR titles** using conventional commit format (`type(scope): summary`) before the description body. The user will pick one.
- Save the final output to `tmp/pr-<short-slug>-<YYYYMMDD>.md` (use today's date).
- Do **not** include the alternatives block inside the saved file — only the chosen/best title goes in the file header as an H1.

## Title conventions

- Format: `type(scope): summary in imperative mood` 
- Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `ci`
- Scope: affected module or feature (e.g. `logging`, `teams`, `graph`)
- Examples:
  - `feat(teams): add team hierarchy fallback for individual mode`
  - `refactor(logging): migrate to structlog with stdlib backend`

## Section guidance

| Section | Notes |
|---|---|
| **Overview** | 2–5 sentences. What changed, why, and any notable design decisions. |
| **Type of change** | Check all that apply. |
| **Summary** | Bullet list grouped by file or feature area. Include test stats if tests changed. |
| **Breaking changes** | Note any env var renames, API changes, or behavior differences. |
| **Testing** | Test count, coverage %, and any new test classes added. |

If the template does not have a Summary or Testing section, add them after the existing sections.

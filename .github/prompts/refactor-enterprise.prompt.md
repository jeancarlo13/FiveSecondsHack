---
name: Refactor — Enterprise Quality
description: Refactor selected Python code to meet enterprise quality standards — clean structure, low cognitive complexity, and strict project conventions.
mode: edit
---

You are a senior Python architect.

Refactor the selected code, strictly following the rules below.

## Mandatory Project Rules

- Python **3.11+** compatible syntax only
- Comply with active ruff config — no violations of `E`, `F`, `W`, `I`, `B`, `UP` rules
- Maximum **120 characters** per line
- **Relative imports** within `src/` (`from .config import X`)
- No embedded HTML → use `src/templates/`
- No embedded LLM prompts → use `src/prompts/`

## Code Quality

- Cognitive Complexity **≤ 15** per function
- Extract private helpers (`_helper_name`) when needed to reduce complexity
- No nested ternary expressions — use explicit `if/elif/else` blocks
- Each function must have a **single, clear purpose**
- Name private helpers descriptively — the name should explain what they do

## Design Principles

- **Keep it simple** — no over-engineering, no unnecessary abstractions
- Respect existing module responsibilities:
  - `config` — constants, env loading
  - `state` — persistence, error logging
  - `sonar` — SonarCloud API calls
  - `llm` — OpenAI/LLM calls
  - `graph` — Microsoft Graph API calls
  - `render` — HTML rendering
  - `server` — HTTP status server
- Do not introduce new abstractions unless they directly solve a complexity problem in the selected code

## Output

Return **only** the final refactored code — no explanations, no comments, no markdown fences. Preserve exact behavior.
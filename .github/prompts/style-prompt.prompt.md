---
name: Coding Style Guide
description: Enforces the fiveSecondsHack project coding style, structure, and conventions for all Python code written or modified.
applyTo: "**/*.py"
---

# Coding Style Guide — fiveSecondsHack

You are an expert programming agent. When writing or modifying code in this project, **strictly follow** the style and structural rules defined below.

---

## Language & Runtime

- **Python 3.11+** (Docker image `python:3.11-alpine`; local dev 3.12).
- Linter: **ruff ≥ 0.4.0** configured in `pyproject.toml`:
  - `line-length = 120`
  - `select = ["E", "F", "W", "I", "B", "UP"]`
  - `ignore = ["E501", "B904"]`
  - `per-file-ignores`: `"tests/**" = ["E402","F401"]`, `"src/main.py" = ["E402"]`
- Pre-commit hooks: `ruff` + `ruff-format` via `astral-sh/ruff-pre-commit`.
- **Never** exceed 120 characters per line unless absolutely unavoidable.

---

## Project Structure

```
src/                  # Main package — internal relative imports
  __init__.py
  config.py           # Constants and load_dotenv()
  state.py            # log_error, load_state, save_state
  sonar.py            # SonarCloud API integration
  llm.py              # OpenAI/LLM integration
  graph.py            # Microsoft Graph API integration
  render.py           # HTML rendering (code blocks, relative time)
  server.py           # HTTP status server
  main.py             # Orchestrator — entry point
  prompts/            # LLM prompts as standalone .md files
  templates/          # HTML in .html files, NEVER embedded in Python
tests/
  conftest.py         # Env vars + sys.argv = ["main.py"] at module scope (before imports)
  test_config.py      # One test file per module
  test_state.py
  test_sonar.py
  test_llm.py
  test_graph.py
  test_render.py
  test_server.py
  test_main.py
container/            # Docker files — NEVER at project root
  Dockerfile
  docker-compose.yml
data/                 # sonar_state.json (not committed)
logs/                 # error.log (not committed)
tmp/                  # Debug JSON dumps per run
```

---

## Imports & Modules

- Inside `src/`: use **relative imports** (`from .config import X`, `from .state import log_error`).
- In `src/main.py` (direct entry point): sys.path fix **before** package imports:
  ```python
  _ROOT = Path(__file__).resolve().parent.parent
  if str(_ROOT) not in sys.path:
      sys.path.insert(0, str(_ROOT))
  from src.config import ...  # imports absolutos después del fix
  ```
- The E402 warning for that block is suppressed in `pyproject.toml` via `per-file-ignores`.

---

## HTML Templates

- **Never** embed long HTML strings inside Python code.
- Extract to `src/templates/*.html` and load **once at module level**:
  ```python
  from pathlib import Path
  from string import Template
  _MY_TEMPLATE = Template((Path(__file__).parent / "templates" / "my.html").read_text(encoding="utf-8"))
  ```
- Use `string.Template` with `${varname}` syntax (not f-strings or `.format()`).
- CSS `{}` blocks do not conflict with `string.Template`.
- HTML emails **must use inline CSS** (required for email client compatibility).
- Always use `.safe_substitute(...)` (never `.substitute(...)`) to avoid errors with unescaped `$`.

---

## LLM Prompts

- **Never** embed LLM prompts inside Python code.
- Extract to `src/prompts/*.md` using `{varname}` placeholders (Python `.format()` style).
- Load once at module level:
  ```python
  _PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "refactor.md").read_text(encoding="utf-8")
  ```
- Invoke with `.format(**kwargs)` using named parameters.

---

## Testing

- **pytest** + **pytest-cov** with minimum **95% coverage** (`--cov-fail-under=95`).
- `pytest.ini`: `testpaths = tests`, `addopts = --cov=src --cov-report=term-missing --cov-fail-under=95`.
- One test file **per module** (`test_sonar.py`, `test_graph.py`, etc.). Never a monolithic `test_main.py`.
- Tests organized in **classes** (e.g., `class TestFetchAndSelectSonarIssue:`).
- `conftest.py` sets **all** required env vars at module scope (not in fixtures), so they run before any `src/` import:
  ```python
  import os, sys
  os.environ.update({ "OPENAI_API_KEY": "test-key", ... })
  sys.argv = ["main.py"]
  ```
- Use `unittest.mock`: `patch`, `MagicMock`, `mock_open`.
- Define module-level sample data in each test file (e.g., `_SAMPLE_ISSUE = {...}`).

---

## Docker

- Dockerfiles and docker-compose files belong in `container/`, **never at the project root**.
- `docker-compose.yml` rules:
  - No `version:` key (deprecated in Compose v2).
  - `build.context: ..` and `build.dockerfile: container/Dockerfile`.
  - `env_file: ../.env` (relative to the compose file, not the project directory).
  - Healthcheck must use `CMD-SHELL` (not `CMD`) for shell variable expansion.
  - Use `$$VAR` (double `$`) in YAML to prevent Docker Compose from interpolating variables that must expand **inside the container** at runtime:
    ```yaml
    test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:$${STATUS_PORT:-8080} > /dev/null 2>&1"]
    ```
  - For `ports:`, always include a default value because Docker Compose cannot read the root `.env` when the compose file is in a subdirectory:
    ```yaml
    ports:
      - "${STATUS_PORT:-9080}:${STATUS_PORT:-9080}"
    ```

---

## Code Quality (Sonar / Cognitive Complexity)

- Maximum Cognitive Complexity: **15** per function.
- If a function exceeds that threshold, refactor by extracting descriptively named private helper functions.
- **Never** nest ternary expressions in a single line; extract to an independent variable:
  ```python
  # BAD
  days = 3 if wd == 4 else (2 if wd == 5 else 1)
  # GOOD
  if wd == 4:
      days = 3
  elif wd == 5:
      days = 2
  else:
      days = 1
  ```
- Long orchestration functions (e.g., `run_bot`) must be split into single-purpose private helpers.

---

## General Conventions

- **No over-engineering**: only add features, abstractions, or helpers when directly necessary.
- Do not add docstrings, comments, or type annotations to code that is not being modified.
- Do not create error handling for impossible scenarios; only handle errors at system boundaries.
- Module-internal functions and constants must use a `_` prefix.
- State file: `data/sonar_state.json`. Error log: `logs/error.log`. Both relative to WORKDIR `/app` in Docker.
- Relevant env var keys: `SONAR_HOST_URL`, `SONAR_TOKEN`, `SONAR_ORGANIZATION`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `EMAIL_USERNAME`, `ALERT_RECIPIENTS`, `WORK_TIMEZONE`, `WORK_DAY_START`, `WORK_DAY_END`, `STATUS_PORT`, `ISSUE_LOOKBACK_HOURS`.

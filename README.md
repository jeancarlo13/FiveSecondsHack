# Five Seconds Hack

An intelligent automation bot that integrates **SonarCloud**, **AI Models (LLMs via GitHub Models / OpenAI)**, and **Microsoft Graph API** to promote a culture of code quality and Defensive Programming within development teams.

The name comes from the idea that every developer can improve their code in five seconds — by glancing at a calendar event that lands in their inbox with a real code smell, a plain-language explanation, and a ready-to-apply refactoring suggestion.

## 🚀 Purpose

The bot detects open Code Smells from SonarCloud, fetches the real source context, asks an LLM for an explanation and refactoring proposal, and delivers the result as a calendar event directly in the developers' Microsoft 365 agenda — all automatically, on a smart schedule.

## ✨ Key Features

- **SonarCloud Issue Detection:** Paginated scan of open issues within a configurable lookback window (`ISSUE_LOOKBACK_HOURS`).
- **Weighted Selection:** Randomly picks an issue not yet in history, weighted by severity (BLOCKER > CRITICAL > MAJOR > MINOR > INFO).
- **Source Context:** Fetches the actual code lines locally first, then falls back to SonarCloud's `/api/sources/show` API.
- **AI-Assisted Refactoring:** Queries an LLM (GitHub Models / OpenAI) for a structured JSON response with title, explanation, and suggested code.
- **Alert Mode Toggle:** `ALERT_MODE=broadcast` (default) sends one event to all recipients; `ALERT_MODE=individual` fetches a **different** SonarCloud issue per recipient and dispatches a separate calendar event to each one.
- **Recipient-Aware Issue Selection:** `ISSUE_ONLY_FROM_INVITED=true` restricts selection to issues authored by invited recipients; when `false`, any author is eligible.
- **Microsoft Graph Calendar Event:** Creates a 15-minute calendar event at a random slot within configurable work hours, always in the future.
- **Business Day Scheduling:** Next execution is always the next business day (skips weekends); Friday schedules to Monday.
- **Status HTTP Endpoint:** Lightweight built-in web server (`--serve`) showing scheduler state, last notification sent, and a live HTML preview.
- **Force Send:** POST `/force` on the status page triggers an immediate run bypassing schedule and history.
- **State Persistence:** `data/sonar_state.json` tracks `next_execution`, `history` (last 50 issues), and `last_sent` (including HTML preview).
- **Docker-first:** Designed to run as a persistent container with `restart: unless-stopped` and a built-in healthcheck.

## 🏗️ Architecture

```
SonarCloud API
    │  paginated issues + source code
    ▼
sonar.py ──► render.py (clean source)
    │
    │  weighted issue selection
    ▼
main.py (run_bot)
    │
    ├──► llm.py ──► OpenAI API
    │        structured JSON (title, explanation, suggested_code)
    │
    ├──► render.py ──► HTML code blocks (original + refactored)
    │
    ├──► graph.py ──► Microsoft Graph API ──► 📅 Calendar event
    │
    └──► state.py ──► data/sonar_state.json (next_exec, history, last_sent)

server.py  ──► GET  /       → HTML dashboard
(--serve)  ──► POST /force  → spawns run_bot --force
```

## 📁 Project Structure

```
src/
  __init__.py          Package marker with module docstring
  config.py            Constants: file paths, severity weights, context lines
  state.py             load_state / save_state / log_error
  sonar.py             SonarCloud API: fetch & select issues, fetch source
  llm.py               OpenAI integration: prompt formatting + JSON parsing
  graph.py             Microsoft Graph: OAuth2 token + calendar event creation
  render.py            HTML code blocks, clean_sonar_source, relative_time
  server.py            HTTP status dashboard (GET /, POST /force)
  main.py              Orchestrator: run_bot, helpers, CLI entry point
  prompts/
    refactor.md        LLM prompt template (uses {varname} placeholders)
  templates/
    code_block.html    Email-safe code block (inline CSS, single-cell table)
    email_alert.html   Alert email body template
    email_no_issues.html  "No new issues" email body template
    status_page.html   HTML status dashboard template
tests/
  conftest.py          Env var setup + sys.argv fix before any imports
  test_config.py       …one test file per source module
  test_state.py
  test_sonar.py
  test_llm.py
  test_graph.py
  test_render.py
  test_server.py
  test_main.py
container/
  Dockerfile           python:3.11-alpine, runs both server + scheduler loop
  docker-compose.yml   Port mapping, volume mounts, healthcheck, env_file
data/
  sonar_state.json     Runtime state (not committed)
logs/
  error.log            Append-only error log (not committed)
tmp/
  *.json               Per-run debug dumps (not committed)
```

## 🛠️ Environment Variables

Create a `.env` file at the project root (already in `.gitignore`):

```env
# SonarCloud
SONAR_HOST_URL=https://sonarcloud.io
SONAR_TOKEN=your_sonar_token
SONAR_ORGANIZATION=your_org

# LLM — GitHub Models (or any OpenAI-compatible endpoint)
GITHUB_TOKEN=your_github_token
OPENAI_API_KEY=your_github_token        # same value as GITHUB_TOKEN
OPENAI_BASE_URL=https://models.inference.ai.azure.com
OPENAI_MODEL=gpt-4o-mini                # optional, default: gpt-4o-mini

# Microsoft Graph (OAuth2 client credentials flow)
AZURE_TENANT_ID=your_tenant_id
AZURE_CLIENT_ID=your_client_id
AZURE_CLIENT_SECRET=your_client_secret
EMAIL_USERNAME=calendar_owner@company.com

# Recipients — comma-separated; trailing comma is ignored
ALERT_RECIPIENTS=dev1@company.com,dev2@company.com

# Alert delivery mode: 'broadcast' (one event for all) | 'individual' (one event per recipient, unique issue each)
ALERT_MODE=broadcast

# When true, only issues authored by invited recipients are eligible
ISSUE_ONLY_FROM_INVITED=false

# Scheduling
WORK_TIMEZONE=America/Chihuahua        # IANA timezone name
WORK_DAY_START=09:00                   # events are never created before this
WORK_DAY_END=18:00                     # events end by this time
ISSUE_LOOKBACK_HOURS=72                # how far back to scan for new issues

# Status server
STATUS_PORT=9080                       # host port exposed by docker-compose
```

## 📦 Running with Docker (recommended)

```bash
# First run — create required local files
touch data/sonar_state.json logs/error.log
mkdir -p tmp

# Build and start (detached)
docker compose -f container/docker-compose.yml up --build -d

# Follow logs
docker compose -f container/docker-compose.yml logs -f

# Open the status dashboard
open http://localhost:9080

# Stop
docker compose -f container/docker-compose.yml down
```

The container runs both processes in parallel:
- `python src/main.py --serve` — status HTTP server (always on, port `STATUS_PORT`)
- `python src/main.py` in a 60-second loop — scheduler / notification sender

## 🖥️ Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start status server in background (http://localhost:9080)
python src/main.py --serve &

# Run one scheduler cycle
python src/main.py

# Force an immediate send (bypasses schedule and history deduplication)
python src/main.py --force
```

## 🧪 Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests with coverage report
pytest

# Coverage threshold is 95% (enforced in pytest.ini)
```

The test suite uses `unittest.mock` to patch all external calls (SonarCloud,
OpenAI, Microsoft Graph, filesystem).  `conftest.py` sets all required
environment variables and fixes `sys.argv` before any module is imported.

## 🔁 Scheduling Algorithm

1. On startup, `run_bot` reads `next_execution` from state.
2. If `now < next_execution` and `--force` was not passed, the bot exits immediately (cron-safe).
3. After each successful or failed send, `next_execution` is advanced to a random time on the **next business day** within work hours.
4. Weekend guard: Friday → Monday (+3 days), Saturday → Monday (+2 days), all other days → tomorrow (+1 day).
5. When no issues are found, a 1-hour retry is scheduled instead.

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Status page is slow to load | Large HTML preview stored in `last_sent.html` | Normal; the full email HTML is inlined in the page |
| `Event not created: outside of work hours` in logs | Bot ran outside `WORK_DAY_START`–`WORK_DAY_END` | Run during work hours or expand the window |
| `No open Code Smells` with `--force` | All issues are outside the `ISSUE_LOOKBACK_HOURS` window | Increase `ISSUE_LOOKBACK_HOURS` or check SonarCloud |
| `LLM Inference failed` | Invalid `OPENAI_API_KEY` or quota exceeded | Check the key and model availability |
| `Microsoft Entra ID Authentication failed` | Wrong `AZURE_*` credentials or missing Graph permissions | Verify app registration and `Calendars.ReadWrite` permission |
| In `individual` mode, only N-1 events sent | SonarCloud ran out of unique issues for the last recipient | Increase `ISSUE_LOOKBACK_HOURS` or reduce recipient count |
| `400 Bad Request` on calendar event | Attendee list malformed (e.g. plain string instead of object) | Upgrade to the latest version of `graph.py` / `main.py` |

# Five Seconds Hack

An intelligent automation bot that integrates **SonarCloud**, **AI Models (LLMs via GitHub Models / OpenAI)**, and **Microsoft Graph API** to promote a culture of code quality and Defensive Programming within development teams.

## 🚀 Purpose

The bot detects open Code Smells from SonarCloud, fetches the real source context, asks an LLM for an explanation and refactoring proposal, and delivers the result as a calendar event directly in the developers' Microsoft 365 agenda — all automatically, on a smart schedule.

## ✨ Key Features

- **SonarCloud Issue Detection:** Paginated scan of open issues within a configurable lookback window (`ISSUE_LOOKBACK_HOURS`).
- **Weighted Selection:** Randomly picks an issue not yet in history, avoiding repeated notifications.
- **Source Context:** Fetches the actual code lines from SonarCloud (`/api/sources/show`) around the affected line.
- **AI-Assisted Refactoring:** Queries an LLM (GitHub Models / OpenAI) for a structured JSON response with title, explanation, and suggested code.
- **Microsoft Graph Calendar Event:** Creates a 15-minute calendar event at a random slot within configurable work hours, always in the future.
- **Business Day Scheduling:** Next execution is always the next business day (skips weekends); Friday schedules to Monday.
- **Status HTTP Endpoint:** Lightweight built-in web server (`--serve`) showing scheduler state, last notification sent, and a live HTML preview.
- **Force Send:** POST `/force` on the status page triggers an immediate run bypassing schedule and history.
- **State Persistence:** `sonar_state.json` tracks `next_execution`, `history` (last 50 issues), and `last_sent` (including HTML preview).
- **Docker-first:** Designed to run as a persistent container with `restart: unless-stopped` and a built-in healthcheck.

## 🛠️ Environment Variables

Create a `.env` file at the project root:

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

# Microsoft Graph (OAuth2 client credentials)
AZURE_TENANT_ID=your_tenant_id
AZURE_CLIENT_ID=your_client_id
AZURE_CLIENT_SECRET=your_client_secret
SHAREPOINT_USERNAME=calendar_owner@company.com

# Recipients (comma-separated)
ALERT_RECIPIENTS=dev1@company.com,dev2@company.com

# Scheduling
WORK_TIMEZONE=America/Chihuahua        # IANA timezone name
WORK_DAY_START=09:00
WORK_DAY_END=18:00
ISSUE_LOOKBACK_HOURS=72                # How far back to scan for issues

# Status server
STATUS_PORT=8080
```

## 📦 Running with Docker (recommended)

```bash
# First run — create required local files
touch sonar_state.json error.log
mkdir -p tmp

# Build and start
docker compose up --build -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

The container runs both processes:
- `python src/main.py --serve` — status HTTP server (always on)
- `python src/main.py` in a loop every 60 s — scheduler / sender

## 🖥️ Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start status server in background
python src/main.py --serve &

# Run the bot once
python src/main.py

# Force send (bypass schedule and history)
python src/main.py --force
```

## 🧪 Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests with coverage report
python -m pytest
```

The suite targets **≥95% coverage** (currently at ~99%). Configuration lives in `pytest.ini`.

## 📊 Status Page

Open `http://localhost:<STATUS_PORT>` to see:

- **Scheduler:** next execution time and history count.
- **Last Notification Sent:** issue key, component, rule, and timestamp.
- **Last Notification Preview:** rendered HTML of the last calendar event body.
- **Force Send Now button:** triggers an immediate send from the browser.

## ⚙️ State File

`sonar_state.json` (auto-created on first run):

```json
{
  "next_execution": "2026-05-26T09:35:00",
  "history": ["PROJECT:src/File.cs"],
  "last_sent": {
    "issue_key": "PROJECT:src/File.cs",
    "component": "PROJECT:src/File.cs",
    "rule": "csharpsquid:S1481",
    "title": "...",
    "sent_at": "2026-05-25T14:22:00",
    "html": "..."
  }
}
```

## 🗂️ Project Structure

```
src/
  main.py            # Main orchestrator (entry point)
tests/
  test_main.py       # 101 tests — 99% coverage
requirements.txt     # Runtime Python dependencies
requirements-dev.txt # Dev/test dependencies (pytest, pytest-cov)
pytest.ini           # Test configuration
Dockerfile           # Python 3.11-alpine image
docker-compose.yml   # Service definition with healthcheck
.env                 # Environment variables (not committed)
sonar_state.json     # Runtime state (not committed)
error.log            # Error log (not committed)
tmp/                 # Debug JSON dumps per execution
```
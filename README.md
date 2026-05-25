# SonarCloud Copilot Bot

An intelligent automation bot designed to improve code quality by integrating **SonarCloud**, **AI Models (LLMs)**, and **Microsoft Graph API**.

## 🚀 Purpose
The bot automatically analyzes Code Smells detected by SonarCloud, extracts the real context from local source code, requests an explanation and refactoring proposal from an AI model, and finally injects an educational card into the calendars of the target developers to foster a culture of Defensive Programming.

## ✨ Key Features
* **Real-time Detection:** Scans open issues via the SonarCloud API.
* **Dynamic Analysis:** Reads the actual local source code for each detected issue.
* **AI-Assisted Refactoring:** Queries LLMs (via OpenAI/GitHub Models) for English explanations and precise technical solutions.
* **Corporate Integration:** Automatically sends calendar invitations (Microsoft Graph) so the team receives the alert directly in their agenda.
* **Tactical Silence:** Smart scheduling system that prevents alert spam, with randomized re-execution based on the working calendar.
* **Manual Mode `--force`:** Allows immediate integration tests by bypassing time restrictions and execution history.

## 🛠️ Environment Setup
To run the bot, make sure you have a `.env` file at the root with the following variables:

```env
SONAR_HOST_URL=https://sonarcloud.io
SONAR_TOKEN=your_sonar_token
SONAR_ORGANIZATION=your_org
AZURE_TENANT_ID=your_tenant_id
AZURE_CLIENT_ID=your_client_id
AZURE_CLIENT_SECRET=your_client_secret
SHAREPOINT_USERNAME=owner_email@company.com
ALERT_RECIPIENTS=team1@company.com,team2@company.com
OPENAI_API_KEY=sk-your_openai_api_key
ISSUE_LOOKBACK_HOURS=72   # How far back to search for issues (default: 72h — covers Mon→Fri)
# Optional if using GitHub Models:
# OPENAI_BASE_URL=https://models.inference.ai.azure.com
```

## 📦 Installation and Execution
Due to managed Python environment policies (PEP 668), using a virtual environment is recommended:

**Create virtual environment:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Install dependencies:**
```bash
pip install openai requests python-dotenv
```

**Run the bot:**

- Automatic mode: `python3 main.py`
- Immediate test mode: `python3 main.py --force`

## ⚙️ State Structure
The bot maintains a `sonar_state.json` file that records:

- `next_execution`: Date/time of the next automatic check.
- `history`: List of already-processed issue keys to prevent duplicate notifications.

## 📝 Development Notes
- The bot supports dynamic language mapping (Bash/Shell, C#), automatically adjusting the explanation and refactor command.
- Diagnostic redirection to stderr (`>&2`) is now handled correctly to maintain log integrity and output streams in automation scripts.
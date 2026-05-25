import os
import sys
import random
import json
import re
import html as html_lib
import subprocess
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()

STATE_FILE = "sonar_state.json"
LOG_FILE = "error.log"

# Severity weights for weighted random issue selection (higher = more likely to be picked)
SEVERITY_WEIGHTS = {
    "BLOCKER": 50,
    "CRITICAL": 30,
    "MAJOR": 15,
    "MINOR": 4,
    "INFO": 1,
}

# Number of lines to include before and after the flagged line for context
SOURCE_CONTEXT_LINES = 8

def render_code_block(code, start_line_num, highlight_lines=None, accent_color="#ef4444"):
    """
    Renders a code block with line numbers and optional line highlighting.
    All lines live inside a SINGLE <td>, separated by <br> tags.
    This avoids any inter-row/inter-paragraph spacing that email/calendar clients
    add when each line is its own block element (<div>, <tr>, etc.).
    highlight_lines: a set of absolute line numbers to highlight.
    """
    lines = code.split("\n")
    max_width = len(str(start_line_num + len(lines) - 1))
    line_parts = []
    for i, line in enumerate(lines):
        current_num = start_line_num + i
        is_flagged = (highlight_lines is not None and current_num in highlight_lines)
        num_str = str(current_num).rjust(max_width)
        # Preserve leading whitespace as &nbsp; so indentation renders even if
        # the email client strips white-space:pre-wrap.
        raw = line.rstrip()
        content_start = len(raw) - len(raw.lstrip('\t '))
        indent = raw[:content_start].replace('\t', '    ').replace(' ', '&nbsp;')
        code_html = indent + html_lib.escape(raw[content_start:])
        if is_flagged:
            # Single span — number + marker + code as one text node, nothing can split
            line_parts.append(
                f'<span style="background-color:#1e293b;color:{accent_color};">'
                f'{html_lib.escape(num_str)} &#9658; {code_html}'
                f'</span>'
            )
        else:
            line_parts.append(
                f'<span style="color:#475569;">{html_lib.escape(num_str)}  </span>'
                f'<span style="color:#f8fafc;">{code_html}</span>'
            )
    return (
        '<table cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;width:100%;border-radius:8px;">'
        '<tr><td style="background:#0f172a;padding:8px 12px 8px 8px;'
        'font-family:Consolas,monospace;font-size:13px;'
        'white-space:pre-wrap;word-wrap:break-word;">'
        + '<br>'.join(line_parts)
        + '</td></tr></table>'
    )


def clean_sonar_source(raw_html):
    """Strips HTML syntax-highlighting tags and decodes HTML entities from SonarCloud source API responses."""
    plain = re.sub(r'<[^>]+>', '', raw_html)
    return html_lib.unescape(plain)


def relative_time(dt):
    """Returns a human-readable relative time string (e.g. '3 days ago') from a UTC-aware datetime."""
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        n = seconds // 60
        return f"{n} minute{'s' if n > 1 else ''} ago"
    if seconds < 86400:
        n = seconds // 3600
        return f"{n} hour{'s' if n > 1 else ''} ago"
    if seconds < 7 * 86400:
        n = seconds // 86400
        return f"{n} day{'s' if n > 1 else ''} ago"
    if seconds < 30 * 86400:
        n = seconds // (7 * 86400)
        return f"{n} week{'s' if n > 1 else ''} ago"
    if seconds < 365 * 86400:
        n = seconds // (30 * 86400)
        return f"{n} month{'s' if n > 1 else ''} ago"
    n = seconds // (365 * 86400)
    return f"{n} year{'s' if n > 1 else ''} ago"


def log_error(message):
    """Appends an error message with a timestamp to the local log file."""
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")


def load_state():
    """Loads the execution state. Initializes if the file does not exist or is empty/corrupt."""
    default_state = {"next_execution": datetime.now().isoformat(), "history": []}
    try:
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return default_state
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state


def save_state(state):
    """Persists the execution state into the JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_and_select_sonar_issue(history, created_after=None):
    """
    Fetches all open unresolved issues from SonarCloud (paginated), filters out
    issues already in history, and returns one selected at random weighted by severity.
    Pass created_after (ISO 8601 string) to restrict to a recent time window.
    """
    url = f"{os.getenv('SONAR_HOST_URL')}/api/issues/search"
    headers = {"Authorization": f"Bearer {os.getenv('SONAR_TOKEN')}"}
    all_issues = []
    page = 1

    while True:
        params = {
            "organization": os.getenv("SONAR_ORGANIZATION"),
            "resolved": "false",
            "ps": "500",
            "p": str(page),
        }
        if created_after:
            params["createdAfter"] = created_after
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)
            if len(all_issues) >= data.get("total", 0) or not issues:
                break
            page += 1
        except Exception as e:
            log_error(f"Failed to fetch issues from SonarCloud (page {page}): {e}")
            break

    # Filter out issues that have already been sent
    candidates = [i for i in all_issues if i.get("key") not in history]
    if not candidates:
        return None

    # Weighted random selection: higher severity issues are more likely to be picked
    weights = [SEVERITY_WEIGHTS.get(i.get("severity", "INFO"), 1) for i in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def get_local_source_code(component_path, line_number):
    """
    Attempts to extract the real source code line directly from the local file system
    or from the mapped repository structure.
    """
    # Component typically comes as "project:path/to/file.sh" or similar
    clean_path = component_path.split(":")[-1] if ":" in component_path else component_path

    # Try to locate the file using relative or absolute paths within the development environment
    possible_paths = [
        clean_path,
        os.path.join(os.getcwd(), clean_path),
        os.path.join(os.path.dirname(__file__), clean_path)
    ]

    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if 0 < line_number <= len(lines):
                        start = max(0, line_number - 1 - SOURCE_CONTEXT_LINES)
                        end = min(len(lines), line_number + SOURCE_CONTEXT_LINES)
                        return "".join(lines[start:end]).rstrip()
            except Exception as e:
                log_error(f"Error reading local file {path}: {e}")

    return None


def fetch_source_from_sonar(component_key, line_number):
    """
    Fetches the source code line directly from the SonarCloud API.
    Used as a fallback when the local file is not accessible.
    """
    url = f"{os.getenv('SONAR_HOST_URL')}/api/sources/show"
    headers = {"Authorization": f"Bearer {os.getenv('SONAR_TOKEN')}"}
    params = {
        "key": component_key,
        "from": max(1, line_number - SOURCE_CONTEXT_LINES),
        "to": line_number + SOURCE_CONTEXT_LINES,
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        sources = response.json().get("sources", [])
        if sources:
            # Each entry is [line_number, html_encoded_code with syntax-highlight spans].
            # Strip HTML tags, decode entities, and join all lines into a code block.
            return "\n".join(clean_sonar_source(s[1]) for s in sources if s[1] is not None).strip()
    except Exception as e:
        log_error(f"Failed to fetch source from SonarCloud API for {component_key}:{line_number}: {e}")
    return None


def ask_llm_for_refactor(rule_id, sonar_message, source_line, file_path, line_number):
    """
    Calls the OpenAI/LLM API to dynamically process the real code smell
    and generate a precise explanation and refactoring suggestion.
    """
    client = OpenAI()

    prompt = f"""
    You are an expert bot in Defensive Programming and Code Refactoring.
    Analyze the following issue detected by SonarCloud:

    - File: {file_path}
    - SonarCloud Rule: {rule_id}
    - SonarCloud Message: {sonar_message}
    - Flagged line number: {line_number}
    - Context block ({2 * SOURCE_CONTEXT_LINES + 1} lines starting at line {max(1, line_number - SOURCE_CONTEXT_LINES)}):
```
{source_line}
```

    Generate a strictly structured JSON response with the following keys:

    1. "title": A short, impactful alert title with an appropriate emoji (in English).

    2. "explanation": An educational explanation (3 sentences max) in Spanish (es-MX).
       - Technical terms and concept names MUST appear in English wrapped in <strong> tags.
       - Write the surrounding prose in Spanish, but NEVER translate the technical concept names.
       - Explain why the original code violates best practices and what risk it poses.

    3. "suggested_code": The corrected version of the context block.
       Step-by-step:
         a. Identify every line in the context block that violates rule {rule_id} ("{sonar_message}").
         b. Rewrite ONLY those lines to fix the violation.
         c. Copy every other line EXACTLY as it appears in the context block (same characters, same spacing).
       HARD CONSTRAINTS:
       - MUST contain exactly the same number of lines as the context block above.
       - MUST differ from the context block on at least one line. If they are identical, you have NOT applied the fix — start over.
       - Preserve every tab, space, and character in lines that are NOT being fixed.
       - Preserve the original variable names, framework syntax (Razor, Bash, etc.), and quote style.
       - The first line of suggested_code MUST correspond to line {max(1, line_number - SOURCE_CONTEXT_LINES)} of the file.

    4. "sonar_message_es": REQUIRED. The SonarCloud message translated to Spanish (es-MX). Keep all technical terms, rule names, attribute names, and code identifiers in English.

    Respond ONLY with the raw JSON object, no ```json code blocks or additional text.
    """

    result_text = ""
    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        # Parse the dynamic response from the model
        result_text = response.choices[0].message.content.strip()
        # Strip markdown code fence that the LLM sometimes adds despite being told not to
        if result_text.startswith("```"):
            result_text = re.sub(r'^```(?:json)?\s*\n?', '', result_text)
            result_text = re.sub(r'\n?\s*```\s*$', '', result_text).strip()
        return json.JSONDecoder(strict=False).decode(result_text)
    except Exception as e:
        log_error(f"LLM Inference failed: {e} | raw_response={result_text[:300] if result_text else 'N/A'}")
        # Safe fallback in case of API outage or JSON parse error
        return {
            "title": "🚨 Alerta de Calidad de Código",
            "explanation": f"SonarCloud detectó una anomalía en el código (<strong>{rule_id}</strong>): {sonar_message}.",
            "suggested_code": source_line
        }


def get_graph_access_token():
    """Requests an OAuth2 access token from Microsoft Entra ID using Client Credentials."""
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "client_id": client_id,
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": client_secret,
        "grant_type": "client_credentials"
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        log_error(f"Microsoft Entra ID Authentication failed: {e}")
        return None


def create_graph_calendar_event(subject, html_content, attendees_override=None):
    """Injects an educational alert event into the target user's corporate calendar."""
    token = get_graph_access_token()
    if not token:
        return False

    user_email = os.getenv("SHAREPOINT_USERNAME")
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/events"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    work_tz = ZoneInfo(os.getenv("WORK_TIMEZONE", "America/Chihuahua"))
    work_start_h, work_start_m = map(int, os.getenv("WORK_DAY_START", "09:00").split(":"))
    work_end_h, work_end_m = map(int, os.getenv("WORK_DAY_END", "18:00").split(":"))

    now_local = datetime.now(work_tz)
    today = now_local.date()
    window_start = datetime(today.year, today.month, today.day, work_start_h, work_start_m, tzinfo=work_tz)
    window_end   = datetime(today.year, today.month, today.day, work_end_h,   work_end_m,   tzinfo=work_tz)

    # Earliest allowed start: the later of "now + 5 min" and the work day start
    earliest = max(window_start, now_local + timedelta(minutes=5))
    # Latest allowed start: work day end minus the 15-min event duration
    latest = window_end - timedelta(minutes=15)

    # If the available window has closed for today, do not create the event
    if earliest > latest:
        log_error(
            f"Event not created: outside of work hours "
            f"({os.getenv('WORK_DAY_START','09:00')}–{os.getenv('WORK_DAY_END','18:00')} "
            f"{os.getenv('WORK_TIMEZONE','America/Chihuahua')})"
        )
        return False

    # Pick a random minute within the available window
    available_minutes = int((latest - earliest).total_seconds() // 60)
    candidate_start = earliest + timedelta(minutes=random.randint(0, available_minutes))
    event_end = candidate_start + timedelta(minutes=15)

    tz_name = os.getenv("WORK_TIMEZONE", "America/Chihuahua")
    start_time = candidate_start.strftime("%Y-%m-%dT%H:%M:%S")
    end_time   = event_end.strftime("%Y-%m-%dT%H:%M:%S")

    if attendees_override is not None:
        attendees_list = attendees_override
    else:
        attendees_list = []
        recipients_env = os.getenv("ALERT_RECIPIENTS", "")
        if recipients_env:
            emails = [email.strip() for email in recipients_env.split(",") if email.strip()]
            for email in emails:
                attendees_list.append({
                    "emailAddress": {
                        "address": email,
                        "name": email.split("@")[0].replace(".", " ").title()
                    },
                    "type": "required"
                })

    payload = {
        "subject": subject,
        "body": {
            "contentType": "html",
            "content": html_content
        },
        "start": {
            "dateTime": start_time,
            "timeZone": tz_name
        },
        "end": {
            "dateTime": end_time,
            "timeZone": tz_name
        },
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 0,
        "showAs": "free",
        "attendees": attendees_list
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Successfully injected Graph Calendar Event: {subject}")
        return True
    except Exception as e:
        log_error(f"Graph API Failed to create calendar event: {e}")
        return False


# --- STATUS SERVER ---

class StatusHandler(BaseHTTPRequestHandler):
    """Serves a simple HTML status page read from the local state file."""

    def do_GET(self):
        try:
            with open(STATE_FILE, "r") as f:
                state_data = json.loads(f.read().strip() or "{}")
        except (FileNotFoundError, json.JSONDecodeError):
            state_data = {}

        next_exec = state_data.get("next_execution", "")
        history = state_data.get("history", [])
        last_sent = state_data.get("last_sent", {})

        try:
            next_exec_fmt = datetime.fromisoformat(next_exec).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            next_exec_fmt = next_exec or "—"

        if last_sent:
            last_sent_rows = (
                f'<tr><td>Issue Key</td><td><code>{html_lib.escape(last_sent.get("issue_key", "—"))}</code></td></tr>'
                f'<tr><td>Title</td><td>{html_lib.escape(last_sent.get("title", "—"))}</td></tr>'
                f'<tr><td>File</td><td><code style="font-size:11px;">{html_lib.escape(last_sent.get("component", "—"))}</code></td></tr>'
                f'<tr><td>Rule</td><td><code>{html_lib.escape(last_sent.get("rule", "—"))}</code></td></tr>'
                f'<tr><td>Sent at</td><td>{html_lib.escape(last_sent.get("sent_at", "—"))}</td></tr>'
            )
            last_sent_preview = last_sent.get("html", "")
        else:
            last_sent_rows = "<tr><td colspan='2' style='color:#94a3b8;'>No notifications sent yet.</td></tr>"
            last_sent_preview = ""

        preview_section = (
            f'<h2>Last Notification Preview</h2><div class="preview">{last_sent_preview}</div>'
            if last_sent_preview else ""
        )

        body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Five Seconds Hack — Status</title>
<style>
  body{{font-family:'Segoe UI',sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#334155;background:#f8fafc;}}
  h1{{color:#4f46e5;margin-bottom:4px;}} h2{{color:#475569;border-bottom:1px solid #e2e8f0;padding-bottom:6px;margin-top:32px;}}
  table{{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06);}}
  td{{padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:14px;}}
  td:first-child{{font-weight:600;color:#64748b;width:130px;background:#f8fafc;}}
  code{{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:12px;}}
  .badge{{display:inline-block;padding:3px 12px;border-radius:99px;font-size:12px;font-weight:700;}}
  .green{{background:#dcfce7;color:#16a34a;}}.blue{{background:#eff6ff;color:#2563eb;}}
  .preview{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-top:8px;}}
</style></head>
<body>
  <h1>Five Seconds Hack</h1>
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
    <span class="badge green">● Running</span>
    <form method="POST" action="/force" style="margin:0;">
      <button type="submit" style="background:#4f46e5;color:#fff;border:none;padding:4px 16px;border-radius:99px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:0.3px;">&#9889; Force Send Now</button>
    </form>
  </div>
  <h2>Scheduler</h2>
  <table>
    <tr><td>Next execution</td><td><span class="badge blue">{html_lib.escape(next_exec_fmt)}</span></td></tr>
    <tr><td>Issues in history</td><td>{len(history)}</td></tr>
  </table>
  <h2>Last Notification Sent</h2>
  <table>{last_sent_rows}</table>
  {preview_section}
</body></html>"""

        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client closed the connection before the response was fully sent

    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_POST(self):
        if self.path == "/force":
            try:
                # Inherit container stdout/stderr so output appears in docker logs
                subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "--force"]
                )
            except Exception as e:
                log_error(f"Force trigger from status endpoint failed: {e}")
        # Always redirect back to the status page
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def run_status_server():
    """Starts the HTTP status server (blocking). Run with --serve flag."""
    port = int(os.getenv("STATUS_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    print(f"📊 Status server running at http://localhost:{port}  (Ctrl+C to stop)")
    server.serve_forever()


# --- MAIN EXECUTION FLOW ---

def run_bot(force_execution=False):
    """
    Executes one full notification cycle.
    Returns True on successful send, False on no-issue/failure, None if skipped (too early).
    """
    state = load_state()
    if "next_execution" not in state:
        state["next_execution"] = datetime.now().isoformat()
    if "history" not in state:
        state["history"] = []

    next_dt = datetime.fromisoformat(state["next_execution"])
    print(f"🤖 Five Seconds Hack | Next scheduled execution: {next_dt.strftime('%Y-%m-%d %H:%M:%S')} local time")

    if not force_execution and datetime.now() < next_dt:
        return None  # not yet time

    # --force bypasses history deduplication; the lookback time window still applies.
    candidates_history = [] if force_execution else state["history"]
    lookback_hours = int(os.getenv("ISSUE_LOOKBACK_HOURS", "72"))
    time_filter = (
        datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S+0000")

    print(f"{'⚠️  [FORCED TEST - history bypassed, time window still active]' if force_execution else '🔄 [CRON CYCLE]'} Fetching and selecting issue from SonarCloud...")

    # Accumulates keys that were skipped due to inaccessible source code within this run.
    skipped_keys = []
    issue = None
    source_line = None
    issue_key = component_path = sonar_message = rule_id = issue_created_at = None
    line_number = 0

    while True:
        issue = fetch_and_select_sonar_issue(candidates_history + skipped_keys, created_after=time_filter)

        if not issue:
            break

        issue_key = issue.get("key")
        component_path = issue.get("component", "Unknown File")
        line_number = issue.get("line", 0)
        sonar_message = issue.get("message", "No message provided.")
        rule_id = issue.get("rule", "Unknown Rule")
        raw_creation_date = issue.get("creationDate", "")
        try:
            issue_dt = datetime.fromisoformat(raw_creation_date.replace("+0000", "+00:00"))
            issue_created_at = relative_time(issue_dt)
        except (ValueError, AttributeError):
            issue_created_at = raw_creation_date

        # Real source code extraction (local first, SonarCloud API as fallback)
        print(f"Reading real source code for {component_path} at line {line_number}...")
        source_line = get_local_source_code(component_path, line_number)

        if not source_line:
            print("Local file not found, fetching source from SonarCloud API...")
            source_line = fetch_source_from_sonar(component_path, line_number)

        if not source_line:
            print(f"⚠️ Could not retrieve source code for {component_path}:{line_number}. Trying next issue...")
            log_error(f"Could not retrieve source code for {component_path}:{line_number}. Skipping to next candidate.")
            skipped_keys.append(issue_key)
            continue

        break  # Valid issue with accessible source code found

    if not issue or not source_line:
        if force_execution:
            print("❌ Integration error: No open Code Smells were found in your SonarCloud project.")
            return False

        now = datetime.now()
        next_check = now + timedelta(hours=1)
        state["next_execution"] = next_check.isoformat()
        save_state(state)
        msg = f"No new issues found in the last {lookback_hours}h window. Next scan scheduled for {next_check.strftime('%Y-%m-%d %H:%M:%S')}."
        print(f"✅ {msg}")
        no_issues_html = f'''
<div style="font-family:'Segoe UI',-apple-system,sans-serif;max-width:600px;border:1px solid #e1e4e6;border-radius:12px;background-color:#ffffff;overflow:hidden;margin:10px auto;">
    <div style="background-color:#4f46e5;background-image:linear-gradient(90deg,#4f46e5 0%,#06b6d4 100%);padding:16px 20px;color:#ffffff;">
        <span style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;opacity:.85;">Five Seconds Hack</span>
        <h2 style="margin:4px 0 0 0;font-size:20px;font-weight:600;color:#ffffff;">✅ No New Code Smells Detected</h2>
    </div>
    <div style="padding:20px;">
        <p style="font-size:15px;line-height:1.6;color:#334155;">{html_lib.escape(msg)}</p>
    </div>
</div>'''
        create_graph_calendar_event("✅ Five Seconds Hack: No new issues", no_issues_html, attendees_override=[])
        return False

    history = state["history"]

    # Live generative AI pass
    print("Consulting AI Model for dynamic explanation and code refactoring...")
    llm_response = ask_llm_for_refactor(rule_id, sonar_message, source_line, component_path, line_number)

    # --- HTML TEMPLATE ASSEMBLY ---
    raw_start = max(1, line_number - SOURCE_CONTEXT_LINES)

    code_smell_block = render_code_block(source_line, raw_start, highlight_lines={line_number}, accent_color="#ef4444")

    _orig_lines = source_line.split("\n")
    _suggested_raw = llm_response.get('suggested_code', '')
    _suggested_str = "\n".join(_suggested_raw) if isinstance(_suggested_raw, list) else _suggested_raw
    _new_lines = _suggested_str.split("\n")
    if _new_lines == _orig_lines:
        log_error(f"LLM returned suggested_code identical to source_line for rule {rule_id} at line {line_number} — fix was NOT applied.")
    _changed = {raw_start + i for i, (o, n) in enumerate(zip(_orig_lines, _new_lines)) if o.rstrip() != n.rstrip()}
    for _i in range(len(_orig_lines), len(_new_lines)):
        _changed.add(raw_start + _i)
    suggested_block = render_code_block(
        _suggested_str,
        raw_start,
        highlight_lines=_changed if _changed else None,
        accent_color="#10b981")

    html_template = f'''
<div style="font-family: 'Segoe UI', -apple-system, sans-serif; max-width: 600px; border: 1px solid #e1e4e6; border-radius: 12px; background-color: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden; margin: 10px auto;">
    <div style="background-color: #4f46e5; background-image: linear-gradient(90deg, #4f46e5 0%, #06b6d4 100%); padding: 16px 20px; color: #ffffff;">
        <span style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">Five Seconds Hack</span>
        <h2 style="margin: 4px 0 0 0; font-size: 20px; font-weight: 600; color: #ffffff;">{llm_response.get('title', '🚨 Code Quality Alert')}</h2>
    </div>
    <div style="padding: 20px;">
        <div style="margin-bottom: 16px; font-size: 13px; color: #64748b;">
            <span style="background-color: #f1f5f9; padding: 4px 8px; border-radius: 6px; font-family: monospace; font-weight: 600; color: #334155;">📂 {component_path} (Line {line_number})</span>
            <div style="margin-top: 6px; font-size: 11px; color: #94a3b8; letter-spacing: 0.3px;">🕐 Reported on {issue_created_at}</div>
        </div>
        <p style="font-size: 15px; line-height: 1.6; color: #334155; margin: 0 0 20px 0;">{llm_response.get('explanation', '')}</p>
        <div style="margin-top: 15px;">
            <div style="margin-bottom: 12px;">
                <div style="font-size: 11px; font-weight: 700; color: #ef4444; margin-bottom: 4px; text-transform: uppercase;">❌ Real Code Smell ({rule_id})</div>
                {code_smell_block}
                <div style="font-size: 11px; color: #64748b; margin-top: 4px; font-style: italic;">Sugerencia de Sonar: {llm_response.get('sonar_message_es', sonar_message)}</div>
            </div>
            <div>
                <div style="font-size: 11px; font-weight: 700; color: #10b981; margin-bottom: 4px; text-transform: uppercase;">✅ 5-Second Refactor</div>
                {suggested_block}
            </div>
        </div>
    </div>
</div>
'''

    clean_filename = component_path.split("/")[-1]
    alert_subject = f"{llm_response.get('title', '🚨 Code Alert')} -> {clean_filename} L{line_number}"

    # Dispatch the event via Microsoft Graph API
    success = create_graph_calendar_event(alert_subject, html_template)

    # Save debug file to tmp/ for every dispatched notification
    _debug_data = {
        "timestamp": datetime.now().isoformat(),
        "issue_key": issue_key,
        "rule_id": rule_id,
        "component_path": component_path,
        "line_number": line_number,
        "sonar_message": sonar_message,
        "source_line": source_line,
        "llm_response": llm_response,
        "changed_lines": sorted(_changed),
        "html": html_template,
    }
    _safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', issue_key)
    _debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    _debug_file = os.path.join(_debug_dir, f"five_seconds_hack_{_safe_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    try:
        os.makedirs(_debug_dir, exist_ok=True)
        with open(_debug_file, "w", encoding="utf-8") as _f:
            json.dump(_debug_data, _f, indent=2, ensure_ascii=False)
        print(f"📁 Debug file saved: {_debug_file}")
    except Exception as _e:
        log_error(f"Failed to save debug file: {_e}")

    # --- RE-SCHEDULING ALGORITHM ---
    # Persist the scheduling state ONLY if the run was not forced
    if not force_execution:
        now = datetime.now()
        if success:
            history.append(issue_key)
            state["last_sent"] = {
                "issue_key": issue_key,
                "component": component_path,
                "rule": rule_id,
                "title": llm_response.get("title", ""),
                "sent_at": now.isoformat(),
                "html": html_template
            }
            days_to_add = 3 if now.weekday() == 4 else (2 if now.weekday() == 5 else 1)
            next_slot = (now + timedelta(days=days_to_add)).replace(
                hour=random.randint(9, 17),
                minute=random.randint(0, 59),
                second=0
            )
        else:
            days_to_add = 3 if now.weekday() == 4 else (2 if now.weekday() == 5 else 1)
            next_slot = (now + timedelta(days=days_to_add)).replace(
                hour=random.randint(9, 17),
                minute=random.randint(0, 59),
                second=0
            )

        state["next_execution"] = next_slot.isoformat()
        state["history"] = history[-50:]
        save_state(state)
    else:
        if success:
            history.append(issue_key)
            state["last_sent"] = {
                "issue_key": issue_key,
                "component": component_path,
                "rule": rule_id,
                "title": llm_response.get("title", ""),
                "sent_at": datetime.now().isoformat(),
                "html": html_template
            }
            state["history"] = history[-50:]
            save_state(state)
        print("Forced test run completed successfully. Internal timers left unchanged.")

    return success


def main():
    """Entry point: handles CLI flags and delegates to run_bot or run_status_server."""
    force_execution = "--force" in sys.argv
    serve_mode = "--serve" in sys.argv

    if serve_mode:
        state = load_state()
        state.setdefault("next_execution", datetime.now().isoformat())
        state.setdefault("history", [])
        next_dt = datetime.fromisoformat(state["next_execution"])
        print(f"🤖 Five Seconds Hack | Next scheduled execution: {next_dt.strftime('%Y-%m-%d %H:%M:%S')} local time")
        run_status_server()
        sys.exit(0)

    result = run_bot(force_execution)
    if result is None:
        sys.exit(0)


if __name__ == "__main__":
    main()

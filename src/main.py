import html as html_lib
import json
import os
import random
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template

# Allow running as `python src/main.py` directly (adds project root to sys.path)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import SOURCE_CONTEXT_LINES
from src.graph import create_graph_calendar_event
from src.llm import ask_llm_for_refactor
from src.render import relative_time, render_code_block
from src.server import run_status_server
from src.sonar import fetch_and_select_sonar_issue, fetch_source_from_sonar
from src.state import load_state, log_error, save_state

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_EMAIL_ALERT = Template((_TEMPLATE_DIR / "email_alert.html").read_text(encoding="utf-8"))
_EMAIL_NO_ISSUES = Template((_TEMPLATE_DIR / "email_no_issues.html").read_text(encoding="utf-8"))


def get_local_source_code(component_path, line_number):
    """
    Attempts to extract the real source code line directly from the local file system.
    """
    clean_path = component_path.split(":")[-1] if ":" in component_path else component_path
    possible_paths = [
        clean_path,
        os.path.join(os.getcwd(), clean_path),
        os.path.join(os.path.dirname(__file__), clean_path)
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
                    if 0 < line_number <= len(lines):
                        start = max(0, line_number - 1 - SOURCE_CONTEXT_LINES)
                        end = min(len(lines), line_number + SOURCE_CONTEXT_LINES)
                        return "".join(lines[start:end]).rstrip()
            except Exception as e:
                log_error(f"Error reading local file {path}: {e}")
    return None


def _compute_days_to_add(weekday):
    """Returns days to add to skip over the weekend (Fri→Mon = 3, Sat→Mon = 2, else 1)."""
    if weekday == 4:
        return 3
    if weekday == 5:
        return 2
    return 1


def _fetch_issue_with_source(candidates_history, time_filter):
    """
    Polls SonarCloud until an issue with accessible source code is found.
    Returns (issue, source_line) or (None, None) if none found.
    """
    skipped_keys = []
    while True:
        issue = fetch_and_select_sonar_issue(candidates_history + skipped_keys, created_after=time_filter)
        if not issue:
            return None, None

        issue_key = issue.get("key")
        component_path = issue.get("component", "Unknown File")
        line_number = issue.get("line", 0)

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

        return issue, source_line


def _handle_no_issues(state, force_execution, lookback_hours):
    """Handles the case when no actionable issue is found in the current cycle."""
    if force_execution:
        print("❌ Integration error: No open Code Smells were found in your SonarCloud project.")
        return

    now = datetime.now()
    next_check = now + timedelta(hours=1)
    state["next_execution"] = next_check.isoformat()
    save_state(state)
    msg = f"No new issues found in the last {lookback_hours}h window. Next scan scheduled for {next_check.strftime('%Y-%m-%d %H:%M:%S')}."
    print(f"✅ {msg}")
    no_issues_html = _EMAIL_NO_ISSUES.safe_substitute(msg=html_lib.escape(msg))
    create_graph_calendar_event("✅ Five Seconds Hack: No new issues", no_issues_html, attendees_override=[])


def _save_debug_file(issue_key, rule_id, component_path, line_number, sonar_message,
                     source_line, llm_response, html_template, changed_lines):
    """Persists a JSON debug dump of the current run to tmp/."""
    debug_data = {
        "timestamp": datetime.now().isoformat(),
        "issue_key": issue_key,
        "rule_id": rule_id,
        "component_path": component_path,
        "line_number": line_number,
        "sonar_message": sonar_message,
        "source_line": source_line,
        "llm_response": llm_response,
        "changed_lines": sorted(changed_lines),
        "html": html_template,
    }
    safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', issue_key)
    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    debug_file = os.path.join(debug_dir, f"five_seconds_hack_{safe_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    try:
        os.makedirs(debug_dir, exist_ok=True)
        with open(debug_file, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
        print(f"📁 Debug file saved: {debug_file}")
    except Exception as e:
        log_error(f"Failed to save debug file: {e}")


def _update_state_after_send(state, success, force_execution, issue_key,
                              component_path, rule_id, llm_response, html_template, history):
    """Persists state and reschedules the next run after a notification dispatch."""
    if force_execution:
        if success:
            history.append(issue_key)
            state["last_sent"] = {
                "issue_key": issue_key,
                "component": component_path,
                "rule": rule_id,
                "title": llm_response.get("title", ""),
                "sent_at": datetime.now().isoformat(),
                "html": html_template,
            }
            state["history"] = history[-50:]
            save_state(state)
        print("Forced test run completed successfully. Internal timers left unchanged.")
        return

    now = datetime.now()
    days_to_add = _compute_days_to_add(now.weekday())
    next_slot = (now + timedelta(days=days_to_add)).replace(
        hour=random.randint(9, 17),
        minute=random.randint(0, 59),
        second=0,
    )
    if success:
        history.append(issue_key)
        state["last_sent"] = {
            "issue_key": issue_key,
            "component": component_path,
            "rule": rule_id,
            "title": llm_response.get("title", ""),
            "sent_at": now.isoformat(),
            "html": html_template,
        }
    state["next_execution"] = next_slot.isoformat()
    state["history"] = history[-50:]
    save_state(state)


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
        datetime.now(UTC) - timedelta(hours=lookback_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S+0000")

    print(f"{'⚠️  [FORCED TEST - history bypassed, time window still active]' if force_execution else '🔄 [CRON CYCLE]'} Fetching and selecting issue from SonarCloud...")

    issue, source_line = _fetch_issue_with_source(candidates_history, time_filter)

    if not issue or not source_line:
        _handle_no_issues(state, force_execution, lookback_hours)
        return False

    history = state["history"]
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
    _changed = {raw_start + i for i, (o, n) in enumerate(zip(_orig_lines, _new_lines, strict=False)) if o.rstrip() != n.rstrip()}
    for _i in range(len(_orig_lines), len(_new_lines)):
        _changed.add(raw_start + _i)
    suggested_block = render_code_block(
        _suggested_str,
        raw_start,
        highlight_lines=_changed if _changed else None,
        accent_color="#10b981")

    html_template = _EMAIL_ALERT.safe_substitute(
        title=llm_response.get('title', '🚨 Code Quality Alert'),
        component_path=component_path,
        line_number=line_number,
        issue_created_at=issue_created_at,
        explanation=llm_response.get('explanation', ''),
        rule_id=rule_id,
        code_smell_block=code_smell_block,
        sonar_message_es=llm_response.get('sonar_message_es', sonar_message),
        suggested_block=suggested_block,
    )

    clean_filename = component_path.split("/")[-1]
    alert_subject = f"{llm_response.get('title', '🚨 Code Alert')} -> {clean_filename} L{line_number}"

    # Dispatch the event via Microsoft Graph API
    success = create_graph_calendar_event(alert_subject, html_template)

    _save_debug_file(
        issue_key, rule_id, component_path, line_number, sonar_message,
        source_line, llm_response, html_template, _changed,
    )

    _update_state_after_send(
        state, success, force_execution, issue_key,
        component_path, rule_id, llm_response, html_template, history,
    )

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

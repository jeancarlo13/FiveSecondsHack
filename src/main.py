"""Orchestrator and entry point for the Five Seconds Hack bot.

This module ties all subsystems together and exposes two public functions:
  - ``run_bot``  — executes one full notification cycle.
  - ``main``     — CLI entry point; handles ``--serve``, ``--force``, and cron mode.

Module-level helpers (prefixed with ``_``) are intentionally kept private; they
exist solely to decompose ``run_bot`` into readable, testable units.
"""

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

from src.config import ALERT_MODE, ISSUE_ONLY_FROM_INVITED, SOURCE_CONTEXT_LINES, TEAM_FALLBACK_ENABLED, TEAMS_FILE
from src.graph import create_graph_calendar_event
from src.llm import ask_llm_for_refactor
from src.render import relative_time, render_code_block
from src.server import run_status_server
from src.sonar import fetch_and_select_sonar_issue, fetch_source_from_sonar
from src.state import load_state, log_error, log_info, save_state

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_EMAIL_ALERT = Template((_TEMPLATE_DIR / "email_alert.html").read_text(encoding="utf-8"))
_EMAIL_NO_ISSUES = Template((_TEMPLATE_DIR / "email_no_issues.html").read_text(encoding="utf-8"))


def get_local_source_code(component_path, line_number):
    """Read a source-code window around the flagged line from the local filesystem.

    Tries up to three candidate paths derived from ``component_path``:
    the raw path, the path relative to cwd, and the path relative to this file.
    Returns ``None`` if the file cannot be found or read, so the caller can fall
    back to the SonarCloud API.

    Args:
        component_path: SonarCloud component key or file path, possibly prefixed
                        with a project key (e.g. ``"myorg:src/app.py"``).
        line_number:    1-based line number of the flagged issue.

    Returns:
        Multiline string containing ``SOURCE_CONTEXT_LINES`` lines above and
        below ``line_number``, or ``None`` on failure.
    """
    clean_path = component_path.split(":")[-1] if ":" in component_path else component_path
    possible_paths = [
        clean_path,
        os.path.join(os.getcwd(), clean_path),
        os.path.join(os.path.dirname(__file__), clean_path),
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
    """Return the number of calendar days to add to reach the next business day.

    Ensures notifications are always scheduled on weekdays:
      - Friday (4)  → add 3 days (lands on Monday)
      - Saturday (5) → add 2 days (lands on Monday)
      - Any other day → add 1 day

    Args:
        weekday: Integer weekday from ``datetime.weekday()`` (Monday=0, Sunday=6).

    Returns:
        Integer number of days to add (1, 2, or 3).
    """
    if weekday == 4:
        return 3
    if weekday == 5:
        return 2
    return 1


def _fetch_issue_with_source(candidates_history, time_filter, allowed_authors=None):
    """Poll SonarCloud until an issue with accessible source code is found.

    Iterates through candidates from ``fetch_and_select_sonar_issue``, skipping
    issues whose source code cannot be retrieved (neither locally nor via API).
    The list of skipped keys grows per call to prevent infinite loops when all
    accessible issues are exhausted.

    Args:
        candidates_history: List of issue keys to exclude from selection (already
                            sent or skipped in previous cycles).
        time_filter:        ISO-8601 timestamp string passed as ``createdAfter``
                            to restrict the lookback window.

    Returns:
        Tuple ``(issue, source_line)`` where ``issue`` is the raw SonarCloud
        issue dict and ``source_line`` is the plain-text source snippet.
        Returns ``(None, None)`` when no eligible issue is found.
    """
    skipped_keys = []
    while True:
        issue = fetch_and_select_sonar_issue(
            candidates_history + skipped_keys,
            created_after=time_filter,
            allowed_authors=allowed_authors,
        )
        if not issue:
            return None, None

        issue_key = issue.get("key")
        component_path = issue.get("component", "Unknown File")
        line_number = issue.get("line", 0)

        log_info(f"Reading source code for {component_path} at line {line_number}...")
        source_line = get_local_source_code(component_path, line_number)

        if not source_line:
            log_info(f"Local file not found, fetching {component_path} from SonarCloud API...")
            source_line = fetch_source_from_sonar(component_path, line_number)

        if not source_line:
            log_info(f"⚠️ Could not retrieve source code for {component_path}:{line_number}. Trying next issue...")
            log_error(f"Could not retrieve source code for {component_path}:{line_number}. Skipping to next candidate.")
            skipped_keys.append(issue_key)
            continue

        return issue, source_line


def _handle_no_issues(state, force_execution, lookback_hours):
    """Handle the case when no actionable issue is found in the current cycle.

    In forced mode, prints an error and returns immediately without modifying
    state.  In normal mode, schedules a 1-hour retry, persists the state, and
    dispatches a "no new issues" calendar event.

    Args:
        state:            Mutable scheduler state dict (modified in-place for retry).
        force_execution:  ``True`` if the bot was started with ``--force``.
        lookback_hours:   Configured lookback window used in the status message.
    """
    if force_execution:
        log_info("❌ Integration error: No open Code Smells were found in your SonarCloud project.")
        return

    now = datetime.now()
    next_check = now + timedelta(hours=1)
    state["next_execution"] = next_check.isoformat()
    save_state(state)
    msg = f"No new issues found in the last {lookback_hours}h window. Next scan scheduled for {next_check.strftime('%Y-%m-%d %H:%M:%S')}."
    log_info(f"✅ {msg}")
    no_issues_html = _EMAIL_NO_ISSUES.safe_substitute(msg=html_lib.escape(msg))
    create_graph_calendar_event("✅ Five Seconds Hack: No new issues", no_issues_html, attendees_override=[])


def _save_debug_file(
    issue_key,
    rule_id,
    component_path,
    line_number,
    sonar_message,
    source_line,
    llm_response,
    html_template,
    changed_lines,
):
    """Persist a JSON debug dump of the current run to the ``src/tmp/`` directory.

    The file is named ``five_seconds_hack_<safe_key>_<timestamp>.json`` and
    contains all inputs and outputs of the run, including the rendered HTML.
    Write errors are logged but do not abort the notification pipeline.

    Args:
        issue_key:      SonarCloud issue key (used in the filename).
        rule_id:        SonarCloud rule identifier.
        component_path: Affected file path.
        line_number:    1-based flagged line number.
        sonar_message:  Raw issue message from SonarCloud.
        source_line:    Source code snippet used for the notification.
        llm_response:   Parsed JSON dict returned by the LLM.
        html_template:  Final rendered HTML of the calendar event body.
        changed_lines:  Set of 1-based line numbers that differ between
                        original and suggested code.
    """
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
    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", issue_key)
    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    debug_file = os.path.join(
        debug_dir, f"five_seconds_hack_{safe_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    try:
        os.makedirs(debug_dir, exist_ok=True)
        with open(debug_file, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
        log_info(f"📁 Debug file saved: {debug_file}")
    except Exception as e:
        log_error(f"Failed to save debug file: {e}")


def _update_state_after_send(
    state, success, force_execution, issue_key, component_path, rule_id, llm_response, html_template, history
):
    """Persist state and reschedule the next run after a notification has been dispatched.

    In forced mode, state is only updated if the event was sent successfully
    (history + last_sent), and the ``next_execution`` timestamp is left unchanged
    so the normal cron schedule is not affected.

    In normal mode, ``next_execution`` is always advanced to the next business
    day at a random time within work hours, regardless of whether the send
    succeeded, to prevent rapid retry loops.

    Args:
        state:            Mutable scheduler state dict.
        success:          ``True`` if the Graph API call succeeded.
        force_execution:  ``True`` when running in ``--force`` mode.
        issue_key:        SonarCloud issue key of the dispatched notification.
        component_path:   Affected file path.
        rule_id:          SonarCloud rule identifier.
        llm_response:     Parsed LLM response dict.
        html_template:    Final rendered HTML stored as a preview in state.
        history:          Mutable list of already-sent issue keys (updated in-place).
    """
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
        log_info("Forced test run completed successfully. Internal timers left unchanged.")
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


def _build_alert_payload(issue, source_line):
    """Assemble the LLM response, rendered HTML, and metadata for one issue.

    Args:
        issue:       Raw SonarCloud issue dict.
        source_line: Plain-text source snippet for the flagged line.

    Returns:
        Dict with keys: ``issue_key``, ``component_path``, ``rule_id``,
        ``line_number``, ``sonar_message``, ``html``, ``subject``,
        ``llm_response``, ``changed``.
    """
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

    log_info("Consulting AI Model for dynamic explanation and code refactoring...")
    llm_response = ask_llm_for_refactor(rule_id, sonar_message, source_line, component_path, line_number)

    raw_start = max(1, line_number - SOURCE_CONTEXT_LINES)
    code_smell_block = render_code_block(source_line, raw_start, highlight_lines={line_number}, accent_color="#ef4444")

    _orig_lines = source_line.split("\n")
    _suggested_raw = llm_response.get("suggested_code", "")
    _suggested_str = "\n".join(_suggested_raw) if isinstance(_suggested_raw, list) else _suggested_raw
    _new_lines = _suggested_str.split("\n")
    if _new_lines == _orig_lines:
        log_error(
            f"LLM returned suggested_code identical to source_line for rule {rule_id} at line {line_number} — fix was NOT applied."
        )
    changed = {
        raw_start + i for i, (o, n) in enumerate(zip(_orig_lines, _new_lines, strict=False)) if o.rstrip() != n.rstrip()
    }
    for _i in range(len(_orig_lines), len(_new_lines)):
        changed.add(raw_start + _i)
    suggested_block = render_code_block(
        _suggested_str,
        raw_start,
        highlight_lines=changed if changed else None,
        accent_color="#10b981",
    )

    html_template = _EMAIL_ALERT.safe_substitute(
        title=llm_response.get("title", "🚨 Code Quality Alert"),
        component_path=component_path,
        line_number=line_number,
        issue_created_at=issue_created_at,
        explanation=llm_response.get("explanation", ""),
        rule_id=rule_id,
        code_smell_block=code_smell_block,
        sonar_message_es=llm_response.get("sonar_message_es", sonar_message),
        suggested_block=suggested_block,
    )

    clean_filename = component_path.split("/")[-1]
    subject = f"{llm_response.get('title', '🚨 Code Alert')} -> {clean_filename} L{line_number}"

    return {
        "issue_key": issue_key,
        "component_path": component_path,
        "rule_id": rule_id,
        "line_number": line_number,
        "sonar_message": sonar_message,
        "html": html_template,
        "subject": subject,
        "llm_response": llm_response,
        "changed": changed,
    }


def _load_teams():
    """Load the team hierarchy from ``TEAMS_FILE`` (default: ``data/teams.json``).

    The file format is a JSON object mapping a leader email to a list of member
    emails, supporting arbitrarily deep trees through nested entries::

        {
          "lead@company.com": ["mid@company.com"],
          "mid@company.com":  ["dev1@company.com", "dev2@company.com"]
        }

    Returns:
        Parsed dict, or an empty dict if the file is missing or unreadable.
    """
    try:
        with open(TEAMS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log_error(f"Failed to load teams config from {TEAMS_FILE}: {e}")
        return {}


def _make_attendee(email):
    """Build a Microsoft Graph API attendee object from an email address.

    The display name is derived from the local part of the email by replacing
    dots with spaces and title-casing the result (e.g. ``john.doe@x.com`` →
    ``"John Doe"``).

    Args:
        email: Recipient email address string.

    Returns:
        Dict with ``emailAddress`` and ``type`` keys as expected by the
        Graph ``/v1.0/me/events`` endpoint.
    """
    return {
        "emailAddress": {
            "address": email,
            "name": email.split("@")[0].replace(".", " ").title(),
        },
        "type": "required",
    }


def _find_fallback_leader(recipient, member_to_leader, resolved, visited=None):
    """Walk up the team tree to find the nearest ancestor that has a resolved issue.

    Traverses the ``member_to_leader`` map recursively.  A ``visited`` set
    prevents infinite loops caused by cycles in the config.

    Args:
        recipient:       Email of the member who has no own issue.
        member_to_leader: Flat dict mapping each member email to its direct leader
                          email (inverted from the ``teams.json`` structure).
        resolved:        Dict of emails that already have an assigned issue
                         (keys are the issue owners).
        visited:         Set of emails visited in the current recursion path;
                         created automatically on the first call.

    Returns:
        Email of the closest ancestor present in ``resolved``, or ``None`` if no
        such ancestor exists or a cycle is detected.
    """
    if visited is None:
        visited = set()
    if recipient in visited:
        return None
    visited.add(recipient)
    leader = member_to_leader.get(recipient)
    if not leader:
        return None
    if leader in resolved:
        return leader
    return _find_fallback_leader(leader, member_to_leader, resolved, visited)


def _resolve_issues_per_recipient(recipients, candidates_history, time_filter):
    """Fetch one distinct SonarCloud issue per recipient (first pass).

    Args:
        recipients:         Ordered list of recipient email strings.
        candidates_history: Issue keys to exclude (already sent or skipped).
        time_filter:        ISO-8601 ``createdAfter`` timestamp string.

    Returns:
        Tuple ``(resolved, used_keys)`` where ``resolved`` maps each recipient
        email to ``(issue, source_line)`` and ``used_keys`` lists the consumed
        issue keys in order.
    """
    resolved = {}
    used_keys = []
    for recipient in recipients:
        allowed_authors = [recipient] if ISSUE_ONLY_FROM_INVITED else None
        issue, source_line = _fetch_issue_with_source(candidates_history + used_keys, time_filter, allowed_authors)
        if issue and source_line:
            resolved[recipient] = (issue, source_line)
            used_keys.append(issue.get("key"))
    return resolved, used_keys


def _build_event_groups(recipients, resolved, member_to_leader):
    """Map each recipient to a calendar-event owner and build attendee lists.

    Recipients with a resolved issue own their own event.  Recipients without
    one are attached to their nearest resolved ancestor via
    ``_find_fallback_leader``; if no ancestor exists they are skipped with a
    warning.

    Args:
        recipients:       Ordered list of recipient email strings.
        resolved:         Dict mapping recipient email → ``(issue, source_line)``.
        member_to_leader: Flat dict mapping member email → direct leader email.

    Returns:
        Dict mapping issue-owner email → list of attendee emails (owner first).
    """
    event_groups: dict = {}
    for recipient in recipients:
        if recipient in resolved:
            event_groups.setdefault(recipient, [recipient])
        else:
            ancestor = _find_fallback_leader(recipient, member_to_leader, resolved)
            if ancestor:
                event_groups.setdefault(ancestor, [ancestor])
                event_groups[ancestor].append(recipient)
            else:
                log_info(f"⚠️ No unique issue available for {recipient}, skipping.")
    return event_groups


def _dispatch_event_group(issue_owner, attendees, resolved):
    """Build payload and create one calendar event for an issue owner and attendees.

    Args:
        issue_owner: Email of the recipient whose resolved issue drives the event.
        attendees:   List of email strings to invite (owner always first).
        resolved:    Dict mapping recipient email → ``(issue, source_line)``.

    Returns:
        Tuple ``(success, payload)`` where ``success`` is the Graph API bool
        result and ``payload`` is the dict from ``_build_alert_payload``.
    """
    issue, source_line = resolved[issue_owner]
    payload = _build_alert_payload(issue, source_line)
    success = create_graph_calendar_event(
        payload["subject"],
        payload["html"],
        attendees_override=[_make_attendee(a) for a in attendees],
    )
    _save_debug_file(
        payload["issue_key"],
        payload["rule_id"],
        payload["component_path"],
        payload["line_number"],
        payload["sonar_message"],
        source_line,
        payload["llm_response"],
        payload["html"],
        payload["changed"],
    )
    return success, payload


def _save_individual_state(state, history, used_keys, any_success, last_payload, force_execution):
    """Persist scheduler state after individual-mode dispatch.

    In forced mode, updates history and ``last_sent`` only on success then
    returns without changing ``next_execution``.  In normal mode always
    advances ``next_execution`` to a random slot on the next business day.

    Args:
        state:           Mutable scheduler state dict.
        history:         Mutable list of already-sent issue keys.
        used_keys:       Issue keys consumed this cycle (appended on success).
        any_success:     ``True`` if at least one event was dispatched.
        last_payload:    Payload dict of the last successful dispatch, or ``None``.
        force_execution: ``True`` when running with ``--force``.

    Returns:
        ``any_success`` unchanged.
    """
    if any_success and last_payload:
        history.extend(used_keys)
        state["last_sent"] = {
            "issue_key": last_payload["issue_key"],
            "component": last_payload["component_path"],
            "rule": last_payload["rule_id"],
            "title": last_payload["llm_response"].get("title", ""),
            "sent_at": datetime.now().isoformat(),
            "html": last_payload["html"],
        }
        if force_execution:
            state["history"] = history[-50:]
            save_state(state)

    if force_execution:
        log_info("Forced test run completed successfully. Internal timers left unchanged.")
        return any_success

    now = datetime.now()
    next_slot = (now + timedelta(days=_compute_days_to_add(now.weekday()))).replace(
        hour=random.randint(9, 17),
        minute=random.randint(0, 59),
        second=0,
    )
    state["next_execution"] = next_slot.isoformat()
    state["history"] = history[-50:]
    save_state(state)
    return any_success


def _run_individual_mode(state, force_execution, candidates_history, time_filter, lookback_hours):
    """Execute one notification cycle in individual mode.

    Fetches a distinct SonarCloud issue per recipient and dispatches a separate
    calendar event to each one.  When ``TEAM_FALLBACK_ENABLED`` is set,
    recipients without their own issue are grouped into their nearest ancestor's
    event instead of being skipped.

    Args:
        state:              Mutable scheduler state dict.
        force_execution:    ``True`` when running with ``--force``.
        candidates_history: Issue keys already excluded from selection.
        time_filter:        ISO-8601 ``createdAfter`` timestamp string.
        lookback_hours:     Configured lookback window (used in no-issue message).

    Returns:
        ``True`` if at least one event was dispatched successfully, ``False``
        otherwise.
    """
    recipients = [r.strip() for r in os.getenv("ALERT_RECIPIENTS", "").split(",") if r.strip()]
    if not recipients:
        _handle_no_issues(state, force_execution, lookback_hours)
        return False

    resolved, used_keys = _resolve_issues_per_recipient(recipients, candidates_history, time_filter)

    member_to_leader = {}
    if TEAM_FALLBACK_ENABLED:
        teams = _load_teams()
        member_to_leader = {m: leader for leader, members in teams.items() for m in members}

    event_groups = _build_event_groups(recipients, resolved, member_to_leader)
    if not event_groups:
        _handle_no_issues(state, force_execution, lookback_hours)
        return False

    results = [_dispatch_event_group(owner, attendees, resolved) for owner, attendees in event_groups.items()]
    any_success = any(s for s, _ in results)
    last_payload = next((p for s, p in reversed(results) if s), None)

    return _save_individual_state(state, state["history"], used_keys, any_success, last_payload, force_execution)


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
    log_info(f"🤖 Five Seconds Hack | Next scheduled execution: {next_dt.strftime('%Y-%m-%d %H:%M:%S')} local time")

    if not force_execution and datetime.now() < next_dt:
        return None  # not yet time

    # --force bypasses history deduplication; the lookback time window still applies.
    candidates_history = [] if force_execution else state["history"]
    lookback_hours = int(os.getenv("ISSUE_LOOKBACK_HOURS", "72"))
    time_filter = (datetime.now(UTC) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    log_info(
        f"{'⚠️  [FORCED TEST - history bypassed, time window still active]' if force_execution else '🔄 [CRON CYCLE]'} Fetching and selecting issue from SonarCloud..."
    )

    if ALERT_MODE == "individual":
        return _run_individual_mode(state, force_execution, candidates_history, time_filter, lookback_hours)

    allowed_authors = None
    if ISSUE_ONLY_FROM_INVITED:
        recipients = [r.strip() for r in os.getenv("ALERT_RECIPIENTS", "").split(",") if r.strip()]
        allowed_authors = recipients

    issue, source_line = _fetch_issue_with_source(candidates_history, time_filter, allowed_authors)

    if not issue or not source_line:
        _handle_no_issues(state, force_execution, lookback_hours)
        return False

    history = state["history"]

    payload = _build_alert_payload(issue, source_line)

    # Dispatch the event via Microsoft Graph API
    success = create_graph_calendar_event(payload["subject"], payload["html"])

    _save_debug_file(
        payload["issue_key"],
        payload["rule_id"],
        payload["component_path"],
        payload["line_number"],
        payload["sonar_message"],
        source_line,
        payload["llm_response"],
        payload["html"],
        payload["changed"],
    )

    _update_state_after_send(
        state,
        success,
        force_execution,
        payload["issue_key"],
        payload["component_path"],
        payload["rule_id"],
        payload["llm_response"],
        payload["html"],
        history,
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
        log_info(f"🤖 Five Seconds Hack | Next scheduled execution: {next_dt.strftime('%Y-%m-%d %H:%M:%S')} local time")
        run_status_server()
        sys.exit(0)

    result = run_bot(force_execution)
    if result is None:
        sys.exit(0)


if __name__ == "__main__":
    main()

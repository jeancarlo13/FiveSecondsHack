"""
Tests for src/main.py — targeting ≥95% coverage.
All external calls (requests, OpenAI, file I/O, subprocess) are mocked.
"""
import os
import sys
import json
import io

# ── Set required env vars BEFORE importing the module ──────────────────────────
os.environ.update({
    "OPENAI_API_KEY": "test-key",
    "SONAR_HOST_URL": "https://sonarcloud.io",
    "SONAR_TOKEN": "test-sonar-token",
    "SONAR_ORGANIZATION": "test-org",
    "AZURE_TENANT_ID": "test-tenant",
    "AZURE_CLIENT_ID": "test-client-id",
    "AZURE_CLIENT_SECRET": "test-secret",
    "SHAREPOINT_USERNAME": "test@example.com",
    "WORK_TIMEZONE": "UTC",
    "WORK_DAY_START": "09:00",
    "WORK_DAY_END": "18:00",
    "ALERT_RECIPIENTS": "dev1@example.com,dev2@example.com,",
    "STATUS_PORT": "8080",
    "ISSUE_LOOKBACK_HOURS": "72",
    "OPENAI_MODEL": "gpt-4o-mini",
})

# Prevent main() from auto-executing on import
sys.argv = ["main.py"]

import pytest
import html as html_lib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, mock_open, call
from zoneinfo import ZoneInfo

import src.main as m
from src.main import (
    render_code_block, clean_sonar_source, relative_time, log_error,
    load_state, save_state, fetch_and_select_sonar_issue,
    get_local_source_code, fetch_source_from_sonar, ask_llm_for_refactor,
    get_graph_access_token, create_graph_calendar_event,
    StatusHandler, run_status_server, run_bot, main,
    SEVERITY_WEIGHTS, SOURCE_CONTEXT_LINES,
)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _make_handler(path="/", method="GET"):
    """Create a StatusHandler with all socket/HTTP methods mocked."""
    handler = StatusHandler.__new__(StatusHandler)
    handler.wfile = MagicMock()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.path = path
    handler.command = method
    handler.client_address = ("127.0.0.1", 9999)
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    return handler


_FULL_STATE = json.dumps({
    "next_execution": "2099-06-01T10:00:00",
    "history": ["issue1", "issue2"],
    "last_sent": {
        "issue_key": "PROJ:src/File.cs",
        "title": "🚨 Test Alert",
        "component": "PROJ:src/File.cs",
        "rule": "csharpsquid:S1234",
        "sent_at": "2026-05-25T14:00:00",
        "html": "<p>Preview HTML</p>",
    },
})

_EMPTY_STATE = json.dumps({})

_SAMPLE_ISSUE = {
    "key": "PROJ:src/File.cs",
    "component": "PROJ:src/File.cs",
    "line": 10,
    "message": "Unused variable 'x'",
    "rule": "csharpsquid:S1481",
    "severity": "MAJOR",
    "creationDate": "2026-05-01T12:00:00+0000",
}


# ══════════════════════════════════════════════════════════════════════════════
# render_code_block
# ══════════════════════════════════════════════════════════════════════════════

class TestRenderCodeBlock:
    def test_single_line_no_highlight(self):
        result = render_code_block("int x = 1;", 5)
        assert "int x = 1;" in result
        assert "<table" in result
        assert "5" in result

    def test_highlight_line_uses_accent(self):
        result = render_code_block("int x = 1;", 5, highlight_lines={5}, accent_color="#ff0000")
        assert "#ff0000" in result
        assert "&#9658;" in result

    def test_non_highlighted_line_in_set(self):
        result = render_code_block("a\nb", 1, highlight_lines={2})
        # Line 1 should NOT be highlighted, line 2 should
        assert "#475569" in result   # normal line num color
        assert "&#9658;" in result   # flagged marker present for line 2

    def test_leading_spaces_become_nbsp(self):
        result = render_code_block("    indented", 1)
        assert "&nbsp;" in result

    def test_tab_indent_expanded(self):
        result = render_code_block("\tcode", 1)
        assert "&nbsp;" in result

    def test_multiline(self):
        code = "line1\nline2\nline3"
        result = render_code_block(code, 10)
        assert "line1" in result
        assert "line3" in result
        assert "12" in result  # last line number

    def test_html_chars_escaped(self):
        result = render_code_block("<script>alert(1)</script>", 1)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_default_accent_color(self):
        result = render_code_block("x", 1, highlight_lines={1})
        assert "#ef4444" in result

    def test_empty_highlight_lines_set(self):
        result = render_code_block("code", 1, highlight_lines=set())
        assert "&#9658;" not in result


# ══════════════════════════════════════════════════════════════════════════════
# clean_sonar_source
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanSonarSource:
    def test_strips_html_tags(self):
        assert clean_sonar_source("<span>code</span>") == "code"

    def test_decodes_entities(self):
        assert clean_sonar_source("&lt;div&gt;") == "<div>"

    def test_plain_string_unchanged(self):
        assert clean_sonar_source("plain text") == "plain text"

    def test_mixed_tags_and_entities(self):
        result = clean_sonar_source('<b style="color:red">int&nbsp;x</b>')
        assert "<b" not in result
        assert "int" in result


# ══════════════════════════════════════════════════════════════════════════════
# relative_time
# ══════════════════════════════════════════════════════════════════════════════

class TestRelativeTime:
    def _dt(self, seconds_ago):
        return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)

    def test_just_now(self):
        assert relative_time(self._dt(30)) == "just now"

    def test_one_minute(self):
        assert relative_time(self._dt(90)) == "1 minute ago"

    def test_plural_minutes(self):
        assert relative_time(self._dt(180)) == "3 minutes ago"

    def test_one_hour(self):
        assert relative_time(self._dt(3700)) == "1 hour ago"

    def test_plural_hours(self):
        assert relative_time(self._dt(7400)) == "2 hours ago"

    def test_one_day(self):
        assert relative_time(self._dt(86500)) == "1 day ago"

    def test_plural_days(self):
        assert relative_time(self._dt(86400 * 3)) == "3 days ago"

    def test_one_week(self):
        assert relative_time(self._dt(86400 * 8)) == "1 week ago"

    def test_plural_weeks(self):
        assert relative_time(self._dt(86400 * 15)) == "2 weeks ago"

    def test_one_month(self):
        assert relative_time(self._dt(86400 * 32)) == "1 month ago"

    def test_plural_months(self):
        assert relative_time(self._dt(86400 * 65)) == "2 months ago"

    def test_one_year(self):
        assert relative_time(self._dt(86400 * 366)) == "1 year ago"

    def test_plural_years(self):
        assert relative_time(self._dt(86400 * 800)) == "2 years ago"


# ══════════════════════════════════════════════════════════════════════════════
# log_error
# ══════════════════════════════════════════════════════════════════════════════

class TestLogError:
    def test_writes_message_to_log(self):
        mock_file = mock_open()
        with patch("builtins.open", mock_file):
            log_error("something went wrong")
        handle = mock_file()
        written = handle.write.call_args[0][0]
        assert "something went wrong" in written
        assert "[" in written  # timestamp bracket

    def test_appends_mode(self):
        mock_file = mock_open()
        with patch("builtins.open", mock_file):
            log_error("err")
        mock_file.assert_called_once_with(m.LOG_FILE, "a")


# ══════════════════════════════════════════════════════════════════════════════
# load_state
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadState:
    def test_returns_parsed_json(self):
        data = {"next_execution": "2099-01-01T00:00:00", "history": ["k1"]}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            state = load_state()
        assert state["history"] == ["k1"]

    def test_file_not_found_returns_default(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            state = load_state()
        assert "next_execution" in state
        assert state["history"] == []

    def test_empty_file_returns_default(self):
        with patch("builtins.open", mock_open(read_data="   ")):
            state = load_state()
        assert "history" in state

    def test_invalid_json_returns_default(self):
        with patch("builtins.open", mock_open(read_data="not json {")):
            state = load_state()
        assert "history" in state


# ══════════════════════════════════════════════════════════════════════════════
# save_state
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveState:
    def test_writes_indented_json(self):
        mock_file = mock_open()
        state = {"next_execution": "2099-01-01T00:00:00", "history": []}
        with patch("builtins.open", mock_file):
            save_state(state)
        handle = mock_file()
        written = "".join(c[0][0] for c in handle.write.call_args_list)
        parsed = json.loads(written)
        assert parsed["history"] == []

    def test_uses_write_mode(self):
        mock_file = mock_open()
        with patch("builtins.open", mock_file):
            save_state({})
        mock_file.assert_called_once_with(m.STATE_FILE, "w")


# ══════════════════════════════════════════════════════════════════════════════
# fetch_and_select_sonar_issue
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchAndSelectSonarIssue:
    def _mock_response(self, issues, total=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": issues, "total": total or len(issues)}
        return resp

    def test_returns_issue_from_api(self):
        with patch("src.main.requests.get", return_value=self._mock_response([_SAMPLE_ISSUE])):
            issue = fetch_and_select_sonar_issue([])
        assert issue["key"] == _SAMPLE_ISSUE["key"]

    def test_filters_history(self):
        with patch("src.main.requests.get", return_value=self._mock_response([_SAMPLE_ISSUE])):
            issue = fetch_and_select_sonar_issue([_SAMPLE_ISSUE["key"]])
        assert issue is None

    def test_returns_none_when_no_candidates(self):
        with patch("src.main.requests.get", return_value=self._mock_response([])):
            issue = fetch_and_select_sonar_issue([])
        assert issue is None

    def test_api_error_returns_none(self):
        with patch("src.main.requests.get", side_effect=Exception("network error")), \
             patch("src.main.log_error"):
            issue = fetch_and_select_sonar_issue([])
        assert issue is None

    def test_pagination_fetches_multiple_pages(self):
        page1 = MagicMock()
        page1.raise_for_status = MagicMock()
        page1.json.return_value = {
            "issues": [{"key": "A", "severity": "MAJOR"}],
            "total": 2,
        }
        page2 = MagicMock()
        page2.raise_for_status = MagicMock()
        page2.json.return_value = {
            "issues": [{"key": "B", "severity": "MINOR"}],
            "total": 2,
        }
        with patch("src.main.requests.get", side_effect=[page1, page2]):
            issue = fetch_and_select_sonar_issue([])
        assert issue is not None
        assert issue["key"] in ("A", "B")

    def test_created_after_param_included(self):
        with patch("src.main.requests.get", return_value=self._mock_response([])) as mock_get:
            fetch_and_select_sonar_issue([], created_after="2026-01-01T00:00:00+0000")
        call_params = mock_get.call_args[1]["params"]
        assert "createdAfter" in call_params

    def test_weighted_selection_uses_severity(self):
        issues = [
            {"key": "A", "severity": "BLOCKER"},
            {"key": "B", "severity": "INFO"},
        ]
        counts = {"A": 0, "B": 0}
        with patch("src.main.requests.get", return_value=self._mock_response(issues)):
            for _ in range(100):
                issue = fetch_and_select_sonar_issue([])
                counts[issue["key"]] += 1
        # BLOCKER weight=50, INFO weight=1 → A should win most of the time
        assert counts["A"] > counts["B"]


# ══════════════════════════════════════════════════════════════════════════════
# get_local_source_code
# ══════════════════════════════════════════════════════════════════════════════

class TestGetLocalSourceCode:
    def test_returns_lines_around_target(self):
        file_lines = [f"line{i}\n" for i in range(1, 30)]
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="".join(file_lines))):
            # mock_open read_data doesn't support readlines() properly,
            # so we need to return a proper list
            m_open = MagicMock()
            m_open.return_value.__enter__.return_value.readlines.return_value = file_lines
            with patch("builtins.open", m_open):
                result = get_local_source_code("proj:src/file.py", 15)
        assert result is not None

    def test_returns_none_when_file_not_found(self):
        with patch("os.path.exists", return_value=False):
            result = get_local_source_code("proj:nonexistent.py", 5)
        assert result is None

    def test_strips_project_prefix(self):
        """Component with 'project:path' should try 'path' portion."""
        with patch("os.path.exists", return_value=False) as mock_exists:
            get_local_source_code("myproject:src/file.py", 1)
        checked_paths = [c[0][0] for c in mock_exists.call_args_list]
        assert any("myproject" not in p for p in checked_paths)

    def test_line_out_of_range_returns_none(self):
        file_lines = ["line1\n", "line2\n"]
        m_open = MagicMock()
        m_open.return_value.__enter__.return_value.readlines.return_value = file_lines
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", m_open):
            result = get_local_source_code("file.py", 999)
        assert result is None

    def test_read_exception_logs_and_continues(self):
        m_open = MagicMock(side_effect=IOError("permission denied"))
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", m_open), \
             patch("src.main.log_error") as mock_log:
            result = get_local_source_code("file.py", 5)
        assert result is None
        mock_log.assert_called()

    def test_no_colon_in_component(self):
        with patch("os.path.exists", return_value=False):
            result = get_local_source_code("simplefile.py", 1)
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# fetch_source_from_sonar
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchSourceFromSonar:
    def test_returns_cleaned_source(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": [[1, "<span>int x = 1;</span>"], [2, "return x;"]]}
        with patch("src.main.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 10)
        assert "int x = 1;" in result
        assert "return x;" in result

    def test_filters_none_values_in_sources(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": [[1, "<b>code</b>"], [2, None], [3, "end"]]}
        with patch("src.main.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert "end" in result
        assert result is not None

    def test_empty_sources_returns_none(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": []}
        with patch("src.main.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert result is None

    def test_api_exception_returns_none(self):
        with patch("src.main.requests.get", side_effect=Exception("timeout")), \
             patch("src.main.log_error"):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# ask_llm_for_refactor
# ══════════════════════════════════════════════════════════════════════════════

class TestAskLlmForRefactor:
    _VALID_RESPONSE = json.dumps({
        "title": "🚨 Dead Code",
        "explanation": "Código muerto detectado.",
        "suggested_code": "int x = 0;",
        "sonar_message_es": "Variable no utilizada.",
    })

    def _mock_openai(self, content):
        client_mock = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        client_mock.chat.completions.create.return_value.choices = [choice]
        return client_mock

    def test_returns_parsed_json(self):
        with patch("src.main.OpenAI", return_value=self._mock_openai(self._VALID_RESPONSE)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_strips_json_code_fence(self):
        fenced = f"```json\n{self._VALID_RESPONSE}\n```"
        with patch("src.main.OpenAI", return_value=self._mock_openai(fenced)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_strips_plain_code_fence(self):
        fenced = f"```\n{self._VALID_RESPONSE}\n```"
        with patch("src.main.OpenAI", return_value=self._mock_openai(fenced)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert result["title"] == "🚨 Dead Code"

    def test_fallback_on_api_exception(self):
        client_mock = MagicMock()
        client_mock.chat.completions.create.side_effect = Exception("API down")
        with patch("src.main.OpenAI", return_value=client_mock), \
             patch("src.main.log_error"):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "title" in result
        assert "explanation" in result

    def test_fallback_on_json_parse_error(self):
        with patch("src.main.OpenAI", return_value=self._mock_openai("not json{")), \
             patch("src.main.log_error"):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "suggested_code" in result

    def test_strict_false_handles_control_chars(self):
        # JSON with a literal tab character inside a string value
        raw = '{"title": "test\ttab", "explanation": "ok", "suggested_code": "x", "sonar_message_es": "y"}'
        with patch("src.main.OpenAI", return_value=self._mock_openai(raw)):
            result = ask_llm_for_refactor("rule", "msg", "code", "file.cs", 5)
        assert "title" in result


# ══════════════════════════════════════════════════════════════════════════════
# get_graph_access_token
# ══════════════════════════════════════════════════════════════════════════════

class TestGetGraphAccessToken:
    def test_returns_token_on_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "tok123"}
        with patch("src.main.requests.post", return_value=resp):
            token = get_graph_access_token()
        assert token == "tok123"

    def test_returns_none_on_error(self):
        with patch("src.main.requests.post", side_effect=Exception("auth failed")), \
             patch("src.main.log_error"):
            token = get_graph_access_token()
        assert token is None

    def test_http_error_returns_none(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("401")
        with patch("src.main.requests.post", return_value=resp), \
             patch("src.main.log_error"):
            token = get_graph_access_token()
        assert token is None


# ══════════════════════════════════════════════════════════════════════════════
# create_graph_calendar_event
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateGraphCalendarEvent:
    def _mock_token(self, token="valid-token"):
        return patch("src.main.get_graph_access_token", return_value=token)

    def _mock_post_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return patch("src.main.requests.post", return_value=resp)

    def test_returns_false_when_no_token(self):
        with patch("src.main.get_graph_access_token", return_value=None):
            result = create_graph_calendar_event("subj", "content")
        assert result is False

    def test_returns_false_outside_work_hours(self):
        # Set a work window guaranteed to be in the past (00:00–00:01 UTC)
        with self._mock_token(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "00:01", "WORK_TIMEZONE": "UTC"}), \
             patch("src.main.log_error"):
            result = create_graph_calendar_event("subj", "content")
        assert result is False

    def test_returns_true_on_success(self):
        with self._mock_token(), self._mock_post_success(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}):
            result = create_graph_calendar_event("subj", "content")
        assert result is True

    def test_uses_attendees_override(self):
        with self._mock_token(), self._mock_post_success(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}) as mock_post:
            result = create_graph_calendar_event("subj", "html", attendees_override=[])
        assert result is True

    def test_builds_attendees_from_env(self):
        captured = {}

        def capture_post(url, headers, json, timeout):
            captured["payload"] = json
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        with self._mock_token(), \
             patch("src.main.requests.post", side_effect=capture_post), \
             patch.dict(os.environ, {
                 "ALERT_RECIPIENTS": "a@x.com,b@x.com",
                 "WORK_DAY_START": "00:00",
                 "WORK_DAY_END": "23:59",
                 "WORK_TIMEZONE": "UTC",
             }):
            create_graph_calendar_event("subj", "html")

        emails = [a["emailAddress"]["address"] for a in captured["payload"]["attendees"]]
        assert "a@x.com" in emails
        assert "b@x.com" in emails

    def test_returns_false_on_api_error(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("Graph 500")
        with self._mock_token(), \
             patch("src.main.requests.post", return_value=resp), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}), \
             patch("src.main.log_error"):
            result = create_graph_calendar_event("subj", "html")
        assert result is False

    def test_empty_alert_recipients_env(self):
        with self._mock_token(), self._mock_post_success(), \
             patch.dict(os.environ, {
                 "ALERT_RECIPIENTS": "",
                 "WORK_DAY_START": "00:00",
                 "WORK_DAY_END": "23:59",
                 "WORK_TIMEZONE": "UTC",
             }):
            result = create_graph_calendar_event("subj", "html")
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# StatusHandler
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusHandler:
    def test_get_renders_html_with_state(self):
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=_FULL_STATE)):
            handler.do_GET()
        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "Five Seconds Hack" in written
        assert "PROJ:src/File.cs" in written

    def test_get_shows_preview_when_last_sent(self):
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=_FULL_STATE)):
            handler.do_GET()
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "Last Notification Preview" in written
        assert "Preview HTML" in written

    def test_get_no_preview_without_last_sent(self):
        state = json.dumps({"next_execution": "2099-01-01T00:00:00", "history": []})
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=state)):
            handler.do_GET()
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "No notifications sent yet" in written
        assert "Last Notification Preview" not in written

    def test_get_file_not_found_renders_empty_state(self):
        handler = _make_handler()
        with patch("builtins.open", side_effect=FileNotFoundError):
            handler.do_GET()
        handler.send_response.assert_called_with(200)

    def test_get_invalid_json_renders_empty_state(self):
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data="not json")):
            handler.do_GET()
        handler.send_response.assert_called_with(200)

    def test_get_invalid_next_execution_format(self):
        state = json.dumps({"next_execution": "INVALID", "history": []})
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=state)):
            handler.do_GET()
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "INVALID" in written  # shown as-is

    def test_get_empty_next_execution(self):
        state = json.dumps({"next_execution": "", "history": []})
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=state)):
            handler.do_GET()
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "—" in written

    def test_get_broken_pipe_is_silenced(self):
        handler = _make_handler()
        handler.wfile.write.side_effect = BrokenPipeError
        with patch("builtins.open", mock_open(read_data=_EMPTY_STATE)):
            handler.do_GET()  # should NOT raise

    def test_get_connection_reset_is_silenced(self):
        handler = _make_handler()
        handler.wfile.write.side_effect = ConnectionResetError
        with patch("builtins.open", mock_open(read_data=_EMPTY_STATE)):
            handler.do_GET()  # should NOT raise

    def test_post_force_spawns_subprocess(self):
        handler = _make_handler(path="/force", method="POST")
        with patch("src.main.subprocess.Popen") as mock_popen:
            handler.do_POST()
        mock_popen.assert_called_once()

    def test_post_force_logs_on_popen_error(self):
        handler = _make_handler(path="/force", method="POST")
        with patch("src.main.subprocess.Popen", side_effect=OSError("no binary")), \
             patch("src.main.log_error") as mock_log:
            handler.do_POST()
        mock_log.assert_called_once()

    def test_post_redirects_to_root(self):
        handler = _make_handler(path="/force", method="POST")
        with patch("src.main.subprocess.Popen"):
            handler.do_POST()
        handler.send_response.assert_called_with(303)
        handler.send_header.assert_any_call("Location", "/")

    def test_post_other_path_redirects_without_popen(self):
        handler = _make_handler(path="/other", method="POST")
        with patch("src.main.subprocess.Popen") as mock_popen:
            handler.do_POST()
        mock_popen.assert_not_called()
        handler.send_response.assert_called_with(303)

    def test_log_message_suppressed(self):
        handler = _make_handler()
        # log_message override should not raise or write
        handler.log_message("%s", "info")  # no-op


# ══════════════════════════════════════════════════════════════════════════════
# run_status_server
# ══════════════════════════════════════════════════════════════════════════════

class TestRunStatusServer:
    def test_starts_server_on_configured_port(self):
        mock_server = MagicMock()
        with patch("src.main.HTTPServer", return_value=mock_server) as mock_cls, \
             patch.dict(os.environ, {"STATUS_PORT": "9999"}):
            run_status_server()
        mock_cls.assert_called_once_with(("0.0.0.0", 9999), StatusHandler)
        mock_server.serve_forever.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# run_bot
# ══════════════════════════════════════════════════════════════════════════════

class TestRunBot:
    """Integration-level tests for the main execution cycle (all externals mocked)."""

    _PAST_STATE = {
        "next_execution": "2000-01-01T00:00:00",
        "history": [],
    }

    _FUTURE_STATE = {
        "next_execution": "2099-01-01T00:00:00",
        "history": [],
    }

    def _llm_response(self):
        return {
            "title": "🚨 Test",
            "explanation": "Explicación.",
            "suggested_code": "int x = 0; // fixed",
            "sonar_message_es": "Variable no usada.",
        }

    def test_skips_when_not_yet_scheduled(self):
        with patch("src.main.load_state", return_value=dict(self._FUTURE_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue") as mock_fetch:
            result = run_bot(force_execution=False)
        assert result is None
        mock_fetch.assert_not_called()

    def test_force_bypasses_schedule(self):
        with patch("src.main.load_state", return_value=dict(self._FUTURE_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=None), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"):
            result = run_bot(force_execution=True)
        # force=True, no issues found → returns False (not None)
        assert result is False

    def test_no_issues_found_schedules_next_check(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=None), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state") as mock_save:
            result = run_bot(force_execution=False)
        assert result is False
        mock_save.assert_called_once()

    def test_no_issues_force_returns_false(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=None):
            result = run_bot(force_execution=True)
        assert result is False

    def test_skips_issue_with_no_source(self):
        """Issues without accessible source are skipped; second call returns None → exits."""
        call_count = {"n": 0}

        def fetch_side_effect(history, created_after=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return dict(_SAMPLE_ISSUE)
            return None  # second call: no more candidates

        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", side_effect=fetch_side_effect), \
             patch("src.main.get_local_source_code", return_value=None), \
             patch("src.main.fetch_source_from_sonar", return_value=None), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.log_error"):
            result = run_bot(force_execution=False)
        assert result is False

    def test_successful_send_saves_state_and_returns_true(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="int x = 1;"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=False)
        assert result is True
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        assert "PROJ:src/File.cs" in saved_state["history"]

    def test_failed_send_still_reschedules(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=False), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=False)
        assert result is False
        mock_save.assert_called_once()

    def test_force_true_saves_state_on_success(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=True)
        assert result is True
        mock_save.assert_called_once()

    def test_force_true_no_save_on_failure(self):
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=False), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=True)
        assert result is False
        mock_save.assert_not_called()

    def test_llm_identical_suggestion_logs_error(self):
        original = "int x = 1;"
        llm = {**self._llm_response(), "suggested_code": original}
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value=original), \
             patch("src.main.ask_llm_for_refactor", return_value=llm), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("src.main.log_error") as mock_log:
            run_bot(force_execution=False)
        mock_log.assert_called()

    def test_debug_file_save_error_is_handled(self):
        """If saving the debug JSON fails, it should log and continue."""
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.os.makedirs", side_effect=OSError("disk full")), \
             patch("src.main.log_error") as mock_log:
            result = run_bot(force_execution=False)
        mock_log.assert_called()

    def test_suggested_code_as_list_joined(self):
        """LLM returning suggested_code as a list must be joined to a string."""
        llm = {**self._llm_response(), "suggested_code": ["line1", "line2"]}
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="line1\nline2"), \
             patch("src.main.ask_llm_for_refactor", return_value=llm), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=False)
        assert result is True

    def test_sonar_uses_local_fallback_then_api(self):
        """get_local_source_code returns None → falls back to fetch_source_from_sonar."""
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value=None), \
             patch("src.main.fetch_source_from_sonar", return_value="api source"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=False)
        assert result is True

    def test_friday_schedules_to_monday(self):
        """On a Friday (weekday=4) the next slot should be 3 days ahead."""
        friday_naive = datetime(2026, 5, 22, 10, 0, 0)
        friday_aware = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)
        state = {"next_execution": "2000-01-01T00:00:00", "history": []}
        saved = {}

        def capture_save(s):
            saved.update(s)

        def mock_now(tz=None):
            return friday_aware if tz else friday_naive

        with patch("src.main.load_state", return_value=state), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state", side_effect=capture_save), \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("src.main.datetime") as mock_dt:
            mock_dt.now.side_effect = mock_now
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            run_bot(force_execution=False)

        next_exec = datetime.fromisoformat(saved["next_execution"])
        delta = next_exec.date() - friday_naive.date()
        assert delta.days == 3

    def test_invalid_creation_date_uses_raw(self):
        issue = {**_SAMPLE_ISSUE, "creationDate": "not-a-date"}
        with patch("src.main.load_state", return_value=dict(self._PAST_STATE)), \
             patch("src.main.fetch_and_select_sonar_issue", return_value=issue), \
             patch("src.main.get_local_source_code", return_value="code"), \
             patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()), \
             patch("src.main.create_graph_calendar_event", return_value=True), \
             patch("src.main.save_state"), \
             patch("src.main.os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = run_bot(force_execution=False)
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# main()
# ══════════════════════════════════════════════════════════════════════════════

class TestMain:
    def test_serve_mode_calls_run_status_server(self):
        with patch.object(sys, "argv", ["main.py", "--serve"]), \
             patch("src.main.load_state", return_value={"next_execution": "2099-01-01T00:00:00", "history": []}), \
             patch("src.main.run_status_server") as mock_serve, \
             pytest.raises(SystemExit):
            main()
        mock_serve.assert_called_once()

    def test_normal_mode_calls_run_bot(self):
        with patch.object(sys, "argv", ["main.py"]), \
             patch("src.main.run_bot", return_value=True) as mock_bot:
            main()
        mock_bot.assert_called_once_with(False)

    def test_force_flag_passes_true_to_run_bot(self):
        with patch.object(sys, "argv", ["main.py", "--force"]), \
             patch("src.main.run_bot", return_value=True) as mock_bot:
            main()
        mock_bot.assert_called_once_with(True)

    def test_none_result_causes_sys_exit_0(self):
        with patch.object(sys, "argv", ["main.py"]), \
             patch("src.main.run_bot", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

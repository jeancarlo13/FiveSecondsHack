"""Tests for src/main.py — get_local_source_code, run_bot, main()."""

import json
import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock, mock_open, patch

import pytest

import src.main as m  # noqa: F401
from src.main import get_local_source_code, main, run_bot

_SAMPLE_ISSUE = {
    "key": "PROJ:src/File.cs",
    "component": "PROJ:src/File.cs",
    "line": 10,
    "message": "Unused variable 'x'",
    "rule": "csharpsquid:S1481",
    "severity": "MAJOR",
    "creationDate": "2026-05-01T12:00:00+0000",
}


# -- get_local_source_code --


class TestGetLocalSourceCode:
    def test_returns_lines_around_target(self):
        file_lines = [f"line{i}\n" for i in range(1, 30)]
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="".join(file_lines))),
        ):
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
        with patch("os.path.exists", return_value=True), patch("builtins.open", m_open):
            result = get_local_source_code("file.py", 999)
        assert result is None

    def test_read_exception_logs_and_continues(self):
        m_open = MagicMock(side_effect=OSError("permission denied"))
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", m_open),
            patch("src.main.log_error") as mock_log,
        ):
            result = get_local_source_code("file.py", 5)
        assert result is None
        mock_log.assert_called()

    def test_no_colon_in_component(self):
        with patch("os.path.exists", return_value=False):
            result = get_local_source_code("simplefile.py", 1)
        assert result is None


# -- run_bot --


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
        with (
            patch("src.main.load_state", return_value=dict(self._FUTURE_STATE)),
            patch("src.main.fetch_and_select_sonar_issue") as mock_fetch,
        ):
            result = run_bot(force_execution=False)
        assert result is None
        mock_fetch.assert_not_called()

    def test_force_bypasses_schedule(self):
        with (
            patch("src.main.load_state", return_value=dict(self._FUTURE_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=None),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
        ):
            result = run_bot(force_execution=True)
        # force=True, no issues found → returns False (not None)
        assert result is False

    def test_no_issues_found_schedules_next_check(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=None),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state") as mock_save,
        ):
            result = run_bot(force_execution=False)
        assert result is False
        mock_save.assert_called_once()

    def test_no_issues_force_returns_false(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=None),
        ):
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

        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", side_effect=fetch_side_effect),
            patch("src.main.get_local_source_code", return_value=None),
            patch("src.main.fetch_source_from_sonar", return_value=None),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.log_error"),
        ):
            result = run_bot(force_execution=False)
        assert result is False

    def test_successful_send_saves_state_and_returns_true(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)
        assert result is True
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        assert "PROJ:src/File.cs" in saved_state["history"]

    def test_failed_send_still_reschedules(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=False),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)
        assert result is False
        mock_save.assert_called_once()

    def test_force_true_saves_state_on_success(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=True)
        assert result is True
        mock_save.assert_called_once()

    def test_force_true_no_save_on_failure(self):
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=False),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=True)
        assert result is False
        mock_save.assert_not_called()

    def test_llm_identical_suggestion_logs_error(self):
        original = "int x = 1;"
        llm = {**self._llm_response(), "suggested_code": original}
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value=original),
            patch("src.main.ask_llm_for_refactor", return_value=llm),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
            patch("src.main.log_error") as mock_log,
        ):
            run_bot(force_execution=False)
        mock_log.assert_called()

    def test_debug_file_save_error_is_handled(self):
        """If saving the debug JSON fails, it should log and continue."""
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.os.makedirs", side_effect=OSError("disk full")),
            patch("src.main.log_error") as mock_log,
        ):
            run_bot(force_execution=False)
        mock_log.assert_called()

    def test_suggested_code_as_list_joined(self):
        """LLM returning suggested_code as a list must be joined to a string."""
        llm = {**self._llm_response(), "suggested_code": ["line1", "line2"]}
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="line1\nline2"),
            patch("src.main.ask_llm_for_refactor", return_value=llm),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)
        assert result is True

    def test_sonar_uses_local_fallback_then_api(self):
        """get_local_source_code returns None → falls back to fetch_source_from_sonar."""
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value=None),
            patch("src.main.fetch_source_from_sonar", return_value="api source"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)
        assert result is True

    def test_friday_schedules_to_monday(self):
        """On a Friday (weekday=4) the next slot should be 3 days ahead."""
        friday_naive = datetime(2026, 5, 22, 10, 0, 0)
        friday_aware = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
        state = {"next_execution": "2000-01-01T00:00:00", "history": []}
        saved = {}

        def capture_save(s):
            saved.update(s)

        def mock_now(tz=None):
            return friday_aware if tz else friday_naive

        with (
            patch("src.main.load_state", return_value=state),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(_SAMPLE_ISSUE)),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state", side_effect=capture_save),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
            patch("src.main.datetime") as mock_dt,
        ):
            mock_dt.now.side_effect = mock_now
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            run_bot(force_execution=False)

        next_exec = datetime.fromisoformat(saved["next_execution"])
        delta = next_exec.date() - friday_naive.date()
        assert delta.days == 3

    def test_invalid_creation_date_uses_raw(self):
        issue = {**_SAMPLE_ISSUE, "creationDate": "not-a-date"}
        with (
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=issue),
            patch("src.main.get_local_source_code", return_value="code"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)
        assert result is True


# -- main() --


class TestMain:
    def test_serve_mode_calls_run_status_server(self):
        with (
            patch.object(sys, "argv", ["main.py", "--serve"]),
            patch("src.main.load_state", return_value={"next_execution": "2099-01-01T00:00:00", "history": []}),
            patch("src.main.run_status_server") as mock_serve,
            pytest.raises(SystemExit),
        ):
            main()
        mock_serve.assert_called_once()

    def test_normal_mode_calls_run_bot(self):
        with patch.object(sys, "argv", ["main.py"]), patch("src.main.run_bot", return_value=True) as mock_bot:
            main()
        mock_bot.assert_called_once_with(False)

    def test_force_flag_passes_true_to_run_bot(self):
        with (
            patch.object(sys, "argv", ["main.py", "--force"]),
            patch("src.main.run_bot", return_value=True) as mock_bot,
        ):
            main()
        mock_bot.assert_called_once_with(True)

    def test_none_result_causes_sys_exit_0(self):
        with (
            patch.object(sys, "argv", ["main.py"]),
            patch("src.main.run_bot", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0


# -- run_bot (individual mode) --


class TestRunBotIndividualMode:
    """Tests for ALERT_MODE='individual' — one issue + event per recipient."""

    _PAST_STATE = {
        "next_execution": "2000-01-01T00:00:00",
        "history": [],
    }

    _ISSUE_A = {
        "key": "PROJ:src/FileA.cs",
        "component": "PROJ:src/FileA.cs",
        "line": 5,
        "message": "Unused variable 'a'",
        "rule": "csharpsquid:S1481",
        "severity": "MAJOR",
        "creationDate": "2026-05-01T12:00:00+0000",
    }

    _ISSUE_B = {
        "key": "PROJ:src/FileB.cs",
        "component": "PROJ:src/FileB.cs",
        "line": 20,
        "message": "Unused variable 'b'",
        "rule": "csharpsquid:S1481",
        "severity": "CRITICAL",
        "creationDate": "2026-05-02T08:00:00+0000",
    }

    def _llm_response(self):
        return {
            "title": "🚨 Test",
            "explanation": "Explanation.",
            "suggested_code": "int x = 0; // fixed",
            "sonar_message_es": "Variable no usada.",
        }

    def test_individual_sends_one_event_per_recipient(self):
        """2 recipients → fetch called twice, create_graph_calendar_event called twice."""
        issues = [dict(self._ISSUE_A), dict(self._ISSUE_B)]
        fetch_iter = iter(issues)

        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com,b@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", side_effect=lambda *a, **kw: next(fetch_iter, None)),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True) as mock_send,
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)

        assert result is True
        assert mock_send.call_count == 2
        # Each call must have attendees_override with exactly one properly-formatted attendee dict
        for call in mock_send.call_args_list:
            override = (
                call.kwargs.get("attendees_override") or call.args[2]
                if len(call.args) > 2
                else call.kwargs.get("attendees_override")
            )
            assert override is not None and len(override) == 1
            attendee = override[0]
            assert isinstance(attendee, dict)
            assert "emailAddress" in attendee
            assert "address" in attendee["emailAddress"]

    def test_individual_accumulates_all_keys_in_history(self):
        """Both issue keys must appear in saved state history."""
        issues = [dict(self._ISSUE_A), dict(self._ISSUE_B)]
        fetch_iter = iter(issues)
        saved = {}

        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com,b@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", side_effect=lambda *a, **kw: next(fetch_iter, None)),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state", side_effect=lambda s: saved.update(s)),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            run_bot(force_execution=False)

        assert self._ISSUE_A["key"] in saved["history"]
        assert self._ISSUE_B["key"] in saved["history"]

    def test_individual_skips_recipient_when_no_issue(self):
        """Second fetch returns None → only one event sent; result is still True."""
        call_n = {"n": 0}

        def fetch_side(history, created_after=None):
            call_n["n"] += 1
            return dict(self._ISSUE_A) if call_n["n"] == 1 else None

        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com,b@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", side_effect=fetch_side),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True) as mock_send,
            patch("src.main.save_state"),
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=False)

        assert result is True
        assert mock_send.call_count == 1

    def test_individual_no_recipients_calls_handle_no_issues(self):
        """ALERT_RECIPIENTS='' → no calendar event dispatched, returns False."""
        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": ""}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue") as mock_fetch,
            patch("src.main.create_graph_calendar_event") as mock_send,
            patch("src.main.save_state"),
            patch("src.main._handle_no_issues") as mock_no_issues,
        ):
            result = run_bot(force_execution=False)

        mock_send.assert_not_called()
        mock_fetch.assert_not_called()
        mock_no_issues.assert_called_once()
        assert result is False

    def test_individual_no_issues_at_all_returns_false(self):
        """All fetches return None → _handle_no_issues called, returns False."""
        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com,b@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=None),
            patch("src.main.create_graph_calendar_event") as mock_send,
            patch("src.main.save_state"),
            patch("src.main._handle_no_issues") as mock_no_issues,
        ):
            result = run_bot(force_execution=False)

        mock_send.assert_not_called()
        mock_no_issues.assert_called_once()
        assert result is False

    def test_individual_force_mode_saves_state_on_success(self):
        """force=True, one success → state saved, next_execution unchanged."""
        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", return_value=dict(self._ISSUE_A)),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=True),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=True)

        assert result is True
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        assert self._ISSUE_A["key"] in saved_state["history"]
        # next_execution must remain at original value (force doesn't reschedule)
        assert saved_state.get("next_execution") == self._PAST_STATE["next_execution"]

    def test_individual_force_no_save_on_all_failure(self):
        """force=True, all sends fail → state not saved."""
        with (
            patch.dict(os.environ, {"ALERT_MODE": "individual", "ALERT_RECIPIENTS": "a@ex.com,b@ex.com"}),
            patch("src.main.ALERT_MODE", "individual"),
            patch("src.main.load_state", return_value=dict(self._PAST_STATE)),
            patch("src.main.fetch_and_select_sonar_issue", side_effect=[dict(self._ISSUE_A), dict(self._ISSUE_B)]),
            patch("src.main.get_local_source_code", return_value="int x = 1;"),
            patch("src.main.ask_llm_for_refactor", return_value=self._llm_response()),
            patch("src.main.create_graph_calendar_event", return_value=False),
            patch("src.main.save_state") as mock_save,
            patch("src.main.os.makedirs"),
            patch("builtins.open", mock_open()),
        ):
            result = run_bot(force_execution=True)

        assert result is False
        mock_save.assert_not_called()

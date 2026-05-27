"""Tests for src/state.py."""

import json
from unittest.mock import mock_open, patch

from src.config import LOG_FILE, STATE_FILE
from src.state import load_state, log_error, save_state


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
        mock_file.assert_called_once_with(LOG_FILE, "a")


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
        mock_file.assert_called_once_with(STATE_FILE, "w")

"""Tests for src/state.py."""

import json
import logging
from unittest.mock import mock_open, patch

from src.config import STATE_FILE
from src.state import load_state, log_error, log_info, save_state


class TestLogError:
    def test_logs_error_event(self, caplog):
        with caplog.at_level(logging.ERROR, logger="fsh"):
            log_error("something went wrong")
        assert "something went wrong" in caplog.text

    def test_log_error_level(self, caplog):
        with caplog.at_level(logging.ERROR, logger="fsh"):
            log_error("err")
        assert caplog.records[0].levelno == logging.ERROR


class TestLogInfo:
    def test_logs_info_event(self, caplog):
        with caplog.at_level(logging.INFO, logger="fsh"):
            log_info("operation completed")
        assert "operation completed" in caplog.text

    def test_log_info_level(self, caplog):
        with caplog.at_level(logging.INFO, logger="fsh"):
            log_info("step done")
        assert caplog.records[0].levelno == logging.INFO


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

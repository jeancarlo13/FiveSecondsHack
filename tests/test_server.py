"""Tests for src/server.py."""
import json
import os
from unittest.mock import MagicMock, mock_open, patch

from src.server import StatusHandler, run_status_server


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


class TestStatusHandler:
    def test_get_renders_html_with_state(self):
        handler = _make_handler()
        with patch("builtins.open", mock_open(read_data=_FULL_STATE)):
            handler.do_GET()
        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0].decode("utf-8")
        assert "Five Seconds Hack" in written
        assert "PROJ:src/File.cs" in written
        assert "v1.1.0" in written

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
        with patch("src.server.subprocess.Popen") as mock_popen:
            handler.do_POST()
        mock_popen.assert_called_once()

    def test_post_force_logs_on_popen_error(self):
        handler = _make_handler(path="/force", method="POST")
        with patch("src.server.subprocess.Popen", side_effect=OSError("no binary")), \
             patch("src.server.log_error") as mock_log:
            handler.do_POST()
        mock_log.assert_called_once()

    def test_post_redirects_to_root(self):
        handler = _make_handler(path="/force", method="POST")
        with patch("src.server.subprocess.Popen"):
            handler.do_POST()
        handler.send_response.assert_called_with(303)
        handler.send_header.assert_any_call("Location", "/")

    def test_post_other_path_redirects_without_popen(self):
        handler = _make_handler(path="/other", method="POST")
        with patch("src.server.subprocess.Popen") as mock_popen:
            handler.do_POST()
        mock_popen.assert_not_called()
        handler.send_response.assert_called_with(303)

    def test_log_message_suppressed(self):
        handler = _make_handler()
        # log_message override should not raise or write
        handler.log_message("%s", "info")  # no-op


class TestRunStatusServer:
    def test_starts_server_on_configured_port(self):
        mock_server = MagicMock()
        with patch("src.server.HTTPServer", return_value=mock_server) as mock_cls, \
             patch.dict(os.environ, {"STATUS_PORT": "9999"}):
            run_status_server()
        mock_cls.assert_called_once_with(("0.0.0.0", 9999), StatusHandler)
        mock_server.serve_forever.assert_called_once()

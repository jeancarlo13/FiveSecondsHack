import html as html_lib
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from string import Template

from .config import STATE_FILE
from .state import log_error

_STATUS_PAGE = Template((Path(__file__).parent / "templates" / "status_page.html").read_text(encoding="utf-8"))


class StatusHandler(BaseHTTPRequestHandler):
    """Serves a simple HTML status page read from the local state file."""

    def do_GET(self):
        try:
            with open(STATE_FILE) as f:
                state_data = json.loads(f.read().strip() or "{}")
        except (FileNotFoundError, json.JSONDecodeError):
            state_data = {}

        next_exec = state_data.get("next_execution", "")
        history = state_data.get("history", [])
        last_sent = state_data.get("last_sent", {})

        try:
            next_exec_fmt = datetime.fromisoformat(next_exec).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            next_exec_fmt = next_exec or "\u2014"

        if last_sent:
            _dash = "—"
            last_sent_rows = (
                f'<tr><td>Issue Key</td><td><code>{html_lib.escape(last_sent.get("issue_key", _dash))}</code></td></tr>'
                f'<tr><td>Title</td><td>{html_lib.escape(last_sent.get("title", _dash))}</td></tr>'
                f'<tr><td>File</td><td><code style="font-size:11px;">{html_lib.escape(last_sent.get("component", _dash))}</code></td></tr>'
                f'<tr><td>Rule</td><td><code>{html_lib.escape(last_sent.get("rule", _dash))}</code></td></tr>'
                f'<tr><td>Sent at</td><td>{html_lib.escape(last_sent.get("sent_at", _dash))}</td></tr>'
            )
            last_sent_preview = last_sent.get("html", "")
        else:
            last_sent_rows = "<tr><td colspan='2' style='color:#94a3b8;'>No notifications sent yet.</td></tr>"
            last_sent_preview = ""

        preview_section = (
            f'<h2>Last Notification Preview</h2><div class="preview">{last_sent_preview}</div>'
            if last_sent_preview else ""
        )

        body = _STATUS_PAGE.safe_substitute(
            next_exec_fmt=html_lib.escape(next_exec_fmt),
            history_count=len(history),
            last_sent_rows=last_sent_rows,
            preview_section=preview_section,
        )

        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_POST(self):
        if self.path == "/force":
            try:
                subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "--force"]
                )
            except Exception as e:
                log_error(f"Force trigger from status endpoint failed: {e}")
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def run_status_server():
    """Starts the HTTP status server (blocking). Run with --serve flag."""
    port = int(os.getenv("STATUS_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    print(f"\U0001f4ca Status server running at http://localhost:{port}  (Ctrl+C to stop)")
    server.serve_forever()

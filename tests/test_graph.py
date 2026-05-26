"""Tests for src/graph.py."""
import os
from unittest.mock import MagicMock, patch

from src.graph import create_graph_calendar_event, get_graph_access_token


class TestGetGraphAccessToken:
    def test_returns_token_on_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "tok123"}
        with patch("src.graph.requests.post", return_value=resp):
            token = get_graph_access_token()
        assert token == "tok123"

    def test_returns_none_on_error(self):
        with patch("src.graph.requests.post", side_effect=Exception("auth failed")), \
             patch("src.graph.log_error"):
            token = get_graph_access_token()
        assert token is None

    def test_http_error_returns_none(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("401")
        with patch("src.graph.requests.post", return_value=resp), \
             patch("src.graph.log_error"):
            token = get_graph_access_token()
        assert token is None


class TestCreateGraphCalendarEvent:
    def _mock_token(self, token="valid-token"):
        return patch("src.graph.get_graph_access_token", return_value=token)

    def _mock_post_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return patch("src.graph.requests.post", return_value=resp)

    def test_returns_false_when_no_token(self):
        with patch("src.graph.get_graph_access_token", return_value=None):
            result = create_graph_calendar_event("subj", "content")
        assert result is False

    def test_returns_false_outside_work_hours(self):
        # Set a work window guaranteed to be in the past (00:00–00:01 UTC)
        with self._mock_token(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "00:01", "WORK_TIMEZONE": "UTC"}), \
             patch("src.graph.log_error"):
            result = create_graph_calendar_event("subj", "content")
        assert result is False

    def test_returns_true_on_success(self):
        with self._mock_token(), self._mock_post_success(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}):
            result = create_graph_calendar_event("subj", "content")
        assert result is True

    def test_uses_attendees_override(self):
        with self._mock_token(), self._mock_post_success(), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}):
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
             patch("src.graph.requests.post", side_effect=capture_post), \
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
             patch("src.graph.requests.post", return_value=resp), \
             patch.dict(os.environ, {"WORK_DAY_START": "00:00", "WORK_DAY_END": "23:59", "WORK_TIMEZONE": "UTC"}), \
             patch("src.graph.log_error"):
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

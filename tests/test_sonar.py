"""Tests for src/sonar.py."""

from unittest.mock import MagicMock, patch

from src.config import SEVERITY_WEIGHTS
from src.sonar import fetch_and_select_sonar_issue, fetch_source_from_sonar

_SAMPLE_ISSUE = {
    "key": "PROJ:src/File.cs",
    "component": "PROJ:src/File.cs",
    "line": 10,
    "message": "Unused variable 'x'",
    "rule": "csharpsquid:S1481",
    "severity": "MAJOR",
    "creationDate": "2026-05-01T12:00:00+0000",
}


class TestFetchAndSelectSonarIssue:
    def _mock_response(self, issues, total=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": issues, "total": total or len(issues)}
        return resp

    def test_returns_issue_from_api(self):
        with patch("src.sonar.requests.get", return_value=self._mock_response([_SAMPLE_ISSUE])):
            issue = fetch_and_select_sonar_issue([])
        assert issue["key"] == _SAMPLE_ISSUE["key"]

    def test_filters_history(self):
        with patch("src.sonar.requests.get", return_value=self._mock_response([_SAMPLE_ISSUE])):
            issue = fetch_and_select_sonar_issue([_SAMPLE_ISSUE["key"]])
        assert issue is None

    def test_returns_none_when_no_candidates(self):
        with patch("src.sonar.requests.get", return_value=self._mock_response([])):
            issue = fetch_and_select_sonar_issue([])
        assert issue is None

    def test_api_error_returns_none(self):
        with patch("src.sonar.requests.get", side_effect=Exception("network error")), patch("src.sonar.log_error"):
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
        with patch("src.sonar.requests.get", side_effect=[page1, page2]):
            issue = fetch_and_select_sonar_issue([])
        assert issue is not None
        assert issue["key"] in ("A", "B")

    def test_created_after_param_included(self):
        with patch("src.sonar.requests.get", return_value=self._mock_response([])) as mock_get:
            fetch_and_select_sonar_issue([], created_after="2026-01-01T00:00:00+0000")
        call_params = mock_get.call_args[1]["params"]
        assert "createdAfter" in call_params

    def test_weighted_selection_uses_severity(self):
        issues = [
            {"key": "A", "severity": "BLOCKER"},
            {"key": "B", "severity": "INFO"},
        ]
        counts = {"A": 0, "B": 0}
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            for _ in range(100):
                issue = fetch_and_select_sonar_issue([])
                counts[issue["key"]] += 1
        # BLOCKER weight=50, INFO weight=1 → A should win most of the time
        assert counts["A"] > counts["B"]

    def test_severity_weights_constants(self):
        assert SEVERITY_WEIGHTS["BLOCKER"] > SEVERITY_WEIGHTS["CRITICAL"]
        assert SEVERITY_WEIGHTS["CRITICAL"] > SEVERITY_WEIGHTS["MAJOR"]

    def test_filters_by_allowed_authors_email(self):
        issues = [
            {"key": "A", "severity": "MAJOR", "author": "dev1@example.com"},
            {"key": "B", "severity": "MAJOR", "author": "other@example.com"},
        ]
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            issue = fetch_and_select_sonar_issue([], allowed_authors=["dev1@example.com"])
        assert issue is not None
        assert issue["key"] == "A"

    def test_filters_by_allowed_authors_local_part(self):
        issues = [
            {"key": "A", "severity": "MAJOR", "author": "dev1@example.com"},
            {"key": "B", "severity": "MAJOR", "author": "other@example.com"},
        ]
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            issue = fetch_and_select_sonar_issue([], allowed_authors=["dev1"])
        assert issue is not None
        assert issue["key"] == "A"

    def test_returns_none_when_author_not_invited(self):
        issues = [{"key": "A", "severity": "MAJOR", "author": "other@example.com"}]
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            issue = fetch_and_select_sonar_issue([], allowed_authors=["dev1@example.com"])
        assert issue is None

    def test_matches_github_noreply_email(self):
        issues = [
            {"key": "A", "severity": "MAJOR", "author": "68394537+ccorral1-tenco@users.noreply.github.com"},
            {"key": "B", "severity": "MAJOR", "author": "other@example.com"},
        ]
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            issue = fetch_and_select_sonar_issue([], allowed_authors=["ccorral@tenco.mx"])
        assert issue is not None
        assert issue["key"] == "A"

    def test_no_false_positive_different_name(self):
        issues = [
            {"key": "A", "severity": "MAJOR", "author": "68394537+ccorral1-tenco@users.noreply.github.com"},
        ]
        with patch("src.sonar.requests.get", return_value=self._mock_response(issues)):
            issue = fetch_and_select_sonar_issue([], allowed_authors=["other@tenco.mx"])
        assert issue is None


class TestFetchSourceFromSonar:
    def test_returns_cleaned_source(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": [[1, "<span>int x = 1;</span>"], [2, "return x;"]]}
        with patch("src.sonar.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 10)
        assert "int x = 1;" in result
        assert "return x;" in result

    def test_filters_none_values_in_sources(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": [[1, "<b>code</b>"], [2, None], [3, "end"]]}
        with patch("src.sonar.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert "end" in result
        assert result is not None

    def test_empty_sources_returns_none(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sources": []}
        with patch("src.sonar.requests.get", return_value=resp):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert result is None

    def test_api_exception_returns_none(self):
        with patch("src.sonar.requests.get", side_effect=Exception("timeout")), patch("src.sonar.log_error"):
            result = fetch_source_from_sonar("PROJ:file.cs", 1)
        assert result is None

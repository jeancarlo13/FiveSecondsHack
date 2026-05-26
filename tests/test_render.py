"""Tests for src/render.py."""
from datetime import UTC, datetime, timedelta

from src.render import clean_sonar_source, relative_time, render_code_block


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


class TestRelativeTime:
    def _dt(self, seconds_ago):
        return datetime.now(UTC) - timedelta(seconds=seconds_ago)

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

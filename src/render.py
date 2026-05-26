"""HTML rendering utilities for code blocks and human-readable timestamps.

All templates are loaded once at module import.  The code-block template
lives in ``src/templates/code_block.html`` and is shared across all rendered
snippets (both the original smell block and the refactored suggestion).
"""
import html as html_lib
import re
from datetime import UTC, datetime
from pathlib import Path
from string import Template

# Email code-block template loaded once; uses ${lines} as the only placeholder.
_CODE_BLOCK = Template((Path(__file__).parent / "templates" / "code_block.html").read_text(encoding="utf-8").strip())


def render_code_block(code, start_line_num, highlight_lines=None, accent_color="#ef4444"):
    """Render a syntax-highlighted HTML code block suitable for inclusion in emails.

    Each line is rendered as an inline ``<span>`` with an optional highlight
    background.  Line numbers are right-justified and all content is HTML-escaped.
    Indentation uses ``&nbsp;`` so it survives email-client whitespace collapsing.

    Args:
        code:            Raw source code string (may be multiline).
        start_line_num:  1-based line number of the first line in ``code``;
                         used to display accurate gutter numbers.
        highlight_lines: Optional set of absolute (1-based) line numbers to
                         visually highlight.  ``None`` disables all highlights.
        accent_color:    CSS colour used for the highlight arrow and text on
                         highlighted lines.  Red (``#ef4444``) for smells,
                         green (``#10b981``) for suggestions.

    Returns:
        HTML string ready to be embedded in an email body.
    """
    lines = code.split("\n")
    max_width = len(str(start_line_num + len(lines) - 1))
    line_parts = []
    for i, line in enumerate(lines):
        current_num = start_line_num + i
        is_flagged = (highlight_lines is not None and current_num in highlight_lines)
        num_str = str(current_num).rjust(max_width)
        raw = line.rstrip()
        content_start = len(raw) - len(raw.lstrip('\t '))
        indent = raw[:content_start].replace('\t', '    ').replace(' ', '&nbsp;')
        code_html = indent + html_lib.escape(raw[content_start:])
        if is_flagged:
            line_parts.append(
                f'<span style="background-color:#1e293b;color:{accent_color};">'
                f'{html_lib.escape(num_str)} &#9658; {code_html}'
                f'</span>'
            )
        else:
            line_parts.append(
                f'<span style="color:#475569;">{html_lib.escape(num_str)}  </span>'
                f'<span style="color:#f8fafc;">{code_html}</span>'
            )
    return _CODE_BLOCK.safe_substitute(lines="<br>".join(line_parts))


def clean_sonar_source(raw_html):
    """Strip SonarCloud HTML syntax-highlighting tags and decode HTML entities.

    SonarCloud's ``/api/sources/show`` endpoint wraps tokens in ``<span>`` tags
    for browser display.  This function reduces that markup to plain text so it
    can be re-rendered by the bot's own code-block template.

    Args:
        raw_html: HTML string as returned by the SonarCloud sources API.

    Returns:
        Plain-text source string with entities decoded.
    """
    plain = re.sub(r'<[^>]+>', '', raw_html)
    return html_lib.unescape(plain)


def _fmt_unit(n, unit):
    """Format a numeric duration value with its unit and correct pluralisation.

    Args:
        n:    Integer count (e.g. 3).
        unit: Singular time unit string (e.g. ``"minute"``).

    Returns:
        String such as ``"3 minutes ago"`` or ``"1 hour ago"``.
    """
    suffix = "s" if n > 1 else ""
    return f"{n} {unit}{suffix} ago"


def relative_time(dt):
    """Return a human-readable relative timestamp string from a UTC-aware datetime.

    Converts the delta between *now* (UTC) and ``dt`` into the most appropriate
    granularity, from ``"just now"`` up to years.

    Args:
        dt: A timezone-aware ``datetime`` object (expected in UTC).

    Returns:
        String such as ``"3 minutes ago"``, ``"2 days ago"``, etc.
    """
    delta = datetime.now(UTC) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return _fmt_unit(seconds // 60, "minute")
    if seconds < 86400:
        return _fmt_unit(seconds // 3600, "hour")
    if seconds < 7 * 86400:
        return _fmt_unit(seconds // 86400, "day")
    if seconds < 30 * 86400:
        return _fmt_unit(seconds // (7 * 86400), "week")
    if seconds < 365 * 86400:
        return _fmt_unit(seconds // (30 * 86400), "month")
    return _fmt_unit(seconds // (365 * 86400), "year")

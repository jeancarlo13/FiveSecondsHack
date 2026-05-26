import html as html_lib
import re
from datetime import UTC, datetime
from pathlib import Path
from string import Template

_CODE_BLOCK = Template((Path(__file__).parent / "templates" / "code_block.html").read_text(encoding="utf-8").strip())


def render_code_block(code, start_line_num, highlight_lines=None, accent_color="#ef4444"):
    """
    Renders a code block with line numbers and optional line highlighting.
    All lines live inside a SINGLE <td>, separated by <br> tags.
    highlight_lines: a set of absolute line numbers to highlight.
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
    """Strips HTML syntax-highlighting tags and decodes HTML entities from SonarCloud source API responses."""
    plain = re.sub(r'<[^>]+>', '', raw_html)
    return html_lib.unescape(plain)


def _fmt_unit(n, unit):
    suffix = "s" if n > 1 else ""
    return f"{n} {unit}{suffix} ago"


def relative_time(dt):
    """Returns a human-readable relative time string (e.g. '3 days ago') from a UTC-aware datetime."""
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

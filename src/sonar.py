"""SonarCloud API integration.

Exposes two public functions:
  - fetch_and_select_sonar_issue: paginated issue fetch + weighted random selection.
  - fetch_source_from_sonar: source-code snippet retrieval via /api/sources/show.

All requests are authenticated with a Bearer token read from ``SONAR_TOKEN``.
"""
import os
import random

import requests

from .config import SEVERITY_WEIGHTS, SOURCE_CONTEXT_LINES
from .render import clean_sonar_source
from .state import log_error


def _normalize_author(value):
    """Normalize an author identifier for robust recipient matching."""
    if not value:
        return ""
    return str(value).strip().lower()


def _candidate_matches_allowed_authors(issue, allowed_authors):
    """Return True when an issue author matches one of the allowed recipients."""
    if not allowed_authors:
        return True

    issue_author = _normalize_author(issue.get("author"))
    if not issue_author:
        return False

    if issue_author in allowed_authors:
        return True

    issue_local_part = issue_author.split("@")[0]
    return issue_local_part in allowed_authors


def fetch_and_select_sonar_issue(history, created_after=None, allowed_authors=None):
    """Fetch open issues from SonarCloud and pick one at random, weighted by severity.

    Paginates through the ``/api/issues/search`` endpoint (up to 500 results per
    page) until all issues within the time window have been collected.  Issues
    whose keys are present in ``history`` are excluded from selection to avoid
    sending duplicate notifications.

    Args:
        history: List of issue keys that have already been sent.  Used to
                 filter out already-notified issues.
        created_after: Optional ISO-8601 datetime string.  When supplied, only
                   issues created after this timestamp are considered
                   (maps to the ``createdAfter`` SonarCloud parameter).
        allowed_authors: Optional list of recipient identifiers (usually emails).
                 When provided, only issues whose ``author`` matches one
                 of these identifiers are considered.

    Returns:
        A single issue dict as returned by the SonarCloud API, or ``None``
        if no eligible candidates exist or the request fails.
    """
    url = f"{os.getenv('SONAR_HOST_URL')}/api/issues/search"
    headers = {"Authorization": f"Bearer {os.getenv('SONAR_TOKEN')}"}
    all_issues = []
    page = 1

    while True:
        params = {
            "organization": os.getenv("SONAR_ORGANIZATION"),
            "resolved": "false",
            "ps": "500",
            "p": str(page),
        }
        if created_after:
            params["createdAfter"] = created_after
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)
            # Stop when the accumulated count reaches the reported total or the
            # current page returned no results.
            if len(all_issues) >= data.get("total", 0) or not issues:
                break
            page += 1
        except Exception as e:
            log_error(f"Failed to fetch issues from SonarCloud (page {page}): {e}")
            break

    normalized_allowed_authors = {_normalize_author(a) for a in (allowed_authors or []) if _normalize_author(a)}

    # Remove already-sent issues and, optionally, non-invited authors.
    candidates = [
        i
        for i in all_issues
        if i.get("key") not in history and _candidate_matches_allowed_authors(i, normalized_allowed_authors)
    ]
    if not candidates:
        return None

    # Weighted random selection: BLOCKER issues are picked ~50× more often than INFO.
    weights = [SEVERITY_WEIGHTS.get(i.get("severity", "INFO"), 1) for i in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def fetch_source_from_sonar(component_key, line_number):
    """Retrieve a source-code snippet from the SonarCloud ``/api/sources/show`` endpoint.

    Used as a fallback when the file is not present on the local filesystem
    (e.g. when running inside a container without the source tree mounted).
    Strips the HTML syntax-highlighting markup returned by SonarCloud before
    returning plain text.

    Args:
        component_key: SonarCloud component key, e.g. ``"myorg:src/app.py"``.
        line_number: 1-based target line number.  Lines within
                     ``SOURCE_CONTEXT_LINES`` above and below are also fetched.

    Returns:
        Plain-text source snippet as a single string (newline-delimited), or
        ``None`` if the request fails or the component has no sources.
    """
    url = f"{os.getenv('SONAR_HOST_URL')}/api/sources/show"
    headers = {"Authorization": f"Bearer {os.getenv('SONAR_TOKEN')}"}
    params = {
        "key": component_key,
        "from": max(1, line_number - SOURCE_CONTEXT_LINES),
        "to": line_number + SOURCE_CONTEXT_LINES,
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        sources = response.json().get("sources", [])
        if sources:
            return "\n".join(clean_sonar_source(s[1]) for s in sources if s[1] is not None).strip()
    except Exception as e:
        log_error(f"Failed to fetch source from SonarCloud API for {component_key}:{line_number}: {e}")
    return None

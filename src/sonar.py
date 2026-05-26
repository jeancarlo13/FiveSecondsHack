import os
import random

import requests

from .config import SEVERITY_WEIGHTS, SOURCE_CONTEXT_LINES
from .render import clean_sonar_source
from .state import log_error


def fetch_and_select_sonar_issue(history, created_after=None):
    """
    Fetches all open unresolved issues from SonarCloud (paginated), filters out
    issues already in history, and returns one selected at random weighted by severity.
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
            if len(all_issues) >= data.get("total", 0) or not issues:
                break
            page += 1
        except Exception as e:
            log_error(f"Failed to fetch issues from SonarCloud (page {page}): {e}")
            break

    candidates = [i for i in all_issues if i.get("key") not in history]
    if not candidates:
        return None

    weights = [SEVERITY_WEIGHTS.get(i.get("severity", "INFO"), 1) for i in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def fetch_source_from_sonar(component_key, line_number):
    """
    Fetches the source code snippet from the SonarCloud /api/sources/show endpoint.
    Used as a fallback when the local file is not accessible.
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

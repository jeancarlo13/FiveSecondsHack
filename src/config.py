"""Application-wide configuration and constants.

Loads environment variables from the .env file at startup and exposes
shared constants used across all modules.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Path to the JSON file that persists scheduler state between runs.
STATE_FILE = "data/sonar_state.json"

# Path to the append-only error log.
LOG_FILE = "logs/error.log"

# Relative weights used when randomly selecting an issue by severity.
# Higher weight → more likely to be picked during weighted sampling.
SEVERITY_WEIGHTS = {
    "BLOCKER": 50,
    "CRITICAL": 30,
    "MAJOR": 15,
    "MINOR": 4,
    "INFO": 1,
}

# Number of lines of source code to fetch above and below the flagged line.
# A value of 8 yields a window of 17 lines total (8 + flagged + 8).
SOURCE_CONTEXT_LINES = 8

# Notification delivery mode: 'broadcast' (one event, all recipients) or
# 'individual' (one event per recipient, each with a distinct issue).
ALERT_MODE = os.getenv("ALERT_MODE", "broadcast")


def _env_bool(name, default=False):
    """Parse a boolean environment variable using common truthy/falsey values."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


# If enabled, only issues authored by invited recipients are eligible.
ISSUE_ONLY_FROM_INVITED = _env_bool("ISSUE_ONLY_FROM_INVITED", False)

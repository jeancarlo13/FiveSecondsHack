"""Persistence helpers for scheduler state and error logging.

Provides three public functions:
  - log_error: appends a timestamped message to the error log.
  - load_state: reads scheduler state from JSON, returning a safe default on failure.
  - save_state: atomically overwrites the state file with the given dict.
"""
import json
from datetime import datetime

from .config import LOG_FILE, STATE_FILE


def log_error(message):
    """Append a timestamped error message to the log file.

    Args:
        message: Human-readable description of the error or warning.
    """
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")

def load_state():
    """Read and return the persisted scheduler state from disk.

    Returns the JSON object stored in STATE_FILE.  If the file is missing,
    empty, or contains invalid JSON, a fresh default state is returned so
    the bot can continue operating without manual intervention.

    Returns:
        dict with keys:
            ``next_execution`` (ISO-format datetime string) and
            ``history`` (list of already-sent issue keys).
    """
    default_state = {"next_execution": datetime.now().isoformat(), "history": []}
    try:
        with open(STATE_FILE) as f:
            content = f.read().strip()
            if not content:
                return default_state
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state

def save_state(state):
    """Overwrite the state file with the given scheduler state dict.

    Args:
        state: dict to serialise.  Must be JSON-serialisable; typically
               contains ``next_execution``, ``history``, and ``last_sent``.
    """
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

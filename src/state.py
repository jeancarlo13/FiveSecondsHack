"""Persistence helpers for scheduler state and structured logging.

Provides four public functions:
  - log_error: records an error-level event via structlog (file + console).
  - log_info: records an info-level event via structlog (file + console).
  - load_state: reads scheduler state from JSON, returning a safe default on failure.
  - save_state: atomically overwrites the state file with the given dict.

Logging is configured once at import time using structlog backed by the stdlib
``logging`` module.  All events are written to ``LOG_FILE`` and echoed to
``stdout`` with ISO-8601 timestamps and human-readable level labels.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import structlog

from .config import LOG_FILE, STATE_FILE

# --------------------------------------------------------------------------- #
# Structured logging setup                                                     #
# --------------------------------------------------------------------------- #
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

_formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
)

_pkg_logger = logging.getLogger("fsh")
if not _pkg_logger.handlers:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    _fh.setFormatter(_formatter)
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(_formatter)
    _pkg_logger.addHandler(_fh)
    _pkg_logger.addHandler(_ch)
    _pkg_logger.setLevel(logging.DEBUG)

_logger = structlog.get_logger("fsh")


def log_error(message: str) -> None:
    """Record an error-level message via structlog (file + console).

    Args:
        message: Human-readable description of the error or warning.
    """
    _logger.error(message)


def log_info(message: str) -> None:
    """Record an info-level message via structlog (file + console).

    Args:
        message: Human-readable description of a notable event.
    """
    _logger.info(message)


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

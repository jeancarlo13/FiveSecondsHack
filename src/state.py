import json
from datetime import datetime

from .config import LOG_FILE, STATE_FILE


def log_error(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")

def load_state():
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
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

from dotenv import load_dotenv

load_dotenv()

STATE_FILE = "data/sonar_state.json"
LOG_FILE = "logs/error.log"

SEVERITY_WEIGHTS = {
    "BLOCKER": 50,
    "CRITICAL": 30,
    "MAJOR": 15,
    "MINOR": 4,
    "INFO": 1,
}

SOURCE_CONTEXT_LINES = 8

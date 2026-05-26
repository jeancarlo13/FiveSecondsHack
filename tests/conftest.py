"""
Shared pytest configuration.
Env vars are set at module level so they are available before any src/ import.
"""

import os
import sys

# ── Set required env vars BEFORE importing any module ──────────────────────────
os.environ.update(
    {
        "OPENAI_API_KEY": "test-key",
        "SONAR_HOST_URL": "https://sonarcloud.io",
        "SONAR_TOKEN": "test-sonar-token",
        "SONAR_ORGANIZATION": "test-org",
        "AZURE_TENANT_ID": "test-tenant",
        "AZURE_CLIENT_ID": "test-client-id",
        "AZURE_CLIENT_SECRET": "test-secret",
        "EMAIL_USERNAME": "test@example.com",
        "WORK_TIMEZONE": "UTC",
        "WORK_DAY_START": "09:00",
        "WORK_DAY_END": "18:00",
        "ALERT_RECIPIENTS": "dev1@example.com,dev2@example.com,",
        "ALERT_MODE": "broadcast",
        "STATUS_PORT": "8080",
        "ISSUE_LOOKBACK_HOURS": "72",
        "OPENAI_MODEL": "gpt-4o-mini",
    }
)

# Prevent main() from auto-executing on import
sys.argv = ["main.py"]

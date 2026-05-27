"""Microsoft Graph API integration for calendar event delivery.

Uses the OAuth2 Client Credentials flow (application permissions) to obtain
a short-lived access token from Microsoft Entra ID, then creates a 15-minute
calendar event in the configured user's mailbox via the Graph ``/events``
endpoint.  The event body is HTML and carries the full alert content.

Required environment variables:
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
    EMAIL_USERNAME, ALERT_RECIPIENTS,
    WORK_TIMEZONE, WORK_DAY_START, WORK_DAY_END.
"""

import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .state import log_error


def get_graph_access_token():
    """Request an OAuth2 access token from Microsoft Entra ID.

    Uses the Client Credentials grant with the ``https://graph.microsoft.com/.default``
    scope.  Credentials are read from environment variables.

    Returns:
        Access token string on success, or ``None`` if authentication fails.
    """
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "client_id": client_id,
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        log_error(f"Microsoft Entra ID Authentication failed: {e}")
        return None


def create_graph_calendar_event(subject, html_content, attendees_override=None):
    """Create a 15-minute calendar event in the configured user's Microsoft 365 calendar.

    The event is scheduled at a random time within today's configured work-hour
    window, at least 5 minutes from now to ensure it appears as upcoming.
    If the current time is already outside the work window the event is not
    created and the error is logged.

    Args:
        subject:            Event subject line (displayed as the calendar entry title).
        html_content:       Full HTML body of the event (the alert email).
        attendees_override: Optional list of Graph-formatted attendee dicts.  If
                            ``None``, attendees are built from ``ALERT_RECIPIENTS``.
                            Pass an empty list ``[]`` to create an event with no
                            attendees (used for "no issues" notifications).

    Returns:
        ``True`` if the event was created successfully, ``False`` otherwise.
    """
    token = get_graph_access_token()
    if not token:
        return False

    user_email = os.getenv("EMAIL_USERNAME")
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/events"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    work_tz = ZoneInfo(os.getenv("WORK_TIMEZONE", "America/Chihuahua"))
    work_start_h, work_start_m = map(int, os.getenv("WORK_DAY_START", "09:00").split(":"))
    work_end_h, work_end_m = map(int, os.getenv("WORK_DAY_END", "18:00").split(":"))

    now_local = datetime.now(work_tz)
    today = now_local.date()
    window_start = datetime(today.year, today.month, today.day, work_start_h, work_start_m, tzinfo=work_tz)
    window_end = datetime(today.year, today.month, today.day, work_end_h, work_end_m, tzinfo=work_tz)

    # Earliest possible start: max(window_start, now + 5 min) so the event is always in the future.
    earliest = max(window_start, now_local + timedelta(minutes=5))
    # Latest possible start: window_end minus the event duration (15 min) so it ends within hours.
    latest = window_end - timedelta(minutes=15)

    if earliest > latest:
        log_error(
            f"Event not created: outside of work hours "
            f"({os.getenv('WORK_DAY_START', '09:00')}\u2013{os.getenv('WORK_DAY_END', '18:00')} "
            f"{os.getenv('WORK_TIMEZONE', 'America/Chihuahua')})"
        )
        return False

    available_minutes = int((latest - earliest).total_seconds() // 60)
    candidate_start = earliest + timedelta(minutes=random.randint(0, available_minutes))
    event_end = candidate_start + timedelta(minutes=15)

    tz_name = os.getenv("WORK_TIMEZONE", "America/Chihuahua")
    start_time = candidate_start.strftime("%Y-%m-%dT%H:%M:%S")
    end_time = event_end.strftime("%Y-%m-%dT%H:%M:%S")

    if attendees_override is not None:
        attendees_list = attendees_override
    else:
        # Build attendee list from the comma-separated ALERT_RECIPIENTS env var.
        attendees_list = []
        recipients_env = os.getenv("ALERT_RECIPIENTS", "")
        if recipients_env:
            emails = [email.strip() for email in recipients_env.split(",") if email.strip()]
            for email in emails:
                attendees_list.append(
                    {
                        "emailAddress": {"address": email, "name": email.split("@")[0].replace(".", " ").title()},
                        "type": "required",
                    }
                )

    payload = {
        "subject": subject,
        "body": {"contentType": "html", "content": html_content},
        "start": {"dateTime": start_time, "timeZone": tz_name},
        "end": {"dateTime": end_time, "timeZone": tz_name},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 0,
        "showAs": "free",
        "attendees": attendees_list,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Successfully injected Graph Calendar Event: {subject}")
        return True
    except Exception as e:
        log_error(f"Graph API Failed to create calendar event: {e}")
        return False

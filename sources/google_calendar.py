"""Google Calendar auto-source — the strongest automatic-context signal.

Each timed calendar event becomes a "who you were with" event: the window is the
meeting's start/end, the person is the other attendee(s), and the title is the summary.
All-day events, solo blocks (no other attendees), and events you declined are skipped.

Setup (see docs/AUTO_SOURCES_SETUP.md):
  1. Create an OAuth *Desktop* client in Google Cloud, download the JSON, and point
     GOOGLE_CREDENTIALS_PATH at it.
  2. First run opens a browser to authorize (scope: calendar.readonly); the token is
     cached at GOOGLE_TOKEN_PATH.
"""
from datetime import UTC, datetime, timedelta

import config

from . import Meeting

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _iso_to_epoch(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _name(att):
    return att.get("displayName") or (att.get("email") or "").split("@")[0] or "?"


def event_to_meeting(ev):
    """Raw Google Calendar event -> Meeting | None. Pure & unit-testable.

    Skips: all-day events, events with no other human attendee, and events you declined.
    """
    if not ev.get("id"):
        return None  # dedup icin kararli id sart
    start, end = ev.get("start", {}), ev.get("end", {})
    if "dateTime" not in start or "dateTime" not in end:
        return None  # all-day / date-only

    attendees = ev.get("attendees") or []
    me = next((a for a in attendees if a.get("self")), None)
    if me and me.get("responseStatus") == "declined":
        return None

    others = [a for a in attendees
              if not a.get("self") and not a.get("resource") and a.get("responseStatus") != "declined"]
    if not others:
        return None  # solo block — nobody to attribute

    if len(others) <= 3:
        person = ", ".join(_name(a) for a in others)
    else:
        person = f"{_name(others[0])} +{len(others) - 1}"

    return Meeting(
        ext_id=ev["id"],
        ts_start=_iso_to_epoch(start["dateTime"]),
        ts_end=_iso_to_epoch(end["dateTime"]),
        person=person,
        title=ev.get("summary"),
        tag="work",
        source="google_calendar",
    )


def _service():
    import os

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(config.GOOGLE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config.GOOGLE_CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(config.GOOGLE_TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch(days=None):
    days = days or config.SYNC_DAYS
    svc = _service()
    now = datetime.now(UTC)
    time_min = (now - timedelta(days=days)).isoformat()
    time_max = now.isoformat()

    items, page_token = [], None
    while True:  # tum sayfalari topla (busy takvim sessizce kesilmesin)
        resp = svc.events().list(
            calendarId=config.GOOGLE_CALENDAR_ID, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=250, pageToken=page_token,
        ).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return [m for m in (event_to_meeting(ev) for ev in items) if m]

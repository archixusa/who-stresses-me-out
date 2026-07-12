"""Tests for the pure (network-free) helpers in the auto-source modules."""
from sources import Meeting, google_calendar, slack


# ---------------- slack.cluster ----------------
def test_cluster_groups_into_two_windows():
    # Two bursts: three near t~100, two near t~100000
    result = slack.cluster([100, 130, 160, 100000, 100050], 60)

    assert result == [(100, 160, 3), (100000, 100050, 2)]


def test_cluster_handles_empty_and_single():
    assert slack.cluster([], 60) == []
    assert slack.cluster([5], 60) == [(5, 5, 1)]


# ---------------- google_calendar.event_to_meeting ----------------
def _timed_event(**over):
    ev = {
        "id": "evt-1",
        "start": {"dateTime": "2026-06-01T14:00:00+00:00"},
        "end": {"dateTime": "2026-06-01T15:00:00+00:00"},
        "summary": "1:1",
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
            {"email": "alex@example.com", "displayName": "Alex", "responseStatus": "accepted"},
        ],
    }
    ev.update(over)
    return ev


def test_timed_two_attendee_event_maps_to_meeting():
    m = google_calendar.event_to_meeting(_timed_event())

    assert isinstance(m, Meeting)
    assert m.person == "Alex"
    assert m.tag == "work"
    assert m.ext_id == "evt-1"
    assert m.source == "google_calendar"
    assert m.ts_start < m.ts_end


def test_all_day_event_is_skipped():
    ev = _timed_event(start={"date": "2026-06-01"}, end={"date": "2026-06-02"})
    assert google_calendar.event_to_meeting(ev) is None


def test_solo_event_with_only_self_is_skipped():
    ev = _timed_event(attendees=[{"email": "me@example.com", "self": True,
                                  "responseStatus": "accepted"}])
    assert google_calendar.event_to_meeting(ev) is None


def test_self_declined_event_is_skipped():
    ev = _timed_event(attendees=[
        {"email": "me@example.com", "self": True, "responseStatus": "declined"},
        {"email": "alex@example.com", "displayName": "Alex", "responseStatus": "accepted"},
    ])
    assert google_calendar.event_to_meeting(ev) is None


def test_event_without_id_is_skipped():
    ev = _timed_event()
    del ev["id"]
    assert google_calendar.event_to_meeting(ev) is None

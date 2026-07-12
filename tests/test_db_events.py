"""Tests for db event + participant behavior (multi-participant, confounders, deletion)."""
import db


def _get_event(eid):
    """Fetch a single event dict by id from the full list."""
    return next(e for e in db.get_events(0) if e["id"] == eid)


def test_add_event_sets_primary_friend_and_two_participant_rows():
    # Arrange / Act
    eid = db.add_event(ts_start=1000, participants=["Alex", "Sam"], tag="work")

    # Assert: primary is written to events.friend (backward-compat column)
    ev = _get_event(eid)
    assert ev["friend"] == "Alex"

    parts = db.get_event_participants(eid)
    assert [p["name"] for p in parts] == ["Alex", "Sam"]
    assert parts[0]["is_primary"] == 1
    assert parts[1]["is_primary"] == 0


def test_participant_names_from_rows_and_fallback_to_friend():
    # From participant rows
    eid = db.add_event(ts_start=1000, participants=["Alex", "Sam"])
    ev = _get_event(eid)
    assert db.participant_names(ev) == ["Alex", "Sam"]

    # Fallback: an event dict with no participant rows -> [friend]
    orphan = {"id": 999999, "friend": "Jordan"}
    assert db.participant_names(orphan) == ["Jordan"]


def test_add_participant_appends_without_changing_primary_and_dedupes():
    eid = db.add_event(ts_start=1000, participants=["Alex", "Sam"])

    # Appends a non-primary participant, keeps Alex as primary
    assert db.add_participant(eid, "Jordan") is True
    assert [p["name"] for p in db.get_event_participants(eid)] == ["Alex", "Sam", "Jordan"]
    assert _get_event(eid)["friend"] == "Alex"

    # Case-insensitive duplicate is rejected
    assert db.add_participant(eid, "alex") is False


def test_set_participants_replaces_all_and_updates_primary():
    eid = db.add_event(ts_start=1000, participants=["Alex", "Sam"])

    db.set_participants(eid, ["Sam", "Jordan"])

    parts = db.get_event_participants(eid)
    assert [p["name"] for p in parts] == ["Sam", "Jordan"]
    assert parts[0]["is_primary"] == 1
    assert _get_event(eid)["friend"] == "Sam"


def test_set_event_confounders_updates_only_given_fields():
    eid = db.add_event(ts_start=1000, participants=["Alex"], caffeine="low")

    # Only alcohol is provided -> caffeine untouched, illness stays None
    db.set_event_confounders(eid, alcohol=1)

    ev = _get_event(eid)
    assert ev["caffeine"] == "low"
    assert ev["alcohol"] == 1
    assert ev["illness"] is None


def test_delete_event_also_removes_participants():
    eid = db.add_event(ts_start=1000, participants=["Alex", "Sam"])

    assert db.delete_event(eid) is True

    assert db.get_event_participants(eid) == []
    assert all(e["id"] != eid for e in db.get_events(0))


def test_new_events_do_not_write_feeling():
    # The legacy `feeling` column exists but new inserts must leave it NULL.
    eid = db.add_event(ts_start=1000, participants=["Alex"])
    ev = _get_event(eid)
    assert "feeling" in ev            # column is present on the row
    assert ev["feeling"] is None      # ...but never populated

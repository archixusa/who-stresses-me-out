"""Tests for export.py (JSON/CSV) and db.wipe_all()."""
import json
import os

import db
import export


def _seed_events():
    db.add_event(ts_start=1000, participants=["Alex", "Sam"], tag="work",
                 notes="quarterly review")
    db.add_event(ts_start=2000, participants=["Jordan"], location="Office")


def test_write_export_json_has_warning_and_participants(tmp_path):
    # Arrange
    _seed_events()

    # Act
    path = export.write_export("json")

    # Assert: file written and parses
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    assert "_warning" in data
    assert data["events"], "expected exported events"
    # every exported event carries a participants field
    assert all("participants" in e for e in data["events"])
    joined = " | ".join(e["participants"] for e in data["events"])
    assert "Alex" in joined and "Sam" in joined and "Jordan" in joined


def test_to_csv_has_header_row_with_participants_column():
    # Arrange
    _seed_events()

    # Act
    text = export.to_csv()
    lines = text.splitlines()

    # Assert: comment banner, then a real header row containing the column
    assert lines[0].startswith("#")
    header = lines[1]
    assert "participants" in header.split(",")
    # at least one data row exists
    assert len(lines) >= 3


def test_wipe_all_empties_events_participants_and_hr():
    # Arrange
    _seed_events()
    db.upsert_hr([(1000, 60), (1060, 61)])
    before = db.data_summary()
    assert before["events"] > 0
    assert before["participants"] > 0
    assert before["hr_samples"] > 0

    # Act
    db.wipe_all()

    # Assert
    after = db.data_summary()
    assert after["events"] == 0
    assert after["participants"] == 0
    assert after["hr_samples"] == 0

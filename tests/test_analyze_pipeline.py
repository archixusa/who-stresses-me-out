"""End-to-end-ish test of analyze.run() over synthetic HR + events.

Timestamps are built in Europe/Istanbul (the app's LOCAL_TZ) so the awake-hour and
baseline windows line up with tzutil's local-time logic.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import analyze
import db

TZ = ZoneInfo("Europe/Istanbul")


def _epoch(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp())


def _hr_block(base, minutes, bpm):
    """One (ts, bpm) sample per minute for `minutes` minutes starting at `base`."""
    return [(base + i * 60, bpm) for i in range(minutes)]


def _seed_event_with_hr(start, calm=65, elevated=92):
    """Log a 2-participant event plus a calm pre-baseline and an elevated window."""
    end = start + 40 * 60
    # 90 min of calm baseline HR ending just before the event (awake hours)
    db.upsert_hr(_hr_block(start - 90 * 60, 90, calm))
    # 40 min of elevated HR during the event window
    db.upsert_hr(_hr_block(start, 40, elevated))
    db.add_event(ts_start=start, ts_end=end, participants=["Alex", "Sam"], tag="work")


def test_run_produces_evidence_group_and_no_feeling():
    # Arrange
    since = _epoch(2026, 6, 1, 0, 0)
    _seed_event_with_hr(_epoch(2026, 6, 1, 14, 0))
    _seed_event_with_hr(_epoch(2026, 6, 8, 14, 0))

    # Act
    res = analyze.run(since)

    # Assert: something matched HR and was analyzed
    assert res["events"], "expected at least one analyzed event"

    # every by_context row carries the full analytic envelope
    required = ("evidence", "ci", "coverage", "avg_elev", "group_frac", "confounded_frac")
    for row in res["by_context"]:
        for key in required:
            assert key in row, f"missing {key} in by_context row"

    # a 2-participant event is flagged as a group on the analyzed event
    analyzed = res["events"]
    assert all(e["is_group"] for e in analyzed)

    # ...and its context is entirely group-based
    ctx = analyze.context_label("Alex", "work")
    ctx_row = next(r for r in res["by_context"] if r["name"] == ctx)
    assert ctx_row["group_frac"] == 1.0

    # no mood/feeling leaks anywhere
    assert all("feeling" not in e for e in analyzed)
    assert "feeling_agreement" not in res


def test_analyzed_event_elevation_matches_synthetic_signal():
    # Arrange: calm 65, elevated 92 -> elevation ~ 27 BPM
    since = _epoch(2026, 6, 1, 0, 0)
    _seed_event_with_hr(_epoch(2026, 6, 1, 14, 0), calm=65, elevated=92)

    # Act
    res = analyze.run(since)

    # Assert
    assert res["events"]
    ev = res["events"][0]
    assert ev["baseline"] == 65.0
    assert ev["median"] == 92.0
    assert ev["elev"] == 27.0

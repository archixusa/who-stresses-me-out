"""Tests for db.init_db() migration of an OLD-schema events table.

Builds a pre-migration `events` table by hand, points config.DB_PATH at it, then
asserts init_db() adds the new columns and backfills legacy `friend` values into
`event_participants` — idempotently.
"""
import sqlite3

import config
import db

_OLD_SCHEMA = """
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_start   INTEGER NOT NULL,
    ts_end     INTEGER,
    friend     TEXT,
    location   TEXT,
    topic      TEXT,
    feeling    INTEGER,
    created_at INTEGER NOT NULL
);
"""

_NEW_COLUMNS = ("tag", "source", "ext_id", "caffeine", "alcohol",
                "illness", "commute", "notes")


def _build_old_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO events(ts_start, ts_end, friend, location, topic, feeling, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (1000, 2000, "Legacy", "Office", "sync", None, 1000),
        )
        conn.commit()
    finally:
        conn.close()


def test_init_db_migrates_columns_and_backfills_legacy_friend(tmp_path, monkeypatch):
    # Arrange: an old-schema DB with a single legacy row
    old = tmp_path / "old.db"
    _build_old_db(old)
    monkeypatch.setattr(config, "DB_PATH", str(old))

    # Act
    db.init_db()

    # Assert: all new columns were added
    with db.get_conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(events)").fetchall()}
    for col in _NEW_COLUMNS:
        assert col in cols

    # Assert: legacy friend was backfilled as the primary participant
    parts = db.get_event_participants(1)
    assert parts[0]["name"] == "Legacy"
    assert parts[0]["is_primary"] == 1


def test_init_db_is_idempotent_and_does_not_double_backfill(tmp_path, monkeypatch):
    # Arrange
    old = tmp_path / "old.db"
    _build_old_db(old)
    monkeypatch.setattr(config, "DB_PATH", str(old))

    # Act: run migration twice
    db.init_db()
    db.init_db()  # must not raise or duplicate the backfilled participant

    # Assert
    parts = db.get_event_participants(1)
    assert len(parts) == 1
    assert parts[0]["name"] == "Legacy"

"""SQLite katmani. Zaman damgalari epoch saniye (int, UTC) olarak tutulur."""
import sqlite3
from contextlib import contextmanager

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS friends (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_start   INTEGER NOT NULL,
    ts_end     INTEGER,
    friend     TEXT,
    location   TEXT,
    topic      TEXT,
    tag        TEXT,
    feeling    INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shortcuts (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    friend TEXT NOT NULL,
    tag    TEXT NOT NULL,
    ord    INTEGER DEFAULT 0,
    UNIQUE(friend, tag)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS hr_cache (
    ts  INTEGER PRIMARY KEY,
    bpm INTEGER NOT NULL
);
-- Resmi API'den gunluk baglam
CREATE TABLE IF NOT EXISTS daily (
    day        TEXT PRIMARY KEY,          -- YYYY-MM-DD (yerel)
    recovery   REAL,
    hrv        REAL,
    rhr        REAL,
    strain     REAL,
    sleep_perf REAL,
    updated_at INTEGER
);
-- Resmi API'den workout pencereleri (aktivite dislama icin)
CREATE TABLE IF NOT EXISTS workouts (
    id       TEXT PRIMARY KEY,            -- v2 UUID
    ts_start INTEGER NOT NULL,
    ts_end   INTEGER NOT NULL,
    sport    TEXT,
    strain   REAL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(ts_start);
CREATE INDEX IF NOT EXISTS idx_workouts_start ON workouts(ts_start);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")  # eszamanli bot+sync+report kilit beklesin
        yield conn
        conn.commit()
    finally:
        conn.close()


DEFAULT_SHORTCUTS = [
    ("Alex", "daily"), ("Alex", "work"),
    ("Sam", "casual"), ("Sam", "ex"), ("Sam", "work"),
    ("Jordan", "daily"), ("Jordan", "family"),
]


def init_db():
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        # Migration: eski events tablosuna tag kolonu ekle (idempotent + yaris-guvenli)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        if "tag" not in cols:
            try:
                conn.execute("ALTER TABLE events ADD COLUMN tag TEXT")
            except sqlite3.OperationalError:
                pass  # baska surec ayni anda ekledi (duplicate column) — sorun degil
        # Varsayilan kisayollari SADECE bir kez yukle (kullanici hepsini silerse geri gelmesin)
        seeded = conn.execute("SELECT 1 FROM meta WHERE key='shortcuts_seeded'").fetchone()
        if not seeded:
            n = conn.execute("SELECT COUNT(*) c FROM shortcuts").fetchone()["c"]
            if n == 0:
                conn.executemany(
                    "INSERT OR IGNORE INTO shortcuts(friend, tag, ord) VALUES (?,?,?)",
                    [(f, t, i) for i, (f, t) in enumerate(DEFAULT_SHORTCUTS)],
                )
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('shortcuts_seeded','1')")


# --- friends / locations ---
def add_name(table, name):
    if table not in ("friends", "locations"):
        raise ValueError("gecersiz tablo")
    name = (name or "").strip()
    if not name:
        return
    with get_conn() as conn:
        conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (name,))


def list_names(table, limit=12):
    if table not in ("friends", "locations"):
        raise ValueError("gecersiz tablo")
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT name FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [r["name"] for r in rows]


# --- events ---
def add_event(ts_start, friend=None, location=None, topic=None, feeling=None,
              created_at=None, ts_end=None, tag=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO events(ts_start, ts_end, friend, location, topic, tag, feeling, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts_start, ts_end, friend, location, topic, tag, feeling, created_at or ts_start),
        )
        return cur.lastrowid


# --- shortcuts (hizli butonlar) ---
def list_shortcuts():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM shortcuts ORDER BY ord, id"
        ).fetchall()
    return [dict(r) for r in rows]


def add_shortcut(friend, tag):
    """Doner: 'added' | 'exists' | 'invalid'."""
    friend, tag = (friend or "").strip(), (tag or "").strip()
    if not friend or not tag:
        return "invalid"
    with get_conn() as conn:
        mx = conn.execute("SELECT COALESCE(MAX(ord),-1)+1 n FROM shortcuts").fetchone()["n"]
        cur = conn.execute("INSERT OR IGNORE INTO shortcuts(friend, tag, ord) VALUES (?,?,?)",
                           (friend, tag, mx))
        return "added" if cur.rowcount > 0 else "exists"


def delete_shortcut(shortcut_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM shortcuts WHERE id=?", (shortcut_id,))
        return cur.rowcount > 0


def set_event_feeling(event_id, feeling):
    with get_conn() as conn:
        conn.execute("UPDATE events SET feeling=? WHERE id=?", (feeling, event_id))


def set_event_topic(event_id, topic):
    with get_conn() as conn:
        conn.execute("UPDATE events SET topic=? WHERE id=?", (topic, event_id))


def close_latest_open_event(ts_end, max_age_sec):
    """En son acik event'i kapatir. Cok eski (max_age_sec ustu) ise otomatik
    kapatmaz, uyari doner. Doner: (id, "ok" | None, "no_open" | "too_old")."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, ts_start FROM events WHERE ts_end IS NULL ORDER BY ts_start DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None, "no_open"
        if ts_end - row["ts_start"] > max_age_sec:
            return row["id"], "too_old"
        conn.execute("UPDATE events SET ts_end=? WHERE id=?", (ts_end, row["id"]))
        return row["id"], "ok"


def close_open_events(now, recent_max_sec, default_window_sec):
    """TUM acik event'leri kapatir (yeni bulusma baslarken orphan birikmesin):
    yakinlar -> ts_end=now; cok eskiler -> ts_end=ts_start+default_window (dev pencere olmasin).
    Doner: {'closed': n, 'stale': m}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, ts_start FROM events WHERE ts_end IS NULL"
        ).fetchall()
        stale = 0
        for r in rows:
            if now - r["ts_start"] <= recent_max_sec:
                end = now
            else:
                end = r["ts_start"] + default_window_sec
                stale += 1
            conn.execute("UPDATE events SET ts_end=? WHERE id=?", (end, r["id"]))
        return {"closed": len(rows), "stale": stale}


def get_events(since_ts):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE ts_start >= ? ORDER BY ts_start ASC", (since_ts,)
        ).fetchall()
    return [dict(r) for r in rows]


def recent_events(limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY ts_start DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_event(event_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        return cur.rowcount > 0


# --- heart rate ---
def upsert_hr(samples):
    """samples: iterable of (ts_epoch, bpm)."""
    with get_conn() as conn:
        conn.executemany("INSERT OR REPLACE INTO hr_cache(ts, bpm) VALUES (?,?)", samples)


def get_hr(start_ts, end_ts):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, bpm FROM hr_cache WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
            (start_ts, end_ts),
        ).fetchall()
    return [(r["ts"], r["bpm"]) for r in rows]


def hr_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM hr_cache").fetchone()["c"]


# --- resmi API: daily + workouts ---
def upsert_daily(day, recovery=None, hrv=None, rhr=None, strain=None, sleep_perf=None,
                 updated_at=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily(day, recovery, hrv, rhr, strain, sleep_perf, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
            "recovery=COALESCE(excluded.recovery, recovery), "
            "hrv=COALESCE(excluded.hrv, hrv), rhr=COALESCE(excluded.rhr, rhr), "
            "strain=COALESCE(excluded.strain, strain), "
            "sleep_perf=COALESCE(excluded.sleep_perf, sleep_perf), "
            "updated_at=excluded.updated_at",
            (day, recovery, hrv, rhr, strain, sleep_perf, updated_at),
        )


def get_daily(day):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM daily WHERE day=?", (day,)).fetchone()
    return dict(row) if row else None


def upsert_workout(wid, ts_start, ts_end, sport=None, strain=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workouts(id, ts_start, ts_end, sport, strain) "
            "VALUES (?,?,?,?,?)", (wid, ts_start, ts_end, sport, strain),
        )


def workouts_overlapping(start_ts, end_ts):
    """Verilen araligi kesen workout'lar -> [(ts_start, ts_end)]."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts_start, ts_end FROM workouts WHERE ts_start < ? AND ts_end > ?",
            (end_ts, start_ts),
        ).fetchall()
    return [(r["ts_start"], r["ts_end"]) for r in rows]

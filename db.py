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
    friend     TEXT,                     -- birincil kisi (geriye uyumluluk); tumu event_participants'ta
    location   TEXT,
    topic      TEXT,
    tag        TEXT,
    feeling    INTEGER,                  -- LEGACY/DEPRECATED: artik yazilmaz/kullanilmaz (eski veri korunur)
    source     TEXT DEFAULT 'manual',   -- manual | google_calendar | slack | ...
    ext_id     TEXT,                     -- kaynak-taraf id (dedup icin)
    caffeine   TEXT,                     -- opsiyonel confounder: none | low | high
    alcohol    INTEGER,                  -- opsiyonel confounder: 0/1
    illness    INTEGER,                  -- opsiyonel confounder: 0/1
    commute    INTEGER,                  -- opsiyonel confounder: 0/1 (yuruyus/ulasim)
    notes      TEXT,                     -- opsiyonel serbest not
    created_at INTEGER NOT NULL
);
-- Cok katilimcili gorusme: bir event'in tum katilimcilari (birincil dahil)
CREATE TABLE IF NOT EXISTS event_participants (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER NOT NULL,
    name       TEXT NOT NULL,
    is_primary INTEGER DEFAULT 0,
    ord        INTEGER DEFAULT 0
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
        # Migration: eski events tablosuna eksik kolonlari ekle (idempotent + yaris-guvenli)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        for col, ddl in (("tag", "tag TEXT"),
                         ("source", "source TEXT DEFAULT 'manual'"),
                         ("ext_id", "ext_id TEXT"),
                         ("caffeine", "caffeine TEXT"),
                         ("alcohol", "alcohol INTEGER"),
                         ("illness", "illness INTEGER"),
                         ("commute", "commute INTEGER"),
                         ("notes", "notes TEXT")):
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE events ADD COLUMN {ddl}")
                except sqlite3.OperationalError:
                    pass  # baska surec ayni anda ekledi — sorun degil
        # ext_id kolonu kesin varken olustur: ayni dis-kaynak olayi iki kez eklenmesin
        # (manuel olaylarda ext_id NULL -> UNIQUE'e takilmaz)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_ext "
            "ON events(source, ext_id) WHERE ext_id IS NOT NULL"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_participants_event "
                     "ON event_participants(event_id)")
        # Eski events.friend degerlerini bir kez event_participants'a tasi (birincil)
        backfilled = conn.execute("SELECT 1 FROM meta WHERE key='participants_backfilled'").fetchone()
        if not backfilled:
            rows = conn.execute(
                "SELECT id, friend FROM events WHERE friend IS NOT NULL AND friend != '' "
                "AND id NOT IN (SELECT DISTINCT event_id FROM event_participants)"
            ).fetchall()
            conn.executemany(
                "INSERT INTO event_participants(event_id, name, is_primary, ord) VALUES (?,?,1,0)",
                [(r["id"], r["friend"]) for r in rows],
            )
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('participants_backfilled','1')")
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
def _norm_names(names):
    """Kisi adlarini temizler, sirasi bozulmadan tekrarsizlastirir."""
    out, seen = [], set()
    for n in names or []:
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _insert_participants(conn, event_id, names):
    conn.executemany(
        "INSERT INTO event_participants(event_id, name, is_primary, ord) VALUES (?,?,?,?)",
        [(event_id, n, 1 if i == 0 else 0, i) for i, n in enumerate(names)],
    )


def add_event(ts_start, friend=None, participants=None, location=None, topic=None,
              created_at=None, ts_end=None, tag=None, source="manual", ext_id=None,
              caffeine=None, alcohol=None, illness=None, commute=None, notes=None):
    """Yeni event ekler. feeling YAZILMAZ (legacy alan). participants verilirse ilki
    birincil kabul edilir; verilmezse friend birincil olur."""
    names = _norm_names(participants) if participants else _norm_names([friend])
    primary = names[0] if names else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO events(ts_start, ts_end, friend, location, topic, tag, source, "
            "ext_id, caffeine, alcohol, illness, commute, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts_start, ts_end, primary, location, topic, tag, source, ext_id,
             caffeine, alcohol, illness, commute, notes, created_at or ts_start),
        )
        eid = cur.lastrowid
        _insert_participants(conn, eid, names)
        return eid


# --- katilimcilar (cok kisili gorusme) ---
def get_event_participants(event_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, is_primary FROM event_participants WHERE event_id=? ORDER BY ord, id",
            (event_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def participant_names(event):
    """Bir event dict'i icin katilimci adlari; participant kaydi yoksa friend'e duser."""
    parts = get_event_participants(event["id"])
    if parts:
        return [p["name"] for p in parts]
    return _norm_names([event.get("friend")])


def add_participant(event_id, name):
    name = (name or "").strip()
    if not name:
        return False
    with get_conn() as conn:
        existing = [r["name"].lower() for r in conn.execute(
            "SELECT name FROM event_participants WHERE event_id=?", (event_id,)).fetchall()]
        if name.lower() in existing:
            return False
        mx = conn.execute("SELECT COALESCE(MAX(ord),-1)+1 n FROM event_participants "
                          "WHERE event_id=?", (event_id,)).fetchone()["n"]
        primary = 1 if mx == 0 else 0
        conn.execute("INSERT INTO event_participants(event_id, name, is_primary, ord) "
                     "VALUES (?,?,?,?)", (event_id, name, primary, mx))
        if primary:  # ilk katilimci ayni zamanda events.friend olsun
            conn.execute("UPDATE events SET friend=? WHERE id=?", (name, event_id))
    return True


def set_participants(event_id, names):
    """Bir event'in katilimcilarini tumden degistirir (ilk = birincil)."""
    names = _norm_names(names)
    with get_conn() as conn:
        conn.execute("DELETE FROM event_participants WHERE event_id=?", (event_id,))
        _insert_participants(conn, event_id, names)
        conn.execute("UPDATE events SET friend=? WHERE id=?",
                     (names[0] if names else None, event_id))


def set_event_confounders(event_id, caffeine=None, alcohol=None, illness=None,
                          commute=None, notes=None):
    """Verilen (None olmayan) confounder alanlarini gunceller; digerlerine dokunmaz."""
    fields, vals = [], []
    for col, val in (("caffeine", caffeine), ("alcohol", alcohol), ("illness", illness),
                     ("commute", commute), ("notes", notes)):
        if val is not None:
            fields.append(f"{col}=?")
            vals.append(val)
    if not fields:
        return
    vals.append(event_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=?", vals)


def upsert_external_event(source, ext_id, ts_start, ts_end, friend=None, topic=None,
                          tag=None, created_at=None):
    """Dis kaynaktan (takvim/Slack) gelen olayi (source, ext_id) ile tekilleştirir:
    yoksa ekler, varsa zaman/kişi/başlığı gunceller. Doner: 'inserted' | 'updated'.
    Kullanicinin elle duzenledigi feeling/location'a dokunmaz."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM events WHERE source=? AND ext_id=?", (source, ext_id)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE events SET ts_start=?, ts_end=?, friend=?, topic=?, tag=? WHERE id=?",
                (ts_start, ts_end, friend, topic, tag, row["id"]),
            )
            return "updated"
        conn.execute(
            "INSERT INTO events(ts_start, ts_end, friend, topic, tag, source, ext_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts_start, ts_end, friend, topic, tag, source, ext_id, created_at or ts_start),
        )
        return "inserted"


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


def delete_external_window(source, ts_from, ts_to):
    """Bir kaynagin penceredeki dis olaylarini siler (kararsiz sinirli kaynaklarda —
    or. Slack — her sync'te sil-ve-yeniden-yaz semantigi icin). Doner: silinen sayisi."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM events WHERE source=? AND ext_id IS NOT NULL "
            "AND ts_start >= ? AND ts_start <= ?",
            (source, ts_from, ts_to),
        )
        return cur.rowcount


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
        conn.execute("DELETE FROM event_participants WHERE event_id=?", (event_id,))
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


# --- veri yasam dongusu: ozet / export / sil ---
def data_summary():
    """Saklanan lokal verinin ozeti (/verilerim icin)."""
    with get_conn() as conn:
        def c(q):
            return conn.execute(q).fetchone()[0]
        hr_min = conn.execute("SELECT MIN(ts) FROM hr_cache").fetchone()[0]
        hr_max = conn.execute("SELECT MAX(ts) FROM hr_cache").fetchone()[0]
        return {
            "events": c("SELECT COUNT(*) FROM events"),
            "participants": c("SELECT COUNT(*) FROM event_participants"),
            "hr_samples": c("SELECT COUNT(*) FROM hr_cache"),
            "hr_from": hr_min, "hr_to": hr_max,
            "daily_days": c("SELECT COUNT(*) FROM daily"),
            "workouts": c("SELECT COUNT(*) FROM workouts"),
            "shortcuts": c("SELECT COUNT(*) FROM shortcuts"),
        }


def export_events(since_ts=0):
    """Tum event'leri katilimcilariyla export icin doner (JSON/CSV)."""
    events = get_events(since_ts)
    for e in events:
        e["participants"] = [p["name"] for p in get_event_participants(e["id"])]
    return events


def wipe_all():
    """Tum kisisel/lokal veriyi siler (events, katilimcilar, HR, daily, workouts, kisayollar).
    meta (seed isaretleri) korunur -> varsayilan kisayollar geri gelmez."""
    with get_conn() as conn:
        for t in ("event_participants", "events", "hr_cache", "daily", "workouts", "shortcuts"):
            conn.execute(f"DELETE FROM {t}")

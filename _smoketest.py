"""End-to-end smoke test on synthetic data (no WHOOP/Telegram/network needed).

Verifies the reshaped pipeline: association analysis with evidence levels + bootstrap CI,
multi-participant events, confounder flags, matched-control field, that the legacy `feeling`
field is never written or surfaced, report language is non-causal & feeling-free, plus
export and the old-DB migration path. Detailed unit tests live in tests/ (pytest).
"""
import os
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("DB_PATH", "_smoketest.db")
os.environ.setdefault("LOCAL_TZ", "Europe/Istanbul")
for f in ("_smoketest.db", "_smoketest.db-wal", "_smoketest.db-shm"):
    if os.path.exists(f):
        os.remove(f)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import analyze
import bot
import db
import export
import report

IST = ZoneInfo("Europe/Istanbul")
db.init_db()


def ep(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def fill(day0, from_min, to_min, bpm):
    db.upsert_hr([(day0 + t * 60, bpm) for t in range(from_min, to_min)])


g1 = ep(2026, 7, 10, 0)
fill(g1, 0, 7 * 60, 50)          # sleep
fill(g1, 7 * 60, 24 * 60, 65)    # awake resting
fill(g1, 15 * 60, 16 * 60, 92)   # Sam high
fill(g1, 18 * 60, 19 * 60, 68)   # group mild

# Sam · ex — single-participant, high signal
e1 = db.add_event(ts_start=ep(2026, 7, 10, 15), ts_end=ep(2026, 7, 10, 16),
                  participants=["Sam"], location="Cafe", tag="ex", created_at=g1)
# Group meeting (2 participants) — attribution must be limited
e2 = db.add_event(ts_start=ep(2026, 7, 10, 18), ts_end=ep(2026, 7, 10, 19),
                  participants=["Alex", "Jordan"], tag="work", created_at=g1)
db.set_event_confounders(e2, alcohol=1)  # confounder flag

res = analyze.run(g1 - 86400)
print("=== analyze ===")
for r in res["by_context"]:
    print(f"  {r['name']}: elev {r['avg_elev']} evidence={r['evidence']} ci={r['ci']} "
          f"group_frac={r['group_frac']} conf={r['confounded_frac']}")

evby = {r["name"]: r for r in res["by_context"]}
evev = {e["primary"]: e for e in res["events"]}

# 1) No feeling anywhere
assert all("feeling" not in e for e in res["events"]), "feeling leaked into analysis"
assert "feeling_agreement" not in res, "feeling_agreement must be gone"
# 2) Evidence + CI present on every group
for r in res["by_context"]:
    assert "evidence" in r and "ci" in r and "coverage" in r
# 3) Multi-participant event flagged
assert evev["Alex"]["is_group"] is True and len(evev["Alex"]["participants"]) == 2
assert evev["Sam"]["is_group"] is False
# 4) Confounder detected on the group event
assert evev["Alex"]["confounded"] is True
# 5) matched-control field exists (may be None without control data)
assert "control_elev" in evev["Sam"]
print("analyze invariants: OK")

rep = report.build_report(days=30)
print("\n=== report (head) ===")
print("\n".join(rep.splitlines()[:6]))
low = rep.lower()
assert "associations, not causes" in low, "non-causal disclaimer missing"
for banned in ("feeling", "mood", "how did you feel", "1-5", "tension"):
    assert banned not in low, f"banned emotion language present: {banned}"
print("report language: OK (non-causal, feeling-free)")

# --- export ---
path = export.write_export("json")
assert os.path.exists(path)
import json as _json
data = _json.load(open(path, encoding="utf-8"))
assert data["events"] and "participants" in data["events"][0]
os.remove(path)
print("export: OK")

# --- bot helpers still pure/importable ---
assert bot._label({"friend": "Sam", "tag": "ex"}) == "Sam · ex"
assert callable(bot._main_keyboard)
print("bot helpers: OK")

# --- old-DB migration path (no tag/source/participants/confounders) ---
OLD = "_smoke_old.db"
for f in (OLD, OLD + "-wal", OLD + "-shm"):
    if os.path.exists(f):
        os.remove(f)
c = sqlite3.connect(OLD)
c.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_start INTEGER NOT NULL, "
          "ts_end INTEGER, friend TEXT, location TEXT, topic TEXT, feeling INTEGER, created_at INTEGER NOT NULL)")
c.execute("INSERT INTO events(ts_start, friend, feeling, created_at) VALUES (100,'Legacy',4,100)")
c.commit(); c.close()
os.environ["DB_PATH"] = OLD
import importlib
import config as _cfg
importlib.reload(_cfg)          # config.DB_PATH -> OLD (db reads it fresh per call)
db.init_db()
with db.get_conn() as _c:
    cols = [r["name"] for r in _c.execute("PRAGMA table_info(events)").fetchall()]
assert {"tag", "source", "ext_id", "caffeine", "alcohol", "illness", "commute", "notes"} <= set(cols)
parts = db.get_event_participants(1)
assert parts and parts[0]["name"] == "Legacy", "friend not backfilled to participants"
print("old-DB migration + participant backfill: OK")
for f in (OLD, OLD + "-wal", OLD + "-shm", "_smoketest.db", "_smoketest.db-wal", "_smoketest.db-shm"):
    if os.path.exists(f):
        os.remove(f)

print("\n✅ ALL SMOKE CHECKS PASSED")

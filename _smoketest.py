"""Sentetik veriyle db + analyze + report'u dogrular. Whoop/Telegram gerekmez.

Kapsam: yeni on-pencere baseline (uykuyu haric), workout dislama, HTML escaping,
yerel saat gosterimi, his-uyumu, kucuk-orneklem geri cekme, DB hijyen metodlari.
"""
import os
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

import db, analyze, report

IST = ZoneInfo("Europe/Istanbul")
db.init_db()


def ep(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def fill(day_ep_0, from_min, to_min, bpm):
    db.upsert_hr([(day_ep_0 + t * 60, bpm) for t in range(from_min, to_min)])


# --- Gun 1: 2026-07-10, gece dusuk (uyku 50), gunduz dinlenme 65 ---
g1 = ep(2026, 7, 10, 0)
fill(g1, 0, 7 * 60, 50)         # 00:00-07:00 uyku
fill(g1, 7 * 60, 24 * 60, 65)   # 07:00-24:00 dinlenme
fill(g1, 15 * 60, 16 * 60, 70)  # Leo 15:00-16:00 hafif
fill(g1, 22 * 60, 23 * 60, 95)  # Nate 22:00-23:00 yuksek

# --- Gun 2: 2026-07-11, workout dislama testi ---
g2 = ep(2026, 7, 11, 0)
fill(g2, 0, 7 * 60, 50)
fill(g2, 7 * 60, 24 * 60, 65)
fill(g2, 18 * 60, 18 * 60 + 30, 140)   # 18:00-18:30 SPOR (yuksek)
fill(g2, 18 * 60 + 30, 19 * 60, 72)    # 18:30-19:00 cooldown
db.upsert_workout("wk1", ep(2026, 7, 11, 18), ep(2026, 7, 11, 18, 30), "running", 12.0)

# Events (Nate adinda kasitli Markdown/HTML tehlikeli karakter var)
db.add_event(ts_start=ep(2026, 7, 10, 22), ts_end=ep(2026, 7, 10, 23),
             friend="Nate_<b>x", location="Cafe", topic="debt_talk", feeling=5, created_at=g1)
db.add_event(ts_start=ep(2026, 7, 10, 15), ts_end=ep(2026, 7, 10, 16),
             friend="Leo", location="Park", topic="football", feeling=2, created_at=g1)
db.add_event(ts_start=ep(2026, 7, 11, 18), ts_end=ep(2026, 7, 11, 19),
             friend="Gym", location="Studio", topic="run", feeling=3, created_at=g2)

res = analyze.run(g1 - 86400)
by = {r["name"]: r for r in res["by_friend"]}
ev = {e["friend"]: e for e in res["events"]}

print("=== analyze ===")
for r in res["by_friend"]:
    print(f"  {r['name']}: avg +{r['avg_elev']} adj +{r['adj_elev']} n={r['count']}")
for e in res["events"]:
    print(f"  [{e['friend']}] baseline={e['baseline']} ({e['base_method']}) "
          f"elev={e['elev']} wo_excl={e['workout_excluded']}")

# 1) On-pencere baseline uykuyu HARIC tutmali -> Nate baseline ~65 (50 degil)
assert ev["Nate_<b>x"]["base_method"] == "on-pencere", "on-pencere baseline kullanilmali"
assert 63 <= ev["Nate_<b>x"]["baseline"] <= 67, f"baseline uyku sizdirdi: {ev['Nate_<b>x']['baseline']}"
# 2) Nate (yuksek) Leo'ten (hafif) yuksek siralanmali
assert res["by_friend"][0]["name"] == "Nate_<b>x"
assert by["Nate_<b>x"]["avg_elev"] > by["Leo"]["avg_elev"]
# 3) Workout dislama: ilk 10dk trim (18:00-18:10) sonra workout 18:10-18:30 = 20 ornek
assert ev["Gym"]["workout_excluded"] == 20, f"workout dislanmadi: {ev['Gym']['workout_excluded']}"
# Gym elev'i cooldown (72) uzerinden hesaplanmali, 140 spike'i degil
assert ev["Gym"]["elev"] < 15, f"spor spike'i sizdi: {ev['Gym']['elev']}"
# 4) His-uyumu hesaplandi
assert res["feeling_agreement"] is not None and res["feeling_agreement"]["n"] == 3

rep = report.build_report(days=30)
print("\n=== report ===")
print(rep)

# 5) HTML escaping: tehlikeli kullanici metni kacisli olmali (ham <b> raporu bozmasin)
assert "&lt;b&gt;" in rep, "kullanici <b> kacislanmadi (Markdown/HTML cokme riski)"
assert "Nate_&lt;b&gt;x" in rep, "kullanici adi beklendigi gibi kacislanmadi"
# 6) Yerel saat: Nate event'i 22:00 Istanbul -> raporda 22:00 (UTC 19:00 DEGIL)
assert "22:00" in rep, "yerel saat gosterilmiyor"

# --- DB hijyen metodlari ---
print("\n=== db hijyen ===")
rec = db.recent_events(10)
assert len(rec) == 3
# OldA acik event -> too_old
old_open = db.add_event(ts_start=ep(2026, 7, 8, 12), friend="OldA", created_at=g1)
eid, status = db.close_latest_open_event(ep(2026, 7, 12, 12), 240 * 60)
assert status == "too_old", f"eski acik event korunmali: {status}"
# Fresh acik event -> ok
import time as _t
db.add_event(ts_start=int(_t.time()) - 60, friend="Fresh")
eid2, status2 = db.close_latest_open_event(int(_t.time()), 240 * 60)
assert status2 == "ok", f"yeni acik event kapanmali: {status2}"
# delete
assert db.delete_event(old_open) is True
assert db.delete_event(999999) is False
print("recent_events, close(too_old/ok), delete: OK")

# --- HR chunk tiling: bosluksuz, <=7 gun, ust uste binmez ---
import whoop_source, bot
from datetime import date, timedelta

wins = whoop_source.tile_windows("2026-07-01", "2026-07-20", 7)
days = []
for s, e in wins:
    ds = date.fromisoformat(s)
    de = date.fromisoformat(e)
    span = (de - ds).days + 1
    assert span <= 7, f"pencere 7 gunu asti: {s}..{e} ({span})"
    d = ds
    while d <= de:
        days.append(d)
        d += timedelta(days=1)
assert days == sorted(days), "pencereler sirali degil"
assert len(days) == len(set(days)), "pencereler ust uste bindi"
full = [date(2026, 7, 1) + timedelta(days=i) for i in range(20)]
assert days == full, "tiling [d0,d1]'i tam dosemedi (bosluk var)"
print(f"tile_windows: {len(wins)} pencere, 1-20 Tem tam doseme OK")

# --- render-id: eski klavye tapi reddedilmeli ---
from types import SimpleNamespace
fake = SimpleNamespace(user_data={"fr_rid": 5, "snap_5": ["A", "B"]})
assert bot._resolve(fake, "fr", "fr:5:i:1") == ("pick", "B")
assert bot._resolve(fake, "fr", "fr:4:i:1") == ("expired", None)   # eski rid
assert bot._resolve(fake, "fr", "fr:5:i:9") == ("pick", None)      # aralik disi
assert bot._resolve(fake, "fr", "fr:5:new")[0] == "new"
print("render-id resolve (pick/expired/aralik-disi/new): OK")

# --- set_event_topic ---
tid = db.add_event(ts_start=int(_t.time()) - 30, friend="Ted")
db.set_event_topic(tid, "updated topic")
assert any(e["id"] == tid and e["topic"] == "updated topic" for e in db.recent_events(5))
print("set_event_topic: OK")

# --- kisayollar + tag/baglam boyutu ---
scs = db.list_shortcuts()
assert len(scs) == 7, f"varsayilan kisayol sayisi: {len(scs)}"
assert any(s["friend"] == "Sam" and s["tag"] == "ex" for s in scs), "Sam·ex seed yok"
assert db.add_shortcut("Test", "x") == "added"
assert db.add_shortcut("Test", "x") == "exists"   # duplicate -> yaniltici basari yok
assert db.add_shortcut("", "x") == "invalid"
sid = [s["id"] for s in db.list_shortcuts() if s["friend"] == "Test"][0]
assert db.delete_shortcut(sid) is True
assert analyze.context_label("Sam", "ex") == "Sam · ex"
assert analyze.context_label("Nate", None) == "Nate"
print("kisayollar (added/exists/invalid/delete) + context_label: OK")

# re-seed marker: hepsini sil -> init_db tekrar -> geri GELMEMELI
for s in db.list_shortcuts():
    db.delete_shortcut(s["id"])
db.init_db()
assert len(db.list_shortcuts()) == 0, "silinen kisayollar yeniden yuklendi (marker bug)"
print("re-seed marker (silinen geri gelmiyor): OK")

# close_open_events: yakin -> now, cok eski -> start+default
now = int(_t.time())
db.close_open_events(now, 240 * 60, 90 * 60)                     # onceki testlerden kalanlari temizle
db.add_event(ts_start=now - 60, friend="Recent")                 # yakin acik
db.add_event(ts_start=now - 10 * 3600, friend="OldB")          # 10 saat once acik (stale)
r = db.close_open_events(now, 240 * 60, 90 * 60)
assert r["closed"] == 2 and r["stale"] == 1, f"close_open_events: {r}"
assert not any(e["ts_end"] is None for e in db.recent_events(10)), "acik event kaldi (orphan)"
print("close_open_events (orphan supurme): OK")

# tag'li event -> by_context ayrimi
tt = int(_t.time())
db.add_event(ts_start=ep(2026, 7, 10, 15), ts_end=ep(2026, 7, 10, 16),
             friend="Sam", tag="ex", topic="x", created_at=tt)
# Gun-1 15:00-16:00 icin HR zaten dolu (65 baseline, 70 event) -> analiz eslesir
res2 = analyze.run(g1 - 86400)
ctx_names = [r["name"] for r in res2["by_context"]]
assert "Sam · ex" in ctx_names, f"baglam ayrimi yok: {ctx_names}"
print("by_context (Sam · ex ayri): OK")

# bot importu (yeni handler'lar + kisayol klavyesi kurulumu)
db.add_shortcut("Sam", "ex")   # marker testi sildi, geri ekle
assert bot._label({"friend": "Sam", "tag": "ex"}) == "Sam · ex"
assert bot._find_shortcut("Sam · ex") is not None
assert bot._find_shortcut("yok · yok") is None
print("bot kisayol yardimcilari: OK")

print("\n✅ TUM KONTROLLER GECTI")

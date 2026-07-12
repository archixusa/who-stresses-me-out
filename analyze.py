"""Stres proxy motoru (v2).

Whoop resmi API'si "Stres Monitoru" skorunu vermez; stresi dakikalik nabizdan
tureyen bir PROXY ile olcuyoruz. v1'e gore duzeltmeler:
  * Baseline = bulusma ONCESI dinlenme penceresi (uykuyu haric tutar, sirkadiyen
    kaymayi azaltir); yoksa uyanik-gun, yoksa tum-gun'e duser.
  * Pencere istatistigi MEDYAN + ilk dakikalar kirpilir (varis/yuruyus) ve
    resmi API'den bilinen WORKOUT dakikalari cikarilir (aktivite konfonu).
  * off-wrist 0 bpm ornekleri filtrelenir.
  * Siralama kucuk-orneklem icin geri cekilir (shrinkage).
Yine de bu bir nabiz proxy'sidir, klinik stres degil.
"""
from collections import defaultdict

import config
import db
import tzutil

SEP = " · "


def context_label(friend, tag):
    """Kisi + baglam -> 'Sam · ex' (tag yoksa sadece kisi)."""
    if friend and tag:
        return f"{friend}{SEP}{tag}"
    return friend or tag or None


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2


def _clean(samples):
    """(ts,bpm) listesinden gecerli (bpm>0) olanlari doner."""
    return [(t, b) for t, b in samples if b and b > 0]


def _exclude_workouts(samples, start, end):
    wins = db.workouts_overlapping(start, end)
    if not wins:
        return samples, 0
    kept, dropped = [], 0
    for t, b in samples:
        if any(ws <= t < we for ws, we in wins):
            dropped += 1
        else:
            kept.append((t, b))
    return kept, dropped


def _awake(samples):
    """Uyku saatlerini (yerel 00-07 ve 23-24) haric tutar."""
    return [(t, b) for t, b in samples if 7 <= tzutil.local_hour(t) < 23]


def _baseline(start):
    """Bulusma oncesi dinlenme nabzi tahmini (bpm) + hangi yontem kullanildi.
    On-pencere de uyku saatlerini dislar (erken sabah bulusmalarinda uyku sizmasin)."""
    pre = _awake(_clean(db.get_hr(start - config.BASELINE_PRE_MIN * 60, start)))
    if len(pre) >= config.MIN_BASELINE_SAMPLES:
        return _percentile(sorted(b for _, b in pre), 0.25), "on-pencere"

    day_start, day_end = tzutil.local_day_bounds(start)
    day = _clean(db.get_hr(day_start, day_end))
    awake = _awake(day)
    if len(awake) >= config.MIN_BASELINE_SAMPLES:
        return _percentile(sorted(b for _, b in awake), 0.20), "uyanik-gun"
    if day:
        return _percentile(sorted(b for _, b in day), 0.25), "tum-gun"
    return None, None


def analyze_event(ev):
    """Tek event icin metrikleri hesaplar. Yetersiz veri -> None."""
    start = ev["ts_start"]
    end = ev["ts_end"] or (start + config.DEFAULT_WINDOW_MIN * 60)
    end = min(end, start + config.MAX_WINDOW_MIN * 60)  # tavan

    raw = _clean(db.get_hr(start, end))
    # Trim'i pencere uzunluguna gore olcekle: kisa bulusmada sabit 10dk her seyi yemesin
    window_min = max(1, (end - start) // 60)
    trim_min = min(config.TRIM_MINUTES, window_min // 3)
    trim_from = start + trim_min * 60
    trimmed = [(t, b) for t, b in raw if t >= trim_from] or raw
    kept, wo_dropped = _exclude_workouts(trimmed, start, end)
    if len(kept) < config.MIN_EVENT_SAMPLES:
        return None

    baseline, base_method = _baseline(start)
    if baseline is None:
        return None

    bpms = [b for _, b in kept]
    med = _median(bpms)
    peak = max(bpms)
    thr = baseline + config.ELEVATION_THRESHOLD_BPM
    pct_above = sum(1 for b in bpms if b > thr) / len(bpms)
    return {
        "id": ev["id"],
        "ts_start": start,
        "friend": ev.get("friend"),
        "location": ev.get("location"),
        "topic": ev.get("topic"),
        "tag": ev.get("tag"),
        "context": context_label(ev.get("friend"), ev.get("tag")),
        "feeling": ev.get("feeling"),
        "baseline": round(baseline, 1),
        "base_method": base_method,
        "median": round(med, 1),
        "peak": peak,
        "elev": round(med - baseline, 1),          # ana proxy: medyan yukselmesi
        "elev_peak": round(peak - baseline, 1),
        "pct_above": round(pct_above * 100),
        "samples": len(bpms),
        "workout_excluded": wo_dropped,
    }


def _rank(rows, key, global_mean):
    groups = defaultdict(list)
    for r in rows:
        val = r.get(key)
        if val:
            groups[val].append(r)
    out = []
    k = config.SHRINK_K
    for name, items in groups.items():
        elevs = [i["elev"] for i in items]
        avg = sum(elevs) / len(elevs)
        n = len(items)
        adjusted = (n * avg + k * global_mean) / (n + k)  # kucuk-n'i ortalamaya cek
        out.append({
            "name": name,
            "count": n,
            "avg_elev": round(avg, 1),
            "adj_elev": round(adjusted, 1),
            "max_peak": round(max(i["elev_peak"] for i in items), 1),
            "avg_pct_above": round(sum(i["pct_above"] for i in items) / n),
        })
    return sorted(out, key=lambda x: x["adj_elev"], reverse=True)


def _feeling_agreement(rows):
    """His (1-5) ile olculen yukselmenin ne kadar ortustugunu ozetler."""
    scored = [r for r in rows if r.get("feeling")]
    if len(scored) < 3:
        return None
    elevs = sorted(r["elev"] for r in scored)
    lo_c = _percentile(elevs, 1 / 3)
    hi_c = _percentile(elevs, 2 / 3)

    def m_bucket(e):
        return 0 if e <= lo_c else (2 if e >= hi_c else 1)

    def f_bucket(f):
        return 0 if f <= 2 else (2 if f >= 4 else 1)

    agree = sum(1 for r in scored if m_bucket(r["elev"]) == f_bucket(r["feeling"]))
    return {"n": len(scored), "agree": agree, "pct": round(agree / len(scored) * 100)}


def run(since_ts):
    db.init_db()
    events = db.get_events(since_ts)
    analyzed = [a for a in (analyze_event(e) for e in events) if a]
    global_mean = (sum(a["elev"] for a in analyzed) / len(analyzed)) if analyzed else 0.0
    return {
        "events": analyzed,
        "total_logged": len(events),
        "with_hr": len(analyzed),
        "missing_hr": len(events) - len(analyzed),
        "global_mean": round(global_mean, 1),
        "by_context": _rank(analyzed, "context", global_mean),
        "by_friend": _rank(analyzed, "friend", global_mean),
        "by_location": _rank(analyzed, "location", global_mean),
        "feeling_agreement": _feeling_agreement(analyzed),
    }


# ============================================================================
# GUN-DUZEYI analiz (resmi API verisi) — dakikalik HR yokken/ek olarak.
# Sinyal: bir kisiyle gorusulen gunun ERTESI sabahi recovery/HRV dususu,
# dinlenme nabzi artisi + o gunun strain'i. Stres bedeni gece toparlanmasina
# yansir; recovery dususu / HRV dususu / RHR artisi = daha kotu.
# Karistiran cok faktor var (spor, alkol, uyku); ~3-4 hafta veri onerilir.
# ============================================================================
from datetime import date, timedelta  # noqa: E402


def _daily_baselines():
    with db.get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM daily").fetchall()]

    def _avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return (sum(vals) / len(vals)) if vals else None

    return {
        "recovery": _avg("recovery"), "hrv": _avg("hrv"),
        "rhr": _avg("rhr"), "strain": _avg("strain"), "days": len(rows),
    }


def _next_day(day_str):
    return (date.fromisoformat(day_str) + timedelta(days=1)).isoformat()


def analyze_daily(since_ts):
    """Kisiye gore gun-duzeyi stres sinyali (resmi Whoop metrikleri)."""
    db.init_db()
    base = _daily_baselines()
    events = db.get_events(since_ts)

    groups = defaultdict(list)
    matched = 0
    for e in events:
        ctx = context_label(e.get("friend"), e.get("tag"))
        if not ctx:
            continue
        day = tzutil.fmt(e["ts_start"], "%Y-%m-%d")
        nxt = db.get_daily(_next_day(day))    # ertesi sabahin toparlanmasi
        same = db.get_daily(day)              # o gunun strain'i
        if nxt or same:
            matched += 1
        groups[ctx].append({"next": nxt, "same": same})

    def _mean(items, src, key):
        vals = [it[src][key] for it in items
                if it[src] and it[src].get(key) is not None]
        return (sum(vals) / len(vals)) if vals else None

    out = []
    for name, items in groups.items():
        nrec = _mean(items, "next", "recovery")
        nhrv = _mean(items, "next", "hrv")
        nrhr = _mean(items, "next", "rhr")
        sstr = _mean(items, "same", "strain")
        n = len(items)
        k = config.SHRINK_K
        # recovery dususu = kisisel ort - o kisiyle sonrasi (pozitif=daha kotu)
        deficit = (base["recovery"] - nrec) if (base["recovery"] and nrec is not None) else None
        adj = (deficit * n / (n + k)) if deficit is not None else None
        out.append({
            "name": name, "count": n,
            "next_recovery": round(nrec, 1) if nrec is not None else None,
            "recovery_deficit": round(deficit, 1) if deficit is not None else None,
            "adj_deficit": round(adj, 1) if adj is not None else None,
            "hrv_drop": round(base["hrv"] - nhrv, 1) if (base["hrv"] and nhrv is not None) else None,
            "rhr_rise": round(nrhr - base["rhr"], 1) if (base["rhr"] and nrhr is not None) else None,
            "same_strain": round(sstr, 1) if sstr is not None else None,
        })

    ranked = sorted(
        out, key=lambda x: (x["adj_deficit"] if x["adj_deficit"] is not None else -999),
        reverse=True,
    )
    return {
        "baselines": base,
        "total_logged": len(events),
        "matched": matched,
        "by_friend": ranked,
    }

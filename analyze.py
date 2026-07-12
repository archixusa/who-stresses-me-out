"""Physiological-signal analysis engine (non-causal, association-only).

This does NOT measure clinical stress. It reports where an elevated heart-rate signal
was *associated* with a person/context, with an explicit evidence level and uncertainty
interval — never a causal claim, and never using subjective mood/feeling input.

Minute-level (unofficial HR):
  * baseline = pre-meeting resting window (awake hours only), fallback awake-day / whole-day
  * window statistic = median after trimming arrival minutes and excluding workout minutes
  * ADDED: matched control (same weekday & time-of-day, non-meeting times) as a cross-check
  * ADDED: bootstrap confidence interval + evidence level per group
  * ADDED: confounder flags (caffeine/alcohol/illness/commute) downweight evidence
  * ADDED: multi-participant events are flagged; per-person attribution is limited
Small-sample shrinkage is preserved for ranking; the uncertainty layer does not replace it.

Day-level (official API): next-morning recovery/HRV/RHR change + same-day strain & sleep.
"""
import random
from collections import defaultdict
from datetime import date, timedelta

import config
import db
import tzutil

SEP = " · "

# Evidence levels (weakest -> strongest)
INSUFFICIENT, WEAK, EMERGING, CONSISTENT = "insufficient", "weak", "emerging", "consistent"


def context_label(friend, tag):
    """Person + context -> 'Sam · work' (or just the person if no tag)."""
    if friend and tag:
        return f"{friend}{SEP}{tag}"
    return friend or tag or None


# ---------------- small numeric helpers (pure) ----------------
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


def bootstrap_ci(values, n_boot=None, seed=None, alpha=0.05):
    """Deterministic percentile bootstrap CI of the mean. Pure Python + fixed seed so
    tests are reproducible. Returns (lo, hi) rounded, or None if < 2 values."""
    values = list(values)
    if len(values) < 2:
        return None
    n_boot = n_boot or config.BOOTSTRAP_N
    rng = random.Random(seed if seed is not None else config.BOOTSTRAP_SEED)
    n = len(values)
    means = []
    for _ in range(n_boot):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return round(_percentile(means, alpha / 2), 1), round(_percentile(means, 1 - alpha / 2), 1)


def evidence_level(n, coverage, ci, confounded_frac):
    """Map sample size, coverage, CI width/sign and confounder load to an evidence label."""
    if n < 3 or coverage < config.MIN_COVERAGE:
        return INSUFFICIENT
    capped = confounded_frac > config.CONFOUNDER_FRAC  # heavy confounding caps at 'weak'
    if n < 5 or ci is None or (ci[1] - ci[0]) > config.WIDE_CI_BPM:
        return WEAK
    excludes_zero = ci[0] > 0 or ci[1] < 0
    level = CONSISTENT if excludes_zero else EMERGING
    if capped and level in (EMERGING, CONSISTENT):
        return WEAK
    return level


# ---------------- HR helpers ----------------
def _clean(samples):
    return [(t, b) for t, b in samples if b and b > 0]


def _awake(samples):
    return [(t, b) for t, b in samples if 7 <= tzutil.local_hour(t) < 23]


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


def _baseline(start):
    """Pre-meeting resting HR estimate (awake-only) + which method was used."""
    pre = _awake(_clean(db.get_hr(start - config.BASELINE_PRE_MIN * 60, start)))
    if len(pre) >= config.MIN_BASELINE_SAMPLES:
        return _percentile(sorted(b for _, b in pre), 0.25), "pre-window"
    day_start, day_end = tzutil.local_day_bounds(start)
    day = _clean(db.get_hr(day_start, day_end))
    awake = _awake(day)
    if len(awake) >= config.MIN_BASELINE_SAMPLES:
        return _percentile(sorted(b for _, b in awake), 0.20), "awake-day"
    if day:
        return _percentile(sorted(b for _, b in day), 0.25), "whole-day"
    return None, None


def _matched_control(start, control_hr, event_windows):
    """Matched-control baseline: same weekday & time-of-day on NON-meeting times.
    control_hr: precomputed cleaned [(ts,bpm)] over a broad past window. Returns
    (baseline, method) or (None, None) if not enough matched data (report falls back)."""
    if not control_hr:
        return None, None
    d0 = tzutil.local_dt(start)
    target_wd, target_min = d0.weekday(), d0.hour * 60 + d0.minute
    half = config.MATCHED_CONTROL_HALFWIN_MIN
    vals = []
    for t, b in control_hr:
        if t >= start:
            continue  # only past days as control
        d = tzutil.local_dt(t)
        if d.weekday() != target_wd:
            continue
        if abs((d.hour * 60 + d.minute) - target_min) > half:
            continue
        if any(ws <= t < we for ws, we in event_windows):
            continue  # exclude other meetings
        vals.append(b)
    if len(vals) < config.MIN_BASELINE_SAMPLES:
        return None, None
    return _percentile(sorted(vals), 0.25), "matched-control"


def _confounded(ev):
    return bool(ev.get("alcohol") or ev.get("illness") or ev.get("commute")
                or ev.get("caffeine") == "high")


def analyze_event(ev, event_windows=None, control_hr=None):
    """Per-event metrics. Insufficient data -> None. No feeling is read or produced."""
    start = ev["ts_start"]
    end = ev["ts_end"] or (start + config.DEFAULT_WINDOW_MIN * 60)
    end = min(end, start + config.MAX_WINDOW_MIN * 60)

    raw = _clean(db.get_hr(start, end))
    window_min = max(1, (end - start) // 60)
    trim_min = min(config.TRIM_MINUTES, window_min // 3)
    trimmed = [(t, b) for t, b in raw if t >= start + trim_min * 60] or raw
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

    control_base, control_method = _matched_control(start, control_hr, event_windows or [])
    control_elev = round(med - control_base, 1) if control_base is not None else None

    parts = db.participant_names(ev)
    primary = parts[0] if parts else ev.get("friend")
    is_group = len(parts) > 1
    return {
        "id": ev["id"],
        "ts_start": start,
        "participants": parts,
        "primary": primary,
        "is_group": is_group,
        "location": ev.get("location"),
        "topic": ev.get("topic"),
        "tag": ev.get("tag"),
        "context": context_label(primary, ev.get("tag")),
        "confounded": _confounded(ev),
        "baseline": round(baseline, 1),
        "base_method": base_method,
        "control_baseline": round(control_base, 1) if control_base is not None else None,
        "control_method": control_method,
        "control_elev": control_elev,
        "median": round(med, 1),
        "peak": peak,
        "elev": round(med - baseline, 1),           # primary signal: median elevation
        "elev_peak": round(peak - baseline, 1),
        "pct_above": round(sum(1 for b in bpms if b > thr) / len(bpms) * 100),
        "samples": len(bpms),
        "workout_excluded": wo_dropped,
    }


def _rank(rows, key, global_mean, logged_counts):
    """Group analyzed events by `key`, attach mean/median/CI/evidence/coverage/group flags."""
    groups = defaultdict(list)
    for r in rows:
        val = r.get(key)
        if val:
            groups[val].append(r)
    out = []
    k = config.SHRINK_K
    for name, items in groups.items():
        elevs = [i["elev"] for i in items]
        n = len(items)
        avg = sum(elevs) / n
        adjusted = (n * avg + k * global_mean) / (n + k)  # small-sample shrinkage (kept)
        ci = bootstrap_ci(elevs)
        logged = logged_counts.get(name, n)
        coverage = n / logged if logged else 1.0
        confounded_frac = sum(1 for i in items if i["confounded"]) / n
        group_frac = sum(1 for i in items if i["is_group"]) / n
        control_elevs = [i["control_elev"] for i in items if i["control_elev"] is not None]
        out.append({
            "name": name,
            "count": n,
            "logged": logged,
            "coverage": round(coverage, 2),
            "avg_elev": round(avg, 1),
            "median_elev": round(_median(elevs), 1),
            "adj_elev": round(adjusted, 1),
            "ci": ci,
            "evidence": evidence_level(n, coverage, ci, confounded_frac),
            "confounded_frac": round(confounded_frac, 2),
            "group_frac": round(group_frac, 2),
            "control_avg_elev": round(sum(control_elevs) / len(control_elevs), 1) if control_elevs else None,
            "max_peak": round(max(i["elev_peak"] for i in items), 1),
            "avg_pct_above": round(sum(i["pct_above"] for i in items) / n),
        })
    return sorted(out, key=lambda x: x["adj_elev"], reverse=True)


def _logged_counts(events, keyfn):
    d = defaultdict(int)
    for e in events:
        v = keyfn(e)
        if v:
            d[v] += 1
    return d


def _event_context(e):
    parts = db.participant_names(e)
    primary = parts[0] if parts else e.get("friend")
    return context_label(primary, e.get("tag"))


def run(since_ts):
    db.init_db()
    events = db.get_events(since_ts)
    windows = [(e["ts_start"], e["ts_end"] or (e["ts_start"] + config.DEFAULT_WINDOW_MIN * 60))
               for e in events]
    control_hr = _clean(db.get_hr(since_ts - 28 * 86400, tzutil.now_ts()))

    analyzed = [a for a in (analyze_event(e, windows, control_hr) for e in events) if a]
    global_mean = (sum(a["elev"] for a in analyzed) / len(analyzed)) if analyzed else 0.0

    lc_ctx = _logged_counts(events, _event_context)
    lc_friend = _logged_counts(events, lambda e: (db.participant_names(e) or [None])[0])
    lc_loc = _logged_counts(events, lambda e: e.get("location"))

    # attach 'friend' (primary) so by_friend grouping works on analyzed rows
    for a in analyzed:
        a["friend"] = a["primary"]

    return {
        "events": analyzed,
        "total_logged": len(events),
        "with_hr": len(analyzed),
        "missing_hr": len(events) - len(analyzed),
        "global_mean": round(global_mean, 1),
        "by_context": _rank(analyzed, "context", global_mean, lc_ctx),
        "by_friend": _rank(analyzed, "friend", global_mean, lc_friend),
        "by_location": _rank(analyzed, "location", global_mean, lc_loc),
    }


# ============================================================================
# DAY-LEVEL analysis (official API) — complementary to / fallback for minute HR.
# Signal: next-morning recovery/HRV drop + resting-HR rise after seeing someone,
# plus that day's strain and sleep performance. Association only, not causal.
# ============================================================================
def _daily_baselines():
    with db.get_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM daily").fetchall()]

    def _avg(kk):
        vals = [r[kk] for r in rows if r.get(kk) is not None]
        return (sum(vals) / len(vals)) if vals else None

    return {"recovery": _avg("recovery"), "hrv": _avg("hrv"), "rhr": _avg("rhr"),
            "strain": _avg("strain"), "sleep_perf": _avg("sleep_perf"), "days": len(rows)}


def _next_day(day_str):
    return (date.fromisoformat(day_str) + timedelta(days=1)).isoformat()


def analyze_daily(since_ts):
    """Per-context day-level signal (official WHOOP metrics) with evidence levels."""
    db.init_db()
    base = _daily_baselines()
    events = db.get_events(since_ts)

    groups = defaultdict(list)
    matched = 0
    for e in events:
        ctx = _event_context(e)
        if not ctx:
            continue
        day = tzutil.fmt(e["ts_start"], "%Y-%m-%d")
        nxt, same = db.get_daily(_next_day(day)), db.get_daily(day)
        if nxt or same:
            matched += 1
        groups[ctx].append({"next": nxt, "same": same, "confounded": _confounded(e),
                            "is_group": len(db.participant_names(e)) > 1})

    def _mean(items, src, kk):
        vals = [it[src][kk] for it in items if it[src] and it[src].get(kk) is not None]
        return (sum(vals) / len(vals)) if vals else None

    out = []
    for name, items in groups.items():
        nrec = _mean(items, "next", "recovery")
        n = len(items)
        k = config.SHRINK_K
        deficits = [base["recovery"] - it["next"]["recovery"]
                    for it in items if it["next"] and it["next"].get("recovery") is not None
                    and base["recovery"] is not None]
        deficit = (base["recovery"] - nrec) if (base["recovery"] and nrec is not None) else None
        adj = (deficit * n / (n + k)) if deficit is not None else None
        ci = bootstrap_ci(deficits) if len(deficits) >= 2 else None
        coverage = (len(deficits) / n) if n else 0.0
        confounded_frac = sum(1 for it in items if it["confounded"]) / n
        out.append({
            "name": name, "count": n, "coverage": round(coverage, 2),
            "next_recovery": round(nrec, 1) if nrec is not None else None,
            "recovery_deficit": round(deficit, 1) if deficit is not None else None,
            "adj_deficit": round(adj, 1) if adj is not None else None,
            "ci": ci,
            "evidence": evidence_level(len(deficits), coverage, ci, confounded_frac),
            "hrv_drop": round(base["hrv"] - _mean(items, "next", "hrv"), 1)
                        if (base["hrv"] and _mean(items, "next", "hrv") is not None) else None,
            "rhr_rise": round(_mean(items, "next", "rhr") - base["rhr"], 1)
                        if (base["rhr"] and _mean(items, "next", "rhr") is not None) else None,
            "same_strain": round(_mean(items, "same", "strain"), 1)
                           if _mean(items, "same", "strain") is not None else None,
            "same_sleep": round(_mean(items, "same", "sleep_perf"), 1)
                          if _mean(items, "same", "sleep_perf") is not None else None,
            "group_frac": round(sum(1 for it in items if it["is_group"]) / n, 2),
        })

    ranked = sorted(out, key=lambda x: (x["adj_deficit"] if x["adj_deficit"] is not None else -999),
                    reverse=True)
    return {"baselines": base, "total_logged": len(events), "matched": matched, "by_friend": ranked}

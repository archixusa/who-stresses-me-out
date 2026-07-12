"""Builds the association report (non-causal, evidence-first) and sends it to Telegram.

Language rules baked in here:
  * associations, never causation ("associated with", not "makes you stressed")
  * every ranking carries an evidence level and (where possible) an uncertainty interval
  * low-evidence groups are never presented as "the most stressful"
  * no mood / feeling / survey content anywhere
"""
import asyncio
import html

import analyze
import config
import tzutil

_EVIDENCE_BADGE = {
    analyze.INSUFFICIENT: "insufficient data",
    analyze.WEAK: "weak signal",
    analyze.EMERGING: "emerging signal",
    analyze.CONSISTENT: "consistent signal",
}
_SHOW_EVIDENCE = (analyze.WEAK, analyze.EMERGING, analyze.CONSISTENT)


def _e(x):
    return html.escape(str(x)) if x is not None else "—"


def _ci(row):
    ci = row.get("ci")
    return f" [95% CI {ci[0]}…{ci[1]}]" if ci else ""


def _group_note(row):
    return " · <i>group context — limited per-person attribution</i>" if row.get("group_frac") else ""


def _last_hr_ts():
    import db
    with db.get_conn() as c:
        return c.execute("SELECT MAX(ts) FROM hr_cache").fetchone()[0]


def _sync_health(res, dres):
    lines = []
    hr = _last_hr_ts()
    if hr:
        lines.append(f"Heart-rate data through {tzutil.fmt(hr, '%d %b %H:%M')} "
                     f"({res['with_hr']}/{res['total_logged']} logged events matched).")
    if dres["baselines"]["days"]:
        lines.append(f"Official WHOOP context: {dres['baselines']['days']} days.")
    if not hr and not dres["baselines"]["days"]:
        lines.append("No WHOOP data synced yet — run <code>python sync.py</code>.")
    return lines


def _minute_line(r):
    return (f"  • {_e(r['name'])}: mean <b>{r['avg_elev']:+.1f}</b> BPM (median {r['median_elev']:+.1f})"
            f"{_ci(r)} · {r['count']} events · {_EVIDENCE_BADGE[r['evidence']]}{_group_note(r)}")


def _minute_section(L, res):
    ranked = [r for r in res["by_context"] if r["evidence"] in _SHOW_EVIDENCE and r["avg_elev"] > 0]
    weak_out = [r for r in res["by_context"] if r["evidence"] == analyze.INSUFFICIENT]
    if ranked:
        L.append("")
        L.append("👤 <b>Person · context — higher heart-rate association</b>")
        for r in ranked[:8]:
            L.append(_minute_line(r))
    supportive = [r for r in res["by_context"]
                  if r["avg_elev"] < 0 and r["evidence"] in (analyze.EMERGING, analyze.CONSISTENT)]
    if supportive:
        L.append("")
        L.append("🌿 <b>Contexts associated with a lower heart-rate response</b>")
        for r in sorted(supportive, key=lambda x: x["avg_elev"])[:5]:
            L.append(_minute_line(r))
    if weak_out:
        L.append("")
        L.append(f"⏳ <i>Needs more data ({len(weak_out)}): "
                 + ", ".join(_e(r['name']) for r in weak_out[:6]) + "</i>")


def _daily_section(L, d):
    b = d["baselines"]
    L.append("")
    L.append(f"🌙 <b>Day-level (official WHOOP, {b['days']} days)</b>")
    if b["recovery"] is not None:
        L.append(f"  Personal avg recovery {b['recovery']:.0f}% · "
                 f"HRV {b['hrv']:.0f}ms · RHR {b['rhr']:.0f}"
                 if b["hrv"] and b["rhr"] else f"  Personal avg recovery {b['recovery']:.0f}%")
    shown = [r for r in d["by_friend"]
             if r["recovery_deficit"] is not None and r["evidence"] in _SHOW_EVIDENCE]
    if shown:
        L.append("  <i>Next-morning recovery after seeing them (drop = worse recovery):</i>")
        for r in shown[:6]:
            strain = f", strain {r['same_strain']}" if r["same_strain"] is not None else ""
            sleep = f", sleep {r['same_sleep']}%" if r["same_sleep"] is not None else ""
            sign = "-" if r["recovery_deficit"] > 0 else "+"
            L.append(f"  • {_e(r['name'])}: recovery {sign}{abs(r['recovery_deficit'])} pts "
                     f"({r['next_recovery']}%){strain}{sleep} · {r['count']}d · "
                     f"{_EVIDENCE_BADGE[r['evidence']]}")
    elif d["matched"] == 0:
        L.append("  (Log meetings so their next-morning recovery can be compared.)")


def _data_quality(L, res):
    confounded = [r for r in res["by_context"] if r.get("confounded_frac", 0) > 0]
    low_cov = [r for r in res["by_context"] if r.get("coverage", 1) < config.MIN_COVERAGE]
    notes = []
    if confounded:
        notes.append(f"{len(confounded)} context(s) include events flagged with caffeine/"
                     "alcohol/illness/commute — their evidence is downweighted.")
    if low_cov:
        notes.append(f"{len(low_cov)} context(s) have low heart-rate coverage.")
    if notes:
        L.append("")
        L.append("🔎 <b>Data quality</b>")
        for n in notes:
            L.append(f"  • {n}")


def _experiments(L, res):
    tips = []
    for r in res["by_context"]:
        if r["evidence"] == analyze.INSUFFICIENT and r["count"] >= 1:
            tips.append(f"Log “{_e(r['name'])}” 2–3 more times to reach a usable sample.")
        elif r.get("confounded_frac", 0) > config.CONFOUNDER_FRAC:
            tips.append(f"Observe “{_e(r['name'])}” again under calmer, unconfounded conditions.")
        elif r["evidence"] == analyze.CONSISTENT and r["avg_elev"] > 0:
            tips.append(f"Try a 10-minute calm buffer before “{_e(r['name'])}” and compare later events.")
        elif r["avg_elev"] < 0 and r["evidence"] in (analyze.EMERGING, analyze.CONSISTENT):
            tips.append(f"Note in a word what makes “{_e(r['name'])}” feel easier, for later comparison.")
    tips = tips[:4]
    if tips:
        L.append("")
        L.append("🧪 <b>Small experiments for this week</b>")
        for t in tips:
            L.append(f"  • {t}")


def build_report(days=7):
    since = tzutil.now_ts() - days * 86400
    res = analyze.run(since)
    dres = analyze.analyze_daily(since)

    L = [f"📊 <b>Association report</b> (last {days} days)", ""]
    L.extend("  " + s for s in _sync_health(res, dres))

    if res["total_logged"] == 0:
        L.append("")
        L.append("No meetings logged yet. Start one from the menu or with <b>/meet</b>.")
        return "\n".join(L)

    if res["events"]:
        _minute_section(L, res)
    else:
        L.append("")
        L.append("<i>No minute-level heart rate yet; showing day-level signal below.</i>")

    if dres["baselines"]["days"]:
        _daily_section(L, dres)

    _data_quality(L, res)
    _experiments(L, res)

    L.append("")
    L.append("<i>These are associations, not causes — sleep, activity, caffeine and time of "
             "day all affect heart rate. Not a medical or psychological assessment. "
             f"Times shown in {config.LOCAL_TZ}.</i>")
    return "\n".join(L)


async def send(text):
    from telegram import Bot
    from telegram.constants import ParseMode
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        plain = html.unescape(text.replace("<b>", "").replace("</b>", "")
                              .replace("<i>", "").replace("</i>", "")
                              .replace("<code>", "").replace("</code>", ""))
        await bot.send_message(chat_id=config.TELEGRAM_CHAT_ID,
                               text=f"[plain text — formatting error: {e}]\n\n{plain}")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7
    report = build_report(days)
    print(report)
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        asyncio.run(send(report))

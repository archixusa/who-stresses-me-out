"""Stres raporunu HTML olarak uretir ve Telegram'a gonderir.

HTML parse_mode + html.escape: kullanici adlarindaki _ * [ ` gibi karakterler
raporu ARTIK cokertmez (eski Markdown hatasi giderildi). Gonderim try/except ile
sarili: bicimleme patlarsa duz metin fallback gider, rapor asla sessizce dusmez.
"""
import asyncio
import html

import analyze
import config
import tzutil


def _e(x):
    return html.escape(str(x)) if x is not None else "—"


def _conf(n):
    return "" if n >= 3 else (" ⚠tek" if n == 1 else " ⚠az")


def _minute_section(L, res):
    """Dakikalik HR proxy bolumu (hr_cache doluysa)."""
    L.append("")
    L.append("👤 <b>Kisi·baglama gore (medyan nabiz yukselmesi):</b>")
    for r in res.get("by_context", res["by_friend"])[:8]:
        L.append(f"  • {_e(r['name'])}: <b>+{r['avg_elev']}</b> bpm "
                 f"(duz.+{r['adj_elev']}) · zirve +{r['max_peak']} · "
                 f"%{r['avg_pct_above']} esik ustu · {r['count']}x{_conf(r['count'])}")
    if res["by_location"]:
        L.append("")
        L.append("📍 <b>Yere gore:</b>")
        for r in res["by_location"][:6]:
            L.append(f"  • {_e(r['name'])}: +{r['avg_elev']} bpm · {r['count']}x{_conf(r['count'])}")
    top = sorted(res["events"], key=lambda e: e["elev"], reverse=True)[:5]
    L.append("")
    L.append("🔥 <b>En yuksek tekil bulusmalar:</b>")
    for e in top:
        when = tzutil.fmt(e["ts_start"])
        loc = f" @{_e(e['location'])}" if e["location"] else ""
        topic = f" — {_e(e['topic'])}" if e["topic"] else ""
        wo = " (workout haric)" if e.get("workout_excluded") else ""
        L.append(f"  • {when} {_e(e['friend'])}{loc}: +{e['elev']} bpm "
                 f"(zirve {e['peak']}, {e['base_method']}){wo}{topic}")
    fa = res["feeling_agreement"]
    if fa:
        L.append("")
        L.append(f"🧭 His↔olcum uyumu: {fa['agree']}/{fa['n']} (%{fa['pct']}).")


def _daily_section(L, d):
    """Gun-duzeyi bolumu (resmi API daily verisi doluysa)."""
    b = d["baselines"]
    L.append("")
    L.append(f"🌙 <b>Gun-duzeyi (resmi Whoop, {b['days']} gun):</b>")
    base_recov = f"{b['recovery']:.0f}" if b["recovery"] is not None else "—"
    L.append(f"  Kisisel ort. recovery: {base_recov}%  ·  "
             f"HRV: {b['hrv']:.0f}ms  ·  RHR: {b['rhr']:.0f}" if b["hrv"] and b["rhr"]
             else f"  Kisisel ort. recovery: {base_recov}%")
    if not d["by_friend"]:
        L.append("  (Kisi loglayinca 'gorustugun gunun ertesi toparlanman' burada cikacak.)")
        return
    L.append("  <i>Gorustugun gunun ERTESI sabahi (dusus = daha kotu toparlanma):</i>")
    for r in d["by_friend"][:8]:
        if r["recovery_deficit"] is None:
            continue
        hrv = f", HRV {'-' if r['hrv_drop'] and r['hrv_drop']>0 else '+'}{abs(r['hrv_drop'])}ms" if r["hrv_drop"] is not None else ""
        rhr = f", RHR {'+' if r['rhr_rise'] and r['rhr_rise']>0 else ''}{r['rhr_rise']}" if r["rhr_rise"] is not None else ""
        sign = "-" if r["recovery_deficit"] > 0 else "+"
        L.append(f"  • {_e(r['name'])}: recovery {sign}{abs(r['recovery_deficit'])} puan "
                 f"({r['next_recovery']}%){hrv}{rhr} · {r['count']}g{_conf(r['count'])}")


def build_report(days=7):
    since = tzutil.now_ts() - days * 86400
    res = analyze.run(since)
    dres = analyze.analyze_daily(since)
    has_official = dres["baselines"]["days"] > 0

    L = [f"📊 <b>Stres Raporu</b> (son {days} gun)", ""]
    L.append(f"Loglanan: {res['total_logged']} · dakikalik HR eslesen: {res['with_hr']} · "
             f"resmi gun verisi: {dres['baselines']['days']}")

    if res["events"]:
        _minute_section(L, res)
    elif res["total_logged"] == 0:
        L.append("")
        L.append("Henuz bulusma loglanmadi. Telegram'dan <b>/meet</b> veya "
                 "<b>/log Kisi | Yer | Konu</b> ile baslayin.")
    else:
        L.append("")
        L.append("<i>Dakikalik HR henuz yok (gayriresmi kaynak kapali). "
                 "Asagida resmi gun-duzeyi sinyali:</i>")

    if has_official:
        _daily_section(L, dres)

    L.append("")
    L.append(f"<i>Not: gun-duzeyi sinyal cok faktorden etkilenir (spor/uyku/alkol); "
             f"saglam örüntü icin ~3-4 hafta. Saatler {config.LOCAL_TZ}.</i>")
    return "\n".join(L)


async def send(text):
    from telegram import Bot
    from telegram.constants import ParseMode
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    try:
        await bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text,
                               parse_mode=ParseMode.HTML)
    except Exception as e:  # bicimleme/parse patlarsa duz metin gonder
        plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "") \
                    .replace("</i>", "").replace("<code>", "").replace("</code>", "")
        plain = html.unescape(plain)
        await bot.send_message(chat_id=config.TELEGRAM_CHAT_ID,
                               text=f"[duz metin — bicimleme hatasi: {e}]\n\n{plain}")


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

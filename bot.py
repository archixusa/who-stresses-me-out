"""Telegram logger bot (v2): bulusmalari hizli logla; HR eslestirme sync/analyze'de.

Whoop kimligi GEREKMEZ; bot sadece SQLite'a yazar.
Guvenlik: sadece config.TELEGRAM_CHAT_ID etkilesebilir.

v2 duzeltmeleri: his adiminda kayit KAYBI onlendi (event konu adiminda kaydedilir,
his sonradan islenir), HTML escaping (Markdown cokmesi giderildi), pattern'li
callback'ler (yanlis veri/int crash yok), timeout + yeniden giris, hata handler'i,
non-blocking /rapor, /son /sil /gun veri hijyeni komutlari, yerel saat gosterimi.
"""
import asyncio
import html
import logging
import time

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler,
    CallbackQueryHandler, MessageHandler, filters,
)

import config
import db
import report as report_mod
import tzutil

SEP = " · "                 # kisayol etiketinde kisi-baglam ayraci
BITIR_LABEL = "⏹ Bitir"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
log = logging.getLogger("whoop-bot")

FRIEND, LOCATION, TOPIC, FEELING = range(4)
CONV_TIMEOUT = 600  # sn: yarim kalan /meet bu surede kapanir

_rid_seq = 0  # her klavye render'ina benzersiz kimlik (eski klavye taplarini reddetmek icin)


def _next_rid():
    global _rid_seq
    _rid_seq += 1
    return _rid_seq


def _now():
    return int(time.time())


def _auth(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat) and str(chat.id) == str(config.TELEGRAM_CHAT_ID)


def _render_kb(ctx, ns, names, with_new=True):
    """Klavyeyi benzersiz render-id ile olusturur; index'i o render'in snapshot'ina baglar."""
    rid = _next_rid()
    ctx.user_data[f"{ns}_rid"] = rid
    ctx.user_data[f"snap_{rid}"] = names
    rows = [[InlineKeyboardButton(n, callback_data=f"{ns}:{rid}:i:{i}")]
            for i, n in enumerate(names)]
    tail = []
    if with_new:
        tail.append(InlineKeyboardButton("➕ Yeni", callback_data=f"{ns}:{rid}:new"))
    tail.append(InlineKeyboardButton("⏭ Atla", callback_data=f"{ns}:{rid}:skip"))
    rows.append(tail)
    return InlineKeyboardMarkup(rows)


async def _send_html(target, text):
    """HTML gonder; parse patlarsa duz metne dus."""
    try:
        await target.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("HTML gonderim hatasi, duz metne dusuluyor: %s", e)
        plain = html.unescape(
            text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        )
        await target.reply_text(plain)


# ---------------- guided /meet ----------------
async def meet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["ts_start"] = _now()
    kb = _render_kb(ctx, "fr", db.list_names("friends"))
    await update.message.reply_text("🤝 Kiminle bulustun?", reply_markup=kb)
    return FRIEND


def _resolve(ctx, ns, data):
    """callback_data 'ns:rid:kind[:idx]' -> ('pick'|'new'|'skip'|'expired', name)."""
    parts = data.split(":")
    if len(parts) < 3:
        return "expired", None
    rid = int(parts[1])
    if rid != ctx.user_data.get(f"{ns}_rid"):  # eski klavye tapi -> reddet
        return "expired", None
    kind = parts[2]
    if kind == "new":
        return "new", None
    if kind == "skip":
        return "skip", None
    if kind == "i":
        idx = int(parts[3])
        names = ctx.user_data.get(f"snap_{rid}", [])
        return "pick", (names[idx] if 0 <= idx < len(names) else None)
    return "expired", None


async def friend_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, name = _resolve(ctx, "fr", q.data)
    if action == "expired":
        await q.answer("Bu klavye eskidi — /meet ile tekrar basla.", show_alert=True)
        return FRIEND
    await q.answer()
    if action == "new":
        await q.edit_message_text("✍️ Kisi adini yaz:")
        return FRIEND
    if action == "pick":
        ctx.user_data["friend"] = name
    await q.edit_message_text(f"👤 {ctx.user_data.get('friend') or '—'}")
    return await _ask_location(q.message, ctx)


async def friend_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept_control(update, ctx):
        return ConversationHandler.END
    name = update.message.text.strip()
    ctx.user_data["friend"] = name
    db.add_name("friends", name)
    return await _ask_location(update.message, ctx)


async def _ask_location(message, ctx):
    kb = _render_kb(ctx, "lo", db.list_names("locations"))
    await message.reply_text("📍 Nerede?", reply_markup=kb)
    return LOCATION


async def location_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, name = _resolve(ctx, "lo", q.data)
    if action == "expired":
        await q.answer("Bu klavye eskidi — /meet ile tekrar basla.", show_alert=True)
        return LOCATION
    await q.answer()
    if action == "new":
        await q.edit_message_text("✍️ Yer adini yaz:")
        return LOCATION
    if action == "pick":
        ctx.user_data["location"] = name
    await q.edit_message_text(f"📍 {ctx.user_data.get('location') or '—'}")
    await q.message.reply_text("💬 Ne konustunuz? (kisa not, /atla ile gec)")
    return TOPIC


async def location_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept_control(update, ctx):
        return ConversationHandler.END
    name = update.message.text.strip()
    ctx.user_data["location"] = name
    db.add_name("locations", name)
    await update.message.reply_text("💬 Ne konustunuz? (kisa not, /atla ile gec)")
    return TOPIC


async def topic_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept_control(update, ctx):
        return ConversationHandler.END
    ctx.user_data["topic"] = update.message.text.strip()
    return await _save_and_ask_feeling(update.message, ctx)


async def topic_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _save_and_ask_feeling(update.message, ctx)


async def _save_and_ask_feeling(message, ctx):
    """Event'i BURADA kaydet (his gelmese bile kayip olmasin), sonra his sor.
    Idempotent: prompt gonderimi patlar da kullanici tekrar denerse ikinci kayit acilmaz."""
    d = ctx.user_data
    if ctx.user_data.get("event_id"):
        db.set_event_topic(ctx.user_data["event_id"], d.get("topic"))  # retry: mevcut kaydi guncelle
    else:
        ctx.user_data["event_id"] = db.add_event(
            ts_start=d["ts_start"], friend=d.get("friend"), location=d.get("location"),
            topic=d.get("topic"), feeling=None, created_at=_now(),
        )
    rows = [[InlineKeyboardButton(str(i), callback_data=f"fe:{i}") for i in range(1, 6)],
            [InlineKeyboardButton("⏭ Atla", callback_data="fe:skip")]]
    await message.reply_text(
        "😌 Nasil hissettin? (1=cok rahat … 5=cok gergin)",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return FEELING


async def feeling_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    feeling = None if q.data == "fe:skip" else int(q.data.split(":")[1])
    _finish_feeling(ctx, feeling)
    await q.edit_message_text(_summary(ctx.user_data, feeling))
    return ConversationHandler.END


async def feeling_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept_control(update, ctx):
        return ConversationHandler.END
    t = update.message.text.strip()
    if t not in ("1", "2", "3", "4", "5"):
        await update.message.reply_text("1-5 arasi bir rakam yaz ya da butona bas (⏭ Atla).")
        return FEELING
    _finish_feeling(ctx, int(t))
    await update.message.reply_text(_summary(ctx.user_data, int(t)))
    return ConversationHandler.END


def _finish_feeling(ctx, feeling):
    if feeling is not None and ctx.user_data.get("event_id"):
        db.set_event_feeling(ctx.user_data["event_id"], feeling)


def _summary(d, feeling):
    when = tzutil.fmt(d["ts_start"])
    return (f"✅ Loglandi ({when})\n"
            f"👤 {d.get('friend') or '—'}  📍 {d.get('location') or '—'}\n"
            f"💬 {d.get('topic') or '—'}  😌 {feeling if feeling is not None else '—'}\n\n"
            f"Bittiginde /bitir yaz (yoksa {config.DEFAULT_WINDOW_MIN}dk pencere sayilir).")


async def conv_timeout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("event_id"):
        return ConversationHandler.END  # zaten kaydedildi
    if update and update.effective_message:
        await update.effective_message.reply_text("⏱ Zaman asimi — /meet ile tekrar basla.")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return ConversationHandler.END
    await update.message.reply_text("İptal.")
    return ConversationHandler.END


# ---------------- quick + utility commands ----------------
async def quick_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text("Kullanim: /log Kisi | Yer | Konu")
        return
    parts = [p.strip() or None for p in raw.split("|")]
    friend = parts[0] if len(parts) > 0 else None
    location = parts[1] if len(parts) > 1 else None
    topic = parts[2] if len(parts) > 2 else None
    if friend:
        db.add_name("friends", friend)
    if location:
        db.add_name("locations", location)
    db.add_event(ts_start=_now(), friend=friend, location=location, topic=topic, created_at=_now())
    await update.message.reply_text(
        f"✅ {friend or '—'} · {location or '—'} · {topic or '—'}\n/bitir ile kapat."
    )


async def bitir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    eid, status = db.close_latest_open_event(_now(), config.MAX_WINDOW_MIN * 60)
    if status == "no_open":
        await update.message.reply_text("Acik bulusma yok.")
    elif status == "too_old":
        await update.message.reply_text(
            f"⚠ En son acik bulusma cok eski (#{eid}); otomatik kapatmadim. "
            f"Gecmisse /sil {eid} ile silebilirsin."
        )
    else:
        await update.message.reply_text("⏹ Bulusma kapatildi.")


async def son(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    evs = db.recent_events(10)
    if not evs:
        await update.message.reply_text("Kayit yok.")
        return
    lines = ["🗒 <b>Son kayitlar:</b>"]
    for e in evs:
        when = tzutil.fmt(e["ts_start"])
        open_mark = " ⏺acik" if e["ts_end"] is None else ""
        lines.append(f"#{e['id']} {when} · {html.escape(e['friend'] or '—')} · "
                     f"{html.escape(e['location'] or '—')}{open_mark}")
    lines.append("\nSilmek: /sil <id>")
    await _send_html(update.message, "\n".join(lines))


async def sil(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Kullanim: /sil <id>  (id'leri /son ile gor)")
        return
    ok = db.delete_event(int(arg))
    await update.message.reply_text(f"🗑 #{arg} silindi." if ok else f"#{arg} bulunamadi.")


async def gun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    day = tzutil.fmt(_now(), "%Y-%m-%d")
    d = db.get_daily(day)
    if not d:
        await update.message.reply_text(
            f"{day} icin resmi Whoop verisi yok (OAuth bagli mi? sync calisti mi?)."
        )
        return
    await update.message.reply_text(
        f"📅 {day}\n"
        f"Recovery: {d.get('recovery') or '—'}%  ·  HRV: {d.get('hrv') or '—'} ms\n"
        f"Dinlenme nabzi: {d.get('rhr') or '—'}  ·  Gun strain: {d.get('strain') or '—'}\n"
        f"Uyku performansi: {d.get('sleep_perf') or '—'}%"
    )


async def rapor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await update.message.reply_text("Hesaplaniyor…")
    text = await asyncio.to_thread(report_mod.build_report, 7)  # event loop'u bloklamasin
    await _send_html(update.message, text)


# ---------------- hizli kisayol klavyesi ----------------
def _label(sc):
    return f"{sc['friend']}{SEP}{sc['tag']}"


def _main_keyboard():
    """Kalici alt-klavye: kisayollar (2'li satir) + son satirda ⏹ Bitir."""
    labels = [_label(s) for s in db.list_shortcuts()]
    rows, row = [], []
    for lbl in labels:
        row.append(KeyboardButton(lbl))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(BITIR_LABEL)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _find_shortcut(text):
    for s in db.list_shortcuts():
        if _label(s) == text:
            return s
    return None


async def shortcut_tap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kalici klavyeden gelen metinler: kisayol -> hizli logla; ⏹ Bitir -> kapat."""
    if not _auth(update):
        return
    text = (update.message.text or "").strip()

    if text == BITIR_LABEL:
        eid, status = db.close_latest_open_event(_now(), config.MAX_WINDOW_MIN * 60)
        msg = {"no_open": "Acik bulusma yok.",
               "too_old": f"⚠ Son acik bulusma cok eski (#{eid}); /sil {eid} ile silebilirsin.",
               "ok": "⏹ Bulusma kapatildi."}[status]
        await update.message.reply_text(msg)
        return

    sc = _find_shortcut(text)
    if sc:
        # onceki TUM acik bulusmalari kapat (orphan birikmesin), yenisini ac
        db.close_open_events(_now(), config.MAX_WINDOW_MIN * 60, config.DEFAULT_WINDOW_MIN * 60)
        db.add_event(ts_start=_now(), friend=sc["friend"], tag=sc["tag"], created_at=_now())
        await update.message.reply_text(f"✅ {text} basladi · bitince ⏹ Bitir")
        return

    # taninmayan metin -> kisa ipucu (nadiren, klavye disi yazarsa)
    await update.message.reply_text(
        "Bir kisayola bas, ya da /log Kisi | Yer | Konu · /meet · /kisayollar"
    )


async def _intercept_control(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/meet konusmasi sirasinda kalici klavyeden kisayol/⏹ Bitir gelirse konusmayi
    bitirip hizli-log mantigina yonlendir (label'in friend/topic olarak kaydini onler).
    True donerse cagiran handler ConversationHandler.END dondurmeli."""
    text = (update.message.text or "").strip()
    if text == BITIR_LABEL or _find_shortcut(text):
        await shortcut_tap(update, ctx)
        return True
    return False


async def klavye(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    await update.message.reply_text("⌨️ Kisayol klavyesi acildi.", reply_markup=_main_keyboard())


async def kisayollar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    scs = db.list_shortcuts()
    lines = ["⚡ <b>Kisayollar:</b>"]
    for s in scs:
        lines.append(f"#{s['id']}  {html.escape(_label(s))}")
    lines.append("\nEkle: /ekle Ad | etiket   (or. /ekle Sam | tatil)")
    lines.append("Cikar: /cikar <id>")
    await _send_html(update.message, "\n".join(lines))


async def ekle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.message.reply_text("Kullanim: /ekle Ad | etiket   (or. /ekle Sam | tatil)")
        return
    friend, _, tag = raw.partition("|")
    status = db.add_shortcut(friend.strip(), tag.strip())
    if status == "invalid":
        await update.message.reply_text("Ad ve etiket bos olamaz.")
        return
    if status == "exists":
        await update.message.reply_text(f"ℹ️ Zaten var: {friend.strip()}{SEP}{tag.strip()}")
        return
    await update.message.reply_text(
        f"✅ Eklendi: {friend.strip()}{SEP}{tag.strip()}", reply_markup=_main_keyboard()
    )


async def cikar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Kullanim: /cikar <id>  (id'leri /kisayollar ile gor)")
        return
    ok = db.delete_shortcut(int(arg))
    await update.message.reply_text(
        f"🗑 Kisayol #{arg} silindi." if ok else f"#{arg} bulunamadi.",
        reply_markup=_main_keyboard(),
    )


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        await update.message.reply_text("Yetkisiz.")
        return
    await update.message.reply_text(
        "👋 Stres logger hazir.\n\n"
        "Alttaki <b>kisayol butonlarina</b> bas → o bulusma aninda baslar; "
        "bitince <b>⏹ Bitir</b>.\n\n"
        "/kisayollar — kisayollari yonet (/ekle, /cikar)\n"
        "/klavye — klavyeyi tekrar goster\n"
        "/meet — adim adim (his/konu ekle)\n"
        "/log Kisi | Yer | Konu — hizli\n"
        "/son · /sil &lt;id&gt; — kayitlar\n"
        "/gun — bugunun Whoop baglami\n"
        "/rapor — stres analizi",
        parse_mode=ParseMode.HTML, reply_markup=_main_keyboard(),
    )


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Handler hatasi", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_chat and _auth(update):
            await ctx.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠ Bir hata olustu ama bot ayakta. Tekrar dene ya da /iptal yaz.",
            )
    except Exception:
        pass


def main():
    config.require_bot()
    db.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("meet", meet_start)],
        states={
            FRIEND: [CallbackQueryHandler(friend_cb, pattern=r"^fr:"),
                     MessageHandler(filters.TEXT & ~filters.COMMAND, friend_txt)],
            LOCATION: [CallbackQueryHandler(location_cb, pattern=r"^lo:"),
                       MessageHandler(filters.TEXT & ~filters.COMMAND, location_txt)],
            TOPIC: [CommandHandler("atla", topic_skip),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, topic_txt)],
            FEELING: [CallbackQueryHandler(feeling_cb, pattern=r"^fe:"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, feeling_txt)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conv_timeout)],
        },
        fallbacks=[CommandHandler("iptal", cancel), CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT,
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("klavye", klavye))
    app.add_handler(CommandHandler("kisayollar", kisayollar))
    app.add_handler(CommandHandler("ekle", ekle))
    app.add_handler(CommandHandler("cikar", cikar))
    app.add_handler(CommandHandler("log", quick_log))
    app.add_handler(CommandHandler("bitir", bitir))
    app.add_handler(CommandHandler("son", son))
    app.add_handler(CommandHandler("sil", sil))
    app.add_handler(CommandHandler("gun", gun))
    app.add_handler(CommandHandler("rapor", rapor))
    # Kalici klavye metinleri (kisayol taplari + ⏹ Bitir): en son, konusma aktif degilken yakalar
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, shortcut_tap))
    app.add_error_handler(on_error)
    log.info("bot calisiyor…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

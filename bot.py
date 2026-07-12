"""Telegram bot — button-first UI for logging *who you were with*, so the analysis can
correlate it with your own WHOOP data. No WHOOP credentials needed here; it only writes
to local SQLite. Only config.TELEGRAM_CHAT_ID may interact.

Product rules enforced in the UI:
  * NO mood / feeling / tension / survey input anywhere.
  * Participants are never messaged; the app only uses the owner's own local data.
  * Aliases are encouraged for person names.
"""
import asyncio
import html
import logging
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
import export as export_mod
import report as report_mod
import tzutil

SEP = " · "

# Persistent main-menu labels
M_NEW, M_STOP, M_REPORTS = "➕ New meeting", "⏹ Stop", "📊 Reports"
M_TODAY, M_RECENT, M_SETTINGS = "📅 Today", "🕘 Recent", "⚙️ Settings"
MENU_LABELS = {M_NEW, M_STOP, M_REPORTS, M_TODAY, M_RECENT, M_SETTINGS}

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
log = logging.getLogger("wsmo-bot")

FRIEND, LOCATION, TOPIC = range(3)
CONV_TIMEOUT = 600
_rid_seq = 0


def _next_rid():
    global _rid_seq
    _rid_seq += 1
    return _rid_seq


def _now():
    return int(time.time())


def _auth(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat) and str(chat.id) == str(config.TELEGRAM_CHAT_ID)


async def _send_html(target, text, **kw):
    try:
        return await target.reply_text(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        log.warning("HTML send failed, falling back to plain: %s", e)
        plain = html.unescape(text.replace("<b>", "").replace("</b>", "")
                              .replace("<i>", "").replace("</i>", ""))
        return await target.reply_text(plain, **kw)


# ---------------- persistent menu + shortcuts ----------------
def _label(sc):
    return f"{sc['friend']}{SEP}{sc['tag']}"


def _find_shortcut(text):
    for s in db.list_shortcuts():
        if _label(s) == text:
            return s
    return None


def _main_keyboard():
    rows = [[KeyboardButton(M_NEW), KeyboardButton(M_STOP)]]
    labels = [_label(s) for s in db.list_shortcuts()]
    row = []
    for lbl in labels:
        row.append(KeyboardButton(lbl))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(M_REPORTS), KeyboardButton(M_TODAY)])
    rows.append([KeyboardButton(M_RECENT), KeyboardButton(M_SETTINGS)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


# ---------------- live meeting card ----------------
def _card_text(event_id):
    ev = next((e for e in db.recent_events(50) if e["id"] == event_id), None)
    if not ev:
        return "Meeting not found."
    parts = db.participant_names(ev) or ["—"]
    started = tzutil.fmt(ev["ts_start"], "%H:%M")
    elapsed = max(0, (_now() - ev["ts_start"]) // 60)
    conf = [k for k in ("alcohol", "illness", "commute") if ev.get(k)]
    if ev.get("caffeine") in ("low", "high"):
        conf.append(f"caffeine:{ev['caffeine']}")
    lines = [
        "🟢 <b>Meeting in progress</b>",
        f"👥 {html.escape(', '.join(parts))}",
        f"🏷 {html.escape(ev.get('tag') or '—')}   📍 {html.escape(ev.get('location') or '—')}",
        f"🕒 started {started} · {elapsed} min",
    ]
    if ev.get("topic"):
        lines.append(f"💬 {html.escape(ev['topic'])}")
    if ev.get("notes"):
        lines.append(f"📝 {html.escape(ev['notes'])}")
    if conf:
        lines.append(f"⚙️ {html.escape(', '.join(conf))}")
    return "\n".join(lines)


def _card_kb(event_id):
    e = str(event_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Add participant", callback_data=f"card:{e}:addp"),
         InlineKeyboardButton("📝 Note", callback_data=f"card:{e}:note")],
        [InlineKeyboardButton("⚙️ Extra context", callback_data=f"card:{e}:xc")],
        [InlineKeyboardButton("⏹ Stop", callback_data=f"card:{e}:stop"),
         InlineKeyboardButton("❌ Cancel", callback_data=f"card:{e}:cancel")],
    ])


async def _open_card(message, ctx, event_id):
    sent = await _send_html(message, _card_text(event_id), reply_markup=_card_kb(event_id))
    ctx.chat_data["card"] = {"event_id": event_id, "msg_id": sent.message_id if sent else None}


async def _refresh_card(ctx, event_id):
    card = ctx.chat_data.get("card")
    if not card or card.get("event_id") != event_id or not card.get("msg_id"):
        return
    try:
        await ctx.bot.edit_message_text(
            _card_text(event_id), chat_id=config.TELEGRAM_CHAT_ID, message_id=card["msg_id"],
            parse_mode=ParseMode.HTML, reply_markup=_card_kb(event_id))
    except Exception:
        pass


def _start_meeting(participants=None, friend=None, location=None, topic=None, tag=None):
    """Close any open meeting, open a new one, return its id."""
    db.close_open_events(_now(), config.MAX_WINDOW_MIN * 60, config.DEFAULT_WINDOW_MIN * 60)
    return db.add_event(ts_start=_now(), participants=participants, friend=friend,
                        location=location, topic=topic, tag=tag, created_at=_now())


# ---------------- card callbacks ----------------
_XC = {"caf0": ("caffeine", "none"), "caf1": ("caffeine", "low"), "caf2": ("caffeine", "high"),
       "alc": ("alcohol", 1), "ill": ("illness", 1), "com": ("commute", 1)}


def _xc_kb(event_id):
    e = str(event_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☕ Caffeine: none", callback_data=f"xc:{e}:caf0"),
         InlineKeyboardButton("low", callback_data=f"xc:{e}:caf1"),
         InlineKeyboardButton("high", callback_data=f"xc:{e}:caf2")],
        [InlineKeyboardButton("🍷 Alcohol", callback_data=f"xc:{e}:alc"),
         InlineKeyboardButton("🤒 Illness", callback_data=f"xc:{e}:ill"),
         InlineKeyboardButton("🚶 Commute", callback_data=f"xc:{e}:com")],
        [InlineKeyboardButton("✅ Done", callback_data=f"xc:{e}:done")],
    ])


async def card_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    _, eid, action = q.data.split(":")
    eid = int(eid)
    await q.answer()
    if action == "stop":
        db.close_latest_open_event(_now(), config.MAX_WINDOW_MIN * 60)
        await q.edit_message_text(_card_text(eid).replace("🟢 <b>Meeting in progress</b>",
                                                          "⏹ <b>Meeting ended</b>"),
                                  parse_mode=ParseMode.HTML)
        ctx.chat_data.pop("card", None)
    elif action == "cancel":
        db.delete_event(eid)
        await q.edit_message_text("❌ Meeting canceled and removed.")
        ctx.chat_data.pop("card", None)
    elif action == "addp":
        ctx.chat_data["await"] = ("participant", eid)
        await q.message.reply_text("✍️ Type the participant name (or alias):")
    elif action == "note":
        ctx.chat_data["await"] = ("note", eid)
        await q.message.reply_text("✍️ Type a short note:")
    elif action == "xc":
        await q.message.reply_text("⚙️ Extra context (optional — no mood questions):",
                                   reply_markup=_xc_kb(eid))


async def xc_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    _, eid, key = q.data.split(":")
    eid = int(eid)
    await q.answer()
    if key == "done":
        await q.edit_message_text("✅ Extra context saved.")
        await _refresh_card(ctx, eid)
        return
    col, val = _XC[key]
    if col in ("alcohol", "illness", "commute"):  # toggle
        cur = next((e.get(col) for e in db.recent_events(50) if e["id"] == eid), 0)
        val = 0 if cur else 1
    db.set_event_confounders(eid, **{col: val})
    await q.answer(f"{col} set", show_alert=False)


# ---------------- text dispatcher (menu + shortcuts + awaited input) ----------------
async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    text = (update.message.text or "").strip()

    awaiting = ctx.chat_data.get("await")
    if awaiting:
        kind, eid = awaiting
        ctx.chat_data.pop("await", None)
        if kind == "participant":
            db.add_participant(eid, text)
            db.add_name("friends", text)
        elif kind == "note":
            db.set_event_confounders(eid, notes=text)
        await _refresh_card(ctx, eid)
        await update.message.reply_text("✅ Updated.")
        return

    if text == M_NEW:
        return await _new_meeting_prompt(update, ctx)
    if text == M_STOP:
        return await _stop(update)
    if text == M_REPORTS:
        return await reports_menu(update, ctx)
    if text == M_TODAY:
        return await today(update, ctx)
    if text == M_RECENT:
        return await recent(update, ctx)
    if text == M_SETTINGS:
        return await settings(update, ctx)

    sc = _find_shortcut(text)
    if sc:
        eid = _start_meeting(participants=[sc["friend"]], tag=sc["tag"])
        await _open_card(update.message, ctx, eid)
        return

    await update.message.reply_text("Tap a button below, or use /meet · /log · /help.")


async def _new_meeting_prompt(update, ctx):
    await update.message.reply_text(
        "Start a meeting: tap a shortcut below, or use <b>/meet</b> for the step-by-step "
        "flow (person → context → optional note).", parse_mode=ParseMode.HTML)


async def _stop(update):
    eid, status = db.close_latest_open_event(_now(), config.MAX_WINDOW_MIN * 60)
    msg = {"no_open": "No meeting in progress.",
           "too_old": f"⚠ The last open meeting is very old (#{eid}); I didn't auto-close it. "
                      f"Use /delete {eid} if it was a leftover.",
           "ok": "⏹ Meeting ended."}[status]
    await update.message.reply_text(msg)


# ---------------- /meet step-by-step (no feeling) ----------------
def _render_kb(ctx, ns, names):
    rid = _next_rid()
    ctx.user_data[f"{ns}_rid"] = rid
    ctx.user_data[f"snap_{rid}"] = names
    rows = [[InlineKeyboardButton(n, callback_data=f"{ns}:{rid}:i:{i}")] for i, n in enumerate(names)]
    rows.append([InlineKeyboardButton("➕ New", callback_data=f"{ns}:{rid}:new"),
                 InlineKeyboardButton("⏭ Skip", callback_data=f"{ns}:{rid}:skip")])
    return InlineKeyboardMarkup(rows)


def _resolve(ctx, ns, data):
    parts = data.split(":")
    if len(parts) < 3:
        return "expired", None
    if int(parts[1]) != ctx.user_data.get(f"{ns}_rid"):
        return "expired", None
    kind = parts[2]
    if kind in ("new", "skip"):
        return kind, None
    names = ctx.user_data.get(f"snap_{parts[1]}", [])
    idx = int(parts[3])
    return "pick", (names[idx] if 0 <= idx < len(names) else None)


async def meet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["ts_start"] = _now()
    await update.message.reply_text("🤝 Who were you with?",
                                    reply_markup=_render_kb(ctx, "fr", db.list_names("friends")))
    return FRIEND


async def friend_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, name = _resolve(ctx, "fr", q.data)
    if action == "expired":
        await q.answer("This keyboard expired — start again with /meet.", show_alert=True)
        return FRIEND
    await q.answer()
    if action == "new":
        await q.edit_message_text("✍️ Type the person's name or alias:")
        return FRIEND
    if action == "pick":
        ctx.user_data["friend"] = name
    await q.edit_message_text(f"👤 {ctx.user_data.get('friend') or '—'}")
    return await _ask_location(q.message, ctx)


async def friend_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept(update, ctx):
        return ConversationHandler.END
    ctx.user_data["friend"] = update.message.text.strip()
    db.add_name("friends", ctx.user_data["friend"])
    return await _ask_location(update.message, ctx)


async def _ask_location(message, ctx):
    await message.reply_text("📍 Where?", reply_markup=_render_kb(ctx, "lo", db.list_names("locations")))
    return LOCATION


async def location_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, name = _resolve(ctx, "lo", q.data)
    if action == "expired":
        await q.answer("This keyboard expired — start again with /meet.", show_alert=True)
        return LOCATION
    await q.answer()
    if action == "new":
        await q.edit_message_text("✍️ Type the place:")
        return LOCATION
    if action == "pick":
        ctx.user_data["location"] = name
    await q.edit_message_text(f"📍 {ctx.user_data.get('location') or '—'}")
    await q.message.reply_text("🏷 Context/topic? (short, or /skip)")
    return TOPIC


async def location_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept(update, ctx):
        return ConversationHandler.END
    ctx.user_data["location"] = update.message.text.strip()
    db.add_name("locations", ctx.user_data["location"])
    await update.message.reply_text("🏷 Context/topic? (short, or /skip)")
    return TOPIC


async def topic_txt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _intercept(update, ctx):
        return ConversationHandler.END
    ctx.user_data["tag"] = update.message.text.strip()
    return await _finish_meet(update.message, ctx)


async def topic_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _finish_meet(update.message, ctx)


async def _finish_meet(message, ctx):
    d = ctx.user_data
    if not d.get("event_id"):
        d["event_id"] = _start_meeting(friend=d.get("friend"), location=d.get("location"),
                                       tag=d.get("tag"))
    await _open_card(message, ctx, d["event_id"])
    return ConversationHandler.END


async def conv_timeout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update and update.effective_message and not ctx.user_data.get("event_id"):
        await update.effective_message.reply_text("⏱ Timed out — start again with /meet.")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _auth(update):
        await update.message.reply_text("Canceled.")
    return ConversationHandler.END


async def _intercept(update, ctx):
    """If a menu/shortcut button is tapped mid-/meet, end the conversation and route it."""
    text = (update.message.text or "").strip()
    if text in MENU_LABELS or _find_shortcut(text):
        await text_router(update, ctx)
        return True
    return False


# ---------------- reports / today / recent / settings ----------------
async def reports_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Last 7 days", callback_data="rep:7"),
         InlineKeyboardButton("Last 30 days", callback_data="rep:30")],
        [InlineKeyboardButton("⬇️ Export CSV", callback_data="rep:csv"),
         InlineKeyboardButton("⬇️ Export JSON", callback_data="rep:json")],
    ])
    await update.message.reply_text("📊 Reports", reply_markup=kb)


async def report_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    _, arg = q.data.split(":")
    await q.answer()
    if arg in ("7", "30"):
        await q.message.reply_text("Calculating…")
        text = await asyncio.to_thread(report_mod.build_report, int(arg))
        await _send_html(q.message, text)
    else:
        await _do_export(q.message, arg)


async def today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    day = tzutil.fmt(_now(), "%Y-%m-%d")
    d = db.get_daily(day)
    if not d:
        await update.message.reply_text(
            f"No official WHOOP data for {day} yet (OAuth connected? sync run?).")
        return
    await update.message.reply_text(
        f"📅 {day}\nRecovery {d.get('recovery') or '—'}% · HRV {d.get('hrv') or '—'}ms\n"
        f"Resting HR {d.get('rhr') or '—'} · Day strain {d.get('strain') or '—'}\n"
        f"Sleep performance {d.get('sleep_perf') or '—'}%")


def _recent_kb(evs, page):
    rows = []
    for e in evs:
        rows.append([InlineKeyboardButton(f"🗑 #{e['id']}", callback_data=f"rec:del:{e['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"rec:pg:{page-1}"))
    nav.append(InlineKeyboardButton("↻", callback_data=f"rec:pg:{page}"))
    if len(evs) == 5:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"rec:pg:{page+1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _recent_text(evs, page):
    if not evs:
        return "No records."
    lines = [f"🕘 <b>Recent</b> (page {page + 1})"]
    for e in evs:
        parts = ", ".join(db.participant_names(e)) or "—"
        mark = " ⏺open" if e["ts_end"] is None else ""
        lines.append(f"#{e['id']} {tzutil.fmt(e['ts_start'])} · {html.escape(parts)} · "
                     f"{html.escape(e.get('tag') or '—')}{mark}")
    return "\n".join(lines)


def _recent_page(page):
    with db.get_conn() as c:
        rows = c.execute("SELECT * FROM events ORDER BY ts_start DESC LIMIT 5 OFFSET ?",
                         (page * 5,)).fetchall()
    return [dict(r) for r in rows]


async def recent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    evs = _recent_page(0)
    await _send_html(update.message, _recent_text(evs, 0), reply_markup=_recent_kb(evs, 0))


async def recent_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    parts = q.data.split(":")
    await q.answer()
    if parts[1] == "pg":
        page = int(parts[2])
        evs = _recent_page(page)
        await q.edit_message_text(_recent_text(evs, page), parse_mode=ParseMode.HTML,
                                  reply_markup=_recent_kb(evs, page))
    elif parts[1] == "del":  # step 1: confirm
        eid, page = int(parts[2]), int(parts[3])
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Delete", callback_data=f"rec:yes:{eid}:{page}"),
            InlineKeyboardButton("↩ Keep", callback_data=f"rec:pg:{page}")]])
        await q.edit_message_text(f"Delete record #{eid}? This can be undone briefly.",
                                  reply_markup=kb)
    elif parts[1] == "yes":  # step 2: delete + offer undo
        eid, page = int(parts[2]), int(parts[3])
        ev = next((e for e in db.recent_events(200) if e["id"] == eid), None)
        if ev:
            ev["participants"] = db.participant_names(ev)
            ctx.chat_data["undo"] = ev
        db.delete_event(eid)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩ Undo", callback_data=f"rec:undo:{eid}:{page}")]])
        await q.edit_message_text(f"🗑 Deleted #{eid}.", reply_markup=kb)
    elif parts[1] == "undo":
        ev = ctx.chat_data.pop("undo", None)
        if ev:
            db.add_event(ts_start=ev["ts_start"], ts_end=ev["ts_end"],
                         participants=ev.get("participants"), location=ev.get("location"),
                         topic=ev.get("topic"), tag=ev.get("tag"), created_at=ev.get("created_at"),
                         caffeine=ev.get("caffeine"), alcohol=ev.get("alcohol"),
                         illness=ev.get("illness"), commute=ev.get("commute"), notes=ev.get("notes"))
            await q.edit_message_text("↩ Restored.")
        else:
            await q.edit_message_text("Nothing to undo.")


async def settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = db.data_summary()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Shortcuts", callback_data="set:sc"),
         InlineKeyboardButton("⬇️ Export", callback_data="set:exp")],
        [InlineKeyboardButton("🗑 Delete all data", callback_data="set:wipe")],
    ])
    await _send_html(update.message,
                     f"⚙️ <b>Settings</b>\nTimezone: {config.LOCAL_TZ}\n"
                     f"Events: {s['events']} · HR samples: {s['hr_samples']} · "
                     f"Official days: {s['daily_days']}\n"
                     f"Aliases are encouraged for person names — your data stays local.",
                     reply_markup=kb)


async def settings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    _, arg = q.data.split(":")
    await q.answer()
    if arg == "sc":
        await shortcuts_cmd(update, ctx, q.message)
    elif arg == "exp":
        await _do_export(q.message, "json")
    elif arg == "wipe":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ Yes, delete everything", callback_data="wipe:yes"),
            InlineKeyboardButton("Cancel", callback_data="wipe:no")]])
        await q.message.reply_text("This permanently deletes ALL local data. Continue?",
                                   reply_markup=kb)


async def wipe_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _auth(update):
        await q.answer("Unauthorized", show_alert=True)
        return
    _, arg = q.data.split(":")
    await q.answer()
    if arg == "yes":
        db.wipe_all()
        await q.edit_message_text("🗑 All local data deleted.")
    else:
        await q.edit_message_text("Canceled.")


# ---------------- data lifecycle commands ----------------
async def mydata(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    s = db.data_summary()
    rng = ""
    if s["hr_from"] and s["hr_to"]:
        rng = f"\nHR range: {tzutil.fmt(s['hr_from'], '%d %b')} → {tzutil.fmt(s['hr_to'], '%d %b')}"
    await update.message.reply_text(
        f"🔐 <b>Your stored data</b> (all local)\n"
        f"Events: {s['events']} · Participants: {s['participants']}\n"
        f"HR samples: {s['hr_samples']}{rng}\n"
        f"Official days: {s['daily_days']} · Workouts: {s['workouts']} · Shortcuts: {s['shortcuts']}\n\n"
        f"Export: /export · Delete all: /deletemydata", parse_mode=ParseMode.HTML)


async def _do_export(message, fmt):
    path = export_mod.write_export(fmt)
    with open(path, "rb") as f:
        await message.reply_document(
            f, filename=path.split("/")[-1].split("\\")[-1],
            caption="⚠️ Contains your personal names, notes and heart-rate data — keep it private.")


async def export_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    arg = (update.message.text.partition(" ")[2].strip() or "json").lower()
    await _do_export(update.message, "csv" if arg.startswith("c") else "json")


async def deletemydata(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Yes, delete everything", callback_data="wipe:yes"),
        InlineKeyboardButton("Cancel", callback_data="wipe:no")]])
    await update.message.reply_text("This permanently deletes ALL local data. Continue?",
                                    reply_markup=kb)


# ---------------- shortcuts management (backward compatible) ----------------
async def shortcuts_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE, message=None):
    if not _auth(update):
        return
    message = message or update.message
    lines = ["⚡ <b>Shortcuts</b>"]
    for s in db.list_shortcuts():
        lines.append(f"#{s['id']}  {html.escape(_label(s))}")
    lines.append("\nAdd: /add Name | context     Remove: /remove &lt;id&gt;")
    await _send_html(message, "\n".join(lines))


async def add_shortcut_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.message.reply_text("Usage: /add Name | context   (e.g. /add Sam | trip)")
        return
    friend, _, tag = raw.partition("|")
    status = db.add_shortcut(friend.strip(), tag.strip())
    msg = {"invalid": "Name and context can't be empty.",
           "exists": f"ℹ️ Already exists: {friend.strip()}{SEP}{tag.strip()}",
           "added": f"✅ Added: {friend.strip()}{SEP}{tag.strip()}"}[status]
    await update.message.reply_text(msg, reply_markup=_main_keyboard())


async def remove_shortcut_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Usage: /remove <id>  (see ids with /shortcuts)")
        return
    ok = db.delete_shortcut(int(arg))
    await update.message.reply_text("🗑 Removed." if ok else "Not found.",
                                    reply_markup=_main_keyboard())


async def quick_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text("Usage: /log Person | Place | Context")
        return
    p = [x.strip() or None for x in raw.split("|")]
    friend, location, tag = (p + [None, None, None])[:3]
    if friend:
        db.add_name("friends", friend)
    eid = _start_meeting(friend=friend, location=location, tag=tag)
    await _open_card(update.message, ctx, eid)


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        return
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Usage: /delete <id>  (see ids with 🕘 Recent)")
        return
    ok = db.delete_event(int(arg))
    await update.message.reply_text("🗑 Deleted." if ok else "Not found.")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _auth(update):
        await update.message.reply_text("Unauthorized.")
        return
    await _send_html(update.message,
                     "👋 <b>who-stresses-me-out</b>\n\n"
                     "Log <i>who you were with</i> with one tap; the analysis correlates it with "
                     "your WHOOP data. No mood or survey questions — associations only, not causes.\n\n"
                     "Use the buttons below, or: /meet · /log · /shortcuts · /mydata · /export",
                     reply_markup=_main_keyboard())


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await start(update, ctx)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Handler error", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_chat and _auth(update):
            await ctx.bot.send_message(chat_id=update.effective_chat.id,
                                       text="⚠ Something went wrong, but the bot is fine. Try again.")
    except Exception:
        pass


def _cmd(app, names, fn):
    for n in names if isinstance(names, (list, tuple)) else [names]:
        app.add_handler(CommandHandler(n, fn))


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
            TOPIC: [CommandHandler("skip", topic_skip),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, topic_txt)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conv_timeout)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONV_TIMEOUT, allow_reentry=True,
    )
    app.add_handler(conv)
    _cmd(app, ["start", "help"], start)
    _cmd(app, "log", quick_log)
    _cmd(app, ["shortcuts", "kisayollar"], shortcuts_cmd)
    _cmd(app, ["add", "ekle"], add_shortcut_cmd)
    _cmd(app, ["remove", "cikar"], remove_shortcut_cmd)
    _cmd(app, ["delete", "sil"], delete_cmd)
    _cmd(app, ["mydata", "verilerim"], mydata)
    _cmd(app, ["export", "disaaktar"], export_cmd)
    _cmd(app, ["deletemydata", "verilerimisil"], deletemydata)
    app.add_handler(CallbackQueryHandler(card_cb, pattern=r"^card:"))
    app.add_handler(CallbackQueryHandler(xc_cb, pattern=r"^xc:"))
    app.add_handler(CallbackQueryHandler(report_cb, pattern=r"^rep:"))
    app.add_handler(CallbackQueryHandler(recent_cb, pattern=r"^rec:"))
    app.add_handler(CallbackQueryHandler(settings_cb, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(wipe_cb, pattern=r"^wipe:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(on_error)
    log.info("bot running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

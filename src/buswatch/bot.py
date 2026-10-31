"""Telegram bot: /start, /watch, /check commands with inline-button flows."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from buswatch.config import POLL_TIMES, THRESHOLD, load_settings
from buswatch.db import Database
from buswatch.models import Direction, NormalizedTrip, classify_vehicle
from buswatch.sources import ALL_SOURCES
from buswatch.sources.base import SourceError
from buswatch.watcher import watcher_loop
from buswatch.web import serve as web_serve

log = logging.getLogger("buswatch.bot")

# Flow state, keyed by chat_id (multi-user: each chat has its own).
# /check flow: date + direction + company + results; /watch flow: date.
_check_state: dict[int, dict] = {}
_watch_state: dict[int, dict] = {}
# chat_id -> "check" | "watch": we asked for a date, waiting for a text reply.
_date_wait: dict[int, str] = {}

_DATE_HINT = "dd-mm-yy (e.g. 20-10-26)"


def _parse_user_date(text: str) -> date | None:
    """Parse a user-supplied dd-mm-yy date; None if malformed."""
    try:
        return datetime.strptime(text.strip(), "%d-%m-%y").date()
    except ValueError:
        return None


def _format_user_date(d: date) -> str:
    return d.strftime("%d-%m-%y")


def _direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("PP → SR", callback_data="dir:PP_SR"),
                InlineKeyboardButton("SR → PP", callback_data="dir:SR_PP"),
            ]
        ]
    )


def _company_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("CamboLink21", callback_data="co:CamboLink21"),
                InlineKeyboardButton("Saly VIP", callback_data="co:SalyVIP"),
            ],
            [InlineKeyboardButton("Larryta", callback_data="co:Larryta")],
        ]
    )


def _help_text(name: str) -> str:
    return (
        f"Hello {name}, how can I help you with seat availability?\n\n"
        "Here are the available commands:\n"
        f"/watch [dd-mm-yy]: watch a date for low seats (alerts at ≤{THRESHOLD} seats)\n"
        "/unwatch: stop receiving alerts\n"
        f"/check [dd-mm-yy]: instant seat availability lookup\n\n"
        f"Date format: {_DATE_HINT}\n"
        "Omit the date and I'll ask for it."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await _track_user(update, context)
        name = "there"
        if update.effective_user and update.effective_user.first_name:
            name = update.effective_user.first_name
        await update.message.reply_text(_help_text(name))


async def _track_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    db: Database = context.bot_data["db"]
    await db.upsert_user(user.id, user.first_name, user.username)


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_chat:
        await _track_user(update, context)
        chat_id = update.effective_chat.id
        args = context.args or []
        if args:
            d = _parse_user_date(args[0])
            if d is None:
                await update.message.reply_text(
                    f"Couldn't read that date. Please use format {_DATE_HINT}."
                )
                return
            _watch_state[chat_id] = {"date": d}
            _date_wait.pop(chat_id, None)
            await update.message.reply_text(
                "Which direction do you want to watch?", reply_markup=_direction_keyboard()
            )
            return
        # no date given — ask for it
        _date_wait[chat_id] = "watch"
        await update.message.reply_text(f"What date do you want to watch? Send it as {_DATE_HINT}.")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_chat:
        await _track_user(update, context)
        chat_id = update.effective_chat.id
        args = context.args or []
        if args:
            d = _parse_user_date(args[0])
            if d is None:
                await update.message.reply_text(
                    f"Couldn't read that date. Please use format {_DATE_HINT}."
                )
                return
            _check_state[chat_id] = {"date": d}
            _date_wait.pop(chat_id, None)
            await update.message.reply_text(
                "Check which direction?", reply_markup=_direction_keyboard()
            )
            return
        # no date given — ask for it
        _date_wait[chat_id] = "check"
        await update.message.reply_text(f"What date do you want to check? Send it as {_DATE_HINT}.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch a typed date when a /check or /watch flow is waiting for one."""
    if update.message is None or update.effective_chat is None or update.message.text is None:
        return
    chat_id = update.effective_chat.id
    flow = _date_wait.get(chat_id)
    if flow is None:
        await _track_user(update, context)
        name = "there"
        if update.effective_user and update.effective_user.first_name:
            name = update.effective_user.first_name
        await update.message.reply_text(_help_text(name))
        return

    d = _parse_user_date(update.message.text)
    if d is None:
        await update.message.reply_text(f"Couldn't read that date. Please use format {_DATE_HINT}.")
        return

    _date_wait.pop(chat_id, None)
    if flow == "watch":
        _watch_state[chat_id] = {"date": d}
        await update.message.reply_text(
            "Which direction do you want to watch?", reply_markup=_direction_keyboard()
        )
    else:
        _check_state[chat_id] = {"date": d}
        await update.message.reply_text(
            "Check which direction?", reply_markup=_direction_keyboard()
        )


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_chat:
        await _track_user(update, context)
        db: Database = context.bot_data["db"]
        removed = await db.delete_direction(update.effective_chat.id)
        if removed:
            await update.message.reply_text(
                "🛑 Stopped watching. You won't get seat alerts anymore.\n"
                "Run /watch to start again."
            )
        else:
            await update.message.reply_text(
                "You're not watching anything right now. Run /watch to set a direction."
            )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_chat is None:
        return
    await query.answer()

    chat_id = update.effective_chat.id
    data = query.data or ""

    if data.startswith("dir:"):
        direction = Direction(data.split(":", 1)[1])
        state = _check_state.get(chat_id)
        wstate = _watch_state.get(chat_id)
        if state is not None and "date" in state:
            # part of a /check flow
            state["direction"] = direction
            await query.edit_message_text(
                f"{_format_user_date(state['date'])} · {direction.label}\n\nWhich company?",
                reply_markup=_company_keyboard(),
            )
        elif wstate is not None and "date" in wstate:
            # part of a /watch flow — open to any chat (multi-user)
            db: Database = context.bot_data["db"]
            day = wstate["date"]
            await db.set_direction(chat_id, direction, day.isoformat())
            _watch_state.pop(chat_id, None)
            times = ", ".join(f"{t.hour:02d}:{t.minute:02d}" for t in POLL_TIMES)
            await query.edit_message_text(
                f"✅ Watching <b>{direction.label}</b> on {_format_user_date(day)}.\n"
                f"Alerts at ≤{THRESHOLD} seats, polled daily at {times} (Phnom Penh time).",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("Expired — run /watch or /check again.")
        return

    if data.startswith("co:"):
        company = data.split(":", 1)[1]
        state = _check_state.setdefault(chat_id, {})
        state["company"] = company

        direction = state.get("direction")
        day = state.get("date")
        if direction is None or day is None:
            await query.edit_message_text("Expired — run /check again.")
            _check_state.pop(chat_id, None)
            return

        await query.edit_message_text(
            f"Fetching {company} {direction.label} on {_format_user_date(day)} …"
        )

        source = next(s for s in ALL_SOURCES if s.company == company)
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                trips = await source.fetch(http, day, direction)
        except (SourceError, httpx.HTTPError) as exc:
            log.warning("Check fetch failed: %s", exc)
            await query.edit_message_text(f"Fetch failed for {company}. Try again.")
            _check_state.pop(chat_id, None)
            return

        if not trips:
            await query.edit_message_text("No trips found.")
            _check_state.pop(chat_id, None)
            return

        # group by vehicle class
        groups: dict[str, list[NormalizedTrip]] = {}
        for t in trips:
            groups.setdefault(classify_vehicle(t.vehicle), []).append(t)

        state["trips"] = trips
        buttons = [
            [InlineKeyboardButton(f"{g} ({len(ts)})", callback_data=f"veh:{g}")]
            for g, ts in sorted(groups.items())
        ]
        state["groups"] = groups
        await query.edit_message_text(
            "Pick a vehicle type:", reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("veh:"):
        group = data.split(":", 1)[1]
        state = _check_state.get(chat_id)
        if state is None or "groups" not in state:
            await query.edit_message_text("Expired — run /check again.")
            return
        trips = state["groups"].get(group, [])
        state["filtered"] = trips
        buttons = [
            [
                InlineKeyboardButton(
                    f"{t.depart_time} ({t.seats_available} left)"
                    if t.seats_available
                    else f"{t.depart_time} (sold out)",
                    callback_data=f"trip:{i}",
                )
            ]
            for i, t in enumerate(trips)
        ]
        await query.edit_message_text(
            "Pick a time slot:", reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("trip:"):
        idx = int(data.split(":", 1)[1])
        state = _check_state.pop(chat_id, {})
        trips = state.get("filtered", [])
        if idx >= len(trips):
            await query.edit_message_text("Expired — run /check again.")
            return
        t = trips[idx]
        seats = (
            "sold out"
            if t.seats_available == 0
            else f"{t.seats_available} / {t.seats_total or '?'}"
        )
        await query.edit_message_text(
            f"<b>{t.company}</b> · {t.date}\n"
            f"{t.depart_time} → {t.arrive_time}\n"
            f"{t.vehicle}\n"
            f"Seats: {seats}\n"
            f"Price: {t.price:g} {t.currency}",
            parse_mode="HTML",
        )
        return


def build_application() -> tuple[Application, Database]:
    settings = load_settings()
    db = Database(settings.db_path)

    app = Application.builder().token(settings.bot_token).build()
    app.bot_data["db"] = db
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CallbackQueryHandler(on_callback))
    # catch date replies (must not swallow commands or button taps)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    return app, db


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app, db = build_application()

    await db.connect()

    async def send_alert(chat_id: int, text: str) -> None:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    # start watcher + local users web page as background tasks alongside the bot
    async with app:
        settings = app.bot_data["settings"]
        watcher_task = asyncio.create_task(watcher_loop(db, send_alert))
        web_task = asyncio.create_task(web_serve(db, port=settings.web_port))
        updater = app.updater
        if updater is None:
            raise RuntimeError("Application has no updater — builder misconfigured")
        try:
            await app.start()
            await updater.start_polling(drop_pending_updates=True)

            # register the "/" commands menu (shows available commands)
            try:
                await app.bot.set_my_commands(
                    [
                        ("start", "Introduce the bot"),
                        ("watch", "Watch a date for low seats"),
                        ("unwatch", "Stop receiving alerts"),
                        ("check", "Check seats for a date"),
                    ]
                )
                await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            except Exception:
                log.exception("Could not register command menu")

            # run until interrupted
            await watcher_task
        finally:
            web_task.cancel()
            await updater.stop()
            await app.stop()
            await db.close()

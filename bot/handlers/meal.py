from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ConversationHandler, ContextTypes

import db
import llm
from config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

CLARIFYING = 1


@asynccontextmanager
async def keep_typing(chat):
    # Telegram's typing indicator expires after ~5s; refresh it while LLM calls run.
    async def loop():
        while True:
            try:
                await chat.send_action(ChatAction.TYPING)
            except Exception:
                return
            await asyncio.sleep(4)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def _window_to_datetimes(window: dict) -> tuple[str, str]:
    """Convert an extract_time_window result to (since_iso, until_iso) UTC strings."""
    now = datetime.now(timezone.utc)
    # until is always just past now so current meals are included
    until = now + timedelta(minutes=1)
    w = window.get("window", "today")
    n = window.get("n", 1)

    if w == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif w == "yesterday":
        yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        since = yesterday
        until = yesterday + timedelta(days=1)
    elif w == "last_N_hours":
        since = now - timedelta(hours=n)
    elif w == "last_N_days":
        since = (now - timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif w == "last_N_weeks":
        since = (now - timedelta(weeks=n)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif w == "last_N_months":
        since = (now - timedelta(days=30 * n)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # all_time — hard cap at 365 days
        since = (now - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)

    fmt = "%Y-%m-%d %H:%M:%S"
    return since.strftime(fmt), until.strftime(fmt)


def _sum_meals_macros(meals: list[dict]) -> dict:
    return {
        "calories":  [sum(m["calories_min"] for m in meals), sum(m["calories_max"] for m in meals)],
        "protein_g": [sum(m["protein_min"]  for m in meals), sum(m["protein_max"]  for m in meals)],
        "carbs_g":   [sum(m["carbs_min"]    for m in meals), sum(m["carbs_max"]    for m in meals)],
        "fat_g":     [sum(m["fat_min"]      for m in meals), sum(m["fat_max"]      for m in meals)],
    }


def _window_label(window: dict) -> str:
    w = window.get("window", "today")
    n = window.get("n", 1)
    now = datetime.now(timezone.utc)
    if w == "today":
        return "Today, " + now.strftime("%b %-d")
    if w == "yesterday":
        return "Yesterday, " + (now - timedelta(days=1)).strftime("%b %-d")
    if w == "last_N_hours":
        return f"Last {n} hour{'s' if n != 1 else ''}"
    if w == "last_N_days":
        return f"Last {n} days"
    if w == "last_N_weeks":
        return f"Last {n} week{'s' if n != 1 else ''}"
    if w == "last_N_months":
        return f"Last {n} month{'s' if n != 1 else ''}"
    return "All time"


_DELETE_PREFIX = "del:"
CLARIFY_PREFIX = "clarify:"


def _delete_keyboard(meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Delete", callback_data=f"{_DELETE_PREFIX}{meal_id}")]]
    )


def _question_keyboard(options: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(opt, callback_data=f"{CLARIFY_PREFIX}{opt}")] for opt in options]
    )


def _format_range(lo: float, hi: float, unit: str = "") -> str:
    return f"{int(lo)}–{int(hi)}{unit}"


def _build_reply(description: str, macros: dict) -> str:
    lines = [
        f"Calories:  {_format_range(*macros['calories'], ' kcal')}",
        f"Protein:   {_format_range(*macros['protein_g'], ' g')}",
        f"Carbs:     {_format_range(*macros['carbs_g'], ' g')}",
        f"Fat:       {_format_range(*macros['fat_g'], ' g')}",
    ]
    note = macros.get("portion_note", "")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


async def _log_and_reply(update: Update, user_id: int, description: str, macros: dict) -> None:
    meal_id = await db.log_meal(user_id, description, macros)
    reply = _build_reply(description, macros)
    await update.effective_message.reply_text(reply, reply_markup=_delete_keyboard(meal_id))


async def _process_clarification(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    answer: str,
) -> int:
    pending = context.user_data.get("pending")
    if not pending:
        return ConversationHandler.END

    pending["clarifications"].append(answer)
    pending["current_q_idx"] += 1

    idx = pending["current_q_idx"]
    if idx < len(pending["planned_questions"]):
        next_q = pending["planned_questions"][idx]
        keyboard = _question_keyboard(next_q.get("options", []))
        await update.effective_message.reply_text(next_q["question"], reply_markup=keyboard)
        return CLARIFYING

    async with keep_typing(update.effective_chat):
        try:
            result = await llm.finalize(
                pending["original"],
                pending["planned_questions"],
                pending["clarifications"],
            )
        except ValueError:
            await update.effective_message.reply_text("Sorry, I couldn't process that. Try again.")
            context.user_data.pop("pending", None)
            return ConversationHandler.END

        user = update.effective_user
        user_id = await db.upsert_user(user.id, user.full_name)
        await _log_and_reply(update, user_id, pending["original"], result)
        context.user_data.pop("pending", None)
        return ConversationHandler.END


async def meal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if ALLOWED_USER_IDS and user.id not in ALLOWED_USER_IDS:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text:
        return ConversationHandler.END

    async with keep_typing(update.message.chat):
        try:
            classification = await llm.classify(text)
        except ValueError:
            await update.message.reply_text("Sorry, I couldn't process that. Try again.")
            return ConversationHandler.END

        if classification["type"] == "analytics":
            user_id = await db.upsert_user(user.id, user.full_name)
            try:
                window = await llm.extract_time_window(text)
            except Exception:
                window = {"window": "today"}
            since_dt, until_dt = _window_to_datetimes(window)
            meals = await db.get_meals_in_window(user_id, since_dt, until_dt)
            label = _window_label(window)
            if not meals:
                await update.message.reply_text(f"No meals logged for {label.lower()}.")
                return ConversationHandler.END
            totals = _sum_meals_macros(meals)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                summary = await llm.analytics_summary(text, totals, len(meals), today_str)
            except ValueError:
                summary = f"{len(meals)} meal(s) logged."
            reply = "\n".join([
                label,
                f"Calories:  {_format_range(*totals['calories'], ' kcal')}",
                f"Protein:   {_format_range(*totals['protein_g'], ' g')}",
                f"Carbs:     {_format_range(*totals['carbs_g'], ' g')}",
                f"Fat:       {_format_range(*totals['fat_g'], ' g')}",
                "",
                summary,
            ])
            await update.message.reply_text(reply)
            return ConversationHandler.END

        if classification["type"] == "general":
            history = context.user_data.get("general_history", [])
            try:
                reply = await llm.general_reply(text, history)
            except ValueError:
                await update.message.reply_text("Something went wrong. Try again!")
                return ConversationHandler.END
            await update.message.reply_text(reply)
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            context.user_data["general_history"] = history[-40:]
            return ConversationHandler.END

        try:
            result = await llm.plan(text)
        except ValueError:
            await update.message.reply_text("Sorry, I couldn't process that. Try again.")
            return ConversationHandler.END

        if result["type"] == "questions":
            planned_qs = result["questions"]
            first_q = planned_qs[0]
            context.user_data["pending"] = {
                "original": text,
                "planned_questions": planned_qs,
                "clarifications": [],
                "current_q_idx": 0,
            }
            keyboard = _question_keyboard(first_q.get("options", []))
            await update.message.reply_text(first_q["question"], reply_markup=keyboard)
            return CLARIFYING

        user_id = await db.upsert_user(user.id, user.full_name)
        await _log_and_reply(update, user_id, text, result)
        return ConversationHandler.END


async def clarification_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    pending = context.user_data.get("pending")
    if pending:
        idx = pending["current_q_idx"]
        current_q = pending["planned_questions"][idx]["question"] if idx < len(pending["planned_questions"]) else ""
        async with keep_typing(update.effective_chat):
            intent = await llm.classify_clarification_intent(current_q, text)
        if intent["intent"] == "cancel":
            context.user_data.pop("pending", None)
            await update.message.reply_text("No problem, I've cancelled that. Send a new meal whenever you're ready.")
            return ConversationHandler.END
    return await _process_clarification(update, context, text)


async def clarification_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    option = query.data[len(CLARIFY_PREFIX):]
    await query.edit_message_text(f"{query.message.text}  →  {option}")
    return await _process_clarification(update, context, option)


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending", None)
    await update.message.reply_text("Cancelled. Send a new meal whenever you're ready.")
    return ConversationHandler.END


async def delete_meal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    meal_id = int(query.data[len(_DELETE_PREFIX):])
    user = update.effective_user
    user_id = await db.upsert_user(user.id, user.full_name)

    meal = await db.get_meal_by_id(meal_id, user_id)
    if meal is None:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    await db.delete_meal(meal_id, user_id)
    await query.edit_message_text(f"{query.message.text}\n\nDeleted.")

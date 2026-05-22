from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ConversationHandler, ContextTypes

import db
import llm
from config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

CLARIFYING = 1
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

    await update.effective_chat.send_action(ChatAction.TYPING)

    idx = pending["current_q_idx"]
    if idx < len(pending["planned_questions"]):
        next_q = pending["planned_questions"][idx]
        keyboard = _question_keyboard(next_q.get("options", []))
        await update.effective_message.reply_text(next_q["question"], reply_markup=keyboard)
        return CLARIFYING

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

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        classification = await llm.classify(text)
    except ValueError:
        await update.message.reply_text("Sorry, I couldn't process that. Try again.")
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
    return await _process_clarification(update, context, update.message.text.strip())


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

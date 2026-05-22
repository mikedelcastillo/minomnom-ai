from __future__ import annotations

from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

import db
from config import ALLOWED_USER_IDS


def _rng(lo: float, hi: float, unit: str = "") -> str:
    return f"{int(lo)}–{int(hi)}{unit}"


def _sum_meals(meals: list[dict]) -> dict:
    return {
        "cal": (sum(m["calories_min"] for m in meals), sum(m["calories_max"] for m in meals)),
        "prot": (sum(m["protein_min"] for m in meals), sum(m["protein_max"] for m in meals)),
        "carbs": (sum(m["carbs_min"] for m in meals), sum(m["carbs_max"] for m in meals)),
        "fat": (sum(m["fat_min"] for m in meals), sum(m["fat_max"] for m in meals)),
    }


async def _get_user_id(update: Update) -> int | None:
    user = update.effective_user
    if ALLOWED_USER_IDS and user.id not in ALLOWED_USER_IDS:
        return None
    return await db.upsert_user(user.id, user.full_name)


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _get_user_id(update)
    if user_id is None:
        return

    meals = await db.get_today(user_id)
    if not meals:
        await update.message.reply_text("No meals logged today yet.")
        return

    lines = ["Today's meals:"]
    for m in meals:
        lines.append(f"  {m['description']} — {_rng(m['calories_min'], m['calories_max'], ' kcal')}")

    totals = _sum_meals(meals)
    lines.append(
        f"\nTotal: {_rng(*totals['cal'], ' kcal')} | "
        f"P: {_rng(*totals['prot'], 'g')} | "
        f"C: {_rng(*totals['carbs'], 'g')} | "
        f"F: {_rng(*totals['fat'], 'g')}"
    )
    await update.message.reply_text("\n".join(lines))


async def week_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _get_user_id(update)
    if user_id is None:
        return

    rows = await db.get_week(user_id)
    if not rows:
        await update.message.reply_text("No meals logged in the last 7 days.")
        return

    lines = ["Last 7 days:"]
    for row in rows:
        day = datetime.fromisoformat(row["day"]).strftime("%a %b %d")
        cal = _rng(row["cal_min"], row["cal_max"], " kcal")
        lines.append(f"  {day}: {cal} ({row['meal_count']} meals)")

    await update.message.reply_text("\n".join(lines))


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _get_user_id(update)
    if user_id is None:
        return

    limit = 10
    if context.args:
        try:
            limit = max(1, min(50, int(context.args[0])))
        except ValueError:
            await update.message.reply_text("Usage: /history [count] (e.g. /history 20)")
            return

    meals = await db.get_history(user_id, limit=limit)
    if not meals:
        await update.message.reply_text("No meals logged yet.")
        return

    lines = ["Recent meals:"]
    for i, m in enumerate(meals, start=1):
        ts = datetime.fromisoformat(m["logged_at"]).strftime("%m/%d %H:%M")
        cal = _rng(m["calories_min"], m["calories_max"], " kcal")
        lines.append(f"  {i}. [{ts}] {m['description']} — {cal}")
    lines.append("\nUse /delete [n] to remove an entry.")

    await update.message.reply_text("\n".join(lines))


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _get_user_id(update)
    if user_id is None:
        return

    if not context.args:
        await update.message.reply_text("Usage: /delete [n] — removes the nth most recent meal (1 = latest).")
        return

    try:
        n = int(context.args[0])
        if n < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Usage: /delete [n] — n must be a positive integer.")
        return

    meals = await db.get_history(user_id, limit=n)
    if n > len(meals):
        await update.message.reply_text(f"No entry #{n}.")
        return

    meal = meals[n - 1]
    deleted = await db.delete_meal(meal["id"], user_id)
    if deleted:
        await update.message.reply_text(f'Removed #{n}: "{meal["description"]}"')
    else:
        await update.message.reply_text("Couldn't remove that meal.")


async def undo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _get_user_id(update)
    if user_id is None:
        return

    last = await db.get_last_meal(user_id)
    if not last:
        await update.message.reply_text("Nothing to undo.")
        return

    deleted = await db.delete_meal(last["id"], user_id)
    if deleted:
        await update.message.reply_text(f'Removed: "{last["description"]}"')
    else:
        await update.message.reply_text("Couldn't remove that meal.")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Send any meal description to log it.\n\n"
        "/today — today's meals and totals\n"
        "/week — daily breakdown for the last 7 days\n"
        "/history [n] — last n meals, numbered newest-first (default 10)\n"
        "/delete [n] — remove the nth most recent meal\n"
        "/undo — remove the last logged meal\n"
        "/help — this message"
    )
    await update.message.reply_text(text)

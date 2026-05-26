import asyncio
import time
from collections import deque

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import RATE_LIMIT_PER_MINUTE

_WINDOW_SECONDS = 60.0
_lock = asyncio.Lock()
_timestamps: deque[float] = deque()


async def throttle_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock:
        now = time.monotonic()
        while _timestamps and _timestamps[0] <= now - _WINDOW_SECONDS:
            _timestamps.popleft()
        if len(_timestamps) < RATE_LIMIT_PER_MINUTE:
            _timestamps.append(now)
            return
        retry_after = int(_WINDOW_SECONDS - (now - _timestamps[0])) + 1

    if update.callback_query is not None:
        await update.callback_query.answer(
            f"Busy — try again in ~{retry_after}s.", show_alert=False
        )
    elif update.effective_message is not None:
        await update.effective_message.reply_text(
            f"I'm at the rate limit right now. Try again in ~{retry_after}s."
        )
    raise ApplicationHandlerStop

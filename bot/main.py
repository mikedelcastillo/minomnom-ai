import asyncio
import logging
import re
import signal
from urllib.parse import urlparse

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

import config
from config import BOT_TOKEN
import db
from handlers.meal import (
    CLARIFYING,
    cancel_handler,
    clarification_button_callback,
    clarification_handler,
    delete_meal_callback,
    meal_handler,
)
from handlers.stats import (
    today_handler,
    week_handler,
    history_handler,
    delete_handler,
    undo_handler,
    help_handler,
)
from rate_limit import throttle_incoming

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)


async def post_init(application: Application) -> None:
    await db.init_db()


async def on_error(update: object, context) -> None:
    log = logging.getLogger(__name__)
    log.error("Unhandled handler error", exc_info=context.error)
    # Clear stale clarification state so a failed CLARIFYING flow doesn't
    # poison the user's next message by routing it back into the conversation.
    user_data = getattr(context, "user_data", None)
    if isinstance(user_data, dict):
        user_data.pop("pending", None)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text("Something went wrong. Try again!")
        except Exception:
            log.exception("Failed to send error reply to user")


# Telegram's allowed character set for secret_token (see https://core.telegram.org/bots/api#setwebhook)
_SECRET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _validate_webhook_config() -> None:
    if not config.WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL must be set when USE_WEBHOOK=true")
    parsed = urlparse(config.WEBHOOK_URL)
    if parsed.scheme.lower() != "https":
        raise ValueError(
            f"WEBHOOK_URL must use the https:// scheme (got {config.WEBHOOK_URL!r}); "
            "Telegram requires HTTPS for webhooks"
        )
    if not parsed.netloc:
        raise ValueError(f"WEBHOOK_URL must include a host (got {config.WEBHOOK_URL!r})")
    if parsed.username or parsed.password:
        raise ValueError(
            "WEBHOOK_URL must not embed credentials (user:pass@host); they would leak to logs"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(
            f"WEBHOOK_URL must be a base URL with no path/query/fragment (got {config.WEBHOOK_URL!r}); "
            "the bot appends /telegram itself"
        )
    if config.WEBHOOK_SECRET and not _SECRET_TOKEN_RE.match(config.WEBHOOK_SECRET):
        raise ValueError(
            "WEBHOOK_SECRET must be 1-256 chars from [A-Za-z0-9_-] "
            "(Telegram's setWebhook secret_token rules)"
        )


async def run_webhook(app: Application) -> None:
    _validate_webhook_config()
    log = logging.getLogger(__name__)
    if not config.WEBHOOK_SECRET:
        log.warning(
            "WEBHOOK_SECRET is empty in webhook mode. "
            "set_webhook will clear any pre-existing Telegram-side secret, "
            "and incoming /telegram POSTs will be accepted without authentication. "
            "Set WEBHOOK_SECRET to enable spoofing protection."
        )

    await app.initialize()
    await app.start()

    pending_updates: set[asyncio.Task] = set()

    def _on_update_task_done(task: asyncio.Task) -> None:
        pending_updates.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("Unhandled exception in process_update task", exc_info=exc)

    async def telegram_handler(request: web.Request) -> web.Response:
        if config.WEBHOOK_SECRET:
            token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if token != config.WEBHOOK_SECRET:
                log.warning(
                    "Rejected /telegram POST from %s: bad or missing secret_token",
                    request.remote,
                )
                return web.Response(status=403)
        try:
            data = await request.json()
        except Exception:
            log.warning("Rejected /telegram POST from %s: invalid JSON body", request.remote)
            return web.Response(status=400)
        update = Update.de_json(data, app.bot)
        if update is None:
            log.debug("Ignored /telegram POST from %s: payload not recognised as Update", request.remote)
            return web.Response()
        log.info("Received Telegram update %s", update.update_id)
        # Ack Telegram immediately so it doesn't retry while the LLM pipeline runs.
        task = asyncio.create_task(app.process_update(update))
        pending_updates.add(task)
        task.add_done_callback(_on_update_task_done)
        return web.Response()

    async def health(request: web.Request) -> web.Response:
        log.debug("Health check from %s", request.remote)
        return web.Response(text="ok")

    aio_app = web.Application()
    aio_app.router.add_post("/telegram", telegram_handler)
    aio_app.router.add_get("/health", health)

    runner = web.AppRunner(aio_app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        await site.start()

        webhook_url = f"{config.WEBHOOK_URL}/telegram"
        try:
            await app.bot.set_webhook(
                url=webhook_url,
                secret_token=config.WEBHOOK_SECRET or None,
            )
        except Exception:
            log.exception("Failed to register Telegram webhook at %s", webhook_url)
            raise
        log.info(
            "Registered Telegram webhook at %s (secret_token=%s)",
            webhook_url,
            "set" if config.WEBHOOK_SECRET else "NONE — endpoint accepts unauthenticated POSTs",
        )

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows ProactorEventLoop does not support add_signal_handler

        await stop_event.wait()
    finally:
        # Drain in-flight tasks BEFORE shutting down the Application so
        # handlers can still reach app.bot to deliver replies. Cancel any
        # task that overruns the deadline and gather them so the loop
        # doesn't close with pending work.
        if pending_updates:
            log.info("Waiting up to 10s for %d in-flight update(s)", len(pending_updates))
            done, still_pending = await asyncio.wait(pending_updates, timeout=10)
            if still_pending:
                log.warning("Cancelling %d update task(s) that overran the drain deadline", len(still_pending))
                for t in still_pending:
                    t.cancel()
                await asyncio.gather(*still_pending, return_exceptions=True)
        try:
            await app.stop()
            await app.shutdown()
        finally:
            await runner.cleanup()


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    meal_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, meal_handler)],
        states={
            CLARIFYING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, clarification_handler),
                CallbackQueryHandler(clarification_button_callback, pattern=r"^clarify:.+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )

    app.add_handler(TypeHandler(Update, throttle_incoming), group=-1)
    app.add_handler(CallbackQueryHandler(delete_meal_callback, pattern=r"^del:\d+$"))
    app.add_handler(meal_conv)
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("today", today_handler))
    app.add_handler(CommandHandler("week", week_handler))
    app.add_handler(CommandHandler("history", history_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("undo", undo_handler))
    app.add_handler(CommandHandler("help", help_handler))

    if config.USE_WEBHOOK:
        asyncio.run(run_webhook(app))
    else:
        app.run_polling()


if __name__ == "__main__":
    main()

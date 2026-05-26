import asyncio
import logging
import signal

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)


async def post_init(application: Application) -> None:
    await db.init_db()


async def run_webhook(app: Application) -> None:
    if not config.WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL must be set when USE_WEBHOOK=true")
    if not config.WEBHOOK_URL.startswith("https://"):
        raise ValueError(
            f"WEBHOOK_URL must start with https:// (got {config.WEBHOOK_URL!r}); "
            "Telegram requires HTTPS for webhooks"
        )

    await app.initialize()
    await app.start()

    async def telegram_handler(request: web.Request) -> web.Response:
        if config.WEBHOOK_SECRET:
            token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if token != config.WEBHOOK_SECRET:
                return web.Response(status=403)
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400)
        update = Update.de_json(data, app.bot)
        if update is None:
            return web.Response()
        await app.process_update(update)
        return web.Response()

    async def health(_request: web.Request) -> web.Response:
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
        logging.getLogger(__name__).info("Registering Telegram webhook: %s", webhook_url)
        await app.bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET or None,
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

    app.add_handler(CallbackQueryHandler(delete_meal_callback, pattern=r"^del:\d+$"))
    app.add_handler(meal_conv)
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

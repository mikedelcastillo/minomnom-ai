import asyncio
import logging

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


def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
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
        from aiohttp import web

        async def health(_request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            webhook_url=f"{config.WEBHOOK_URL}/telegram",
            url_path="/telegram",
            custom_routes=[web.get("/health", health)],
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()

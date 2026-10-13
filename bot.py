"""RK Gamers Bot — entry point.

Render (web service) mode: webhook + /healthz keep-alive endpoint.
Local dev mode (no RENDER_EXTERNAL_URL): long polling.
"""
import logging

from telegram import BotCommand
from telegram.ext import Application

import handlers
from config import (
    ALLOWED_UPDATES,
    BOT_TOKEN,
    DB_NAME,
    OWNER_ID,
    PORT,
    WEBHOOK_SECRET,
    WEBHOOK_URL,
    validate,
)
from database.db import init_db

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pymongo").setLevel(logging.WARNING)
log = logging.getLogger("rk-gamers-bot")


async def post_init(app):
    problems = validate()
    if problems:
        log.error("CONFIG PROBLEMS: %s", ", ".join(problems))
    await init_db()
    me = await app.bot.get_me()
    log.info("Bot started: @%s (id=%s) | db=%s | mode=%s",
             me.username, me.id, DB_NAME, "webhook" if WEBHOOK_URL else "polling")
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "🏠 Main Menu"),
            BotCommand("profile", "👤 My Profile"),
            BotCommand("help", "ℹ️ Help"),
            BotCommand("admin", "🛡️ Admin Panel"),
            BotCommand("cancel", "❌ Cancel"),
        ])
    except Exception:
        log.warning("set_my_commands failed")


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    handlers.register(app)

    async def on_error(update, context):
        log.error("Update error: %s", context.error, exc_info=context.error)

    app.add_error_handler(on_error)
    return app


def main():
    app = build_app()
    if WEBHOOK_URL:
        url = WEBHOOK_URL.rstrip("/")
        log.info("Webhook mode: %s/%s port=%s", url, WEBHOOK_SECRET, PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_SECRET,
            webhook_url=f"{url}/{WEBHOOK_SECRET}",
            allowed_updates=ALLOWED_UPDATES,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
            health_check_path="/healthz",
        )
    else:
        log.info("Polling mode (local dev)")
        app.run_polling(allowed_updates=ALLOWED_UPDATES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

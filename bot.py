"""RK Gamers Bot — entry point.

Render (web service) mode: custom aiohttp server serves
  - POST /<WEBHOOK_SECRET>  -> Telegram webhook (processed by PTB)
  - GET  /healthz           -> keep-alive health check (UptimeRobot friendly)
  - GET  /                  -> friendly status page
Local dev mode (no RENDER_EXTERNAL_URL): long polling.
"""
import asyncio
import logging
import signal
import traceback

from aiohttp import web
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application

import handlers
from config import (
    ADMINS,
    ALLOWED_UPDATES,
    BOT_TOKEN,
    DB_NAME,
    PORT,
    WEBHOOK_SECRET,
    WEBHOOK_URL,
    validate,
)
from database.db import init_db
from utils.helpers import esc

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
    log.info("Bot started: @%s (id=%s) | db=%s", me.username, me.id, DB_NAME)
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
    return me


def build_app() -> Application:
    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    if WEBHOOK_URL:
        builder = builder.updater(None)  # custom webhook server (see below)
    app = builder.build()
    handlers.register(app)

    async def on_error(update, context):
        log.error("Update error: %s", context.error, exc_info=context.error)
        # DM the traceback to admins -> errors visible in Telegram, no Render logs needed
        try:
            cb = getattr(update, "callback_query", None)
            msg = getattr(update, "effective_message", None)
            if cb is not None and getattr(cb, "data", None):
                src = f"tap: {cb.data}"
            elif msg is not None and getattr(msg, "text", None):
                src = f"msg: {msg.text[:80]}"
            else:
                src = "?"
            tb = "".join(traceback.format_exception(
                type(context.error), context.error, context.error.__traceback__))
            tail = esc(tb[-1800:])
            for adm in ADMINS:
                try:
                    await context.bot.send_message(
                        adm,
                        f"⚠️ <b>Bot Error</b>\nInput: <code>{esc(src)}</code>\n\n<pre>{tail}</pre>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    app.add_error_handler(on_error)
    return app


# ------------------------------------------------------------------ polling (local dev)
def run_polling(app):
    log.info("Polling mode (local dev)")
    app.run_polling(allowed_updates=ALLOWED_UPDATES, drop_pending_updates=True)


# ------------------------------------------------------------------ webhook (Render)
async def run_webhook(app):
    me = await app.initialize()
    await post_init(app)

    url = WEBHOOK_URL.rstrip("/")
    await app.bot.set_webhook(
        url=f"{url}/{WEBHOOK_SECRET}",
        allowed_updates=ALLOWED_UPDATES,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=False,  # deliver updates queued while the service was down
    )
    log.info("Webhook set: %s/%s", url, WEBHOOK_SECRET)
    await app.start()

    async def health(_request):
        return web.Response(text="OK")

    async def status_page(_request):
        return web.Response(
            text="🤖 RK Gamers Bot is running! | healthz: /healthz",
            content_type="text/plain",
        )

    async def hook(request):
        if request.match_info.get("secret") != WEBHOOK_SECRET:
            return web.Response(status=403, text="forbidden")
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return web.Response(status=403, text="bad secret token")
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")
        try:
            update = Update.de_json(data, app.bot)
        except Exception:
            log.exception("malformed update could not be parsed — skipped")
            return web.Response(text="OK")  # never 500: Telegram would retry forever
        if update is not None:
            try:
                await app.process_update(update)
            except Exception:
                log.exception("process_update failed")
        return web.Response(text="OK")

    webapp = web.Application()
    webapp.router.add_get("/healthz", health)
    webapp.router.add_get("/", status_page)
    # VARIABLE route so request.match_info["secret"] works (static route => match_info empty => 403 bug)
    webapp.router.add_post("/{secret}", hook)

    runner = web.AppRunner(webapp, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server listening on 0.0.0.0:%s (healthz + webhook)", PORT)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
        await app.stop()
        await app.shutdown()


def main():
    app = build_app()
    if WEBHOOK_URL:
        asyncio.run(run_webhook(app))
    else:
        run_polling(app)


if __name__ == "__main__":
    main()

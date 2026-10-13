import os

try:  # local dev: load .env if python-dotenv available (real env vars keep precedence)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
MONGO_URI = os.environ.get("MONGO_URI", "").strip()
DB_NAME = os.environ.get("DB_NAME", "rk_gamers_bot").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "rkgamersbot").lstrip("@")

# Owner is always admin. Extra admins can be passed via ADMINS env (comma separated ids)
ADMINS = set()
if OWNER_ID:
    ADMINS.add(OWNER_ID)
for _x in os.environ.get("ADMINS", "").replace(" ", "").split(","):
    if _x:
        try:
            ADMINS.add(int(_x))
        except ValueError:
            pass

# Render injects RENDER_EXTERNAL_URL automatically. If missing (local dev), bot runs in polling mode.
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "rk-secret-path").strip()

ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "chat_member", "my_chat_member"]


def validate():
    """Return list of config problems (empty = OK)."""
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN missing")
    if not MONGO_URI:
        problems.append("MONGO_URI missing")
    if not OWNER_ID:
        problems.append("OWNER_ID missing")
    return problems

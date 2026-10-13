"""End-to-end local test: compile, DB CRUD, referral join/leave simulation, /start flow."""
import asyncio
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "✅", "❌"
results = []


def check(name, cond, extra=""):
    results.append((PASS if cond else FAIL, name, extra))
    print(f"{PASS if cond else FAIL} {name} {extra}")
    return cond


async def main():
    # 1) compile every module
    import py_compile
    import glob
    ok = True
    for f in glob.glob(os.path.join(os.path.dirname(__file__), "..", "**", "*.py"), recursive=True):
        if "venv" in f:
            continue
        try:
            py_compile.compile(f, doraise=True)
        except Exception as e:
            ok = False
            print("COMPILE FAIL:", f, e)
    check("compile all modules", ok)

    from config import BOT_TOKEN, MONGO_URI, OWNER_ID
    check("config loaded", bool(BOT_TOKEN and MONGO_URI and OWNER_ID),
          f"(owner={OWNER_ID}, mongo={'set' if MONGO_URI else 'MISSING'})")

    # 2) DB layer
    from database.db import init_db, db, create_button, get_button_by_no, delete_button, get_settings, get_join
    await init_db()
    s = await get_settings()
    check("mongo connected + settings", s.get("_id") == "global", str({k: s[k] for k in ('strict', 'force_sub_on')}))

    btn = await create_button("TEST KEY BUTTON")
    got = await get_button_by_no(btn["no"])
    check("button CRUD", got is not None and got["name"] == "TEST KEY BUTTON", f"no={btn['no']}")
    ms = got.get("milestones", {})
    check("default milestone-1 = ALL content", ms.get("1", {}).get("content") == "all")

    # 3) bot app + real Telegram API
    from bot import build_app
    from telegram import Chat, Message, Update, User

    app = build_app()
    await app.initialize()
    me = await app.bot.get_me()
    check("telegram getMe", me.username == "rkgamersbot", f"@{me.username}")

    ctx = SimpleNamespace(bot=app.bot, user_data={})

    # simulate: owner invites fake user via deep link -> fake user joins -> reward flow
    fake = User(900000001, "TestRefUser", is_bot=False, username="testrefuser")

    # 4) /start from owner (real message may go to owner's chat if he started the bot)
    now = datetime.now(timezone.utc)
    upd = Update(update_id=1, message=Message(1, now, Chat(OWNER_ID, Chat.PRIVATE), from_user=User(OWNER_ID, "RKOwner", is_bot=False), text="/start"))
    await app.process_update(upd)
    udoc = await db.users.find_one({"_id": OWNER_ID})
    check("/start handler ran (owner saved in DB)", udoc is not None)

    # 5) referral join simulation via process_join
    from handlers.user import process_join, process_leave, complete_join
    await db.buttons.update_one({"_id": btn["_id"]}, {"$set": {"channel_id": -100999}})
    got = await get_button_by_no(btn["no"])
    await db.invites.update_one(
        {"invitee": fake.id, "inviter": OWNER_ID, "button_no": btn["no"]},
        {"$set": {"channel_id": -100999, "status": "pending", "created_at": now}}, upsert=True)
    await complete_join(ctx, fake, OWNER_ID, got)
    j = await get_join(OWNER_ID, fake.id, btn["no"])
    check("referral join recorded", j is not None and j["active"] is True)
    check("button stats.joins incremented", (await get_button_by_no(btn["no"]))["stats"]["joins"] == 1)
    check("owner refs_count incremented", (await db.users.find_one({"_id": OWNER_ID})).get("refs_count", 0) >= 1)

    # duplicate protection: second join attempt ignored
    dup = await complete_join(ctx, fake, OWNER_ID, got)
    check("duplicate join blocked", dup is False)

    # 6) leave -> count deactivated
    await process_leave(ctx, -100999, fake.id)
    j2 = await get_join(OWNER_ID, fake.id, btn["no"])
    check("leave deactivates join", j2 is not None and j2["active"] is False)

    # 7) strict anti-fraud helper values
    from database.db import get_settings
    s2 = await get_settings()
    check("strict mode default ON", s2.get("strict", True) is True)

    # 8) cleanup test data
    await delete_button(btn["_id"])
    await db.joins.delete_many({"invitee": fake.id})
    await db.invites.delete_many({"invitee": fake.id})
    await db.users.delete_one({"_id": fake.id})
    await db.milestones.delete_many({"user": OWNER_ID, "button_no": btn["no"]})
    await db.users.update_one({"_id": OWNER_ID}, {"$set": {"refs_count": 0}})
    check("test data cleaned", (await get_button_by_no(btn["no"])) is None)

    await app.shutdown()

    print("\n" + "=" * 50)
    failed = [r for r in results if r[0] == FAIL]
    print(f"RESULT: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

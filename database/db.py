"""MongoDB (motor/async) data layer for RK Gamers Bot."""
import datetime

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from config import MONGO_URI, DB_NAME


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


class _DB:
    """Lazy singleton holding collections."""
    def __init__(self):
        self.client = None
        self.db = None
        self.users = None
        self.channels = None
        self.buttons = None
        self.invites = None
        self.joins = None
        self.milestones = None
        self.settings = None
        self.counters = None

    def connect(self):
        if self.client is None:
            self.client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10000)
            self.db = self.client[DB_NAME]
            self.users = self.db.users
            self.channels = self.db.channels
            self.buttons = self.db.buttons
            self.invites = self.db.invites
            self.joins = self.db.joins
            self.milestones = self.db.milestones
            self.settings = self.db.settings
            self.counters = self.db.counters
        return self


db = _DB()

DEFAULT_SETTINGS = {
    "_id": "global",
    "force_sub": [],          # list of channel_ids user must join before using bot
    "force_sub_on": False,
    "strict": True,           # anti-fraud: one invitee counts only once per button (globally)
    "newuser_alert": True,    # notify owner about new users
}


async def init_db():
    db.connect()
    await db.users.create_index("created_at")
    await db.buttons.create_index("no", unique=True)
    await db.channels.create_index("channel_id", unique=True)
    await db.joins.create_index([("inviter", 1), ("button_no", 1), ("invitee", 1)], unique=True)
    await db.joins.create_index([("invitee", 1), ("channel_id", 1), ("active", 1)])
    await db.invites.create_index([("invitee", 1), ("status", 1)])
    await db.milestones.create_index([("user", 1), ("button_no", 1), ("n", 1)], unique=True)


# ---------------------------------------------------------------- settings
async def get_settings():
    s = await db.settings.find_one({"_id": "global"})
    if not s:
        await db.settings.update_one({"_id": "global"}, {"$setOnInsert": DEFAULT_SETTINGS}, upsert=True)
        return dict(DEFAULT_SETTINGS)
    return s


async def upd_settings(**kw):
    await db.settings.update_one({"_id": "global"}, {"$set": kw}, upsert=True)


# ---------------------------------------------------------------- users
async def upsert_user(tgu):
    """Insert or refresh a user from a telegram.User. Returns (doc, created)."""
    now = utcnow()
    doc = await db.users.find_one({"_id": tgu.id})
    if doc:
        patch = {"first_name": tgu.first_name or "User", "username": tgu.username or "", "last_seen": now}
        await db.users.update_one({"_id": tgu.id}, {"$set": patch})
        doc.update(patch)
        return doc, False
    doc = {
        "_id": tgu.id,
        "first_name": tgu.first_name or "User",
        "username": tgu.username or "",
        "banned": False,
        "created_at": now,
        "last_seen": now,
        "refs_count": 0,   # total successful invites across all buttons
        "rewards": 0,      # milestone rewards claimed
    }
    await db.users.insert_one(doc)
    return doc, True


async def get_user(uid):
    return await db.users.find_one({"_id": uid})


# ---------------------------------------------------------------- counters
async def next_seq(name):
    doc = await db.counters.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return doc["seq"]


# ---------------------------------------------------------------- channels
async def upsert_channel(chat, verified):
    """chat: telegram.Chat"""
    data = {
        "channel_id": chat.id,
        "title": chat.title or str(chat.id),
        "username": chat.username or "",
        "type": str(chat.type),
        "verified": bool(verified),
        "updated_at": utcnow(),
    }
    doc = await db.channels.find_one({"channel_id": chat.id})
    if doc:
        await db.channels.update_one({"_id": doc["_id"]}, {"$set": data})
        data["_id"] = doc["_id"]
        data["invite_link"] = doc.get("invite_link", "")
    else:
        data["created_at"] = utcnow()
        data["invite_link"] = ""
        res = await db.channels.insert_one(data)
        data["_id"] = res.inserted_id
    return data


async def get_channel(channel_id):
    return await db.channels.find_one({"channel_id": channel_id})


async def all_channels():
    return await db.channels.find().sort("created_at", 1).to_list(length=None)


async def verified_channels():
    return await db.channels.find({"verified": True}).sort("created_at", 1).to_list(length=None)


async def delete_channel(channel_id):
    await db.channels.delete_one({"channel_id": channel_id})
    # remove from force-sub list too
    await upd_settings(force_sub=[c for c in (await get_settings()).get("force_sub", []) if c != channel_id])


# ---------------------------------------------------------------- buttons (reward actions)
async def create_button(name, channel_id=None):
    no = await next_seq("button")
    doc = {
        "no": no,
        "name": name,
        "channel_id": channel_id,
        "content": [],            # list of items: text or media (file_id + msg ref)
        "notebook": "",           # owner-editable description shown to users
        "milestones": {"1": {"content": "all", "text": ""}},
        "direct": False,          # True = deliver content without referral
        "active": True,
        "stats": {"joins": 0, "delivered": 0},
        "created_at": utcnow(),
    }
    res = await db.buttons.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_button(bid):
    return await db.buttons.find_one({"_id": bid})


async def get_button_by_no(no):
    try:
        return await db.buttons.find_one({"no": int(no)})
    except (TypeError, ValueError):
        return None


async def all_buttons():
    return await db.buttons.find().sort("no", 1).to_list(length=None)


async def active_buttons():
    return await db.buttons.find({"active": True}).sort("no", 1).to_list(length=None)


async def save_button_content(bid, items):
    await db.buttons.update_one({"_id": bid}, {"$set": {"content": items}})


async def delete_button(bid):
    b = await db.buttons.find_one({"_id": bid})
    if b:
        await db.buttons.delete_one({"_id": bid})
        await db.milestones.delete_many({"button_no": b["no"]})
        await db.invites.delete_many({"button_no": b["no"]})
    return b


# ---------------------------------------------------------------- invites (pending referrals)
async def upsert_invite(invitee_id, inviter_id, button_no, channel_id):
    await db.invites.update_one(
        {"invitee": invitee_id, "inviter": inviter_id, "button_no": button_no},
        {"$set": {"channel_id": channel_id, "status": "pending", "created_at": utcnow()}},
        upsert=True,
    )


async def mark_invites_done(invitee_id, inviter_id, button_no):
    await db.invites.update_many(
        {"invitee": invitee_id, "inviter": inviter_id, "button_no": button_no},
        {"$set": {"status": "done", "done_at": utcnow()}},
    )


# ---------------------------------------------------------------- joins (completed, anti-fraud ledger)
async def get_join(inviter_id, invitee_id, button_no):
    return await db.joins.find_one({"inviter": inviter_id, "invitee": invitee_id, "button_no": button_no})


async def count_joins(inviter_id, button_no):
    return await db.joins.count_documents({"inviter": inviter_id, "button_no": button_no, "active": True})


async def prior_channel_join(invitee_id, channel_id, exclude_button_no):
    """ANTI-FRAUD: is user+channel ka koi bhi join record (active ho ya leave ke
    baad inactive), kisi DUSRE button pe. Ek user ek channel me sirf ek baar
    count hota hai — leave karke dobara join (kisi bhi button/key se) dobara
    credit nahi dega. Same-button rejoin ka reactivate behavior alag se intact
    rehta hai (wo existing record pe hota hai, naya record nahi banta)."""
    if not channel_id:
        return None
    return await db.joins.find_one({
        "invitee": invitee_id,
        "channel_id": channel_id,
        "button_no": {"$ne": exclude_button_no},
    })


# ---------------------------------------------------------------- milestones (delivered rewards ledger)
async def milestone_delivered(user_id, button_no, n):
    return await db.milestones.find_one({"user": user_id, "button_no": button_no, "n": n})


async def mark_milestone(user_id, button_no, n):
    await db.milestones.insert_one({"user": user_id, "button_no": button_no, "n": n, "delivered_at": utcnow()})
    await db.buttons.update_one({"no": button_no}, {"$inc": {"stats.delivered": 1}})
    await db.users.update_one({"_id": user_id}, {"$inc": {"rewards": 1}})

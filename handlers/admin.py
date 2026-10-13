"""Admin panel: stats, broadcast, user management, settings (force-sub, anti-fraud)."""
import asyncio
import logging

from telegram.ext import ContextTypes

from config import OWNER_ID
from database import db
from database.db import (
    all_channels,
    all_buttons,
    get_channel,
    get_settings,
    get_user,
    upd_settings,
    upsert_user,
)
from utils.helpers import esc, is_admin, item_label, render, send_content_item, send_safe
from utils.keyboards import back_adm, cb, confirm_kb, kb, nav_row, paginate, url

log = logging.getLogger(__name__)


def admin_check(func):
    async def wrapper(update, context):
        u = update.effective_user
        if not u or not is_admin(u.id):
            if update.callback_query:
                await update.callback_query.answer("⛔ Admin only!", show_alert=True)
            return
        return await func(update, context)
    return wrapper


# ------------------------------------------------------------------ panel & stats
@admin_check
async def cmd_admin(update, context):
    await render_panel(update, context)


async def render_panel(update, context):
    users = await db.users.count_documents({})
    btns = await db.buttons.count_documents({})
    chans = await db.channels.count_documents({})
    text = (
        "🛡️ <b>RK GAMERS — Admin Panel</b>\n\n"
        f"👥 Users: <b>{users}</b> | 🔘 Buttons: <b>{btns}</b> | 📺 Channels: <b>{chans}</b>\n\n"
        "👇 Kya karna hai select karo:"
    )
    rows = [
        [cb("📊 Stats", "adst"), cb("📢 Broadcast", "adbc")],
        [cb("🔘 Manage Buttons", "adbt_0"), cb("📺 Manage Channels", "adch_0")],
        [cb("👥 Users", "adbu_0"), cb("⚙️ Settings", "adset")],
        [cb("🏠 User Menu", "home")],
    ]
    await render(update, context, text, kb(rows))


@admin_check
async def cb_panel(update, context):
    await update.callback_query.answer()
    await render_panel(update, context)


@admin_check
async def cb_stats(update, context):
    await update.callback_query.answer()
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    day_ago = now - datetime.timedelta(days=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total = await db.users.count_documents({})
    today = await db.users.count_documents({"created_at": {"$gte": day_start}})
    banned = await db.users.count_documents({"banned": True})
    chans = await db.channels.count_documents({})
    verified = await db.channels.count_documents({"verified": True})
    btns = await db.buttons.count_documents({})
    active_btns = await db.buttons.count_documents({"active": True})
    joins = await db.joins.count_documents({})
    joins24 = await db.joins.count_documents({"counted_at": {"$gte": day_ago}})
    pending = await db.invites.count_documents({"status": "pending"})
    delivered = await db.milestones.count_documents({})

    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{total}</b> (aaj: +{today})\n"
        f"🚫 Banned: {banned}\n\n"
        f"🔘 Reward Buttons: <b>{btns}</b> (active: {active_btns})\n"
        f"📺 Channels: <b>{chans}</b> (✅ verified: {verified})\n\n"
        f"🔗 Total Joins: <b>{joins}</b> (24h: {joins24})\n"
        f"⏳ Pending Invites: {pending}\n"
        f"🎁 Rewards Delivered: <b>{delivered}</b>"
    )
    await render(update, context, text, back_adm())


# ------------------------------------------------------------------ broadcast
@admin_check
async def cb_broadcast(update, context):
    await update.callback_query.answer()
    context.user_data["st"] = ("bc",)
    await render(
        update, context,
        "📢 <b>Broadcast</b>\n\nJo bhi bhejna hai (text / photo / video / file / kuch bhi) — "
        "ab bhejo. Sab users ko chalega.\n\n❌ Cancel karne ke liye niche button hai.",
        kb([[cb("❌ Cancel", "cancel")]]),
    )


@admin_check
async def cb_bc_ok(update, context):
    await update.callback_query.answer()
    item = context.user_data.pop("bc_item", None)
    if not item:
        return await render(update, context, "❌ Koi broadcast content nahi mila.", back_adm())

    total = await db.users.count_documents({"banned": {"$ne": True}})
    status = await send_safe(context.bot, update.effective_chat.id, f"📢 Broadcast start... 0/{total}")
    sent = failed = 0
    cursor = db.users.find({"banned": {"$ne": True}}, {"_id": 1})
    async for u in cursor:
        ok = await send_content_item(context.bot, u["_id"], item)
        if ok:
            sent += 1
        else:
            failed += 1
        done = sent + failed
        if done % 25 == 0 and status:
            try:
                await status.edit_text(f"📢 Broadcasting... {done}/{total} (✅ {sent} | ❌ {failed})")
            except Exception:
                pass
        await asyncio.sleep(0.05)  # ~20 msg/sec, Telegram-safe
    summary = f"✅ <b>Broadcast Complete!</b>\n\n👥 Sent: <b>{sent}</b>\n❌ Failed: {failed}\n📊 Total: {total}"
    if status:
        try:
            await status.edit_text(summary)
        except Exception:
            await send_safe(context.bot, update.effective_chat.id, summary)
    else:
        await send_safe(context.bot, update.effective_chat.id, summary)


@admin_check
async def cb_bc_no(update, context):
    await update.callback_query.answer()
    context.user_data.pop("bc_item", None)
    context.user_data.pop("st", None)
    await render(update, context, "❌ Broadcast cancel ho gaya.", back_adm())


# ------------------------------------------------------------------ user management
@admin_check
async def cb_users(update, context):
    await update.callback_query.answer()
    page = int(context.matches[0].group(1))
    await render_user_list(update, context, page)


async def render_user_list(update, context, page=0):
    users = await db.users.find().sort("created_at", -1).to_list(length=None)
    chunk, page, pages = paginate(users, page, 10)
    text = f"👥 <b>All Users</b> ({len(users)} total)"
    rows = []
    for u in chunk:
        flag = "🚫" if u.get("banned") else "🟢"
        rows.append([cb(f"{flag} {u.get('first_name', '?')[:18]} | {u['_id']}", f"adus_{u['_id']}_{page}")])
    rows += nav_row(page, pages, "adbu")
    rows.append([cb("🔍 Search by ID", "usearch")])
    rows.append([cb("⬅️ Back", "adm")])
    await render(update, context, text, kb(rows))


@admin_check
async def cb_user_view(update, context):
    await update.callback_query.answer()
    m = context.matches[0]
    uid, page = int(m.group(1)), int(m.group(2))
    await render_user_view(update, context, uid, page)


async def render_user_view(update, context, uid, page=0):
    u = await get_user(uid)
    if not u:
        return await render(update, context, f"❌ User <code>{uid}</code> nahi mila.", back_adm())
    joins = await db.joins.count_documents({"inviter": uid})
    created = u.get("created_at")
    joined = created.strftime("%d %b %Y %H:%M") if created else "—"
    text = (
        f"👤 <b>{esc(u.get('first_name'))}</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"🔗 Username: {'@' + u['username'] if u.get('username') else '—'}\n"
        f"📅 Joined: {joined}\n\n"
        f"🔗 Total Joins (referrals): <b>{joins}</b>\n"
        f"🎁 Rewards: {u.get('rewards', 0)}\n"
        f"Status: {'🚫 Banned' if u.get('banned') else '🟢 Active'}"
    )
    rows = []
    if uid != OWNER_ID and not is_admin(uid):
        if u.get("banned"):
            rows.append([cb("✅ Unban", f"adub_{uid}_{page}")])
        else:
            rows.append([cb("🚫 Ban User", f"adbn_{uid}_{page}")])
    rows.append([cb("⬅️ Back", f"adbu_{page}")])
    await render(update, context, text, kb(rows))


@admin_check
async def cb_search(update, context):
    await update.callback_query.answer()
    context.user_data["st"] = ("u_search",)
    await render(
        update, context,
        "🔍 <b>User ID bhejo</b> (jaise: <code>8967004608</code>):",
        kb([[cb("❌ Cancel", "cancel")]]),
    )


@admin_check
async def cb_ban(update, context):
    await update.callback_query.answer()
    m = context.matches[0]
    uid, page = int(m.group(1)), int(m.group(2))
    if is_admin(uid):
        return await render(update, context, "⛔ Admin ko ban nahi kar sakte!", back_adm())
    await db.users.update_one({"_id": uid}, {"$set": {"banned": True}})
    await render_user_view(update, context, uid, page)


@admin_check
async def cb_unban(update, context):
    await update.callback_query.answer()
    m = context.matches[0]
    uid, page = int(m.group(1)), int(m.group(2))
    await db.users.update_one({"_id": uid}, {"$set": {"banned": False}})
    await render_user_view(update, context, uid, page)


# ------------------------------------------------------------------ settings
async def render_settings(update, context):
    s = await get_settings()
    fs = s.get("force_sub", [])
    chans = []
    for cid in fs:
        ch = await get_channel(cid)
        if ch:
            chans.append(ch["title"])
    text = (
        "⚙️ <b>Settings</b>\n\n"
        f"📢 Force Join: <b>{'ON' if s.get('force_sub_on') else 'OFF'}</b>"
        f" ({len(fs)} channels{' — ' + ', '.join(esc(c[:14]) for c in chans[:3]) if chans else ''})\n"
        f"🔒 Strict Anti-Fraud: <b>{'ON' if s.get('strict', True) else 'OFF'}</b>\n"
        f"🆕 New User Alert: <b>{'ON' if s.get('newuser_alert', True) else 'OFF'}</b>\n\n"
        "ℹ️ <b>Force Join ON</b> = bot use karne se pehle users ko channel join karna padega.\n"
        "ℹ️ <b>Strict ON</b> = ek user ek reward button me sirf EK baar kisi ke liye count hoga."
    )
    rows = [
        [cb(f"📢 Force Join: {'ON ✅' if s.get('force_sub_on') else 'OFF ❌'}", "adfson")],
        [cb("📢 Force Sub Channels", "fsadd")],
        [cb(f"🔒 Strict: {'ON ✅' if s.get('strict', True) else 'OFF ❌'}", "adstrict")],
        [cb(f"🆕 Alert: {'ON ✅' if s.get('newuser_alert', True) else 'OFF ❌'}", "adalert")],
        [cb("⬅️ Back", "adm")],
    ]
    await render(update, context, text, kb(rows))


@admin_check
async def cb_settings(update, context):
    await update.callback_query.answer()
    context.user_data.pop("st", None)
    await render_settings(update, context)


@admin_check
async def cb_tgl_fs(update, context):
    await update.callback_query.answer()
    s = await get_settings()
    if s.get("force_sub_on") and not s.get("force_sub"):
        await update.callback_query.answer("⚠️ Pehle channels add karo!", show_alert=True)
        return
    await upd_settings(force_sub_on=not s.get("force_sub_on"))
    await render_settings(update, context)


@admin_check
async def cb_tgl_strict(update, context):
    await update.callback_query.answer()
    s = await get_settings()
    await upd_settings(strict=not s.get("strict", True))
    await render_settings(update, context)


@admin_check
async def cb_tgl_alert(update, context):
    await update.callback_query.answer()
    s = await get_settings()
    await upd_settings(newuser_alert=not s.get("newuser_alert", True))
    await render_settings(update, context)


@admin_check
async def cb_fs_pick(update, context):
    await update.callback_query.answer()
    s = await get_settings()
    chans = await all_channels()
    rows = []
    for ch in chans:
        mark = "✅" if ch["channel_id"] in s.get("force_sub", []) else "⬜"
        v = "✔️" if ch.get("verified") else "❌"
        rows.append([cb(f"{mark} {ch['title'][:22]} {v}", f"fstgl_{ch['channel_id']}")])
    if not chans:
        rows.append([cb("➕ Pehle Channel Add Karo", "chnew")])
    rows.append([cb("✅ Done", "fsdone")])
    await render(update, context, "📢 <b>Force Sub Channels</b> — tap karke select karo:", kb(rows))


@admin_check
async def cb_fs_toggle(update, context):
    await update.callback_query.answer()
    cid = int(context.matches[0].group(1))
    s = await get_settings()
    cur = s.get("force_sub", [])
    new = [c for c in cur if c != cid] if cid in cur else cur + [cid]
    await upd_settings(force_sub=new)
    await cb_fs_pick(update, context)


@admin_check
async def cb_fs_done(update, context):
    await update.callback_query.answer()
    await render_settings(update, context)

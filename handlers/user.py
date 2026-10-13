"""User-facing flows: open reward buttons, share links, profile, help, join verification."""
import logging
from urllib.parse import quote

from telegram import InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import BOT_USERNAME
from database import db
from database.db import (
    count_joins,
    get_button_by_no,
    get_channel,
    get_join,
    get_settings,
    get_user,
    mark_invites_done,
    mark_milestone,
    milestone_delivered,
    upsert_user,
)
from utils.helpers import (
    bot_is_member,
    esc,
    is_admin,
    item_label,
    render,
    safe_answer,
    send_content_item,
    send_safe,
    user_link_name,
)
from utils.keyboards import back_home, cb, kb, url

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ reward delivery
async def deliver_reward(context, uid, btn, n, cfg):
    """Deliver a milestone reward to the inviter."""
    header = (
        f"🎉 <b>REWARD UNLOCKED!</b>\n"
        f"🔘 <b>{esc(btn['name'])}</b> — 🎯 <b>{n} join complete!</b>"
    )
    text = (cfg or {}).get("text") or ""
    content = (cfg or {}).get("content") or []
    items = []
    if content:
        all_items = btn.get("content") or []
        if content == "all":
            items = all_items
        else:
            items = [all_items[i] for i in content if isinstance(i, int) and 0 <= i < len(all_items)]
    if not text and not items:
        return
    await send_safe(context.bot, uid, header)
    if text:
        await send_safe(context.bot, uid, f"💌 <b>Aapka Reward:</b>\n\n{text}")
    for it in items:
        await send_content_item(context.bot, uid, it)


async def check_milestones(context, inviter_id, btn, count):
    """Deliver every milestone the inviter has reached but not yet claimed."""
    ms = btn.get("milestones") or {}
    for key in sorted(ms.keys(), key=lambda k: int(k)):
        n = int(key)
        if count >= n and not await milestone_delivered(inviter_id, btn["no"], n):
            await mark_milestone(inviter_id, btn["no"], n)
            await deliver_reward(context, inviter_id, btn, n, ms[key])


async def complete_join(context, invitee_tg, inviter_id, btn) -> bool:
    """Mark a referral join complete, notify inviter, deliver milestone rewards."""
    from database.db import utcnow
    invitee_id = invitee_tg.id
    bno = btn["no"]

    existing = await get_join(inviter_id, invitee_id, bno)
    if existing and existing.get("active"):
        return False  # already counted (race/duplicate)

    reactivated = False
    if existing:
        await db.joins.update_one(
            {"_id": existing["_id"]},
            {"$set": {"active": True, "counted_at": utcnow()}, "$unset": {"left_at": ""}},
        )
        reactivated = True
    else:
        await db.joins.insert_one({
            "inviter": inviter_id, "invitee": invitee_id, "button_no": bno,
            "channel_id": btn.get("channel_id"), "active": True, "counted_at": utcnow(),
        })
        await db.buttons.update_one({"_id": btn["_id"]}, {"$inc": {"stats.joins": 1}})
        await db.users.update_one({"_id": inviter_id}, {"$inc": {"refs_count": 1}})

    await mark_invites_done(invitee_id, inviter_id, bno)

    count = await count_joins(inviter_id, bno)
    ms = btn.get("milestones") or {}
    mx = max((int(k) for k in ms.keys()), default=0)

    await send_safe(
        context.bot, inviter_id,
        f"{'🔁' if reactivated else '🎉'} <b>{user_link_name(invitee_tg)}</b> ne "
        f"<b>{esc(btn['name'])}</b> ke liye channel join kiya!\n"
        f"👥 Aapke total joins: <b>{count}</b>{f' / {mx} required' if mx else ''}",
    )
    await check_milestones(context, inviter_id, btn, count)
    return True


async def process_join(context, channel_id, invitee_tg):
    """Called on chat_member join: complete all pending invites for this channel."""
    invites = await db.invites.find(
        {"invitee": invitee_tg.id, "channel_id": channel_id, "status": "pending"}
    ).to_list(length=None)
    for inv in invites:
        btn = await get_button_by_no(inv["button_no"])
        if btn and btn.get("active"):
            try:
                await complete_join(context, invitee_tg, inv["inviter"], btn)
            except Exception:
                log.exception("complete_join failed")


async def process_leave(context, channel_id, invitee_id):
    """Called on chat_member leave: deactivate counted joins + notify inviters."""
    from database.db import utcnow
    cursor = db.joins.find({"invitee": invitee_id, "channel_id": channel_id, "active": True})
    async for j in cursor:
        await db.joins.update_one({"_id": j["_id"]}, {"$set": {"active": False, "left_at": utcnow()}})
        btn = await get_button_by_no(j["button_no"])
        if btn:
            name = invitee_id
            u = await get_user(invitee_id)
            if u:
                name = u.get("first_name", invitee_id)
            await send_safe(
                context.bot, j["inviter"],
                f"⚠️ <b>{esc(name)}</b> (<code>{invitee_id}</code>) ne "
                f"<b>{esc(btn['name'])}</b> ka channel chhod diya — count -1 ho gaya.",
            )


# ------------------------------------------------------------------ join check (invitee side)
async def cb_join_check(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bno, inviter_id = int(m.group(1)), int(m.group(2))
    tg = q.from_user

    btn = await get_button_by_no(bno)
    invite = await db.invites.find_one(
        {"invitee": tg.id, "inviter": inviter_id, "button_no": bno, "status": "pending"}
    )
    if not btn or not invite:
        return await render(update, context, "❌ Invite nahi mila. Link se dobara start karo.", back_home())

    member = await bot_is_member(context, btn.get("channel_id"), tg.id)
    if member is True:
        ok = await complete_join(context, tg, inviter_id, btn)
        if ok:
            await render(update, context, "✅ <b>Verified!</b> Aap join ho gaye 🎉\nAapke dost ko reward de diya gaya hai!")
        else:
            await render(update, context, "ℹ️ Aap pehle hi counted ho. Kuch naya nahi mila.")
    elif member is False:
        await render(
            update, context,
            "⏳ Abhi tak channel me nahi dikhe!\nPehle channel join karo, fir dobara <b>I've Joined</b> dabao.",
            kb([[cb("🔄 Try Again", f"jchk_{bno}_{inviter_id}")], [cb("🏠 Main Menu", "home")]]),
        )
    else:
        await render(
            update, context,
            "⚠️ Bot is channel pe admin nahi hai — verify nahi kar pa raha.\nOwner se channel verification karwao.",
            back_home(),
        )


# ------------------------------------------------------------------ open reward button
async def cb_open(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bno, page = int(m.group(1)), int(m.group(2))
    tg = q.from_user
    context.user_data.pop("st", None)  # navigation cancels any pending input

    btn = await get_button_by_no(bno)
    if not btn or (not btn.get("active") and not is_admin(tg.id)):
        return await render(update, context, "❌ Ye reward abhi available nahi hai.", back_home())

    cnt = await count_joins(tg.id, bno)
    ms = btn.get("milestones") or {}
    mx = max((int(k) for k in ms.keys()), default=0)

    notebook = btn.get("notebook") or (
        "🎯 Is button ke rewards paane ke liye apne dosto ko invite karo!\n"
        "Jitne zyada invite, utne zyada rewards — details niche 👇"
    )
    text = f"🎁 <b>{esc(btn['name'])}</b>\n\n{notebook}\n\n👥 <b>Aapke invites: {cnt}</b>"
    if mx:
        text += f" / {mx} required"

    rows = []
    if btn.get("direct"):
        rows.append([cb("📥 Get Content Now", f"ucont_{bno}_{page}")])
    else:
        rows.append([cb("🔗 Get My Share Link", f"ulink_{bno}_{page}")])
    rows.append([cb("👤 Profile", "uprof"), cb("🏠 Menu", "home")])
    if is_admin(tg.id):
        from bson import ObjectId
        rows.append([cb("🛡️ Admin Edit", f"btv_{str(btn['_id'])}_{page}")])
    await render(update, context, text, kb(rows))


async def cb_link(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bno, page = int(m.group(1)), int(m.group(2))
    tg = q.from_user

    btn = await get_button_by_no(bno)
    if not btn:
        return await render(update, context, "❌ Ye reward available nahi hai.", back_home())

    link = f"https://t.me/{BOT_USERNAME}?start=r_{tg.id}_{bno}"
    share_url = (
        f"https://t.me/share/url?url={quote(link)}&text="
        + quote("🎁 Free Keys aur Rewards ke liye ye bot try karo! Join karo aur jeeto 🎉")
    )
    ms = btn.get("milestones") or {}
    mx = max((int(k) for k in ms.keys()), default=0)
    text = (
        f"🔗 <b>Aapka Invite Link</b> — {esc(btn['name'])}\n\n"
        f"<code>{link}</code>\n\n"
        f"📤 Ye link dosto ko share karo. Jab koi is link se aakar channel <b>join</b> karega, "
        f"aapko reward milega!{f' (🎯 {mx} join required)' if mx else ''}"
    )
    rows = [[url("📤 Share Karo", share_url)]]
    if btn.get("notebook"):
        rows.append([cb("📝 Rewards Details", f"ub_{bno}_{page}")])
    rows.append([cb("⬅️ Back", f"ub_{bno}_{page}"), cb("🏠 Menu", "home")])
    await render(update, context, text, kb(rows))


async def cb_getc(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bno = int(m.group(1))
    tg = q.from_user
    context.user_data.pop("st", None)

    btn = await get_button_by_no(bno)
    if not btn or not btn.get("direct"):
        return await render(update, context, "❌ Ye content direct available nahi hai.", back_home())
    items = btn.get("content") or []
    if not items:
        return await render(update, context, "📭 Abhi is button me koi content nahi hai.", back_home())
    await q.message.reply_text("📤 Delivering...")
    for it in items:
        await send_content_item(context.bot, tg.id, it)


# ------------------------------------------------------------------ profile & help
async def profile_text(tg):
    u = await get_user(tg.id) or {}
    from database.db import utcnow
    created = u.get("created_at")
    joined = created.strftime("%d %b %Y") if created else "—"
    return (
        f"👤 <b>My Profile</b>\n\n"
        f"👤 Name: {esc(tg.first_name)}\n"
        f"🆔 ID: <code>{tg.id}</code>\n"
        f"🔗 Username: {'@' + tg.username if tg.username else '—'}\n\n"
        f"👥 Total Invites: <b>{u.get('refs_count', 0)}</b>\n"
        f"🎁 Rewards Claimed: <b>{u.get('rewards', 0)}</b>\n"
        f"📅 Joined: {joined}"
    )


async def cb_prof(update, context):
    q = update.callback_query
    await safe_answer(q)
    context.user_data.pop("st", None)
    await render(update, context, await profile_text(q.from_user), back_home())


async def cmd_profile(update, context):
    await update.message.reply_text(
        await profile_text(update.effective_user),
        reply_markup=back_home(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


HELP_TEXT = (
    "ℹ️ <b>Help — Kaise use karein?</b>\n\n"
    "1️⃣ Main menu se koi bhi <b>🎁 reward button</b> choose karo\n"
    "2️⃣ <b>Get My Share Link</b> dabao — aapka unique link ban jayega\n"
    "3️⃣ Link dosto ko share karo (WhatsApp/Telegram/Insta kahin bhi)\n"
    "4️⃣ Jaise hi koi link se aakar channel join karega — aapko notification milega "
    "aur reward automatically deliver ho jayega! 🎉\n\n"
    "🛡️ <b>Rules:</b>\n"
    "• Fake/self join kaam nahi karega — bot sab yaad rakhta hai\n"
    "• Channel leave karne pe count minus ho jata hai\n"
    "• Ek user ek reward me sirf ek baar count hota hai\n\n"
    "❓ Koi problem ho to owner se contact karo."
)


async def cb_help(update, context):
    await safe_answer(update.callback_query)
    context.user_data.pop("st", None)
    await render(update, context, HELP_TEXT, back_home())


async def cmd_help(update, context):
    await update.message.reply_text(
        HELP_TEXT,
        reply_markup=back_home(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

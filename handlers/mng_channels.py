"""Channel management: add (forward/@username/ID), verify bot admin, remove."""
import logging

from bson import ObjectId
from telegram import Chat
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import OWNER_ID
from database import db
from database.db import all_channels, delete_channel, get_channel, upsert_channel, upd_settings, get_settings
from utils.helpers import bot_is_channel_admin, esc, is_admin, render, send_safe
from utils.keyboards import back_adm, cb, confirm_kb, kb, nav_row, paginate, url

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ list
async def render_channel_list(update, context, page=0):
    chans = await all_channels()
    chunk, page, pages = paginate(chans, page, 6)
    text = f"📺 <b>Channels</b> ({len(chans)} total)\n\nBot admin hona zaroori hai join-detect ke liye:"
    rows = []
    for ch in chunk:
        v = "✅" if ch.get("verified") else "❌"
        rows.append([cb(f"{v} {ch['title'][:24]}", f"chv_{str(ch['_id'])}")])
    rows.append([cb("➕ Add Channel", "chnew")])
    rows += nav_row(page, pages, "adch")
    rows.append([cb("⬅️ Back", "adm")])
    await render(update, context, text, kb(rows))


async def cb_list(update, context):
    await update.callback_query.answer()
    await render_channel_list(update, context, int(context.matches[0].group(1)))


# ------------------------------------------------------------------ view / verify
async def render_channel_view(update, context, ch):
    cid = str(ch["_id"])
    v = ch.get("verified")
    text = (
        f"📺 <b>{esc(ch['title'])}</b>\n\n"
        f"🆔 <code>{ch['channel_id']}</code>\n"
        f"🔗 Username: {'@' + ch['username'] if ch.get('username') else '— (private)'}\n"
        f"🛡️ Bot Admin: <b>{'✅ Verified' if v else '❌ Not admin — add me as admin!'}</b>"
    )
    rows = []
    link = ch.get("invite_link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
    if link:
        rows.append([url("🔗 Open Channel", link)])
    if not v:
        rows.append([cb("🔄 Check Again", f"chck_{cid}")])
    rows.append([cb("🗑️ Remove", f"chdel_{cid}"), cb("⬅️ Back", "adch_0")])
    await render(update, context, text, kb(rows))


async def cb_view(update, context):
    await update.callback_query.answer()
    ch = await db.channels.find_one({"_id": ObjectId(context.matches[0].group(1))})
    if ch:
        await render_channel_view(update, context, ch)
    else:
        await render(update, context, "❌ Channel delete ho chuka.", back_adm())


async def verify_and_update(context, channel_id):
    """Re-check bot admin status, refresh title + invite link. Returns updated doc or None."""
    try:
        chat = await context.bot.get_chat(channel_id)
        if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
            return None
        is_admin_now = await bot_is_channel_admin(context, channel_id)
        ch = await upsert_channel(chat, is_admin_now)
        link = ch.get("invite_link") or ""
        if not link:
            link = chat.invite_link or ""
            if not link and is_admin_now:
                try:
                    link = (await context.bot.create_chat_invite_link(channel_id)).invite_link
                except TelegramError:
                    link = ""
        if link != ch.get("invite_link"):
            await db.channels.update_one({"channel_id": channel_id}, {"$set": {"invite_link": link}})
            ch["invite_link"] = link
        return ch
    except TelegramError as e:
        log.warning("verify failed for %s: %s", channel_id, e)
        return None


async def cb_check(update, context):
    q = update.callback_query
    await q.answer("Checking...")
    doc = await db.channels.find_one({"_id": ObjectId(context.matches[0].group(1))})
    if not doc:
        return await render(update, context, "❌ Channel nahi mila.", back_adm())
    ch = await verify_and_update(context, doc["channel_id"])
    if ch:
        if ch.get("verified"):
            await q.answer("✅ Bot admin hai — Verified!", show_alert=True)
        else:
            await q.answer(
                "❌ Bot admin nahi hai.\nChannel → Administrators → Add Admin → @rkgamersbot "
                "(Invite Users + Ban Users rights dena) — fir Check Again dabao.",
                show_alert=True,
            )
        await render_channel_view(update, context, ch)
    else:
        await render(update, context, "❌ Channel access nahi ho pa raha. Kya channel exist karta hai?", back_adm())


# ------------------------------------------------------------------ delete
async def cb_del(update, context):
    await update.callback_query.answer()
    cid = context.matches[0].group(1)
    ch = await db.channels.find_one({"_id": ObjectId(cid)})
    await render(
        update, context,
        f"🗑️ <b>Remove “{esc(ch['title']) if ch else '?'}”?</b>",
        confirm_kb(f"chdely_{cid}", f"adch_0", "✅ Haan", "❌ Nahi"),
    )


async def cb_del_yes(update, context):
    q = update.callback_query
    await q.answer("Removed!")
    doc = await db.channels.find_one({"_id": ObjectId(context.matches[0].group(1))})
    if doc:
        await delete_channel(doc["channel_id"])
    await render_channel_list(update, context, 0)


# ------------------------------------------------------------------ add flow (called from FSM)
async def add_channel_from_input(update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a forwarded message / @username / -100... ID from the admin."""
    m = update.effective_message
    chat_obj = None
    # forward from a channel/supergroup
    origin = getattr(m, "forward_origin", None)
    if origin is not None:
        chat_obj = getattr(origin, "chat", None)
    if chat_obj is None and getattr(m, "forward_from_chat", None):
        chat_obj = m.forward_from_chat

    if chat_obj is None and m.text:
        raw = m.text.strip().lstrip("@")
        raw = raw.split("?")[0].replace("https://t.me/", "").replace("t.me/", "")
        if raw.startswith("-") or raw.lstrip("-").isdigit():
            try:
                chat_obj = await context.bot.get_chat(int(raw))
            except TelegramError:
                chat_obj = None
        elif raw:
            try:
                chat_obj = await context.bot.get_chat(f"@{raw}")
            except TelegramError:
                chat_obj = None

    if chat_obj is None or chat_obj.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        return False, "❌ Channel samajh nahi aaya.\n\nChannel ka @username / ID bhejo YA channel se koi message forward karo."

    if chat_obj.id < 0 and str(chat_obj.id).startswith("-100") is False and chat_obj.type == ChatType.CHANNEL:
        return False, "❌ Private channel ki numeric ID chahiye (-100...). Channel pe koi message forward karo."

    ch = await upsert_channel(chat_obj, verified=False)
    await verify_and_update(context, chat_obj.id)
    ch = await get_channel(chat_obj.id)
    return True, ch


ADD_INSTRUCTIONS = (
    "✅ Channel save ho gaya: <b>{title}</b>\n\n"
    "{status}"
    "🛡️ <b>Mujhe admin banao:</b>\n"
    "Channel → Administrators → Add Admin → <b>@rkgamersbot</b>\n"
    "Rights: Invite Users + Ban Users (join detect karne ke liye)\n\n"
    "Phir <b>🔄 Check Again</b> dabao — main khud verify kar lunga!"
)
VERIFIED_NOTE = "✅ <b>Bot admin hai — VERIFIED!</b> Ab referral joins detect ho sakte hain.\n\n"


async def cb_new(update, context):
    await update.callback_query.answer()
    context.user_data["st"] = ("ch_in",)
    await render(
        update, context,
        "➕ <b>Add Channel</b>\n\nInme se kuch bhi bhejo:\n"
        "1️⃣ Channel ka <b>@username</b>\n"
        "2️⃣ Channel ki numeric <b>ID</b> (-100...)\n"
        "3️⃣ Channel se <b>koi bhi message forward</b> karke (best for private channels)",
        kb([[cb("❌ Cancel", "cancel")]]),
    )

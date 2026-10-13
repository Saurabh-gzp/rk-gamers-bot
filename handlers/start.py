"""/start, deep-link referral processing, force-sub gate, main user menu."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import BOT_USERNAME
from database import db
from database.db import (
    active_buttons,
    get_button_by_no,
    get_settings,
    get_channel,
    prior_channel_join,
    upsert_invite,
    upsert_user,
    upd_settings,
)
from utils.helpers import bot_is_member, esc, is_admin, notify_admins_error, render, safe_answer, send_safe, user_link_name
from utils.keyboards import cb, kb, nav_row, paginate, url

log = logging.getLogger(__name__)

BANNED_TEXT = "🚫 Aap is bot se ban kar diye gaye ho. Owner se contact karo."

WELCOME = (
    "👋 <b>Welcome to RK Gamers Bot!</b>\n\n"
    "🎮 Yahan se aapko <b>free keys, apps aur rewards</b> milenge — "
    "bas apne dosto ko invite karo aur jeeto! 🎁\n\n"
    "👥 Total Users: <b>{users}</b>"
)


def parse_ref(payload):
    """'r_<inviterId>_<buttonNo>' -> (inviter_id, button_no) or None."""
    try:
        parts = payload.split("_")
        if parts[0] != "r" or len(parts) != 3:
            return None
        return int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return None


async def show_main_menu(update, context: ContextTypes.DEFAULT_TYPE, notice=None, page=0):
    tg = update.effective_user
    btns = await active_buttons()
    users = await db.users.count_documents({})
    text = WELCOME.format(users=users)
    if notice:
        text = notice + "\n\n" + text

    chunk, page, pages = paginate(btns, page, 6)
    rows = []
    if not chunk:
        text += "\n\n🔔 Abhi koi reward live nahi hai — jald aa raha hai!"
    for b in chunk:
        rows.append([InlineKeyboardButton(f"🎁 {b['name'][:28]}", callback_data=f"ub_{b['no']}_{page}")])
    rows += nav_row(page, pages, "upg")
    rows.append([cb("👤 My Profile", "uprof"), cb("ℹ️ Help", "uhelp")])
    if is_admin(tg.id):
        rows.append([cb("🛡️ Admin Panel", "adm")])
    await render(update, context, text, kb(rows))


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    if tg is None:
        return
    chat = update.effective_chat
    if chat is None or chat.type != chat.PRIVATE:
        if update.message:
            await update.message.reply_text(
                "😅 Mujhe personal message me use karo!",
                reply_markup=kb([[InlineKeyboardButton("🤖 Bot Open Karo", url=f"https://t.me/{BOT_USERNAME}")]]),
            )
        return

    user, created = await upsert_user(tg)
    if user.get("banned"):
        await send_safe(context.bot, tg.id, BANNED_TEXT)
        return

    # New-user alert to owner
    s = await get_settings()
    if created and s.get("newuser_alert", True) and not is_admin(tg.id):
        from config import OWNER_ID
        total = await db.users.count_documents({})
        await send_safe(
            context.bot, OWNER_ID,
            f"🆕 <b>New User!</b>\n👤 {esc(tg.first_name)}\n"
            f"🆔 <code>{tg.id}</code>{' @' + tg.username if tg.username else ''}\n"
            f"👥 Total users: <b>{total}</b>",
        )

    # Deep-link referral
    notice = None
    payload = context.args[0] if context.args else None
    if payload and payload.startswith("r_"):
        try:
            notice = await handle_ref(update, context, tg, payload)
        except Exception as e:
            # NEVER silent: owner must see every referral failure in Telegram
            log.exception("ref handling failed")
            await notify_admins_error(context, f"/start {payload}", e)
            notice = "⚠️ Invite process me technical problem aayi. Dobara try karo ya owner se batao."

    # Force-sub gate (admins exempt)
    if not is_admin(tg.id) and s.get("force_sub_on") and s.get("force_sub"):
        missing = []
        for cid in s["force_sub"]:
            ch = await get_channel(cid)
            if ch and await bot_is_member(context, cid, tg.id) is False:
                missing.append(ch)
        if missing:
            await send_fs_gate(update, context, missing)
            return

    await show_main_menu(update, context, notice=notice)


# ------------------------------------------------------------------ referral logic
async def handle_ref(update, context, tg, payload) -> str | None:
    """Process 'r_<inviter>_<button>' deep link. Returns notice text for main menu (or None).
    EVERY valid click is reported to the link owner — click / join / reward / leave, sab visible."""
    p = parse_ref(payload)
    if not p:
        return None
    inviter_id, bno = p

    btn = await get_button_by_no(bno)
    if not btn or not btn.get("active"):
        return "❌ Ye invite link abhi active nahi hai."

    if inviter_id == tg.id:
        return "😅 Aap khud ko invite nahi kar sakte! Apna link dusre users ko share karo."

    inviter = await db.users.find_one({"_id": inviter_id})
    if not inviter or inviter.get("banned"):
        return "❌ Ye invite link valid nahi hai."

    # track the click + identify the visitor for owner notifications
    await db.buttons.update_one({"_id": btn["_id"]}, {"$inc": {"stats.clicks": 1}})
    who = user_link_name(tg)
    pair = await db.joins.find_one({"inviter": inviter_id, "invitee": tg.id, "button_no": bno})
    s = await get_settings()

    if pair and pair.get("active"):
        # duplicate attempt — notify BOTH sides (anti-fraud)
        await send_safe(
            context.bot, inviter_id,
            f"⚠️ {esc(tg.first_name)} (<code>{tg.id}</code>) ne dobara aapka link khola — "
            f"ye user pehle se aapke liye counted hai.\n"
            f"👋 Naye users invite karo, wahi reward dete hain!",
        )
        return "⚠️ Aap is link se pehle hi join kar chuke ho! Dubara count nahi hoga."

    if not pair and s.get("strict", True):
        other = await db.joins.find_one({"invitee": tg.id, "button_no": bno})
        if other:
            await send_safe(
                context.bot, inviter_id,
                f"👀 {who} ne aapka link click kiya!\n"
                f"⚠️ Lekin ye user is reward me pehle hi kisi aur ke through counted hai "
                f"(anti-fraud) — aapko is click se count nahi milega.",
            )
            return "⚠️ Is reward ke liye aap pehle hi kisi aur ke through counted ho. Dobara count nahi hoga."

    chan = await get_channel(btn.get("channel_id"))
    if not chan:
        return "⚠️ Is reward ka channel abhi setup nahi hua. Owner se contact karo."

    member = await bot_is_member(context, chan["channel_id"], tg.id)
    if member is True:
        # old member = can never be counted — but the owner MUST see this click
        await send_safe(
            context.bot, inviter_id,
            f"👀 {who} ne aapka link click kiya!\n"
            f"⚠️ Lekin ye user <b>{esc(chan['title'])}</b> ka member <b>pehle se</b> hai — "
            f"sirf <b>naye members</b> hi invite me count hote hain.\n"
            f"💡 Aise users ko link bhejo jo channel me <b>pehle kabhi join nahi hue</b>.",
        )
        return (
            "ℹ️ Aap pehle se is channel ke member ho.\n"
            "Sirf <b>naye members</b> hi invite me count hote hain — purana member dobara count nahi hota."
        )

    # ANTI-FRAUD (cross-button farming): ek user ek channel me SIRF EK BAAR count
    # hota hai, chahe kitne bhi buttons/keys ho. Leave karke dobara join karne pe
    # dobara credit nahi milega — farmer ko link-click pe hi rok do.
    prior = await prior_channel_join(tg.id, chan["channel_id"], bno)
    if prior is not None:
        await send_safe(
            context.bot, inviter_id,
            f"🛡️ {who} ne aapka link click kiya!\n"
            f"⚠️ Lekin ye user is channel me <b>pehle hi counted</b> ho chuka hai "
            f"(kisi aur key se) — <b>dobara count nahi milega</b>.\n"
            f"Leave karke rejoin karne se farming nahi chalegi.",
        )
        return (
            "🛡️ Aap is channel me <b>pehle hi counted</b> ho chuke ho (kisi aur key se).\n"
            "Ek user ek channel me <b>sirf ek baar</b> count hota hai — dobara reward nahi milega."
        )

    # store pending invite — completed when user actually joins the channel
    await upsert_invite(tg.id, inviter_id, bno, chan["channel_id"])
    await send_safe(
        context.bot, inviter_id,
        f"👀 <b>{who}</b> ne aapka link click kiya! 🎯\n"
        f"⏳ Wo ab <b>{esc(chan['title'])}</b> join karega — join hote hi aapko "
        f"<b>+1 join</b> milega aur reward bhi!",
    )

    link = chan.get("invite_link") or (f"https://t.me/{chan['username']}" if chan.get("username") else None)
    rows = []
    if link:
        rows.append([InlineKeyboardButton("➕ Channel Join Karo", url=link)])
    rows.append([cb("✅ I've Joined", f"jchk_{bno}_{inviter_id}")])
    await send_safe(
        context.bot, tg.id,
        f"🎁 <b>{esc(btn['name'])}</b>\n\n"
        f"1️⃣ Pehle upar diye channel ko join karo\n"
        f"2️⃣ Fir <b>\"I've Joined\"</b> dabao\n\n"
        f"Jaise hi aap join karoge, aapke dost ko uska reward mil jayega! 🎉",
        kb(rows),
    )
    return "👇 Neeche apna invite step complete karo!"


async def send_fs_gate(update, context, missing_channels):
    rows = []
    for ch in missing_channels:
        link = ch.get("invite_link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
        if link:
            rows.append([InlineKeyboardButton(f"📢 {ch['title'][:24]}", url=link)])
    rows.append([cb("✅ Verify / I've Joined", "fschk")])
    await render(
        update, context,
        "🔒 <b>Bot use karne ke liye pehle ye channel(s) join karo:</b>\n\n"
        "Join karne ke baad <b>Verify</b> dabao 👇",
        kb(rows),
    )


async def cb_fschk(update, context):
    q = update.callback_query
    await safe_answer(q)
    tg = q.from_user
    context.user_data.pop("st", None)
    s = await get_settings()
    if is_admin(tg.id) or not s.get("force_sub_on") or not s.get("force_sub"):
        return await show_main_menu(update, context)
    missing = []
    for cid in s["force_sub"]:
        ch = await get_channel(cid)
        if ch and await bot_is_member(context, cid, tg.id) is False:
            missing.append(ch)
    if missing:
        await send_fs_gate(update, context, missing)
    else:
        await show_main_menu(update, context, notice="✅ Verified! Welcome 🎉")


# ------------------------------------------------------------------ callbacks
async def cb_noop(update, context):
    await safe_answer(update.callback_query)


async def cb_home(update, context):
    await safe_answer(update.callback_query)
    context.user_data.pop("st", None)  # navigation cancels any pending input
    await show_main_menu(update, context)


async def cb_page(update, context):
    """User main-menu pagination: upg_<page>"""
    await safe_answer(update.callback_query)
    context.user_data.pop("st", None)
    page = int(context.matches[0].group(1))
    await show_main_menu(update, context, page=page)

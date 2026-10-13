"""chat_member / my_chat_member updates: channel join = referral complete, leave = count -1."""
import logging

from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from config import OWNER_ID
from database import db
from database.db import get_button_by_no, upsert_channel
from handlers.user import process_join, process_leave
from utils.helpers import esc, send_safe

log = logging.getLogger(__name__)


async def on_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if cmu is None:
        return
    chat = cmu.chat
    if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        return
    new, old = cmu.new_chat_member, cmu.old_chat_member
    u = new.user
    if u.is_bot:
        return

    joined = (
        new.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
        and old.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
    )
    left = (
        new.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
        and old.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    )
    try:
        if joined:
            await process_join(context, chat.id, u)
        elif left:
            await process_leave(context, chat.id, u.id)
    except Exception:
        log.exception("chat_member handling failed")


async def on_my_chat_member(update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-verify channels when the bot is promoted/demoted/removed."""
    cmu = update.my_chat_member
    if cmu is None:
        return
    chat = cmu.chat
    if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        return
    new = cmu.new_chat_member.status
    if new == ChatMemberStatus.ADMINISTRATOR:
        ch = await upsert_channel(chat, verified=True)
        if not ch.get("invite_link"):
            from handlers.mng_channels import verify_and_update
            await verify_and_update(context, chat.id)
        await send_safe(
            context.bot, OWNER_ID,
            f"✅ Bot ab <b>{esc(chat.title)}</b> ka <b>admin</b> ban gaya — channel verified!\n"
            f"Ab is channel ke liye reward buttons setup kar sakte ho 🎯",
        )
    elif new in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        await db.channels.update_one({"channel_id": chat.id}, {"$set": {"verified": False}})
        await send_safe(
            context.bot, OWNER_ID,
            f"❌ Bot <b>{esc(chat.title)}</b> me admin nahi raha. Referral detection band ho gayi is channel ke liye.",
        )

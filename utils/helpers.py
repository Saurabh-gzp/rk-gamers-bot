"""Shared helpers: safe sending, HTML rendering, content extraction."""
import html

from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, TelegramError

from config import ADMINS


def is_admin(uid) -> bool:
    try:
        return int(uid) in ADMINS
    except (TypeError, ValueError):
        return False


def esc(s) -> str:
    return html.escape(str(s or ""))


def mention(uid, name) -> str:
    return f"<a href='tg://user?id={uid}'>{esc(name)}</a>"


def user_link_name(tg_user) -> str:
    name = tg_user.first_name or "User"
    return mention(tg_user.id, name)


async def send_safe(bot, chat_id, text, kb=None):
    """Send HTML text, falling back to plain text if parse fails. Never raises."""
    if text is None:
        return None
    try:
        return await bot.send_message(
            chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )
    except BadRequest as e:
        msg = str(e).lower()
        if "parse" in msg or "entities" in msg or "markdown" in msg or "tag" in msg:
            try:
                return await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
            except TelegramError:
                return None
        return None
    except TelegramError:
        return None


async def render(update, context, text, kb=None):
    """Edit message on callback, reply on message. Falls back to sending a new message."""
    q = getattr(update, "callback_query", None)
    if q is not None and q.message is not None:
        try:
            await q.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
            )
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except TelegramError:
            pass
        chat_id = update.effective_chat.id if update.effective_chat else q.from_user.id
        await send_safe(context.bot, chat_id, text, kb)
    else:
        m = update.effective_message
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None and m is not None:
            chat_id = m.chat_id
        if chat_id is not None:
            try:
                await context.bot.send_message(
                    chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
                )
            except BadRequest:
                try:
                    await context.bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
                except TelegramError:
                    pass
            except TelegramError:
                pass


# ------------------------------------------------------------------ content items
MEDIA_SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "document": "send_document",
    "audio": "send_audio",
    "voice": "send_voice",
    "animation": "send_animation",
}
NO_CAPTION_TYPES = {"video_note", "sticker"}
ITEM_EMOJI = {
    "text": "📝", "photo": "📷", "video": "🎬", "document": "📄", "audio": "🎵",
    "voice": "🎤", "video_note": "🎥", "sticker": "🌟", "animation": "🌀",
}


def extract_item(m):
    """Convert an incoming telegram.Message into a storable content item dict.
    We store only references (file_id + chat_id + message_id) — files are NEVER re-uploaded."""
    base = {"chat_id": m.chat.id, "msg_id": m.message_id}
    if m.text:
        return {"type": "text", "text": m.text_html, **base}
    if m.photo:
        return {"type": "photo", "file_id": m.photo[-1].file_id, "caption": m.caption_html or "", **base}
    if m.video:
        return {"type": "video", "file_id": m.video.file_id, "caption": m.caption_html or "", **base}
    if m.animation:
        return {"type": "animation", "file_id": m.animation.file_id, "caption": m.caption_html or "", **base}
    if m.audio:
        return {"type": "audio", "file_id": m.audio.file_id, "caption": m.caption_html or "", **base}
    if m.voice:
        return {"type": "voice", "file_id": m.voice.file_id, "caption": m.caption_html or "", **base}
    if m.video_note:
        return {"type": "video_note", "file_id": m.video_note.file_id, "caption": "", **base}
    if m.sticker:
        return {"type": "sticker", "file_id": m.sticker.file_id, "caption": "", **base}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id, "caption": m.caption_html or "", **base}
    return None


async def send_content_item(bot, chat_id, item):
    """Deliver a stored content item by reference. Falls back to copy_message."""
    try:
        t = item.get("type")
        if t == "text":
            return await send_safe(bot, chat_id, item.get("text", ""))
        cap = item.get("caption") or ""
        kw = {}
        if cap and t not in NO_CAPTION_TYPES:
            kw = {"caption": cap, "parse_mode": ParseMode.HTML}
        if t == "sticker":
            return await bot.send_sticker(chat_id, item["file_id"])
        if t == "video_note":
            return await bot.send_video_note(chat_id, item["file_id"])
        sender = getattr(bot, MEDIA_SENDERS[t], None)
        if sender is None:
            return None
        return await sender(chat_id, item["file_id"], **kw)
    except TelegramError:
        # Fallback: forward the original message by stored reference
        try:
            if item.get("chat_id") and item.get("msg_id"):
                return await bot.copy_message(chat_id, item["chat_id"], item["msg_id"])
        except TelegramError:
            pass
    return None


async def send_content_list(bot, chat_id, items, header=None):
    if header:
        await send_safe(bot, chat_id, header)
    for it in items:
        await send_content_item(bot, chat_id, it)


def item_label(item, idx=None):
    e = ITEM_EMOJI.get(item.get("type"), "📦")
    if item.get("type") == "text":
        t = html.unescape(item.get("text", ""))
        t = t.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        t = t.replace("<code>", "").replace("</code>", "").replace("<u>", "").replace("</u>", "")
        t = t.replace("<s>", "").replace("</s>", "").replace("<a href=", "").replace("</a>", "")
        t = " ".join(t.split())[:40]
        return f"{e} Text: {t}..."
    return f"{e} {item.get('type', 'media').capitalize()}" + (f" {idx + 1}" if idx is not None else "")


# ------------------------------------------------------------------ membership check
def member_in_chat(m) -> bool:
    """True if a ChatMember object represents someone currently inside the chat.
    Handles: member / administrator / creator / restricted-with-is_member (supergroups)."""
    if m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return True
    return m.status == ChatMemberStatus.RESTRICTED and bool(getattr(m, "is_member", False))


async def bot_is_member(context, channel_id, uid):
    """True/False. None = unknown (bot not admin in that channel)."""
    try:
        m = await context.bot.get_chat_member(channel_id, uid)
        return member_in_chat(m)
    except TelegramError:
        return None


async def bot_is_channel_admin(context, channel_id):
    try:
        m = await context.bot.get_chat_member(channel_id, (await context.bot.get_me()).id)
        return m.status == ChatMemberStatus.ADMINISTRATOR
    except TelegramError:
        return False

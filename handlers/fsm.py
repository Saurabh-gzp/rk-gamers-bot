"""Message state machine: routes admin/user text+media inputs based on user_data['st']."""
import logging

from bson import ObjectId
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import OWNER_ID
from database import db
from database.db import get_button, get_user, save_button_content, delete_button, upsert_user, get_button_by_no
from handlers.admin import render_panel, render_user_list, render_user_view, render_settings
from handlers.mng_channels import (
    ADD_INSTRUCTIONS,
    VERIFIED_NOTE,
    add_channel_from_input,
    render_channel_view,
)
from handlers.mng_buttons import render_button, render_button_list, render_mi_items
from handlers.start import show_main_menu
from utils.helpers import esc, extract_item, is_admin, item_label, render, safe_answer, send_content_item, send_safe
from utils.keyboards import back_adm, back_home, cb, kb

log = logging.getLogger(__name__)


async def cb_cancel(update, context):
    q = update.callback_query
    await safe_answer(q, "Cancelled!")
    context.user_data.pop("st", None)
    context.user_data.pop("tmp_content", None)
    context.user_data.pop("bc_item", None)
    if is_admin(q.from_user.id):
        await render_panel(update, context)
    else:
        await show_main_menu(update, context)


async def cmd_cancel(update, context):
    context.user_data.pop("st", None)
    context.user_data.pop("tmp_content", None)
    await update.message.reply_text("❌ Cancelled.")
    if is_admin(update.effective_user.id):
        await render_panel(update, context)
    else:
        await show_main_menu(update, context)


# ------------------------------------------------------------------ content save/cancel
async def cb_save_yes(update, context):
    q = update.callback_query
    await safe_answer(q, "💾 Saved!")
    st = context.user_data.get("st")
    buf = context.user_data.get("tmp_content") or []
    if st and st[0] == "btn_content":
        _, bid, mode = st
        b = await get_button(ObjectId(bid))
        if b:
            if mode == "new":
                await save_button_content(ObjectId(bid), list(buf))
            else:  # btn_contadd handled below via st too
                await save_button_content(ObjectId(bid), (b.get("content") or []) + list(buf))
            # default milestone-1 = all content (only if untouched & no content before)
            ms = b.get("milestones") or {}
            if "1" in ms and ms["1"].get("content") == "all" and buf:
                await db.buttons.update_one({"_id": b["_id"]}, {"$set": {"milestones": ms}})
        context.user_data.pop("st", None)
        context.user_data.pop("tmp_content", None)
        context.user_data.pop("tmp_msg", None)
        if b:
            await render_button(update, context, await get_button(b["_id"]), 0)
    elif st and st[0] == "btn_contadd":
        _, bid = st
        b = await get_button(ObjectId(bid))
        if b:
            await save_button_content(b["_id"], (b.get("content") or []) + list(buf))
            context.user_data.pop("st", None)
            context.user_data.pop("tmp_content", None)
            context.user_data.pop("tmp_msg", None)
            from handlers.mng_buttons import render_content_list
            await render_content_list(update, context, b["_id"], 0)
    else:
        context.user_data.pop("st", None)
        await render(update, context, "✅ Saved.", back_adm())


async def cb_save_no(update, context):
    q = update.callback_query
    await safe_answer(q, "Cancelled!")
    st = context.user_data.get("st") or ("", "")
    context.user_data.pop("st", None)
    context.user_data.pop("tmp_content", None)
    context.user_data.pop("tmp_msg", None)
    if st[0] == "btn_content":
        # fresh button cancelled during creation -> delete it
        try:
            b = await get_button(ObjectId(st[1]))
            if b and not (b.get("content") or []):
                await delete_button(b["_id"])
                return await render_button_list(update, context, 0)
            if b:
                return await render_button(update, context, b, 0)
        except Exception:
            log.exception("cancel new button failed")
        return await render_button_list(update, context, 0)
    if st[0] == "btn_contadd" and st[1]:
        b = await get_button(ObjectId(st[1]))
        if b:
            return await render_button(update, context, b, 0)
    if is_admin(q.from_user.id):
        await render_panel(update, context)


async def _content_status(update, context, buf):
    """Create/edit a single status message showing collected item count + Save/Cancel."""
    text = (
        f"📦 <b>{len(buf)} item(s) collected</b>\n\n"
        "➕ Aur bhej sakte ho, ya save karo 👇\n"
        + "\n".join(f"  • {item_label(it)}" for it in buf[-5:])
    )
    kb_rows = kb([[cb("✅ Save", "csvy"), cb("❌ Cancel", "csvn")]])
    sid = context.user_data.get("tmp_msg")
    if sid:
        try:
            await context.bot.edit_message_text(
                text, chat_id=update.effective_chat.id, message_id=sid,
                reply_markup=kb_rows, disable_web_page_preview=True,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:
            pass
    msg = await send_safe(context.bot, update.effective_chat.id, text, kb_rows)
    if msg:
        context.user_data["tmp_msg"] = msg.message_id


# ------------------------------------------------------------------ FSM entry
async def fsm_entry(update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    m = update.effective_message
    if tg is None or m is None:
        return

    st = context.user_data.get("st")
    if st:
        kind = st[0] if isinstance(st, tuple) else None
        try:
            if kind == "btn_name":
                name = (m.text or "").strip()
                if not name:
                    return await m.reply_text("📝 Text bhejo (button ka naam):")
                context.user_data["st"] = None
                await _name_to_channel_pick(update, context, name)
                return

            if kind == "btn_name2":
                name = (m.text or "").strip()
                if not name:
                    return await m.reply_text("📝 Text bhejo (naya naam):")
                bid = ObjectId(st[1])
                await db.buttons.update_one({"_id": bid}, {"$set": {"name": name[:64]}})
                context.user_data.pop("st", None)
                b = await get_button(bid)
                return await render_button(update, context, b, 0)

            if kind in ("btn_content", "btn_contadd"):
                item = extract_item(m)
                if not item:
                    return await m.reply_text(
                        "❌ Ye type support nahi hai. Text / photo / video / audio / file bhejo."
                    )
                buf = context.user_data.setdefault("tmp_content", [])
                buf.append(item)
                await _content_status(update, context, buf)
                return

            if kind == "btn_note":
                if not m.text:
                    return await m.reply_text("📝 Sirf TEXT bhejo (photo/file nahi).")
                bid = ObjectId(st[1])
                note = m.text.strip()[:3000]
                await db.buttons.update_one({"_id": bid}, {"$set": {"notebook": note}})
                context.user_data.pop("st", None)
                b = await get_button(bid)
                return await render_button(update, context, b, 0)

            if kind == "mi_text":
                if not m.text:
                    return await m.reply_text("📝 Sirf TEXT bhejo (photo/video nahi).")
                bid, n = st[1], st[2]
                text = m.text.strip()[:3000]
                b = await get_button(ObjectId(bid))
                if b:
                    ms = b.get("milestones") or {}
                    cfg = ms.get(str(n)) or {}
                    cfg["text"] = text
                    ms[str(n)] = cfg
                    await db.buttons.update_one({"_id": b["_id"]}, {"$set": {"milestones": ms}})
                context.user_data.pop("st", None)
                after = context.user_data.pop("mi_after_text", None)
                if after:
                    return await render_mi_items(update, context, ObjectId(bid), n)
                if b:
                    return await render_button(update, context, b, 0)
                return

            if kind == "bc":
                item = extract_item(m)
                if not item:
                    return await m.reply_text("❌ Text / photo / video / file bhejo broadcast ke liye.")
                context.user_data.pop("st", None)
                context.user_data["bc_item"] = item
                await send_content_item(context.bot, tg.id, item)
                return await m.reply_text(
                    "👀 Upar <b>preview</b> hai. Sab users ko bhejna hai?",
                    reply_markup=kb([[cb("✅ Haan Send Karo", "bcok"), cb("❌ Cancel", "bcno")]]),
                    parse_mode=ParseMode.HTML,
                )

            if kind == "ch_in":
                context.user_data.pop("st", None)
                ok, res = await add_channel_from_input(update, context)
                if not ok:
                    context.user_data["st"] = ("ch_in",)
                    return await m.reply_text(res)
                await m.reply_text(
                    ADD_INSTRUCTIONS.format(title=esc(res["title"]), status=VERIFIED_NOTE if res.get("verified") else ""),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                return await render_channel_view(update, context, res)

            if kind == "u_search":
                raw = (m.text or "").strip()
                context.user_data.pop("st", None)
                try:
                    uid = int(raw)
                except ValueError:
                    return await m.reply_text("❌ Numeric user ID bhejo.", reply_markup=back_adm())
                u = await get_user(uid)
                if not u:
                    return await m.reply_text(
                        f"❌ User <code>{uid}</code> nahi mila.",
                        reply_markup=back_adm(),
                        parse_mode=ParseMode.HTML,
                    )
                return await render_user_view(update, context, uid, 0)

        except Exception:
            log.exception("fsm state failed")
            context.user_data.pop("st", None)
            return await m.reply_text("⚠️ Kuch galat ho gaya — dobara try karo.", reply_markup=back_adm())

    # no active state → friendly menu
    user, _ = await upsert_user(tg)
    if user.get("banned"):
        return
    if is_admin(tg.id):
        await show_main_menu(update, context, notice="👇 Buttons use karo! (Admin: /admin)")
    else:
        await show_main_menu(update, context, notice="👇 Neeche diye buttons use karo 😊")


async def _name_to_channel_pick(update, context, name):
    """Create the button after name input, then ask channel pick."""
    from handlers.mng_buttons import render_chan_pick
    from database.db import create_button
    btn = await create_button(name[:64])
    await render_chan_pick(update, context, btn, 0, fresh=True)

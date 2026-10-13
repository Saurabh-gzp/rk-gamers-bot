"""Reward buttons CRUD: name, channel, content (multi-upload with confirm), milestones, toggles."""
import logging

from bson import ObjectId
from telegram.ext import ContextTypes

from database import db
from database.db import (
    all_buttons,
    create_button,
    delete_button,
    get_button,
    get_button_by_no,
    get_channel,
    verified_channels,
)
from utils.helpers import esc, is_admin, item_label, render, safe_answer, send_safe
from utils.keyboards import back_adm, back_home, cb, confirm_kb, kb, nav_row, nav_row_fmt, paginate, url

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ list
async def render_button_list(update, context, page=0):
    btns = await all_buttons()
    chunk, page, pages = paginate(btns, page, 6)
    text = f"🔘 <b>Reward Buttons</b> ({len(btns)} total)\n\nTap karke manage karo:"
    rows = []
    for b in chunk:
        mark = "🟢" if b.get("active") else "⛔"
        rows.append([cb(f"{mark} {b['name'][:24]} #{b['no']}", f"btv_{str(b['_id'])}_{page}")])
    rows.append([cb("➕ New Button", "btnew")])
    rows += nav_row(page, pages, "adbt")
    rows.append([cb("⬅️ Back", "adm")])
    await render(update, context, text, kb(rows))


async def cb_list(update, context):
    await safe_answer(update.callback_query)
    context.user_data.pop("st", None)  # navigation cancels any pending input
    await render_button_list(update, context, int(context.matches[0].group(1)))


# ------------------------------------------------------------------ create
async def cb_new(update, context):
    await safe_answer(update.callback_query)
    context.user_data.pop("tmp_content", None)
    context.user_data["st"] = ("btn_name",)
    await render(
        update, context,
        "➕ <b>New Reward Button</b>\n\n📝 Button ka <b>naam</b> bhejo "
        "(jaise: <code>Get Free Key</code>):\n\n❌ Cancel: /cancel ya niche button.",
        kb([[cb("❌ Cancel", "cancel")]]),
    )


async def finish_name(update, context, name, bid=None):
    """After receiving a name — create (new) or rename (existing), then channel pick."""
    if bid:
        await db.buttons.update_one({"_id": ObjectId(bid)}, {"$set": {"name": name}})
        context.user_data.pop("st", None)
        btn = await get_button(ObjectId(bid))
        return await render_button(update, context, btn, 0)
    btn = await create_button(name[:64])
    context.user_data["st"] = None
    await render_chan_pick(update, context, btn, 0, fresh=True)


# ------------------------------------------------------------------ channel pick
async def render_chan_pick(update, context, btn, page=0, fresh=False):
    chans = await verified_channels()
    bid = str(btn["_id"])
    rows = []
    if chans:
        chunk, page, pages = paginate(chans, page, 6)
        for ch in chunk:
            rows.append([cb(f"📺 {ch['title'][:26]}", f"btsel_{bid}_{ch['channel_id']}")])
        rows += nav_row_fmt(page, pages, lambda p: f"btchan_{bid}_{p}")
    else:
        rows.append([cb("➕ Add Channel First", "chnew")])
    rows.append([cb("⏭️ Skip (No Channel)", f"btskip_{bid}")])
    rows.append([cb("❌ Cancel & Delete", f"btdely_{bid}")])
    await render(
        update, context,
        f"📺 <b>Channel Select Karo</b> — “{esc(btn['name'])}”\n\n"
        "Ye channel users ko join karna hoga reward paane ke liye.\n"
        "(Baad me bhi change kar sakte ho)",
        kb(rows),
    )


async def cb_chan_pick(update, context):
    await safe_answer(update.callback_query)
    m = context.matches[0]
    btn = await get_button(ObjectId(m.group(1)))
    if btn:
        await render_chan_pick(update, context, btn, int(m.group(2)))


async def cb_chan_set(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bid, chan_id = ObjectId(m.group(1)), int(m.group(2))
    ch = await get_channel(chan_id)
    if not ch:
        return await render(update, context, "❌ Channel nahi mila.", back_adm())
    await db.buttons.update_one({"_id": bid}, {"$set": {"channel_id": chan_id}})
    btn = await get_button(bid)
    await start_content_upload(update, context, btn)


async def cb_chan_skip(update, context):
    q = update.callback_query
    await safe_answer(q)
    btn = await get_button(ObjectId(context.matches[0].group(1)))
    if btn:
        await start_content_upload(update, context, btn)


async def start_content_upload(update, context, btn, mode="new"):
    bid = str(btn["_id"])
    context.user_data["st"] = ("btn_content", bid, mode)
    context.user_data["tmp_content"] = []
    context.user_data.pop("tmp_msg", None)
    await render(
        update, context,
        f"📦 <b>Ab Content Bhejo</b> — “{esc(btn['name'])}”\n\n"
        "Aap kuch bhi bhej sakte ho — <b>text, photo, video, link, file...</b> "
        "ek sath ya multiple! 📎\n\n"
        "Sab bhejne ke baad <b>✅ Save</b> dabao. Bot files upload nahi karta — "
        "sirf message reference save karta hai (fast + same file forward hoti hai).",
        kb([[cb("✅ Save (Kuch nahi bhejna)", "csvy"), cb("❌ Cancel", "csvn")]]),
    )


# ------------------------------------------------------------------ button detail view
async def render_button(update, context, btn, page=0):
    bid = str(btn["_id"])
    ch = await get_channel(btn.get("channel_id")) if btn.get("channel_id") else None
    ms = btn.get("milestones") or {}
    ms_parts = []
    for k in sorted(ms.keys(), key=lambda x: int(x)):
        v = ms[k]
        content = v.get("content")
        bits = []
        if content == "all":
            bits.append("📦 All")
        elif content:
            bits.append(f"📦 {len(content)} items")
        if v.get("text"):
            bits.append("📝")
        ms_parts.append(f"{k}→{'+'.join(bits) if bits else '—'}")
    items = btn.get("content") or []
    stats = btn.get("stats") or {}

    text = (
        f"🔘 <b>{esc(btn['name'])}</b>  #{btn['no']}\n\n"
        f"📺 Channel: {('✅ ' + esc(ch['title']) + (' (@' + ch['username'] + ')' if ch.get('username') else '')) if ch else '— Not set'}\n"
        f"📦 Content: <b>{len(items)}</b> items\n"
        f"🔗 Direct Delivery: <b>{'ON ✅' if btn.get('direct') else 'OFF (referral needed)'}</b>\n"
        f"🎯 Milestones: {esc(', '.join(ms_parts)) if ms_parts else '— none'}\n"
        f"📝 Notebook: {esc((btn.get('notebook') or '— default text —')[:80])}{'...' if len(btn.get('notebook') or '') > 80 else ''}\n\n"
        f"📊 Joins: <b>{stats.get('joins', 0)}</b> | 🎁 Delivered: <b>{stats.get('delivered', 0)}</b>\n"
        f"Status: {'🟢 Active' if btn.get('active') else '⛔ Hidden'}"
    )
    rows = [
        [cb("✏️ Name", f"btname_{bid}"), cb("📝 Notebook", f"btnote_{bid}")],
        [cb(f"📦 Content ({len(items)})", f"btcont_{bid}_0"), cb("📺 Channel", f"btchan_{bid}_0")],
        [cb("🎯 Milestones", f"btmil_{bid}"), cb(f"🔗 Direct: {'ON' if btn.get('direct') else 'OFF'}", f"btdir_{bid}")],
        [
            cb(f"{'⛔ Hide' if btn.get('active') else '🟢 Activate'}", f"btact_{bid}"),
            cb("🗑️ Delete", f"btdel_{bid}"),
        ],
        [cb("⬅️ Back", f"adbt_{page}")],
    ]
    await render(update, context, text, kb(rows))


async def cb_view(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    btn = await get_button(ObjectId(m.group(1)))
    if btn:
        context.user_data.pop("st", None)
        await render_button(update, context, btn, int(m.group(2)))
    else:
        await render(update, context, "❌ Button delete ho chuka hai.", back_adm())


# ------------------------------------------------------------------ edit name / notebook
async def cb_rename(update, context):
    await safe_answer(update.callback_query)
    bid = context.matches[0].group(1)
    context.user_data.pop("tmp_content", None)
    context.user_data["st"] = ("btn_name2", bid)
    await render(update, context, "✏️ <b>Naya naam bhejo:</b>", kb([[cb("❌ Cancel", "cancel")]]))


async def cb_note(update, context):
    await safe_answer(update.callback_query)
    bid = context.matches[0].group(1)
    context.user_data.pop("tmp_content", None)
    context.user_data["st"] = ("btn_note", bid)
    await render(
        update, context,
        "📝 <b>Notebook</b> — ye text users ko button khulte hi dikhega.\n\n"
        "Yahan likho ki kitne join pe kya milega, jaise:\n"
        "<code>1 invite = ₹2000 key\n2 invite = ₹3000 key\n3 invite = Premium App 🔥</code>\n\n"
        "Ab text bhejo (HTML supported: &lt;b&gt;bold&lt;/b&gt;):",
        kb([[cb("❌ Cancel", "cancel")]]),
    )


# ------------------------------------------------------------------ content manager
async def cb_content(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    await render_content_list(update, context, ObjectId(m.group(1)), int(m.group(2)))


async def render_content_list(update, context, bid, page=0):
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    items = btn.get("content") or []
    chunk, page, pages = paginate(list(enumerate(items)), page, 8)
    text = (
        f"📦 <b>Content — {esc(btn['name'])}</b>\n\n"
        f"Total: <b>{len(items)}</b> items\n"
        f"<i>🗑️ Tap karke delete kar sakte ho</i>"
    )
    rows = []
    for idx, it in chunk:
        rows.append([cb(f"🗑️ {item_label(it, idx)}", f"btcdel_{str(bid)}_{idx}")])
    rows.append([cb("➕ Add More Content", f"btcadd_{str(bid)}")])
    rows += nav_row_fmt(page, pages, lambda p: f"btcont_{str(bid)}_{p}")
    rows.append([cb("⬅️ Back", f"btv_{str(bid)}_0")])
    await render(update, context, text, kb(rows))


async def cb_cont_del(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bid, idx = ObjectId(m.group(1)), int(m.group(2))
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    items = btn.get("content") or []
    if 0 <= idx < len(items):
        items.pop(idx)
        await db.buttons.update_one({"_id": bid}, {"$set": {"content": items}})
    await render_content_list(update, context, bid, 0)


async def cb_cont_add(update, context):
    q = update.callback_query
    await safe_answer(q)
    bid = context.matches[0].group(1)
    context.user_data["st"] = ("btn_contadd", str(bid))
    context.user_data["tmp_content"] = []
    context.user_data.pop("tmp_msg", None)
    context.user_data.pop("mi_setup", None)  # leaving wizard context
    await render(
        update, context,
        "➕ <b>Naya Content Bhejo</b> (multiple allowed)\n\n"
        "Ho jane pe <b>✅ Save</b> dabao.",
        kb([[cb("❌ Cancel", "csvn")]]),
    )


# ------------------------------------------------------------------ toggles & delete
async def cb_dir(update, context):
    q = update.callback_query
    bid = ObjectId(context.matches[0].group(1))
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    new = not btn.get("direct")
    await db.buttons.update_one({"_id": bid}, {"$set": {"direct": new}})
    btn["direct"] = new
    # single answer with alert (double-answer kills the alert!)
    if new:
        await safe_answer(q, "✅ Direct ON — ab ye content bina referral ke milega!", alert=True)
    else:
        await safe_answer(q, "🔗 Referral required — link share karke reward milega.", alert=True)
    await render_button(update, context, btn, 0)


async def cb_act(update, context):
    q = update.callback_query
    await safe_answer(q)
    bid = ObjectId(context.matches[0].group(1))
    btn = await get_button(bid)
    if btn:
        new = not btn.get("active", True)
        await db.buttons.update_one({"_id": bid}, {"$set": {"active": new}})
        btn["active"] = new
        await render_button(update, context, btn, 0)


async def cb_del(update, context):
    await safe_answer(update.callback_query)
    bid = context.matches[0].group(1)
    btn = await get_button(ObjectId(bid))
    name = btn["name"] if btn else "ye button"
    await render(
        update, context,
        f"🗑️ <b>Delete “{esc(name)}”?</b>\nIske saare joins data bhi clean ho jayega!",
        confirm_kb(f"btdely_{bid}", f"btv_{bid}_0", "✅ Haan Delete", "❌ Nahi"),
    )


async def cb_del_yes(update, context):
    q = update.callback_query
    await safe_answer(q, "🗑️ Deleted!")
    await delete_button(ObjectId(context.matches[0].group(1)))
    context.user_data.pop("st", None)
    context.user_data.pop("tmp_content", None)
    await render_button_list(update, context, 0)


# ------------------------------------------------------------------ milestones
async def cb_milestones(update, context):
    q = update.callback_query
    await safe_answer(q)
    context.user_data.pop("mi_all", None)
    context.user_data.pop("mi_items", None)
    context.user_data.pop("mi_setup", None)  # manual mode — creation wizard cancelled
    await render_mil_panel(update, context, ObjectId(context.matches[0].group(1)))


async def render_mil_panel(update, context, bid):
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    ms = btn.get("milestones") or {}
    text = (
        f"🎯 <b>Milestones — {esc(btn['name'])}</b>\n\n"
        "Set karo: <b>kitne join pe kya milega</b>. Reward me saved content, "
        "custom text ya dono de sakte ho! 👇"
    )
    rows = []
    cur = [n for n in range(1, 11)]
    line = []
    for n in cur:
        mark = "✨" if str(n) in ms else ""
        line.append(cb(f"{n}{mark}", f"misel_{str(bid)}_{n}"))
        if len(line) == 5:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([cb("⬅️ Back", f"btv_{str(bid)}_0")])
    await render(update, context, text, kb(rows))


async def cb_mi_sel(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    su = context.user_data.get("mi_setup")
    setup = (int(su.get("cur")), int(su.get("total"))) if su and su.get("bid") == m.group(1) else None
    await render_milestone(update, context, ObjectId(m.group(1)), int(m.group(2)), setup=setup)


def _ms_desc(cfg):
    if not cfg or (not cfg.get("content") and not cfg.get("text")):
        return "— Not set —"
    bits = []
    c = cfg.get("content")
    if c == "all":
        bits.append("📦 ALL content")
    elif c:
        bits.append(f"📦 {len(c)} items")
    if cfg.get("text"):
        bits.append("📝 custom text")
    return " + ".join(bits)


async def render_milestone(update, context, bid, n, setup=None):
    """setup=(cur,total) -> creation-wizard mode with progress + skip label."""
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    cfg = (btn.get("milestones") or {}).get(str(n)) or {}
    title_n = f"{n}/{setup[1]}" if setup else str(n)
    prog = " <i>• setup chal raha hai</i>" if setup else ""
    text = (
        f"🎯 <b>Milestone {title_n} join</b> — {esc(btn['name'])}{prog}\n\n"
        f"Current: <b>{_ms_desc(cfg)}</b>\n\n"
        "Kya dena hai is milestone pe? 👇"
    )
    skip_label = "⏭️ Isko Skip Karo" if setup else "🗑️ Remove Milestone"
    rows = [
        [cb("📦 Saved Content", f"mitype_{str(bid)}_{n}_c"), cb("📝 Custom Text", f"mitype_{str(bid)}_{n}_t")],
        [cb("📦 + 📝 Both", f"mitype_{str(bid)}_{n}_b"), cb(skip_label, f"mitype_{str(bid)}_{n}_r")],
        [cb("⬅️ Back", f"btmil_{str(bid)}")],
    ]
    await render(update, context, text, kb(rows))


async def render_mi_items(update, context, bid, n, reset=True):
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    if reset:
        # init selection from SAVED config — ONLY on fresh entry!
        # (cb_mi_toggle re-renders with reset=False, else every tap looks dead)
        cfg = (btn.get("milestones") or {}).get(str(n)) or {}
        content = cfg.get("content") or []
        if content == "all":
            context.user_data["mi_all"] = True
            context.user_data["mi_items"] = set()
        else:
            context.user_data["mi_all"] = False
            context.user_data["mi_items"] = set(content if isinstance(content, list) else [])
    mi_all = bool(context.user_data.get("mi_all"))
    mi_items = context.user_data.get("mi_items") or set()

    items = btn.get("content") or []
    rows = []
    if not items:
        text = "📭 Pehle <b>Content add karo</b> — abhi button me koi item nahi hai!"
        rows.append([cb("📦 Content Add Karo", f"btcadd_{str(bid)}")])
    else:
        all_mark = "✅" if mi_all else "⬜"
        rows.append([cb(f"{all_mark} 🌟 ALL Content", f"mitgl_{str(bid)}_{n}_all")])
        for idx, it in enumerate(items):
            mark = "✅" if (not mi_all and idx in mi_items) else "⬜"
            rows.append([cb(f"{mark} {item_label(it, idx)}", f"mitgl_{str(bid)}_{n}_{idx}")])
        text = f"📦 <b>Select Content</b> — Milestone {n} join\n\nJo items select karoge wahi deliver honge:"
    rows.append([cb("✅ Done", f"midone_{str(bid)}_{n}"), cb("⬅️ Back", f"misel_{str(bid)}_{n}")])
    await render(update, context, text, kb(rows))


async def cb_mi_type(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bid, n, t = ObjectId(m.group(1)), int(m.group(2)), m.group(3)
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    ms = btn.setdefault("milestones", {}) if isinstance(btn.get("milestones"), dict) else {}
    cfg = ms.get(str(n), {})
    su = context.user_data.get("mi_setup")
    setup = (int(su.get("cur")), int(su.get("total"))) if su and su.get("bid") == str(bid) else None
    if t == "r":
        if setup:  # wizard mode: skip = leave as-is, move to next milestone
            return await mi_setup_advance(update, context, bid, n)
        ms.pop(str(n), None)
        await db.buttons.update_one({"_id": bid}, {"$set": {"milestones": ms}})
        return await render_mil_panel(update, context, bid)
    if t in ("c", "b"):
        await render_mi_items(update, context, bid, n)
        if t == "b" and not cfg.get("text"):
            context.user_data["mi_after_text"] = (str(bid), n)
            await send_safe(
                context.bot, update.effective_chat.id,
                "📝 <b>Both</b> select kiya — pehle <b>reward text</b> bhejo "
                "(jo content ke sath jayega):",
            )
            context.user_data["st"] = ("mi_text", str(bid), n)
    else:  # text only
        if cfg.get("text"):
            await render(
                update, context,
                f"📝 Current text:\n\n{cfg['text']}\n\nNaya text bhejo ya ⬅️ Back:",
                kb([[cb("✍️ Text Change Karo", f"mitext_{str(bid)}_{n}")], [cb("⬅️ Back", f"misel_{str(bid)}_{n}")]]),
            )
        else:
            # fresh text-only reward: clear default content so milestone stays text-only
            ms[str(n)] = {"content": [], "text": ""}
            await db.buttons.update_one({"_id": bid}, {"$set": {"milestones": ms}})
            context.user_data["st"] = ("mi_text", str(bid), n)
            await render(update, context, "📝 <b>Reward text bhejo</b> (milestone complete hone pe ye jayega):",
                         kb([[cb("❌ Cancel", "cancel")]]))


async def cb_mi_toggle(update, context):
    q = update.callback_query
    await safe_answer(q)
    m = context.matches[0]
    bid, n, arg = m.group(1), int(m.group(2)), m.group(3)
    if arg == "all":
        context.user_data["mi_all"] = not context.user_data.get("mi_all")
        context.user_data["mi_items"] = set()
    else:
        idx = int(arg)
        sel = context.user_data.setdefault("mi_items", set())
        if idx in sel:
            sel.remove(idx)
        else:
            sel.add(idx)
        context.user_data["mi_all"] = False
    # re-render WITHOUT resetting selection — else every tap would look dead
    await render_mi_items(update, context, ObjectId(bid), n, reset=False)


async def cb_mi_done(update, context):
    q = update.callback_query
    m = context.matches[0]
    bid, n = ObjectId(m.group(1)), int(m.group(2))
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    ms = btn.get("milestones") or {}
    cfg = ms.get(str(n), {})
    text = cfg.get("text", "")
    if context.user_data.get("mi_all"):
        content = "all"
    else:
        content = sorted(context.user_data.get("mi_items", set()))
    context.user_data.pop("mi_all", None)
    context.user_data.pop("mi_items", None)

    ms = btn.setdefault("milestones", {})
    if (content in ("all",) or content) or text:
        ms[str(n)] = {"content": content, "text": text}
        note = f"✅ Milestone {n} set: {_ms_desc(ms[str(n)])}"
    else:
        ms.pop(str(n), None)
        note = f"🗑️ Milestone {n} remove ho gaya (kuch select nahi tha)."
    await db.buttons.update_one({"_id": bid}, {"$set": {"milestones": ms}})
    # single answer with alert — was double-answered before (alert never showed)
    await safe_answer(q, note, alert=True)
    if await mi_setup_advance(update, context, bid, n):
        return
    await render_mil_panel(update, context, bid)


async def cb_mi_text(update, context):
    await safe_answer(update.callback_query)
    m = context.matches[0]
    context.user_data.pop("tmp_content", None)
    context.user_data["st"] = ("mi_text", m.group(1), int(m.group(2)))
    await render(update, context, "📝 <b>Naya reward text bhejo:</b>", kb([[cb("❌ Cancel", "cancel")]]))


# ------------------------------------------------------------------ creation-time milestone wizard
async def render_mi_count(update, context, btn):
    """After first content save of a NEW button: ask how many milestones to configure."""
    bid = str(btn["_id"])
    ms = btn.get("milestones") or {}
    text = (
        f"🎯 <b>“{esc(btn['name'])}” save ho gaya!</b>\n\n"
        "Ab batao — <b>kitne milestones</b> pe reward dena hai?\n"
        "<i>Jaise 3 → 1, 2, 3 join pe alag-alag reward.\n"
        "Baad me 🎯 Milestones se kabhi bhi change kar sakte ho.</i>"
    )
    rows = []
    line = []
    for k in range(1, 11):
        line.append(cb(f"{k}{'✨' if str(k) in ms else ''}", f"misetup_{bid}_{k}"))
        if len(line) == 5:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([cb("⏭️ Skip (Baad me set karunga)", f"misetup_{bid}_0")])
    await render(update, context, text, kb(rows))


async def cb_mi_count(update, context):
    await safe_answer(update.callback_query)
    m = context.matches[0]
    bid_s, n = m.group(1), int(m.group(2))
    bid = ObjectId(bid_s)
    btn = await get_button(bid)
    if not btn:
        return await render(update, context, "❌ Button nahi mila.", back_adm())
    if n < 1:  # skip
        context.user_data.pop("mi_setup", None)
        return await render_button(update, context, btn, 0)
    context.user_data["mi_setup"] = {"bid": bid_s, "cur": 1, "total": n}
    context.user_data.pop("mi_all", None)
    context.user_data.pop("mi_items", None)
    await render_milestone(update, context, bid, 1, setup=(1, n))


async def mi_setup_advance(update, context, bid, n):
    """Advance the creation wizard after a milestone was configured/skipped.
    Returns True if the wizard was active for this button (screen advanced/finished)."""
    su = context.user_data.get("mi_setup")
    if not su or su.get("bid") != str(bid):
        return False
    context.user_data.pop("mi_setup", None)
    cur, total = int(su.get("cur", 1)), int(su.get("total", 1))
    if cur < total:
        context.user_data["mi_setup"] = {"bid": str(bid), "cur": cur + 1, "total": total}
        context.user_data.pop("mi_all", None)
        context.user_data.pop("mi_items", None)
        await render_milestone(update, context, bid, cur + 1, setup=(cur + 1, total))
    else:
        context.user_data.pop("st", None)
        btn = await get_button(bid)
        if btn:
            await render(
                update, context,
                f"🎉 <b>Sab {total} milestones set ho gaye!</b>\n\n"
                "Baad me change karna ho to: Button kholo → 🎯 Milestones:",
                kb([[cb(f"🔘 {esc(btn['name'])} kholo", f"btv_{str(bid)}_0")],
                    [cb("⬅️ Reward Buttons", "adbt_0")]]),
            )
    return True

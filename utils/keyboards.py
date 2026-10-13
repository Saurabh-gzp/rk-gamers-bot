"""Inline keyboard builders + left/right pagination (⬅️ ➡️ shown only when needed)."""
import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def cb(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def url(text, u):
    return InlineKeyboardButton(text, url=u)


def kb(rows):
    return InlineKeyboardMarkup(rows)


def paginate(items, page, per):
    pages = max(1, math.ceil(len(items) / per))
    page = max(0, min(page, pages - 1))
    return items[page * per:(page + 1) * per], page, pages


def nav_row(page, pages, prefix):
    """Single row [⬅️ | page x/y | ➡️] — only when there is more than one page.
    prefix: callback prefix where page gets appended as f"{prefix}_{page}"""
    if pages <= 1:
        return []
    return nav_row_fmt(page, pages, lambda p: f"{prefix}_{p}")


def nav_row_fmt(page, pages, fmt):
    """Pagination row with a callable fmt(page)->callback_data (for compound prefixes)."""
    if pages <= 1:
        return []
    prev_p = (page - 1) % pages
    next_p = (page + 1) % pages
    return [[
        cb("⬅️", fmt(prev_p)),
        cb(f"📄 {page + 1}/{pages}", "noop"),
        cb("➡️", fmt(next_p)),
    ]]


def back_home():
    return kb([[cb("🏠 Main Menu", "home")]])


def back_adm():
    return kb([[cb("⬅️ Admin Panel", "adm")]])


def confirm_kb(yes_data, no_data, yes_text="✅ Haan", no_text="❌ Nahi"):
    return kb([[cb(yes_text, yes_data), cb(no_text, no_data)]])

#!/usr/bin/env python3
"""
Apna AI Store — Telegram Store Bot
===================================
Ek product ka store: ElevenLabs Starter Plan (Telegram Stars payment).

Features (locked spec):
- Force Join: bot tabhi chalega jab user Public Group ka member ho
- Home screen: Welcome text + inline buttons (LetXShop style, 2-per-row grid)
- Product page: pura detail (credits, features, validity, warranty, price, live stock)
- Payment: Telegram Stars (XTR) invoice, delivery sirf successful_payment ke baad
- Manual Delivery: 2-4 ghante (timing sirf payment ke baad show hoti hai)
- Validity 30 din / Warranty 25 din — delivery time se ginna jata hai
- My Orders / My Subscription / Renewal reminders
- Report System: Order ID based, auto-filled details, warranty status ke saath
- NO Refund — sirf Replacement (warranty me)
- Admin Panel: pending orders, deliver, stock, broadcast, stats, reports
- Public Group Lite Guard: dusro ke links auto-delete, welcome message
- Hidden /paysupport: Public Group ka link deta hai (Telegram ki requirement)

Config (.env / Render Environment Variables):
  BOT_TOKEN      - @BotFather wala token (required)
  ADMIN_IDS      - Admin Telegram IDs, comma se alag (e.g. 123456789,987654321)
  GROUP_ID       - Public Group ka numeric ID (-100xxxxxxxxxx). Khali = force-join OFF
  GROUP_LINK     - Public Group ka link (e.g. https://t.me/ApnaStoreGroup)
  DATABASE_URL   - (optional) Postgres URL. Khali ho to SQLite file use hogi.
                   NOTE: Render free plan pe disk reset hota hai — hamesha
                   ke liye Neon/Supabase ka free Postgres lagana best hai.
  PRICE_STARS    - Product price in Stars (default 150)
  INITIAL_STOCK  - Shuruaat ka stock (default 10)
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().lstrip("-").isdigit()
}
GROUP_ID = int(os.environ.get("GROUP_ID", "0") or 0)
GROUP_LINK = os.environ.get("GROUP_LINK", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PRICE_STARS = int(os.environ.get("PRICE_STARS", "150") or 150)
INITIAL_STOCK = int(os.environ.get("INITIAL_STOCK", "10") or 10)
DB_PATH = os.environ.get("DB_PATH", "store.db")

STORE_NAME = "Apna AI Store"
PRODUCT_NAME = "ElevenLabs Starter Plan"
PRODUCT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "product.png")
VALIDITY_DAYS = 30
WARRANTY_DAYS = 25
DELIVERY_HOURS = 4

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("apna-store")

PRODUCT_TEXT = (
    "🔥 <b>ELEVENLABS — STARTER PLAN</b> 🚀\n\n"
    "💎 <b>40,000 Credits Available</b>\n\n"
    "🎬 <b>Video Models:</b>\n"
    "✅ Text-to-Video\n"
    "✅ Image-to-Video\n\n"
    "🎙️ <b>Voice &amp; Audio Models:</b>\n"
    "✅ Text-to-Speech (TTS)\n"
    "✅ Voice Cloning\n"
    "✅ Speech-to-Text (STT)\n"
    "✅ AI Voice Generation\n"
    "✅ Sound Effects Generation\n\n"
    f"⏳ <b>Validity: {VALIDITY_DAYS} Days</b> (delivery ke din se)\n"
    f"🛡️ <b>Warranty: {WARRANTY_DAYS} Days</b> (delivery ke din se)\n"
)

TERMS_TEXT = (
    "📜 <b>Terms — kharidne se pehle padh lo</b>\n\n"
    "1️⃣ Ye ek digital subscription hai — ElevenLabs Starter Plan, 40,000 credits.\n"
    f"2️⃣ Validity {VALIDITY_DAYS} din aur Warranty {WARRANTY_DAYS} din <b>delivery ke time</b> se ginni jayegi.\n"
    "3️⃣ <b>Refund policy nahi hai.</b> Koi dikkat aaye to Warranty period me <b>Replacement</b> milega — Report option se Order ID ke saath report karo.\n"
    "4️⃣ Account ka galat use (spam, resale, sharing rules todna) karne pe account band ho sakta hai — uski zimmedari store ki nahi hogi.\n"
    "5️⃣ Ek order = ek subscription. Order kisi aur ko transfer nahi hoga.\n\n"
    "Agree karte ho to neeche wala button dabao — phir payment khulega."
)

QUICK_GUIDE = (
    "\n\n📖 <b>Quick Start Guide:</b>\n"
    "• Upar diye gaye access se ElevenLabs me login karo.\n"
    "• Credits: Text-to-Speech / Voice Cloning me credits use hote hain — pehle chhota test karke dekho.\n"
    "• Password / account details kisi ke saath share mat karo, warna account band ho sakta hai.\n"
    "• Koi dikkat aaye to bot me <b>Report</b> option se isi Order ID ke saath report karo — Warranty me replacement milega."
)

REPORT_CATEGORIES = {
    "delivery": "⏰ Delivery nahi mili",
    "login": "🔐 Login / Access problem",
    "credits": "💎 Credits kam / khatm",
    "ban": "🚫 Account logout / ban ho gaya",
    "wrong": "📦 Galat cheez mili",
    "other": "❓ Kuch aur problem",
}

# --------------------------------------------------------------------------- #
# Database layer — SQLite (default) ya Postgres (DATABASE_URL ho to)
# --------------------------------------------------------------------------- #
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()


def _connect():
    if USE_PG:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if USE_PG else sql


def db_exec(sql, params=()):
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(_q(sql), tuple(params))
            conn.commit()
        finally:
            conn.close()


def db_insert(sql, params=()):
    """Insert karke nayi row ka id do."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            if USE_PG:
                cur.execute(_q(sql) + " RETURNING id", tuple(params))
                new_id = cur.fetchone()["id"]
            else:
                cur.execute(_q(sql), tuple(params))
                new_id = cur.lastrowid
            conn.commit()
            return new_id
        finally:
            conn.close()


def db_one(sql, params=()):
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(_q(sql), tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def db_all(sql, params=()):
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(_q(sql), tuple(params))
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


def init_db():
    id_col = "BIGSERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    stmts = [
        f"""CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
            terms_accepted INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
            created_at TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS orders(
            id {id_col}, user_id BIGINT, product TEXT, kind TEXT DEFAULT 'new',
            stars INTEGER, charge_id TEXT UNIQUE, provider_charge_id TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT, delivered_at TEXT,
            delivery_note TEXT, reminded3 INTEGER DEFAULT 0, reminded4 INTEGER DEFAULT 0)""",
        f"""CREATE TABLE IF NOT EXISTS subscriptions(
            id {id_col}, user_id BIGINT, order_id INTEGER UNIQUE, product TEXT,
            delivered_at TEXT, validity_end TEXT, warranty_end TEXT,
            renewal_reminded INTEGER DEFAULT 0, warranty_reminded INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1)""",
        f"""CREATE TABLE IF NOT EXISTS reports(
            id {id_col}, user_id BIGINT, order_id INTEGER, category TEXT,
            description TEXT, photo_file_id TEXT, status TEXT DEFAULT 'open',
            created_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)""",
    ]
    for s in stmts:
        db_exec(s)
    if db_one("SELECT value FROM settings WHERE key='stock'") is None:
        db_exec(
            "INSERT INTO settings(key, value) VALUES('stock', ?)",
            (str(INITIAL_STOCK),),
        )
    log.info("DB ready (%s). Admins: %s | Group: %s", "Postgres" if USE_PG else "SQLite", ADMIN_IDS, GROUP_ID)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fmt_dt(s) -> str:
    dt = from_iso(s) if isinstance(s, str) else s
    if not dt:
        return "-"
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p")


def days_left(end_iso) -> int:
    dt = from_iso(end_iso)
    if not dt:
        return 0
    return max(0, (dt - now_utc()).days)


def order_code(order_id: int) -> str:
    return f"EL{1000 + int(order_id)}"


def report_code(report_id: int) -> str:
    return f"RPT-{2000 + int(report_id)}"


def get_stock() -> int:
    row = db_one("SELECT value FROM settings WHERE key='stock'")
    return int(row["value"]) if row else 0


def set_stock(n: int):
    db_exec(
        "INSERT INTO settings(key, value) VALUES('stock', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        if not USE_PG
        else "INSERT INTO settings(key, value) VALUES('stock', %s) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        (str(max(0, n)),),
    )


def get_setting(key, default=None):
    row = db_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def register_user(user) -> dict:
    row = db_one("SELECT * FROM users WHERE user_id=?", (user.id,))
    if row is None:
        db_exec(
            "INSERT INTO users(user_id, username, first_name, created_at) VALUES(?,?,?,?)",
            (user.id, user.username or "", user.first_name or "", to_iso(now_utc())),
        )
        row = db_one("SELECT * FROM users WHERE user_id=?", (user.id,))
    else:
        db_exec(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (user.username or "", user.first_name or "", user.id),
        )
    return row


async def is_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Force-join check. GROUP_ID set na ho to gate OFF (True)."""
    if not GROUP_ID:
        return True
    if is_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(GROUP_ID, user_id)
    except BadRequest as e:
        log.warning("get_chat_member failed (%s) — bot group me admin hai?", e)
        return False
    except Exception as e:  # network etc.
        log.warning("member check error: %s", e)
        return False
    if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return True
    if member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False):
        return True
    return False


# --------------------------------------------------------------------------- #
# Keyboards & screens
# --------------------------------------------------------------------------- #
def home_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🎙️ {PRODUCT_NAME} — ⭐{PRICE_STARS}", callback_data="nav:product")],
        [
            InlineKeyboardButton("📦 My Orders", callback_data="nav:myorders"),
            InlineKeyboardButton("💎 My Subscription", callback_data="nav:mysub"),
        ],
        [
            InlineKeyboardButton("🚨 Report", callback_data="report:new"),
            InlineKeyboardButton("📋 My Reports", callback_data="report:my"),
        ],
    ]
    if GROUP_LINK:
        rows.append([InlineKeyboardButton("👥 Public Group", url=GROUP_LINK)])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def home_text(first_name: str) -> str:
    return (
        f"⚡ <b>Welcome to {STORE_NAME}!</b>\n\n"
        f"Hello {first_name}!\n\n"
        "🛍️ Browse our premium AI services and choose your plan.\n"
        "⭐ Pay securely via Telegram Stars\n"
        "🍀 Trust issues? Visit our Public Group 👥\n\n"
        "Select a product below to get started:"
    )


def join_kb() -> InlineKeyboardMarkup:
    rows = []
    if GROUP_LINK:
        rows.append([InlineKeyboardButton("👥 Public Group Join Karo", url=GROUP_LINK)])
    rows.append([InlineKeyboardButton("✅ Join Kar Liya — Verify Karo", callback_data="join:verify")])
    return InlineKeyboardMarkup(rows)


JOIN_TEXT = (
    f"🔒 <b>{STORE_NAME} abhi locked hai!</b>\n\n"
    "Bot use karne ke liye pehle hamara <b>Public Group</b> join karna zaroori hai — "
    "wahi sab buyers baat karte hain, reviews aur delivery proofs wahi milte hain.\n\n"
    "1️⃣ Neeche wala button dabake group join karo\n"
    "2️⃣ Phir wapas aake <b>Verify</b> dabao — store khul jayega 🚀"
)


def back_kb(target="nav:home", label="🏠 Home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=target)]])


def product_kb() -> InlineKeyboardMarkup:
    stock = get_stock()
    rows = []
    if stock > 0:
        rows.append([InlineKeyboardButton(f"⭐ Buy Now — {PRICE_STARS} Stars", callback_data="buy:start")])
    else:
        rows.append([InlineKeyboardButton("❌ Out of Stock", callback_data="noop")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
    return InlineKeyboardMarkup(rows)


def product_text() -> str:
    stock = get_stock()
    stock_line = "📦 <b>Stock: Available ✅</b>" if stock > 0 else "📦 <b>Stock: Khatm ❌</b> — thoda wait karo, refill hote hi khulega."
    return (
        PRODUCT_TEXT
        + f"{stock_line}\n\n"
        + f"💰 Price: <s>₹599</s> <b>⭐{PRICE_STARS} Stars</b>"
    )


async def send_product(chat_send, user_id: int):
    """Product page photo ke saath bhejo (photo = admin-set file_id ya bundled asset)."""
    text = product_text()
    kb = product_kb()
    photo_id = get_setting("product_photo_file_id")
    try:
        if photo_id:
            await chat_send.send_photo(photo=photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
            return
        if os.path.exists(PRODUCT_FILE):
            with open(PRODUCT_FILE, "rb") as f:
                await chat_send.send_photo(photo=f, caption=text, parse_mode="HTML", reply_markup=kb)
            return
    except Exception as e:
        log.warning("product photo failed: %s", e)
    await chat_send.send_message(text=text, parse_mode="HTML", reply_markup=kb)


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = home_text(user.first_name or "Dost")
    kb = home_kb(user.id)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except BadRequest:
            pass  # photo wale message ko edit nahi kar sakte — naya bhej do
    await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)


async def show_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(JOIN_TEXT, parse_mode="HTML", reply_markup=join_kb())
            return
        except BadRequest:
            pass
    await context.bot.send_message(update.effective_chat.id, JOIN_TEXT, parse_mode="HTML", reply_markup=join_kb())


# --------------------------------------------------------------------------- #
# Basic commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = register_user(update.effective_user)
    if user.get("is_banned"):
        await update.message.reply_text("🚫 Tumhara account is store se banned hai.")
        return
    if not await is_group_member(context, user["user_id"]):
        await show_join(update, context)
        return
    await show_home(update, context)


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = register_user(update.effective_user)
    if not await is_group_member(context, user["user_id"]):
        await show_join(update, context)
        return
    await send_product(context.bot, user["user_id"])


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = GROUP_LINK or "Public Group"
    await update.message.reply_text(
        f"💳 Payment me dikkat? Hamare Public Group me aake admin se baat karo: {link}\n"
        "Order ki dikkat ho to bot me 🚨 Report option use karo (Order ID ke saath)."
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_admin_panel(update, context)


# --------------------------------------------------------------------------- #
# Navigation callbacks
# --------------------------------------------------------------------------- #
async def cb_join_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_group_member(context, q.from_user.id):
        await q.answer("✅ Verify ho gaya! Store khul gaya 🚀", show_alert=True)
        await show_home(update, context)
    else:
        await q.answer("❌ Abhi group member nahi mile. Pehle join karo, phir verify dabao.", show_alert=True)


async def cb_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = register_user(q.from_user)

    # Force-join gate: har screen se pehle check (home/join khud exempt hai)
    if data not in ("nav:home",) and not await is_group_member(context, user["user_id"]):
        await show_join(update, context)
        return

    if data == "nav:home":
        if not await is_group_member(context, user["user_id"]):
            await show_join(update, context)
            return
        await show_home(update, context)
    elif data == "nav:product":
        try:
            await q.delete_message()
        except Exception:
            pass
        await send_product(context.bot, user["user_id"])
    elif data == "nav:myorders":
        await show_my_orders(update, context, user["user_id"])
    elif data == "nav:mysub":
        await show_my_subscription(update, context, user["user_id"])
    elif data == "noop":
        pass


async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    orders = db_all(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)
    )
    if not orders:
        text = "📦 <b>My Orders</b>\n\nAbhi tak koi order nahi hai. Pehle product kharido!"
        kb = back_kb()
    else:
        lines = ["📦 <b>My Orders</b>\n"]
        rows = []
        for o in orders:
            emoji = "⏳" if o["status"] == "pending" else "✅" if o["status"] == "delivered" else "❌"
            lines.append(
                f"{emoji} <b>{order_code(o['id'])}</b> — {o['product']}\n"
                f"    ⭐{o['stars']} Stars • {fmt_dt(o['created_at'])} • Status: <b>{o['status'].title()}</b>"
            )
            rows.append(
                [
                    InlineKeyboardButton(f"👁️ {order_code(o['id'])}", callback_data=f"order:view:{o['id']}"),
                    InlineKeyboardButton("🚨 Report", callback_data=f"report:order:{o['id']}"),
                ]
            )
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(rows)
    try:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)


async def cb_order_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = int(q.data.split(":")[2])
    o = db_one("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, q.from_user.id))
    if not o:
        await q.answer("Order nahi mila.", show_alert=True)
        return
    lines = [
        f"🧾 <b>Order {order_code(o['id'])}</b>\n",
        f"Product: {o['product']}",
        f"Paid: ⭐{o['stars']} Stars",
        f"Order Time: {fmt_dt(o['created_at'])}",
        f"Status: <b>{o['status'].title()}</b>",
    ]
    if o["status"] == "pending":
        deadline = from_iso(o["created_at"]) + timedelta(hours=DELIVERY_HOURS)
        lines.append(f"⏳ Delivery Time: <b>2-4 Hours</b> (deadline {deadline.astimezone(IST).strftime('%I:%M %p')})")
        lines.append("Delivery isi chat me hogi — please wait karo. 🙏")
    if o.get("delivered_at"):
        lines.append(f"Delivered: {fmt_dt(o['delivered_at'])}")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚨 Is Order ki Report", callback_data=f"report:order:{o['id']}")],
            [InlineKeyboardButton("📦 My Orders", callback_data="nav:myorders")],
            [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
        ]
    )
    try:
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(update.effective_chat.id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def show_my_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    sub = db_one(
        "SELECT * FROM subscriptions WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if not sub:
        text = "💎 <b>My Subscription</b>\n\nAbhi koi active subscription nahi hai.\nProduct kharido, delivery ke baad yaha dikhega."
        kb = back_kb()
    else:
        v_left = days_left(sub["validity_end"])
        w_left = days_left(sub["warranty_end"])
        text = (
            "💎 <b>My Subscription</b>\n\n"
            f"Product: {sub['product']}\n"
            f"Order: {order_code(sub['order_id'])}\n"
            f"Delivered: {fmt_dt(sub['delivered_at'])}\n"
            f"⏳ Validity: <b>{v_left} din bache</b> ({fmt_dt(sub['validity_end'])} tak)\n"
            f"🛡️ Warranty: <b>{w_left} din bache</b> ({fmt_dt(sub['warranty_end'])} tak)"
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"🔄 Renew Karo — ⭐{PRICE_STARS}", callback_data="buy:renew")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        )
    try:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)


# --------------------------------------------------------------------------- #
# Stars payment flow
# --------------------------------------------------------------------------- #
async def send_stars_invoice(update_or_query, context, user_id: int, kind: str):
    chat_id = update_or_query.effective_chat.id if hasattr(update_or_query, "effective_chat") else user_id
    payload = f"{kind}:{user_id}:{int(now_utc().timestamp())}"
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=f"{PRODUCT_NAME}",
        description=f"{PRODUCT_NAME} — 40,000 Credits, {VALIDITY_DAYS} Days Validity, {WARRANTY_DAYS} Days Warranty",
        payload=payload,
        provider_token="",  # Telegram Stars ke liye khali rehta hai
        currency="XTR",
        prices=[LabeledPrice(f"{PRODUCT_NAME} — {VALIDITY_DAYS} Days", PRICE_STARS)],
    )


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = register_user(q.from_user)

    if user.get("is_banned"):
        await q.answer("🚫 Tumhara account banned hai.", show_alert=True)
        return
    if not await is_group_member(context, user["user_id"]):
        await show_join(update, context)
        return

    kind = "renew" if q.data == "buy:renew" else "new"
    context.user_data["buy_kind"] = kind

    # Terms gate: pehli kharid se pehle ek baar terms accept karna hoga
    if not user.get("terms_accepted"):
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ I Agree — Aage Badho", callback_data="buy:terms")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        )
        try:
            await q.edit_message_text(TERMS_TEXT, parse_mode="HTML", reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(q.message.chat_id, TERMS_TEXT, parse_mode="HTML", reply_markup=kb)
        return

    if get_stock() <= 0:
        await q.answer("❌ Stock khatm hai! Thoda wait karo.", show_alert=True)
        return
    await send_stars_invoice(q, context, user["user_id"], kind)


async def cb_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("✅ Terms accept ho gaye!")
    db_exec("UPDATE users SET terms_accepted=1 WHERE user_id=?", (q.from_user.id,))
    if get_stock() <= 0:
        await q.answer("❌ Stock khatm hai! Thoda wait karo.", show_alert=True)
        return
    kind = context.user_data.get("buy_kind", "new")
    await send_stars_invoice(q, context, q.from_user.id, kind)


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pq = update.pre_checkout_query
    parts = (pq.invoice_payload or "").split(":")
    ok = (
        len(parts) == 3
        and parts[1].isdigit()
        and int(parts[1]) == pq.from_user.id
        and pq.currency == "XTR"
        and pq.total_amount == PRICE_STARS
        and get_stock() > 0
    )
    if ok:
        await pq.answer(ok=True)
    else:
        await pq.answer(ok=False, error_message="Stock ya price badal gaya hai — dobara try karo.")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    user = register_user(update.effective_user)

    # Duplicate protection: ek Charge ID pe ek hi order
    existing = db_one("SELECT id FROM orders WHERE charge_id=?", (sp.telegram_payment_charge_id,))
    if existing:
        log.info("Duplicate payment callback ignored: %s", sp.telegram_payment_charge_id)
        return

    parts = (sp.invoice_payload or "new").split(":")
    kind = parts[0] if parts and parts[0] in ("new", "renew") else "new"

    oid = db_insert(
        "INSERT INTO orders(user_id, product, kind, stars, charge_id, provider_charge_id, status, created_at) "
        "VALUES(?,?,?,?,?,?, 'pending', ?)",
        (
            user["user_id"],
            PRODUCT_NAME,
            kind,
            sp.total_amount,
            sp.telegram_payment_charge_id,
            sp.provider_payment_charge_id or "",
            to_iso(now_utc()),
        ),
    )
    if get_stock() > 0:
        set_stock(get_stock() - 1)

    code = order_code(oid)
    deadline = (now_utc() + timedelta(hours=DELIVERY_HOURS)).astimezone(IST).strftime("%I:%M %p")
    await update.message.reply_text(
        "🎉 <b>Payment Successful!</b>\n\n"
        f"Order ID: <b>{code}</b>\n"
        f"Paid: ⭐{sp.total_amount} Stars\n"
        f"⏳ <b>Delivery Time: 2-4 Hours</b> (deadline {deadline})\n"
        "Status: ⏳ Processing…\n\n"
        "Delivery isi chat me hogi, tab tak please wait karo. 🙏",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📦 Order Status Dekho", callback_data=f"order:view:{oid}")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        ),
    )
    await notify_admins_new_order(context, oid)


async def notify_admins_new_order(context: ContextTypes.DEFAULT_TYPE, oid: int):
    o = db_one("SELECT * FROM orders WHERE id=?", (oid,))
    if not o:
        return
    u = db_one("SELECT * FROM users WHERE user_id=?", (o["user_id"],)) or {}
    deadline = (from_iso(o["created_at"]) + timedelta(hours=DELIVERY_HOURS)).astimezone(IST).strftime("%I:%M %p")
    text = (
        "🔔 <b>NAYA ORDER — PAYMENT MIL GAYI!</b>\n\n"
        f"Order ID: <b>{order_code(o['id'])}</b>" + (" (RENEWAL)" if o.get("kind") == "renew" else "") + "\n"
        f"User: {u.get('first_name', '')} (@{u.get('username', '-')})\n"
        f"User ID: <code>{o['user_id']}</code>\n"
        f"Product: {o['product']}\n"
        f"Paid: ⭐{o['stars']} Stars ✅\n"
        f"Time: {fmt_dt(o['created_at'])}\n"
        "Status: ⏳ Pending Delivery\n"
        f"⚠️ Deadline: <b>{deadline}</b> (4 ghante)"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Deliver Karo", callback_data=f"admin:deliver:{o['id']}")],
            [InlineKeyboardButton("📋 Pending Orders", callback_data="admin:pending")],
        ]
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            log.warning("admin notify failed %s: %s", aid, e)


# --------------------------------------------------------------------------- #
# Delivery (admin) — subscription yahi se shuru hota hai
# --------------------------------------------------------------------------- #
async def do_delivery(context: ContextTypes.DEFAULT_TYPE, oid: int, admin_msg, note_text: str = ""):
    o = db_one("SELECT * FROM orders WHERE id=?", (oid,))
    if not o or o["status"] != "pending":
        return False
    delivered_at = now_utc()
    db_exec(
        "UPDATE orders SET status='delivered', delivered_at=?, delivery_note=? WHERE id=?",
        (to_iso(delivered_at), note_text[:200] if note_text else "", oid),
    )
    # Purani active subscription band karo, nayi banao (dates delivery se)
    db_exec("UPDATE subscriptions SET active=0 WHERE user_id=? AND active=1", (o["user_id"],))
    validity_end = delivered_at + timedelta(days=VALIDITY_DAYS)
    warranty_end = delivered_at + timedelta(days=WARRANTY_DAYS)
    db_insert(
        "INSERT INTO subscriptions(user_id, order_id, product, delivered_at, validity_end, warranty_end) "
        "VALUES(?,?,?,?,?,?)",
        (o["user_id"], oid, o["product"], to_iso(delivered_at), to_iso(validity_end), to_iso(warranty_end)),
    )

    header = (
        "✅ <b>Delivery Ho Gayi!</b>\n\n"
        f"Order ID: <b>{order_code(oid)}</b>\n"
        f"Product: {o['product']}\n"
        f"⏳ Validity: {fmt_dt(to_iso(validity_end))} tak ({VALIDITY_DAYS} din)\n"
        f"🛡️ Warranty: {fmt_dt(to_iso(warranty_end))} tak ({WARRANTY_DAYS} din)\n"
        "⬇️ <b>Tumhara Access:</b>\n"
    )
    try:
        if admin_msg is not None and admin_msg.text:
            await context.bot.send_message(
                o["user_id"], header + admin_msg.text + QUICK_GUIDE, parse_mode="HTML"
            )
        elif admin_msg is not None:
            await context.bot.send_message(o["user_id"], header, parse_mode="HTML")
            await admin_msg.copy(o["user_id"])
            await context.bot.send_message(o["user_id"], QUICK_GUIDE.strip(), parse_mode="HTML")
        else:
            await context.bot.send_message(o["user_id"], header + QUICK_GUIDE, parse_mode="HTML")
    except Exception as e:
        log.warning("delivery send failed for order %s: %s", oid, e)
        return False
    return True


# --------------------------------------------------------------------------- #
# Report system (Order ID based)
# --------------------------------------------------------------------------- #
async def cb_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id

    if data == "report:my":
        reps = db_all("SELECT * FROM reports WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
        if not reps:
            text = "📋 <b>My Reports</b>\n\nAbhi koi report nahi hai."
            kb = back_kb()
        else:
            lines = ["📋 <b>My Reports</b>\n"]
            for r in reps:
                lines.append(
                    f"• <b>{report_code(r['id'])}</b> — Order {order_code(r['order_id'])}\n"
                    f"  {REPORT_CATEGORIES.get(r['category'], r['category'])} • Status: <b>{r['status'].title()}</b>"
                )
            text = "\n".join(lines)
            kb = back_kb()
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=kb)
        return

    if data == "report:new":
        orders = db_all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
        if not orders:
            await q.answer("Pehle koi order karo — bina order ke report nahi banti. Aam sawal ke liye Public Group hai.", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton(f"{order_code(o['id'])} — {o['status'].title()}", callback_data=f"report:order:{o['id']}")]
            for o in orders
        ]
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
        try:
            await q.edit_message_text(
                "🚨 <b>Report — pehle apna Order chuno:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
            )
        except BadRequest:
            await context.bot.send_message(
                q.message.chat_id, "🚨 <b>Report — pehle apna Order chuno:</b>",
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
            )
        return

    if data.startswith("report:order:"):
        oid = int(data.split(":")[2])
        o = db_one("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, uid))
        if not o:
            await q.answer("Ye order tumhara nahi hai.", show_alert=True)
            return
        open_rep = db_one("SELECT id FROM reports WHERE order_id=? AND status='open'", (oid,))
        if open_rep:
            await q.answer(f"Is order pe pehle se ek report open hai ({report_code(open_rep['id'])}). Woh band hone ke baad nayi report banna sakti hai.", show_alert=True)
            return
        context.user_data["report_order"] = oid
        rows = [
            [InlineKeyboardButton(label, callback_data=f"report:cat:{key}")]
            for key, label in REPORT_CATEGORIES.items()
        ]
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
        await q.edit_message_text(
            f"🚨 <b>Report — Order {order_code(oid)}</b>\n\nProblem kis type ki hai? Button dabao:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("report:cat:"):
        if "report_order" not in context.user_data:
            await q.answer("Pehle order chuno.", show_alert=True)
            return
        context.user_data["report_category"] = data.split(":")[2]
        context.user_data["awaiting"] = {"kind": "report_desc"}
        await q.edit_message_text(
            "✍️ Ab apni problem <b>likh ke bhejo</b> — kya dikkat aa rahi hai, saaf-saaf likho.\n"
            "(Agla step: screenshot manga jayega — hai to bhejna, warna skip kar dena.)",
            parse_mode="HTML",
        )
        return

    if data == "report:skipphoto":
        if context.user_data.get("awaiting", {}).get("kind") == "report_photo":
            context.user_data["report_photo"] = None
            await finalize_report(update, context)
        return


async def finalize_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    oid = context.user_data.pop("report_order", None)
    cat = context.user_data.pop("report_category", "other")
    desc = context.user_data.pop("report_desc", "")
    photo = context.user_data.pop("report_photo", None)
    context.user_data.pop("awaiting", None)
    if not oid:
        return
    rid = db_insert(
        "INSERT INTO reports(user_id, order_id, category, description, photo_file_id, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (uid, oid, cat, desc, photo, to_iso(now_utc())),
    )
    await context.bot.send_message(
        uid,
        f"✅ <b>Report mil gayi!</b>\n\nReport ID: <b>{report_code(rid)}</b>\nOrder: {order_code(oid)}\n"
        "Status: ⏳ Open — admin check karke yahi jawab dega. Status <b>My Reports</b> me dikhta rahega.",
        parse_mode="HTML", reply_markup=back_kb(),
    )
    # --- Admin ko report, order ki puri detail ke saath ---
    o = db_one("SELECT * FROM orders WHERE id=?", (oid,)) or {}
    u = db_one("SELECT * FROM users WHERE user_id=?", (uid,)) or {}
    sub = db_one("SELECT * FROM subscriptions WHERE order_id=?", (oid,))
    if sub:
        w_left = days_left(sub["warranty_end"])
        warranty_line = f"🛡️ Warranty: <b>{w_left} din bache</b> ({fmt_dt(sub['warranty_end'])} tak)"
    elif o.get("status") == "delivered":
        warranty_line = "🛡️ Warranty: subscription record nahi mila"
    else:
        warranty_line = "🛡️ Warranty: abhi delivery hi nahi hui (delivery ke baad ginni jayegi)"
    total_orders = db_one("SELECT COUNT(*) AS c FROM orders WHERE user_id=?", (uid,))["c"]
    text = (
        "🚨 <b>NAYI REPORT!</b>\n\n"
        f"Report ID: <b>{report_code(rid)}</b>\n"
        f"Order ID: <b>{order_code(oid)}</b> ({o.get('product', '')})\n"
        f"User: {u.get('first_name', '')} (@{u.get('username', '-')}) — <code>{uid}</code>\n"
        f"Category: {REPORT_CATEGORIES.get(cat, cat)}\n"
        f"Problem: {desc}\n\n"
        f"Paid: ⭐{o.get('stars', '-')} Stars • Order Time: {fmt_dt(o.get('created_at'))}\n"
        f"Delivery: {fmt_dt(o.get('delivered_at')) if o.get('delivered_at') else 'Abhi pending'}\n"
        f"{warranty_line}\n"
        f"Customer: total {total_orders} order(s)"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Reply Karo", callback_data=f"admin:rreply:{rid}")],
            [InlineKeyboardButton("🔄 Replacement Do", callback_data=f"admin:rreplace:{rid}")],
            [InlineKeyboardButton("✅ Report Close", callback_data=f"admin:rclose:{rid}")],
        ]
    )
    for aid in ADMIN_IDS:
        try:
            if photo:
                await context.bot.send_photo(aid, photo=photo, caption=text, parse_mode="HTML", reply_markup=kb)
            else:
                await context.bot.send_message(aid, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            log.warning("report admin notify failed: %s", e)


async def show_my_reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    reps = db_all("SELECT * FROM reports WHERE user_id=? ORDER BY id DESC LIMIT 10", (update.effective_user.id,))
    if not reps:
        await update.message.reply_text("📋 Abhi koi report nahi hai.")
        return
    lines = ["📋 <b>My Reports</b>\n"]
    for r in reps:
        lines.append(f"• <b>{report_code(r['id'])}</b> — {order_code(r['order_id'])} • {r['status'].title()}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# --------------------------------------------------------------------------- #
# Admin panel
# --------------------------------------------------------------------------- #
def admin_panel_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Pending Orders", callback_data="admin:pending")],
            [
                InlineKeyboardButton("📊 Stats", callback_data="admin:stats"),
                InlineKeyboardButton("📦 Stock", callback_data="admin:stock"),
            ],
            [
                InlineKeyboardButton("🚨 Open Reports", callback_data="admin:reports"),
                InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast"),
            ],
            [InlineKeyboardButton("🖼️ Product Photo Set Karo", callback_data="admin:setphoto")],
            [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
        ]
    )


async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='pending'")["c"]
    open_reps = db_one("SELECT COUNT(*) AS c FROM reports WHERE status='open'")["c"]
    text = (
        "🔐 <b>Admin Panel — Apna AI Store</b>\n\n"
        f"⏳ Pending Orders: <b>{pending}</b>\n"
        f"🚨 Open Reports: <b>{open_reps}</b>\n"
        f"📦 Stock: <b>{get_stock()}</b>"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
            return
        except BadRequest:
            pass
    await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=admin_panel_kb())


async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("🚫 Ye sirf admin ke liye hai.", show_alert=True)
        return
    await q.answer()
    data = q.data

    if data == "admin:panel":
        await send_admin_panel(update, context)

    elif data == "admin:pending":
        orders = db_all("SELECT * FROM orders WHERE status='pending' ORDER BY id ASC LIMIT 15")
        if not orders:
            text, rows = "📋 <b>Pending Orders</b>\n\nKoi pending order nahi hai. 🎉", []
        else:
            lines, rows = ["📋 <b>Pending Orders</b>\n"], []
            for o in orders:
                age_h = (now_utc() - from_iso(o["created_at"])).total_seconds() / 3600
                lines.append(f"• <b>{order_code(o['id'])}</b> — ⭐{o['stars']} • {age_h:.1f} ghante ho gaye")
                rows.append([InlineKeyboardButton(f"✅ Deliver {order_code(o['id'])}", callback_data=f"admin:deliver:{o['id']}")])
            text = "\n".join(lines)
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")])
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("admin:deliver:"):
        oid = int(data.split(":")[2])
        o = db_one("SELECT * FROM orders WHERE id=?", (oid,))
        if not o or o["status"] != "pending":
            await q.answer("Ye order pending nahi hai (shayad deliver ho chuka hai).", show_alert=True)
            return
        context.user_data["awaiting"] = {"kind": "deliver", "order_id": oid}
        await context.bot.send_message(
            q.from_user.id,
            f"📦 <b>Delivery — Order {order_code(oid)}</b>\n\n"
            "Ab customer ko bhejne wala <b>access / details likh ke (ya photo/file) bhejo</b> — "
            "bot wahi cheez customer ko delivery bana ke bhej dega.\nCancel karna ho to /cancel bhejo.",
            parse_mode="HTML",
        )

    elif data == "admin:stats":
        users_c = db_one("SELECT COUNT(*) AS c FROM users")["c"]
        orders_c = db_one("SELECT COUNT(*) AS c FROM orders")["c"]
        delivered = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='delivered'")["c"]
        stars_sum = db_one("SELECT COALESCE(SUM(stars),0) AS s FROM orders")["s"]
        bal_line = ""
        try:
            bal = await context.bot.get_my_star_balance()
            bal_line = f"\n⭐ Bot Stars Balance: <b>{getattr(bal, 'amount', bal)}</b>"
        except Exception:
            pass
        await q.edit_message_text(
            "📊 <b>Store Stats</b>\n\n"
            f"👥 Total Users: <b>{users_c}</b>\n"
            f"🧾 Total Orders: <b>{orders_c}</b> (Delivered: {delivered})\n"
            f"⭐ Total Stars Earned: <b>{stars_sum}</b>{bal_line}\n"
            f"📦 Stock Bacha: <b>{get_stock()}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")]]),
        )

    elif data == "admin:stock":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ Stock Set Karo", callback_data="admin:stockset")],
                [InlineKeyboardButton("➕ Stock Add Karo", callback_data="admin:stockadd")],
                [InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")],
            ]
        )
        await q.edit_message_text(f"📦 <b>Stock</b>\n\nAbhi stock: <b>{get_stock()}</b>", parse_mode="HTML", reply_markup=kb)

    elif data in ("admin:stockset", "admin:stockadd"):
        mode = "set" if data.endswith("stockset") else "add"
        context.user_data["awaiting"] = {"kind": "stock", "mode": mode}
        await context.bot.send_message(
            q.from_user.id,
            "🔢 Sirf <b>number</b> bhejo — " + ("utna stock SET ho jayega." if mode == "set" else "utna stock ADD ho jayega."),
            parse_mode="HTML",
        )

    elif data == "admin:setphoto":
        context.user_data["awaiting"] = {"kind": "product_photo"}
        await context.bot.send_message(q.from_user.id, "🖼️ Product ki nayi photo bhejo — product page pe wahi dikhegi.")

    elif data == "admin:broadcast":
        context.user_data["awaiting"] = {"kind": "broadcast"}
        await context.bot.send_message(
            q.from_user.id,
            "📣 <b>Broadcast</b> — jo message (text/photo) bhejoge, woh <b>sab users</b> ko jayega. Dhyaan se bhejna!",
            parse_mode="HTML",
        )

    elif data == "admin:reports":
        reps = db_all("SELECT * FROM reports WHERE status='open' ORDER BY id ASC LIMIT 15")
        if not reps:
            text, rows = "🚨 <b>Open Reports</b>\n\nKoi open report nahi hai. 🎉", []
        else:
            lines, rows = ["🚨 <b>Open Reports</b>\n"], []
            for r in reps:
                lines.append(f"• <b>{report_code(r['id'])}</b> — {order_code(r['order_id'])} — {REPORT_CATEGORIES.get(r['category'], '')}")
                rows.append([InlineKeyboardButton(f"👁️ {report_code(r['id'])}", callback_data=f"admin:report:{r['id']}")])
            text = "\n".join(lines)
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")])
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("admin:report:"):
        rid = int(data.split(":")[2])
        r = db_one("SELECT * FROM reports WHERE id=?", (rid,))
        if not r:
            return
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💬 Reply Karo", callback_data=f"admin:rreply:{rid}")],
                [InlineKeyboardButton("🔄 Replacement Do", callback_data=f"admin:rreplace:{rid}")],
                [InlineKeyboardButton("✅ Report Close", callback_data=f"admin:rclose:{rid}")],
            ]
        )
        await context.bot.send_message(
            q.from_user.id,
            f"🚨 <b>{report_code(rid)}</b> — Order {order_code(r['order_id'])}\n"
            f"Category: {REPORT_CATEGORIES.get(r['category'], '')}\nProblem: {r['description']}\nStatus: {r['status']}",
            parse_mode="HTML", reply_markup=kb,
        )

    elif data.startswith("admin:rreply:"):
        rid = int(data.split(":")[2])
        context.user_data["awaiting"] = {"kind": "report_reply", "report_id": rid}
        await context.bot.send_message(q.from_user.id, f"💬 <b>{report_code(rid)}</b> — customer ko bhejne wala jawab likho:", parse_mode="HTML")

    elif data.startswith("admin:rreplace:"):
        rid = int(data.split(":")[2])
        context.user_data["awaiting"] = {"kind": "report_replace", "report_id": rid}
        await context.bot.send_message(
            q.from_user.id,
            f"🔄 <b>{report_code(rid)}</b> — replacement access/details bhejo, bot customer ko bhej dega aur report band kar dega:",
            parse_mode="HTML",
        )

    elif data.startswith("admin:rclose:"):
        rid = int(data.split(":")[2])
        r = db_one("SELECT * FROM reports WHERE id=?", (rid,))
        if r:
            db_exec("UPDATE reports SET status='closed' WHERE id=?", (rid,))
            await q.answer("✅ Report close ho gayi.")
            try:
                await context.bot.send_message(
                    r["user_id"],
                    f"✅ Tumhari report <b>{report_code(rid)}</b> (Order {order_code(r['order_id'])}) admin ne close kar di hai.",
                    parse_mode="HTML",
                )
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Pending-input dispatcher (delivery, report steps, broadcast, stock...)
# --------------------------------------------------------------------------- #
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("❌ Cancel ho gaya.")


async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    state = context.user_data.get("awaiting")
    if not state:
        return
    kind = state.get("kind")
    uid = msg.from_user.id

    # ---- Report: description (sirf text) ----
    if kind == "report_desc":
        if not msg.text:
            await msg.reply_text("✍️ Problem likh ke bhejo (text me).")
            return
        context.user_data["report_desc"] = msg.text.strip()
        context.user_data["awaiting"] = {"kind": "report_photo"}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Screenshot Nahi Hai — Skip", callback_data="report:skipphoto")]])
        await msg.reply_text(
            "📸 Ab <b>screenshot bhejo</b> (error/problem ka photo) — ya Skip dabao:",
            parse_mode="HTML", reply_markup=kb,
        )
        return

    # ---- Report: photo ----
    if kind == "report_photo":
        if msg.photo:
            context.user_data["report_photo"] = msg.photo[-1].file_id
            await finalize_report(update, context)
        else:
            await msg.reply_text("📸 Photo bhejo, ya upar wala Skip button dabao.")
        return

    # ---- Neeche wale sirf ADMIN ke liye ----
    if not is_admin(uid):
        context.user_data.pop("awaiting", None)
        return

    if kind == "deliver":
        oid = state["order_id"]
        ok = await do_delivery(context, oid, msg, note_text=msg.text or "media")
        context.user_data.pop("awaiting", None)
        await msg.reply_text(
            f"✅ Order {order_code(oid)} DELIVERED — customer ko bhej diya gaya, validity/warranty aaj se shuru."
            if ok else f"⚠️ Order {order_code(oid)} deliver nahi hua (shayad pehle hi delivered hai)."
        )
        return

    if kind == "report_reply":
        rid = state["report_id"]
        r = db_one("SELECT * FROM reports WHERE id=?", (rid,))
        context.user_data.pop("awaiting", None)
        if r and msg.text:
            try:
                await context.bot.send_message(
                    r["user_id"],
                    f"💬 <b>Admin ka jawab — Report {report_code(rid)}</b> (Order {order_code(r['order_id'])}):\n\n{msg.text}",
                    parse_mode="HTML",
                )
                await msg.reply_text("✅ Jawab customer ko bhej diya.")
            except Exception as e:
                await msg.reply_text(f"⚠️ Jawab nahi gaya: {e}")
        return

    if kind == "report_replace":
        rid = state["report_id"]
        r = db_one("SELECT * FROM reports WHERE id=?", (rid,))
        context.user_data.pop("awaiting", None)
        if r:
            header = f"🔄 <b>Replacement Mil Gaya!</b>\n\nReport: {report_code(rid)} • Order: {order_code(r['order_id'])}\n⬇️ <b>Naya Access:</b>\n"
            try:
                if msg.text:
                    await context.bot.send_message(r["user_id"], header + msg.text, parse_mode="HTML")
                else:
                    await context.bot.send_message(r["user_id"], header, parse_mode="HTML")
                    await msg.copy(r["user_id"])
                db_exec("UPDATE reports SET status='resolved' WHERE id=?", (rid,))
                await msg.reply_text("✅ Replacement bhej diya, report resolved mark ho gayi.")
            except Exception as e:
                await msg.reply_text(f"⚠️ Replacement nahi gaya: {e}")
        return

    if kind == "stock":
        if not (msg.text and msg.text.strip().lstrip("-").isdigit()):
            await msg.reply_text("🔢 Sirf number bhejo.")
            return
        n = int(msg.text.strip())
        if state.get("mode") == "add":
            set_stock(get_stock() + n)
        else:
            set_stock(n)
        context.user_data.pop("awaiting", None)
        await msg.reply_text(f"✅ Stock update ho gaya. Abhi stock: <b>{get_stock()}</b>", parse_mode="HTML")
        return

    if kind == "product_photo":
        if msg.photo:
            db_exec(
                "INSERT INTO settings(key, value) VALUES('product_photo_file_id', ?) "
                + ("ON CONFLICT(key) DO UPDATE SET value=excluded.value" if not USE_PG else "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"),
                (msg.photo[-1].file_id,),
            )
            context.user_data.pop("awaiting", None)
            await msg.reply_text("✅ Product photo set ho gayi — product page pe ab yahi dikhegi.")
        else:
            await msg.reply_text("🖼️ Photo bhejo.")
        return

    if kind == "broadcast":
        context.user_data.pop("awaiting", None)
        users = db_all("SELECT user_id FROM users")
        sent = 0
        for u in users:
            try:
                await msg.copy(u["user_id"])
                sent += 1
            except Exception:
                pass
        await msg.reply_text(f"📣 Broadcast ho gaya — {sent}/{len(users)} users ko gaya.")
        return


# --------------------------------------------------------------------------- #
# Public Group Lite Guard
# --------------------------------------------------------------------------- #
def _has_link(msg) -> bool:
    text = (msg.text or msg.caption or "")
    low = text.lower()
    if "http://" in low or "https://" in low or "www." in low or "t.me/" in low:
        return True
    for ent in (msg.entities or msg.caption_entities or []):
        if ent.type in ("url", "text_link"):
            return True
    return False


async def group_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not GROUP_ID or msg.chat_id != GROUP_ID:
        return
    # Naye member ka welcome
    if msg.new_chat_members:
        for m in msg.new_chat_members:
            if not m.is_bot:
                await msg.reply_text(
                    f"👋 Welcome {m.first_name}! <b>{STORE_NAME} Public Group</b> me swagat hai.\n"
                    "📌 Rules: Spam/link mat bhejo, sabse tameez se baat karo, order ki dikkat bot me Report se bhejo.",
                    parse_mode="HTML",
                )
        return
    if not _has_link(msg):
        return
    try:
        member = await context.bot.get_chat_member(GROUP_ID, msg.from_user.id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return
    except Exception:
        pass
    try:
        await msg.delete()
        await context.bot.send_message(
            GROUP_ID,
            f"🚫 {msg.from_user.first_name} — group me bahar ke link allowed nahi hain (admin ke alawa). Message delete kar diya gaya.",
        )
    except Exception as e:
        log.warning("guard delete failed: %s", e)


# --------------------------------------------------------------------------- #
# Reminder jobs
# --------------------------------------------------------------------------- #
async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    # Pending order reminders: 3 ghante (reminder) + 4 ghante (red alert)
    for o in db_all("SELECT * FROM orders WHERE status='pending'"):
        age_h = (now_utc() - from_iso(o["created_at"])).total_seconds() / 3600
        if age_h >= DELIVERY_HOURS and not o.get("reminded4"):
            db_exec("UPDATE orders SET reminded4=1 WHERE id=?", (o["id"],))
            for aid in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        aid,
                        f"🔴 <b>ORDER LATE HAI!</b> {order_code(o['id'])} — {age_h:.1f} ghante ho gaye, 4 ghante wali deadline nikal gayi. Turant deliver karo!",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("✅ Deliver Karo", callback_data=f"admin:deliver:{o['id']}")]]
                        ),
                    )
                except Exception:
                    pass
        elif age_h >= 3 and not o.get("reminded3"):
            db_exec("UPDATE orders SET reminded3=1 WHERE id=?", (o["id"],))
            for aid in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        aid,
                        f"⏰ Reminder: Order {order_code(o['id'])} pending hai — {age_h:.1f} ghante ho gaye, deadline 4 ghante hai.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    # Subscription reminders: warranty (3 din pehle) + renewal (validity ke 3 din pehle)
    for s in db_all("SELECT * FROM subscriptions WHERE active=1"):
        if not s.get("warranty_reminded") and days_left(s["warranty_end"]) <= 3 and days_left(s["warranty_end"]) > 0:
            db_exec("UPDATE subscriptions SET warranty_reminded=1 WHERE id=?", (s["id"],))
            try:
                await context.bot.send_message(
                    s["user_id"],
                    f"🛡️ Tumhari warranty <b>{days_left(s['warranty_end'])} din</b> me khatm ho jayegi ({fmt_dt(s['warranty_end'])}).\n"
                    "Koi dikkat hai to warranty khatm hone se pehle <b>Report</b> kar do.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        if not s.get("renewal_reminded") and days_left(s["validity_end"]) <= 3:
            db_exec("UPDATE subscriptions SET renewal_reminded=1 WHERE id=?", (s["id"],))
            try:
                await context.bot.send_message(
                    s["user_id"],
                    f"⏳ Tumhari subscription <b>{days_left(s['validity_end'])} din</b> me expire ho jayegi ({fmt_dt(s['validity_end'])}).\n"
                    f"Renew karna ho to bot me My Subscription se Renew dabao — ⭐{PRICE_STARS} Stars.",
                    parse_mode="HTML",
                )
            except Exception:
                pass


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    today = now_utc().astimezone(IST).strftime("%Y-%m-%d")
    todays = [o for o in db_all("SELECT * FROM orders") if (from_iso(o["created_at"]) or now_utc()).astimezone(IST).strftime("%Y-%m-%d") == today]
    stars = sum(o["stars"] for o in todays)
    pending = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='pending'")["c"]
    text = (
        f"🌙 <b>Daily Report — {today}</b>\n\n"
        f"🧾 Aaj ke Orders: <b>{len(todays)}</b>\n"
        f"⭐ Aaj ke Stars: <b>{stars}</b>\n"
        f"⏳ Abhi Pending: <b>{pending}</b>\n"
        f"📦 Stock Bacha: <b>{get_stock()}</b>"
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, parse_mode="HTML")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def _start_health_server():
    """Render WEB SERVICE ke liye chhota sa HTTP health server ($PORT pe).

    Render ke free plan pe Background Worker nahi milta — isliye bot ek
    Web Service ki tarah chalta hai, aur Render ko ek khula port chahiye
    hota hai. Ye server sirf 'alive' jawab deta hai (pinger ke liye bhi).
    Local/Worker run me PORT nahi hota — waha ye kuch nahi karta.
    """
    import os as _os
    import threading as _threading
    port = _os.getenv("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Apna AI Store Bot is alive")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", int(port)), _H)
        _threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("🌐 Health server chalu hai — port %s", port)
    except Exception as e:
        log.warning("Health server nahi chala: %s", e)


async def _post_init(app: Application):
    init_db()


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN set nahi hai! .env / Render Environment me BOT_TOKEN dalo "
            "(token @BotFather se milega)."
        )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("myorders", lambda u, c: _cmd_list_orders(u, c)))
    app.add_handler(CommandHandler("mysubscription", lambda u, c: _cmd_mysub(u, c)))
    app.add_handler(CommandHandler("report", lambda u, c: u.message.reply_text("🚨 Report ke liye koi order chuno — /start dabake Report button use karo.")))
    app.add_handler(CommandHandler("myreports", show_my_reports_cmd))
    app.add_handler(CommandHandler("paysupport", cmd_paysupport))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(CallbackQueryHandler(cb_join_verify, pattern="^join:verify$"))
    app.add_handler(CallbackQueryHandler(cb_terms, pattern="^buy:terms$"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(cb_order_view, pattern="^order:view:"))
    app.add_handler(CallbackQueryHandler(cb_report, pattern="^report:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(cb_nav, pattern="^(nav:|noop$)"))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Pending-input (private chat) — commands ke alawa
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.Document) & ~filters.COMMAND,
            handle_input,
        )
    )
    # Public group guard
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_guard))

    if app.job_queue:
        app.job_queue.run_repeating(reminder_job, interval=1800, first=120)
        from datetime import time as dtime

        app.job_queue.run_daily(daily_report_job, time=dtime(hour=21, minute=0, tzinfo=IST))

    log.info("🚀 %s bot start ho raha hai…", STORE_NAME)
    _start_health_server()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def _cmd_list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    orders = db_all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (update.effective_user.id,))
    if not orders:
        await update.message.reply_text("📦 Abhi koi order nahi hai.")
        return
    lines = ["📦 <b>My Orders</b>\n"]
    for o in orders:
        lines.append(f"• <b>{order_code(o['id'])}</b> — ⭐{o['stars']} • {o['status'].title()} • {fmt_dt(o['created_at'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _cmd_mysub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    sub = db_one("SELECT * FROM subscriptions WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1", (update.effective_user.id,))
    if not sub:
        await update.message.reply_text("💎 Abhi koi active subscription nahi hai.")
        return
    await update.message.reply_text(
        f"💎 <b>{sub['product']}</b>\nValidity: {days_left(sub['validity_end'])} din bache\nWarranty: {days_left(sub['warranty_end'])} din bache",
        parse_mode="HTML",
    )


if __name__ == "__main__":
    main()

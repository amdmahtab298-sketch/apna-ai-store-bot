#!/usr/bin/env python3
"""
Apna AI Store — Telegram Store Bot
===================================
Ek product ka store: ElevenLabs Starter Plan (UPI payment — screenshot + admin verify).

Features (locked spec):
- Force Join: bot tabhi chalega jab user Public Group ka member ho
- Home screen: Welcome text + inline buttons (LetXShop style, 2-per-row grid)
- Product page: pura detail (credits, features, validity, warranty, price, live stock)
- Payment: UPI — user screenshot bhejta hai, admin Approve kare tab order confirm; delivery uske baad
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
  PRICE_INR    - Product price rupaye (₹) me (default 199)
  INITIAL_STOCK  - Shuruaat ka stock (default 10)
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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
def _env_int(name, default):
    """Env var ko surakshit int me badlo — galat/text value pe crash ki
    jagah default use hoga (deploy pe chhoti typing galti bot na mare)."""
    try:
        raw = str(os.environ.get(name, "")).strip().strip('"').strip("'").strip()
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        print(f"⚠️ {name} ki value number nahi hai — default {default} use hoga")
        return int(default)


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().lstrip("-").isdigit()
}
GROUP_ID = _env_int("GROUP_ID", 0)
if GROUP_ID >= 10**12:
    # Supergroup ID hamesha -100... se shuru hoti hai. Env me minus gir
    # jaaye to bot khud theek kar lega — users ka Verify kabhi na atke.
    print(f"⚠️ GROUP_ID {GROUP_ID} positive hai — supergroup ID negative hoti hai, minus jod diya: {-GROUP_ID}")
    GROUP_ID = -GROUP_ID
GROUP_LINK = os.environ.get("GROUP_LINK", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PRICE_INR = _env_int("PRICE_INR", 199)
INITIAL_STOCK = _env_int("INITIAL_STOCK", 10)
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

# Seed ke liye sirf features wala hissa (validity/warranty page pe product-row se aayenge)
SEED_PRODUCT_DESC = PRODUCT_TEXT.split("⏳")[0].rstrip()

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
_pg_conn = None  # Postgres connection ek baar banta hai, phir reuse hota hai


def _connect():
    global _pg_conn
    if USE_PG:
        import psycopg
        from psycopg.rows import dict_row

        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg.connect(
                DATABASE_URL,
                row_factory=dict_row,
                connect_timeout=15,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
        return _pg_conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _drop_pg_conn():
    global _pg_conn
    try:
        if _pg_conn is not None:
            _pg_conn.close()
    except Exception:
        pass
    _pg_conn = None


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if USE_PG else sql


def _is_conn_error(e) -> bool:
    """Sirf asli connection-todne wale error pe True — aam SQL error
    (jaise duplicate column) pe healthy connection NAHI todna chahiye."""
    if not USE_PG:
        return False
    classes = []
    try:
        import psycopg

        for n in ("OperationalError", "InterfaceError"):
            c = getattr(psycopg, n, None)
            if isinstance(c, type):
                classes.append(c)
    except Exception:
        pass
    if classes and isinstance(e, tuple(classes)):
        return True
    s = str(e).lower()
    return any(k in s for k in ("closed", "connection", "server closed", "broken pipe", "eof detected", "terminating"))


def _with_conn(work):
    """work(cur) ko ek connection pe chalao. PG connection REUSE hota hai
    (har query pe naya connection nahi — bot ki speed yahi se aati hai);
    connection toota ho to ek baar naya bana ke phir try hota hai."""
    with _lock:
        for attempt in (0, 1):
            conn = _connect()
            try:
                cur = conn.cursor()
                out = work(cur)
                conn.commit()
                return out
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                if USE_PG and attempt == 0 and _is_conn_error(e):
                    _drop_pg_conn()
                    continue
                raise
            finally:
                if not USE_PG:
                    conn.close()


def db_exec(sql, params=()):
    def work(cur):
        cur.execute(_q(sql), tuple(params))

    _with_conn(work)


def db_insert(sql, params=()):
    """Insert karke nayi row ka id do."""

    def work(cur):
        if USE_PG:
            cur.execute(_q(sql) + " RETURNING id", tuple(params))
            return cur.fetchone()["id"]
        cur.execute(_q(sql), tuple(params))
        return cur.lastrowid

    return _with_conn(work)


def db_one(sql, params=()):
    def work(cur):
        cur.execute(_q(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None

    return _with_conn(work)


def db_all(sql, params=()):
    def work(cur):
        cur.execute(_q(sql), tuple(params))
        return [dict(r) for r in cur.fetchall()]

    return _with_conn(work)


def _ensure_column(table, col, decl):
    try:
        db_exec(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception:
        pass  # column pehle se maujud hai


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
        f"""CREATE TABLE IF NOT EXISTS products(
            id {id_col}, name TEXT, price_inr INTEGER DEFAULT 0, mrp_text TEXT DEFAULT '',
            description TEXT DEFAULT '', validity_days INTEGER DEFAULT 30,
            warranty_days INTEGER DEFAULT 25, stock INTEGER DEFAULT 0,
            photo_file_id TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0, created_at TEXT)""",
    ]
    for s in stmts:
        db_exec(s)
    if db_one("SELECT value FROM settings WHERE key='stock'") is None:
        db_exec(
            "INSERT INTO settings(key, value) VALUES('stock', ?)",
            (str(INITIAL_STOCK),),
        )
    _ensure_column("products", "stock_mode", "TEXT DEFAULT 'count'")
    _ensure_column("orders", "product_id", "INTEGER DEFAULT 0")
    _ensure_column("subscriptions", "product_id", "INTEGER DEFAULT 0")
    _ensure_column("products", "price_inr", "INTEGER DEFAULT 0")
    _ensure_column("orders", "amount_inr", "INTEGER DEFAULT 0")
    _ensure_column("orders", "pay_shot", "TEXT DEFAULT ''")
    _ensure_column("orders", "pay_utr", "TEXT DEFAULT ''")
    db_exec("UPDATE products SET price_inr=? WHERE price_inr IS NULL OR price_inr=0", (PRICE_INR,))
    if db_one("SELECT id FROM products LIMIT 1") is None:
        db_exec(
            "INSERT INTO products(name, price_inr, mrp_text, description, validity_days, "
            "warranty_days, stock, photo_file_id, is_active, sort_order, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,1,1,?)",
            (
                PRODUCT_NAME, PRICE_INR, "₹599", SEED_PRODUCT_DESC or PRODUCT_TEXT,
                VALIDITY_DAYS, WARRANTY_DAYS, get_stock(),
                get_setting("product_photo_file_id", "") or "", to_iso(now_utc()),
            ),
        )
        log.info("Products table khali thi — ElevenLabs product seed kar diya.")
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


def _to_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def days_left(end_iso) -> int:
    dt = from_iso(end_iso)
    if not dt:
        return 0
    return max(0, (dt - now_utc()).days)


def order_code(order_id: int) -> str:
    return f"EL{1000 + int(order_id)}"

def status_label(s) -> str:
    return {
        "payment_review": "💳 Payment Review Me",
        "pending": "⏳ Processing",
        "delivered": "✅ Delivered",
        "rejected": "❌ Payment Reject",
    }.get(s or "", (s or "").title())


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


def set_setting(key, value):
    db_exec(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        + ("ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value" if USE_PG else "ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
        (key, str(value)),
    )


def get_store_name() -> str:
    return get_setting("store_name") or STORE_NAME


def get_group_link() -> str:
    return get_setting("group_link") or GROUP_LINK


def get_approved_ids():
    raw = get_setting("approved_ids", "") or ""
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def set_approved_ids(ids):
    set_setting("approved_ids", ",".join(str(i) for i in sorted(ids)))


# --------------------------------------------------------------------------- #
# Products — sab kuch DATABASE me hai. Admin bot se hi add/edit karta hai,
# naya product turant store me LIVE ho jata hai (code change ki zaroorat nahi).
# --------------------------------------------------------------------------- #
def get_products(active_only=False):
    if active_only:
        return db_all("SELECT * FROM products WHERE is_active=1 ORDER BY sort_order ASC, id ASC")
    return db_all("SELECT * FROM products ORDER BY sort_order ASC, id ASC")


def get_product(pid):
    if not pid:
        return None
    try:
        return db_one("SELECT * FROM products WHERE id=?", (int(pid),))
    except (TypeError, ValueError):
        return None


def get_product_by_name(name):
    if not name:
        return None
    return db_one("SELECT * FROM products WHERE name=?", (name,))


def default_product():
    ps = get_products(active_only=True)
    return ps[0] if ps else None


def is_in_stock(prod) -> bool:
    """Unlimited mode = humesha available; count mode = stock > 0."""
    if not prod:
        return False
    if (prod.get("stock_mode") or "count") == "unlimited":
        return True
    return int(prod["stock"]) > 0


def set_product_stock(pid, n):
    db_exec("UPDATE products SET stock=? WHERE id=?", (max(0, int(n)), int(pid)))


def total_stock() -> int:
    row = db_one("SELECT COALESCE(SUM(stock),0) AS s FROM products")
    return int(row["s"]) if row else 0


def terms_text(p=None) -> str:
    if not p:
        return TERMS_TEXT
    return (
        "📜 <b>Terms — kharidne se pehle padh lo</b>\n\n"
        f"1️⃣ Ye ek digital subscription hai — {p['name']}.\n"
        f"2️⃣ Validity {p['validity_days']} din aur Warranty {p['warranty_days']} din <b>delivery ke time</b> se ginni jayegi.\n"
        "3️⃣ <b>Refund policy nahi hai.</b> Koi dikkat aaye to Warranty period me <b>Replacement</b> milega — Report option se Order ID ke saath report karo.\n"
        "4️⃣ Account ka galat use (spam, resale, sharing rules todna) karne pe account band ho sakta hai — uski zimmedari store ki nahi hogi.\n"
        "5️⃣ Ek order = ek subscription. Order kisi aur ko transfer nahi hoga.\n\n"
        "Agree karte ho to neeche wala button dabao — phir payment khulega."
    )


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


_member_cache: dict = {}  # user_id -> (is_member, monotonic_ts)
_MEMBER_TTL = 300  # 5 minute — har tap pe Telegram API nahi (speed)
_MEMBER_TTL_NEG = 20  # "member nahi" wala jawab sirf 20s cache — join ke turant baad atke nahi
_last_member_error = ""  # aakhiri membership-check error (Health screen ke liye)


async def is_group_member(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, force: bool = False
) -> bool:
    """Force-join check. GROUP_ID set na ho to gate OFF (True).
    Aam taps pe 5-min cache; Verify aur Buy pe force=True (taaza check)."""
    if not GROUP_ID:
        return True
    if is_admin(user_id):
        return True
    if user_id in get_approved_ids():
        return True  # admin ne khud approve kiya hai (Verify atke to ilaaj)
    import time as _time

    global _last_member_error
    if not force:
        hit = _member_cache.get(user_id)
        if hit:
            ttl = _MEMBER_TTL if hit[0] else _MEMBER_TTL_NEG
            if (_time.monotonic() - hit[1]) < ttl:
                return hit[0]
    try:
        member = await context.bot.get_chat_member(GROUP_ID, user_id)
    except BadRequest as e:
        _last_member_error = f"BadRequest: {e}"
        log.warning("get_chat_member failed (%s) — bot group me admin hai?", e)
        return False
    except Exception as e:  # network etc.
        _last_member_error = f"{type(e).__name__}: {e}"
        log.warning("member check error: %s", e)
        return False
    _last_member_error = ""
    if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        result = True
    elif member.status == ChatMemberStatus.RESTRICTED and getattr(member, "is_member", False):
        result = True
    else:
        result = False
    _member_cache[user_id] = (result, _time.monotonic())
    return result


# --------------------------------------------------------------------------- #
# Keyboards & screens
# --------------------------------------------------------------------------- #
def home_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for prod in get_products(active_only=True):
        rows.append([InlineKeyboardButton(f"🛍️ {prod['name']} — ₹{prod['price_inr']}", callback_data=f"nav:product:{prod['id']}")])
    if not rows:
        rows.append([InlineKeyboardButton("🛍️ Products jald aa rahe hain…", callback_data="noop")])
    rows += [
        [
            InlineKeyboardButton("📦 My Orders", callback_data="nav:myorders"),
            InlineKeyboardButton("💎 My Subscription", callback_data="nav:mysub"),
        ],
        [
            InlineKeyboardButton("🚨 Report", callback_data="report:new"),
            InlineKeyboardButton("📋 My Reports", callback_data="report:my"),
        ],
    ]
    _gl = get_group_link()
    if _gl:
        rows.append([InlineKeyboardButton("👥 Public Group", url=_gl)])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")])
    return InlineKeyboardMarkup(rows)


def home_text(first_name: str) -> str:
    custom = get_setting("welcome_text")
    body = custom if custom else (
        "🛍️ Browse our premium AI services and choose your plan.\n"
        "💳 Pay easily via UPI — GPay / PhonePe / Paytm\n"
        "🍀 Trust issues? Visit our Public Group 👥\n\n"
        "Select a product below to get started:"
    )
    return (
        f"⚡ <b>Welcome to {get_store_name()}!</b>\n\n"
        f"Hello {first_name}!\n\n"
        + body
    )


def join_kb() -> InlineKeyboardMarkup:
    rows = []
    _gl = get_group_link()
    if _gl:
        rows.append([InlineKeyboardButton("👥 Public Group Join Karo", url=_gl)])
    rows.append([InlineKeyboardButton("✅ Join Kar Liya — Verify Karo", callback_data="join:verify")])
    return InlineKeyboardMarkup(rows)


def join_text() -> str:
    return (
    f"🔒 <b>{get_store_name()} abhi locked hai!</b>\n\n"
    "Bot use karne ke liye pehle hamara <b>Public Group</b> join karna zaroori hai — "
    "wahi sab buyers baat karte hain, reviews aur delivery proofs wahi milte hain.\n\n"
    "1️⃣ Neeche wala button dabake group join karo\n"
    "2️⃣ Phir wapas aake <b>Verify</b> dabao — store khul jayega 🚀"
)


def back_kb(target="nav:home", label="🏠 Home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=target)]])


def admin_nav_row(back_cb=None):
    """Har admin screen ke neeche: Back + Home + Exit Admin Panel."""
    row = []
    if back_cb:
        row.append(InlineKeyboardButton("⬅️ Back", callback_data=back_cb))
    row.append(InlineKeyboardButton("🏠 Home", callback_data="nav:home"))
    row.append(InlineKeyboardButton("🚪 Exit", callback_data="nav:home"))
    return row


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Karo", callback_data="input:cancel")]])


def product_kb(prod) -> InlineKeyboardMarkup:
    rows = []
    if is_in_stock(prod):
        rows.append([InlineKeyboardButton(f"🛒 Buy Now — ₹{prod['price_inr']}", callback_data=f"buy:start:{prod['id']}")])
    else:
        rows.append([InlineKeyboardButton("❌ Out of Stock", callback_data="noop")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
    return InlineKeyboardMarkup(rows)


def product_text(prod) -> str:
    stock_line = "📦 <b>Stock: Available ✅</b>" if is_in_stock(prod) else "📦 <b>Stock: Khatm ❌</b> — thoda wait karo, refill hote hi khulega."
    if prod.get("mrp_text"):
        price_line = f"💰 Price: <s>{prod['mrp_text']}</s> <b>₹{prod['price_inr']}</b>"
    else:
        price_line = f"💰 Price: <b>₹{prod['price_inr']}</b>"
    desc = (prod.get("description") or "").strip() or f"🛍️ <b>{prod['name']}</b>"
    return (
        desc
        + f"\n\n⏳ <b>Validity: {prod['validity_days']} Days</b> (delivery ke din se)\n"
        + f"🛡️ <b>Warranty: {prod['warranty_days']} Days</b> (delivery ke din se)\n\n"
        + f"{stock_line}\n\n"
        + price_line
    )


async def send_product(chat_send, user_id: int, pid=None):
    """Product page — photo usi product ki jo database me uske saath judi hai."""
    prod = get_product(pid) if pid else default_product()
    if not prod:
        await chat_send.send_message(
            chat_id=user_id,
            text="🛍️ Abhi koi product available nahi hai — thoda wait karo!",
            reply_markup=back_kb(),
        )
        return
    text = product_text(prod)
    kb = product_kb(prod)
    photo_id = prod.get("photo_file_id")
    try:
        if photo_id:
            await chat_send.send_photo(chat_id=user_id, photo=photo_id, caption=text, parse_mode="HTML", reply_markup=kb)
            return
        # Bundled poster sirf sabse pehle (seed) product ke liye fallback hai
        first = db_one("SELECT MIN(id) AS m FROM products")
        if first and int(prod["id"]) == int(first["m"] or 0) and os.path.exists(PRODUCT_FILE):
            with open(PRODUCT_FILE, "rb") as f:
                await chat_send.send_photo(chat_id=user_id, photo=f, caption=text, parse_mode="HTML", reply_markup=kb)
            return
    except Exception as e:
        log.warning("product photo failed: %s", e)
    await chat_send.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=kb)


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
            await update.callback_query.edit_message_text(join_text(), parse_mode="HTML", reply_markup=join_kb())
            return
        except BadRequest:
            pass
    await context.bot.send_message(update.effective_chat.id, join_text(), parse_mode="HTML", reply_markup=join_kb())


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
    await show_home(update, context)


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = get_group_link() or "Public Group"
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
    if await is_group_member(context, q.from_user.id, force=True):
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
    elif data == "nav:product" or data.startswith("nav:product:"):
        pid = None
        if data.startswith("nav:product:"):
            try:
                pid = int(data.split(":")[2])
            except (IndexError, ValueError):
                pid = None
        # Pehle naya page bhejo, phir purana hatao — ulta karne pe koi
        # gadbad ho to user ke saamne sab gayab ho jata tha.
        await send_product(context.bot, user["user_id"], pid)
        try:
            await q.delete_message()
        except Exception:
            pass
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
            emoji = "⏳" if o["status"] == "pending" else "✅" if o["status"] == "delivered" else "💳" if o["status"] == "payment_review" else "❌"
            lines.append(
                f"{emoji} <b>{order_code(o['id'])}</b> — {o['product']}\n"
                f"    ₹{o['amount_inr']} • {fmt_dt(o['created_at'])} • Status: <b>{status_label(o['status'])}</b>"
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
        f"Paid: ₹{o['amount_inr']}",
        f"Order Time: {fmt_dt(o['created_at'])}",
        f"Status: <b>{status_label(o['status'])}</b>",
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
        renew_prod = get_product(sub.get("product_id")) or get_product_by_name(sub.get("product"))
        renew_price = int(renew_prod["price_inr"]) if renew_prod else PRICE_INR
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"🔄 Renew Karo — ₹{renew_price}", callback_data="buy:renew")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        )
    try:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=kb)


# --------------------------------------------------------------------------- #
# UPI payment flow
# --------------------------------------------------------------------------- #
async def show_upi_payment(context, user_id: int, kind: str, product):
    """UPI payment screen: QR photo (agar set hai) + UPI ID + screenshot button.
    Payment ka koi auto-confirm nahi — user screenshot bhejega, admin Approve
    karega, tab order confirm hoga."""
    price = int(product["price_inr"])
    upi_id = (get_setting("upi_id") or "").strip()
    qr = (get_setting("pay_qr_file_id") or "").strip()
    context.user_data["buy_pid"] = int(product["id"])
    context.user_data["buy_kind"] = kind
    if not upi_id and not qr:
        try:
            await context.bot.send_message(
                user_id,
                "🛠️ <b>Payment abhi set ho raha hai</b> — thodi der me dobara try karo. Admin ko khabar bhej di hai. 🙏",
                parse_mode="HTML", reply_markup=back_kb(),
            )
        except Exception:
            pass
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    "⚠️ <b>UPI Payment set nahi hai!</b>\nEk customer kharidne aaya tha, lekin UPI ID/QR set nahi hai.\n"
                    "Admin Panel → ⚙️ Settings → 💳 UPI ID aur 🖼️ Payment QR set karo — warna koi kharid nahi payega!",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return
    from urllib.parse import quote
    text = (
        "💳 <b>Payment Karo</b>\n\n"
        f"🛍️ Product: <b>{product['name']}</b>\n"
        f"💰 Amount: <b>₹{price}</b>\n\n"
    )
    if upi_id:
        text += (
            f"📲 <b>UPI ID:</b> <code>{upi_id}</code>\n"
            f"(ID pe tap karke copy karo — kisi bhi UPI app GPay / PhonePe / Paytm / BHIM se <b>₹{price}</b> bhejo)\n\n"
            f"📱 Direct link (copy karke app/browser me kholo):\n"
            f"<code>upi://pay?pa={upi_id}&amp;pn={quote(get_store_name())}&amp;am={price}&amp;cu=INR</code>\n\n"
        )
    text += (
        "📸 Payment ke baad neeche wala button dabake <b>screenshot bhejna lazmi hai</b> — "
        "admin verify karega, uske baad hi order confirm hoga."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Payment Kar Diya — Screenshot Bhejo", callback_data="pay:proof")],
            [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
        ]
    )
    try:
        if qr:
            await context.bot.send_photo(user_id, qr, caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await context.bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        log.warning("UPI payment screen (photo) fail %s: %s — text bhej raha hu", user_id, e)
        await context.bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)


async def finalize_pay_review(context, user_id: int, utr: str):
    """Screenshot + UTR aa gaya — order 'payment_review' me daalo, admin ko
    screenshot ke saath Approve/Reject alert bhejo."""
    product = get_product(context.user_data.get("buy_pid")) or default_product()
    kind = context.user_data.get("buy_kind", "new")
    shot = context.user_data.get("pay_shot", "")
    context.user_data.pop("awaiting", None)
    if not product or not shot:
        await context.bot.send_message(
            user_id, "⚠️ Kuch gadbad ho gayi — product page se dobara Buy dabao.", reply_markup=back_kb()
        )
        return
    existing = db_one(
        "SELECT * FROM orders WHERE user_id=? AND product_id=? AND status='payment_review' ORDER BY id DESC LIMIT 1",
        (user_id, int(product["id"])),
    )
    if existing:
        await context.bot.send_message(
            user_id,
            f"🧾 Tumhara payment pehle se <b>review me hai</b> (Order <b>{order_code(existing['id'])}</b>) — "
            "admin ke confirm karte hi yahin khabar milegi. 🙏",
            parse_mode="HTML",
        )
        return
    oid = db_insert(
        "INSERT INTO orders(user_id, product, product_id, kind, amount_inr, status, created_at, pay_shot, pay_utr) "
        "VALUES(?,?,?,?,?,'payment_review',?,?,?)",
        (user_id, product["name"], int(product["id"]), kind, int(product["price_inr"]),
         to_iso(now_utc()), shot, utr or ""),
    )
    if not oid:
        row = db_one("SELECT MAX(id) AS m FROM orders")
        oid = row["m"] if row else 0
    await context.bot.send_message(
        user_id,
        "🧾 <b>Payment mil gaya — Review me hai!</b>\n\n"
        f"Order ID: <b>{order_code(oid)}</b>\n"
        f"Amount: ₹{int(product['price_inr'])}\n\n"
        "Admin tumhara payment verify karke order confirm karega — aam taur par zyada der nahi lagti. "
        "Confirm hote hi yahin message ayega. 🙏",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📦 My Orders", callback_data="nav:orders")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        ),
    )
    u = db_one("SELECT * FROM users WHERE user_id=?", (user_id,)) or {}
    caption = (
        "💰 <b>NAYA PAYMENT AAYA — VERIFY KARO!</b>\n\n"
        f"Order ID: <b>{order_code(oid)}</b>" + (" (RENEWAL)" if kind == "renew" else "") + "\n"
        f"User: {u.get('first_name', '')} (@{u.get('username', '-')})\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Product: {product['name']}\n"
        f"Amount: <b>₹{int(product['price_inr'])}</b>\n"
        f"UTR: <code>{utr or '-'}</code>\n\n"
        "Screenshot dhyaan se dekho — amount aur Successful saaf dikh raha ho tabhi <b>Approve</b> karo."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Approve — Order Confirm Karo", callback_data=f"payok:{oid}")],
            [InlineKeyboardButton("❌ Reject — Payment Galat Hai", callback_data=f"payno:{oid}")],
        ]
    )
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(aid, shot, caption=caption, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            log.warning("payment alert photo fail %s: %s", aid, e)
            try:
                await context.bot.send_message(aid, caption, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass


async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, o):
    q = update.callback_query
    db_exec("UPDATE orders SET status='pending' WHERE id=?", (o["id"],))
    prod = get_product(o.get("product_id"))
    if prod and (prod.get("stock_mode") or "count") == "count" and int(prod["stock"]) > 0:
        set_product_stock(prod["id"], int(prod["stock"]) - 1)
    try:
        if q.message is not None and q.message.caption:
            await q.edit_message_caption(caption=q.message.caption + "\n\n✅ <b>APPROVED — order confirm ho gaya!</b>", parse_mode="HTML")
        else:
            await context.bot.send_message(q.from_user.id, f"✅ Order {order_code(o['id'])} APPROVE ho gaya.")
    except Exception:
        pass
    deadline = (now_utc() + timedelta(hours=DELIVERY_HOURS)).astimezone(IST).strftime("%I:%M %p")
    try:
        await context.bot.send_message(
            o["user_id"],
            "🎉 <b>Payment Confirm Ho Gaya!</b>\n\n"
            f"Order ID: <b>{order_code(o['id'])}</b>\n"
            f"Paid: ₹{o['amount_inr']} ✅\n"
            f"⏳ <b>Delivery Time: 2-4 Hours</b> (deadline {deadline})\n"
            "Status: ⏳ Processing…\n\n"
            "Delivery isi chat me hogi, tab tak please wait karo. 🙏",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📦 Order Status Dekho", callback_data=f"order:view:{o['id']}")],
                    [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
                ]
            ),
        )
    except Exception as e:
        log.warning("approve user notify fail: %s", e)
    await notify_admins_new_order(context, o["id"])


async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, o):
    q = update.callback_query
    db_exec("UPDATE orders SET status='rejected' WHERE id=?", (o["id"],))
    try:
        if q.message is not None and q.message.caption:
            await q.edit_message_caption(caption=q.message.caption + "\n\n❌ <b>REJECTED</b>", parse_mode="HTML")
        else:
            await context.bot.send_message(q.from_user.id, f"❌ Order {order_code(o['id'])} REJECT ho gaya.")
    except Exception:
        pass
    try:
        await context.bot.send_message(
            o["user_id"],
            "❌ <b>Payment Verify Nahi Ho Paya</b>\n\n"
            f"Order ID: <b>{order_code(o['id'])}</b>\n"
            "Tumhare screenshot se payment confirm nahi hua (amount/UTR saaf nahi dikha, ya payment mila nahi).\n\n"
            "Agar tumne <b>sach me payment kiya hai</b> to ghabrao mat — <b>Public Group</b> me apna screenshot aur Order ID bhejo, admin wahi check karke madad karega.\n"
            "Dobara kharidna ho to product page se phir se try kar sakte ho.",
            parse_mode="HTML",
            reply_markup=back_kb(),
        )
    except Exception as e:
        log.warning("reject user notify fail: %s", e)


async def cb_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    if data == "pay:proof":
        product = get_product(context.user_data.get("buy_pid")) or default_product()
        if not product:
            await q.answer("Product nahi mila — Home se dobara try karo.", show_alert=True)
            return
        await q.answer()
        context.user_data["awaiting"] = {"kind": "pay_shot"}
        await context.bot.send_message(
            q.from_user.id,
            "📸 <b>Payment ka screenshot bhejo</b>\n\n"
            f"Amount <b>₹{int(product['price_inr'])}</b> aur <b>Successful / Completed</b> saaf dikhna chahiye.\n"
            "Jhoota ya purana screenshot bheja to order reject ho jayega.",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )
        return
    if data == "pay:utrskip":
        await q.answer()
        await finalize_pay_review(context, q.from_user.id, "")
        return
    if data.startswith("payok:") or data.startswith("payno:"):
        if not is_admin(q.from_user.id):
            await q.answer("🚫 Ye sirf admin ke liye hai.", show_alert=True)
            return
        await q.answer()
        oid = int(data.split(":")[1])
        o = db_one("SELECT * FROM orders WHERE id=?", (oid,))
        if not o or o["status"] != "payment_review":
            await context.bot.send_message(q.from_user.id, "Ye payment review me nahi hai — pehle hi decide ho chuka hai.")
            return
        if data.startswith("payok:"):
            await approve_payment(update, context, o)
        else:
            await reject_payment(update, context, o)
        return
    await q.answer()


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = register_user(q.from_user)

    if user.get("is_banned"):
        await q.answer("🚫 Tumhara account banned hai.", show_alert=True)
        return
    if not await is_group_member(context, user["user_id"], force=True):
        await show_join(update, context)
        return

    # Kaunsa product? Renew = subscription wala, Buy = button wala product
    product = None
    if q.data == "buy:renew":
        kind = "renew"
        sub = db_one(
            "SELECT * FROM subscriptions WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
            (user["user_id"],),
        )
        if sub:
            product = get_product(sub.get("product_id")) or get_product_by_name(sub.get("product"))
        if not product:
            product = default_product()
    elif q.data.startswith("buy:start:"):
        kind = "new"
        try:
            product = get_product(int(q.data.split(":")[2]))
        except (IndexError, ValueError):
            product = None
    else:
        kind = "new"
        product = default_product()
    if not product:
        await q.answer("❌ Ye product abhi available nahi hai.", show_alert=True)
        return
    context.user_data["buy_kind"] = kind
    context.user_data["buy_pid"] = int(product["id"])

    # Terms gate: pehli kharid se pehle ek baar terms accept karna hoga
    if not user.get("terms_accepted"):
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ I Agree — Aage Badho", callback_data="buy:terms")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]
        )
        try:
            await q.edit_message_text(terms_text(product), parse_mode="HTML", reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(q.message.chat_id, terms_text(product), parse_mode="HTML", reply_markup=kb)
        return

    if not is_in_stock(product):
        await q.answer("❌ Is product ka stock khatm hai! Thoda wait karo.", show_alert=True)
        return
    await show_upi_payment(context, user["user_id"], kind, product)


async def cb_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer("✅ Terms accept ho gaye!")
    except Exception:
        pass  # purana query — invoice phir bhi bhejna hai
    db_exec("UPDATE users SET terms_accepted=1 WHERE user_id=?", (q.from_user.id,))
    product = get_product(context.user_data.get("buy_pid")) or default_product()
    if not product or not is_in_stock(product):
        try:
            await context.bot.send_message(q.from_user.id, "❌ Is product ka stock khatm hai — thoda wait karo, refill hote hi khulega.", reply_markup=back_kb())
        except Exception:
            pass
        return
    kind = context.user_data.get("buy_kind", "new")
    await show_upi_payment(context, q.from_user.id, kind, product)


async def notify_admins_new_order(context: ContextTypes.DEFAULT_TYPE, oid: int):
    o = db_one("SELECT * FROM orders WHERE id=?", (oid,))
    if not o:
        return
    u = db_one("SELECT * FROM users WHERE user_id=?", (o["user_id"],)) or {}
    deadline = (from_iso(o["created_at"]) + timedelta(hours=DELIVERY_HOURS)).astimezone(IST).strftime("%I:%M %p")
    text = (
        "🔔 <b>NAYA ORDER — PAYMENT VERIFY HO GAYA!</b>\n\n"
        f"Order ID: <b>{order_code(o['id'])}</b>" + (" (RENEWAL)" if o.get("kind") == "renew" else "") + "\n"
        f"User: {u.get('first_name', '')} (@{u.get('username', '-')})\n"
        f"User ID: <code>{o['user_id']}</code>\n"
        f"Product: {o['product']}\n"
        f"Paid: ₹{o['amount_inr']} ✅\n"
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
    # Validity/Warranty ORDER WALE PRODUCT ke hisaab se (delivery ke din se)
    prod = get_product(o.get("product_id")) or get_product_by_name(o.get("product"))
    v_days = int(prod["validity_days"]) if prod else VALIDITY_DAYS
    w_days = int(prod["warranty_days"]) if prod else WARRANTY_DAYS
    # Purani active subscription band karo, nayi banao (dates delivery se)
    db_exec("UPDATE subscriptions SET active=0 WHERE user_id=? AND active=1", (o["user_id"],))
    validity_end = delivered_at + timedelta(days=v_days)
    warranty_end = delivered_at + timedelta(days=w_days)
    db_insert(
        "INSERT INTO subscriptions(user_id, order_id, product, product_id, delivered_at, validity_end, warranty_end) "
        "VALUES(?,?,?,?,?,?,?)",
        (o["user_id"], oid, o["product"], int(prod["id"]) if prod else int(o.get("product_id") or 0),
         to_iso(delivered_at), to_iso(validity_end), to_iso(warranty_end)),
    )

    header = (
        "✅ <b>Delivery Ho Gayi!</b>\n\n"
        f"Order ID: <b>{order_code(oid)}</b>\n"
        f"Product: {o['product']}\n"
        f"⏳ Validity: {fmt_dt(to_iso(validity_end))} tak ({v_days} din)\n"
        f"🛡️ Warranty: {fmt_dt(to_iso(warranty_end))} tak ({w_days} din)\n"
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
        orders = db_all("SELECT * FROM orders WHERE user_id=? AND status IN ('pending','delivered') ORDER BY id DESC LIMIT 10", (uid,))
        if not orders:
            text = (
                "🚨 <b>Report</b>\n\n"
                "Report kisi <b>order</b> ki dikkat ke liye hoti hai — tumhara abhi koi order nahi hai, isliye report ban nahi sakti.\n\n"
                "🛍️ Kuch kharidna hai → neeche se product chuno\n"
                "💬 Aam sawal ya dikkat hai → <b>Public Group</b> me puchh lo, wahan sabse tez jawab milega."
            )
            rows = []
            first_prod = default_product()
            if first_prod:
                rows.append([InlineKeyboardButton(f"🛍️ {first_prod['name']} — ₹{first_prod['price_inr']}", callback_data=f"nav:product:{first_prod['id']}")])
            _gl = get_group_link()
            if _gl:
                rows.append([InlineKeyboardButton("👥 Public Group", url=_gl)])
            rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
            try:
                await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
            except BadRequest:
                await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
            return
        rows = [
            [InlineKeyboardButton(f"{order_code(o['id'])} — {status_label(o['status'])}", callback_data=f"report:order:{o['id']}")]
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
        f"Paid: ₹{o.get('amount_inr', '-')} • Order Time: {fmt_dt(o.get('created_at'))}\n"
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
                InlineKeyboardButton("🛍️ Products", callback_data="admin:products"),
            ],
            [
                InlineKeyboardButton("🚨 Open Reports", callback_data="admin:reports"),
                InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast"),
            ],
            [
                InlineKeyboardButton("👥 Users", callback_data="admin:users"),
                InlineKeyboardButton("🩺 Health Check", callback_data="admin:health"),
            ],
            [
                InlineKeyboardButton("🔍 Order Search", callback_data="admin:osearch"),
                InlineKeyboardButton("⚙️ Settings", callback_data="admin:settings"),
            ],
            [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
        ]
    )


async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='pending'")["c"]
    open_reps = db_one("SELECT COUNT(*) AS c FROM reports WHERE status='open'")["c"]
    text = (
        f"🔐 <b>Admin Panel — {get_store_name()}</b>\n\n"
        f"⏳ Pending Orders: <b>{pending}</b>\n"
        f"🚨 Open Reports: <b>{open_reps}</b>\n"
        f"🛍️ Products: <b>{len(get_products())}</b>\n"
        f"📦 Kul Stock: <b>{total_stock()}</b>"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_panel_kb())
            return
        except BadRequest:
            pass
    await context.bot.send_message(update.effective_chat.id, text, parse_mode="HTML", reply_markup=admin_panel_kb())


async def _show_products_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    prods = get_products()
    lines = ["🛍️ <b>Products Manager</b>\n"]
    rows = []
    if not prods:
        lines.append("Abhi koi product nahi hai — neeche se pehla add karo.")
    for pr in prods:
        state = "✅ LIVE" if pr["is_active"] else "🙈 Hidden"
        lines.append(f"• <b>{pr['name']}</b> — ₹{pr['price_inr']} • Stock {pr['stock']} • {state}")
        rows.append([InlineKeyboardButton(f"⚙️ Manage: {pr['name'][:26]}", callback_data=f"admin:prod:{pr['id']}")])
    rows.append([InlineKeyboardButton("➕ Naya Product Add Karo", callback_data="admin:prodadd")])
    rows.append(admin_nav_row("admin:panel"))
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=kb)


async def _show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
    q = update.callback_query
    pr = get_product(pid)
    if not pr:
        await q.answer("Product nahi mila.", show_alert=True)
        return
    state = "✅ LIVE — store me dikh raha hai" if pr["is_active"] else "🙈 HIDDEN — store me nahi dikh raha"
    text = (
        f"🛍️ <b>{pr['name']}</b>\n\n"
        f"💰 Daam: <b>₹{pr['price_inr']}</b>\n"
        f"🏷️ Asli Daam (cut wala): {pr['mrp_text'] or '-'}\n"
        f"📦 Stock: <b>{'♾️ Unlimited — humesha Available' if (pr.get('stock_mode') or 'count') == 'unlimited' else pr['stock']}</b>\n"
        f"⏳ Validity: {pr['validity_days']} din • 🛡️ Warranty: {pr['warranty_days']} din\n"
        f"🖼️ Photo: {'lagi hai ✅' if pr['photo_file_id'] else 'nahi lagi'}\n"
        f"📌 Haalat: {state}\n\n"
        "Jo badalna hai, uska button dabao 👇"
    )
    toggle = (
        InlineKeyboardButton("🙈 Hide Karo", callback_data=f"admin:prodhide:{pid}")
        if pr["is_active"]
        else InlineKeyboardButton("✅ Store Me Dikhao", callback_data=f"admin:prodshow:{pid}")
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Naam", callback_data=f"admin:prodedit:name:{pid}"),
             InlineKeyboardButton("💰 Daam", callback_data=f"admin:prodedit:price_inr:{pid}")],
            [InlineKeyboardButton("📝 Details", callback_data=f"admin:prodedit:description:{pid}"),
             InlineKeyboardButton("🏷️ Asli Daam", callback_data=f"admin:prodedit:mrp_text:{pid}")],
            [InlineKeyboardButton("⏳ Validity", callback_data=f"admin:prodedit:validity_days:{pid}"),
             InlineKeyboardButton("🛡️ Warranty", callback_data=f"admin:prodedit:warranty_days:{pid}")],
            [InlineKeyboardButton("📦 Stock Set", callback_data=f"admin:prodstock:set:{pid}"),
             InlineKeyboardButton("➕ Stock Add", callback_data=f"admin:prodstock:add:{pid}")],
            [InlineKeyboardButton("🖼️ Photo Badlo", callback_data=f"admin:prodphoto:{pid}")],
            [InlineKeyboardButton("🔀 Stock Mode: Number ⇄ Unlimited", callback_data=f"admin:prodmode:{pid}")],
            [toggle, InlineKeyboardButton("🗑️ Delete", callback_data=f"admin:proddel:{pid}")],
            [InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")],
            admin_nav_row("admin:products"),
        ]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=kb)


async def _finalize_product_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, photo_file_id):
    draft = context.user_data.pop("prod_draft", {}) or {}
    context.user_data.pop("awaiting", None)
    chat_id = update.effective_chat.id
    if not draft.get("name"):
        await context.bot.send_message(chat_id, "⚠️ Product adhoora reh gaya — Products Manager se dobara add karo.")
        return
    maxord = db_one("SELECT COALESCE(MAX(sort_order),0) AS m FROM products")
    db_insert(
        "INSERT INTO products(name, price_inr, mrp_text, description, validity_days, warranty_days, "
        "stock, photo_file_id, is_active, sort_order, created_at, stock_mode) VALUES(?,?,?,?,?,?,?,?,1,?,?,?)",
        (
            draft["name"], draft["price"], draft.get("mrp", ""), draft.get("desc", ""),
            draft["validity"], draft["warranty"], draft["stock"], photo_file_id or "",
            int(maxord["m"]) + 1, to_iso(now_utc()), draft.get("stock_mode", "count"),
        ),
    )
    text = (
        f"✅ <b>{draft['name']}</b> — store me <b>LIVE</b> ho gaya! 🎉\n\n"
        f"💰 Daam: ₹{draft['price']}\n"
        f"📦 Stock: {draft['stock']}\n"
        f"⏳ Validity {draft['validity']} din • 🛡️ Warranty {draft['warranty']} din\n\n"
        "Home screen pe iska button abhi se dikhega — bot.py chhedne ki koi zaroorat nahi. 😎"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Products Manager", callback_data="admin:products")],
            [InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")],
        ]
    )
    if photo_file_id:
        await context.bot.send_photo(
            chat_id, photo=photo_file_id,
            caption=text + "\n\n🖼️ Ye photo ISI product ke saath judi hai.",
            parse_mode="HTML", reply_markup=kb,
        )
    else:
        await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


async def _show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ids = sorted(get_approved_ids())
    lines = [
        "👥 <b>Approved Users</b>\n",
        "Ye users group-join check ke <b>bina</b> bhi store use kar sakte hain — jinka Verify atak raha ho, unke liye ye ilaaj hai.\n",
    ]
    rows = []
    if not ids:
        lines.append("Abhi koi approved user nahi hai.")
    for uid_ in ids:
        u = db_one("SELECT * FROM users WHERE user_id=?", (uid_,)) or {}
        lines.append(f"• <code>{uid_}</code> — {u.get('first_name', '?')}")
        rows.append([InlineKeyboardButton(f"❌ {uid_} ko Hatayo", callback_data=f"admin:unapprove:{uid_}")])
    rows.append([InlineKeyboardButton("➕ User Approve Karo (Telegram ID se)", callback_data="admin:approve")])
    rows.append(admin_nav_row("admin:panel"))
    text = "\n".join(lines)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    except BadRequest:
        await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def _show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    wel = get_setting("welcome_text")
    text = (
        "⚙️ <b>Store Settings</b>\n\n"
        f"🏪 Store ka Naam: <b>{get_store_name()}</b>\n"
        f"🔗 Group Link: {get_group_link() or '(set nahi hai)'}\n"
        f"📝 Welcome Text: {'custom set hai ✅' if wel else 'default chal raha hai'}\n"
        f"💳 UPI ID: <b>{get_setting('upi_id') or 'set nahi hai ❌'}</b>\n"
        f"🖼️ Payment QR: {'set hai ✅' if get_setting('pay_qr_file_id') else 'set nahi hai'}\n\n"
        "Jo badalna hai, dabao — <b>turant LIVE</b> ho jayega, redeploy ki zaroorat nahi:"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Store ka Naam", callback_data="admin:setfield:store_name"),
             InlineKeyboardButton("📝 Welcome Text", callback_data="admin:setfield:welcome_text")],
            [InlineKeyboardButton("🔗 Group Link Badlo", callback_data="admin:setfield:group_link")],
            [InlineKeyboardButton("💳 UPI ID Badlo", callback_data="admin:setfield:upi_id"),
             InlineKeyboardButton("🖼️ Payment QR", callback_data="admin:payqr")],
            admin_nav_row("admin:panel"),
        ]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest:
        await context.bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=kb)


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
        reviews = db_all("SELECT * FROM orders WHERE status='payment_review' ORDER BY id ASC LIMIT 15")
        orders = db_all("SELECT * FROM orders WHERE status='pending' ORDER BY id ASC LIMIT 15")
        lines, rows = ["📋 <b>Pending Orders</b>\n"], []
        if reviews:
            lines.append("💳 <b>Payment Review Wale (pehle inhe dekho):</b>")
            for o in reviews:
                lines.append(f"• <b>{order_code(o['id'])}</b> — {o['product']} — ₹{o['amount_inr']} • UTR: {o.get('pay_utr') or '-'}")
                rows.append([
                    InlineKeyboardButton(f"✅ Approve {order_code(o['id'])}", callback_data=f"payok:{o['id']}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"payno:{o['id']}"),
                ])
            lines.append("")
        if orders:
            lines.append("📦 <b>Delivery Wale:</b>")
            for o in orders:
                age_h = (now_utc() - from_iso(o["created_at"])).total_seconds() / 3600
                lines.append(f"• <b>{order_code(o['id'])}</b> — ₹{o['amount_inr']} • {age_h:.1f} ghante ho gaye")
                rows.append([InlineKeyboardButton(f"✅ Deliver {order_code(o['id'])}", callback_data=f"admin:deliver:{o['id']}")])
        if not reviews and not orders:
            lines.append("Koi pending order nahi hai. 🎉")
        text = "\n".join(lines)
        rows.append(admin_nav_row("admin:panel"))
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
            "bot wahi cheez customer ko delivery bana ke bhej dega.",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data == "admin:stats":
        users_c = db_one("SELECT COUNT(*) AS c FROM users")["c"]
        orders_c = db_one("SELECT COUNT(*) AS c FROM orders")["c"]
        delivered = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='delivered'")["c"]
        earned = db_one("SELECT COALESCE(SUM(amount_inr),0) AS s FROM orders WHERE status IN ('pending','delivered')")["s"]
        await q.edit_message_text(
            "📊 <b>Store Stats</b>\n\n"
            f"👥 Total Users: <b>{users_c}</b>\n"
            f"🧾 Total Orders: <b>{orders_c}</b> (Delivered: {delivered})\n"
            f"💰 Total Kamai: <b>₹{earned}</b>\n"
            f"📦 Kul Stock Bacha: <b>{total_stock()}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([admin_nav_row("admin:panel")]),
        )

    elif data == "admin:health":
        lines = ["🩺 <b>Bot Health Check</b>\n"]
        try:
            me = await context.bot.get_me()
            lines.append(f"🤖 Bot: @{me.username} ✅")
        except Exception as e:
            lines.append(f"🤖 Bot jaanch: ❌ {e}")
        lines.append(f"👥 GROUP_ID (bot ko jo dikh raha hai): <code>{GROUP_ID}</code>")
        if not GROUP_ID:
            lines.append("⚠️ <b>GROUP_ID set hi nahi hai</b> — users ka Verify kaam nahi karega! Render → Environment me dalo.")
        else:
            try:
                chat = await context.bot.get_chat(GROUP_ID)
                lines.append(f"🏠 Group: <b>{getattr(chat, 'title', '')}</b> ✅ mil gaya")
                me = await context.bot.get_me()
                mem = await context.bot.get_chat_member(GROUP_ID, me.id)
                ok_role = mem.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
                lines.append(f"🛡️ Bot ka group me role: <b>{mem.status}</b> " + ("✅" if ok_role else "⚠️ — bot ko group me ADMIN banao, warna membership check fail hoga!"))
            except Exception as e:
                lines.append(f"🏠 Group jaanch: ❌ <code>{e}</code>")
                lines.append("👉 Matlab GROUP_ID galat hai. Sahi value Render → Environment me bilkul aisi honi chahiye: <code>-1003961533075</code> (minus ke saath, bina space/quotes).")
        try:
            db_one("SELECT 1 AS x")
            lines.append("🗄️ Database: ✅ chal raha hai")
        except Exception as e:
            lines.append(f"🗄️ Database: ❌ {e}")
        lines.append(f"🛍️ Products: {len(get_products())} (Live: {len(get_products(active_only=True))})")
        lines.append(f"🔓 Approved users: {len(get_approved_ids())}")
        if _last_member_error:
            lines.append(f"⚠️ Aakhiri membership-check error: <code>{_last_member_error}</code>")
        else:
            lines.append("✅ Membership check me aakhiri baar koi error nahi aaya")
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Dobara Check Karo", callback_data="admin:health")],
                admin_nav_row("admin:panel"),
            ]
        )
        try:
            await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        except BadRequest:
            await context.bot.send_message(q.message.chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)

    elif data == "admin:users":
        await _show_users(update, context)

    elif data == "admin:approve":
        context.user_data["awaiting"] = {"kind": "approve_id"}
        await context.bot.send_message(
            q.from_user.id,
            "➕ <b>User Approve</b>\n\nJis user ka Verify atak raha hai, uski <b>Telegram ID (number)</b> bhejo — woh bina group-check ke store use kar payega.\n(ID user se @userinfobot pe bhijwa sakte ho, ya woh apni ID bot me dekh sakta hai.)",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:unapprove:"):
        uid_ = int(data.split(":")[2])
        ids = get_approved_ids()
        ids.discard(uid_)
        set_approved_ids(ids)
        await q.answer(f"❌ {uid_} approved list se hat gaya.")
        await _show_users(update, context)

    elif data == "admin:settings":
        await _show_settings(update, context)

    elif data.startswith("admin:setfield:"):
        field = data.split(":")[2]
        prompts = {
            "store_name": "✏️ Store ka <b>naya naam</b> bhejo (jaise: Apna AI Store):",
            "welcome_text": "📝 Home screen ka <b>naya welcome text</b> bhejo (jo 'Hello {name}' ke neeche dikhega).\nDefault pe wapas karna ho to sirf <b>-</b> bhejo:",
            "group_link": "🔗 <b>Naya Public Group link</b> bhejo (https://t.me/... se shuru hona chahiye).\nPurane (env wale) link pe wapas karna ho to sirf <b>-</b> bhejo:",
            "upi_id": "💳 Apna <b>UPI ID</b> bhejo — customer isi pe payment karega.\n(jaise: <code>9876543210@paytm</code> ya <code>naam@okhdfcbank</code>)",
        }
        context.user_data["awaiting"] = {"kind": "set_field", "field": field}
        await context.bot.send_message(q.from_user.id, prompts[field], parse_mode="HTML", reply_markup=cancel_kb())

    elif data == "admin:payqr":
        context.user_data["awaiting"] = {"kind": "set_payqr"}
        await context.bot.send_message(
            q.from_user.id,
            "🖼️ <b>Payment QR Photo</b>\n\nApne UPI app (GPay/PhonePe/Paytm) wala <b>QR code photo</b> bhejo — "
            "wahi QR har customer ko payment screen pe dikhega.\n\n"
            "💡 Tip: QR aisa rakho jisme amount khud bharna pade — bot amount alag se batata hai. "
            "Agar QR kisi ek amount pe lock hai to purane hisaab se dikkat hogi.",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data == "admin:osearch":
        context.user_data["awaiting"] = {"kind": "order_search"}
        await context.bot.send_message(
            q.from_user.id,
            "🔍 <b>Order Search</b>\n\nOrder ID bhejo — jaise <b>EL1005</b> (ya sirf number):",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data in ("admin:products", "admin:stock", "admin:setphoto"):
        await _show_products_list(update, context)

    elif data == "admin:prodadd":
        context.user_data["prod_draft"] = {}
        context.user_data["awaiting"] = {"kind": "pw_name"}
        await context.bot.send_message(
            q.from_user.id,
            "🆕 <b>Naya Product — Step 1/8: Naam</b>\n\n"
            "Product ka <b>naam</b> bhejo:\n(jaise: <i>ChatGPT Plus — 1 Month</i>)",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:prod:"):
        await _show_product_detail(update, context, int(data.split(":")[2]))

    elif data.startswith("admin:prodhide:") or data.startswith("admin:prodshow:"):
        pid = int(data.split(":")[2])
        hide = data.startswith("admin:prodhide:")
        db_exec("UPDATE products SET is_active=? WHERE id=?", (0 if hide else 1, pid))
        await q.answer("🙈 Product hide ho gaya — store me nahi dikhega." if hide else "✅ Product LIVE ho gaya — store me dikhega!")
        await _show_product_detail(update, context, pid)

    elif data.startswith("admin:prodedit:"):
        parts = data.split(":")
        field, pid = parts[2], int(parts[3])
        prod = get_product(pid)
        if not prod:
            await q.answer("Product nahi mila.", show_alert=True)
            return
        prompts = {
            "name": f"✏️ <b>{prod['name']}</b> ka naya <b>naam</b> bhejo:",
            "price_inr": f"💰 <b>{prod['name']}</b> ka naya <b>daam (₹)</b> — sirf number bhejo:",
            "mrp_text": f"🏷️ <b>{prod['name']}</b> ka <b>asli (cut wala) daam</b> bhejo — jaise ₹599. Hatana ho to sirf - bhejo:",
            "description": f"📝 <b>{prod['name']}</b> ki nayi <b>details/description</b> likh ke bhejo (kai line bhi chalega). Khali rakhna ho to - bhejo:",
            "validity_days": f"⏳ <b>{prod['name']}</b> ki <b>Validity kitne din</b>? Sirf number bhejo:",
            "warranty_days": f"🛡️ <b>{prod['name']}</b> ki <b>Warranty kitne din</b>? Sirf number bhejo:",
        }
        context.user_data["awaiting"] = {"kind": "pe_field", "pid": pid, "field": field}
        await context.bot.send_message(q.from_user.id, prompts[field], parse_mode="HTML", reply_markup=cancel_kb())

    elif data.startswith("admin:prodstock:"):
        parts = data.split(":")
        mode, pid = parts[2], int(parts[3])
        prod = get_product(pid)
        if not prod:
            await q.answer("Product nahi mila.", show_alert=True)
            return
        context.user_data["awaiting"] = {"kind": "pe_stock", "pid": pid, "mode": mode}
        await context.bot.send_message(
            q.from_user.id,
            f"📦 <b>{prod['name']}</b> — abhi stock: <b>{prod['stock']}</b>\n"
            + ("🔢 Kitna stock <b>SET</b> karna hai? Number bhejo:" if mode == "set" else "🔢 Kitna stock <b>ADD</b> karna hai? Number bhejo:")
            + "\n♾️ Ya <b>unlimited</b> likho — bina-number wala mode (humesha Available) ho jayega.",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:prodphoto:"):
        pid = int(data.split(":")[2])
        prod = get_product(pid)
        if not prod:
            await q.answer("Product nahi mila.", show_alert=True)
            return
        context.user_data["awaiting"] = {"kind": "pe_photo", "pid": pid}
        await context.bot.send_message(
            q.from_user.id,
            f"🖼️ <b>{prod['name']}</b> ki photo bhejo —\njo photo bhejoge wahi <b>sirf isi product</b> pe lagegi, dusre product ki photo se koi lena-dena nahi.",
            parse_mode="HTML", reply_markup=cancel_kb(),
        )

    elif data.startswith("admin:proddelok:"):
        pid = int(data.split(":")[2])
        db_exec("DELETE FROM products WHERE id=?", (pid,))
        await q.answer("🗑️ Product delete ho gaya.")
        await _show_products_list(update, context)

    elif data.startswith("admin:proddel:"):
        pid = int(data.split(":")[2])
        prod = get_product(pid)
        if not prod:
            await q.answer("Product nahi mila.", show_alert=True)
            return
        orders_c = db_one("SELECT COUNT(*) AS c FROM orders WHERE product_id=?", (pid,))["c"]
        if orders_c:
            await q.edit_message_text(
                f"⚠️ <b>{prod['name']}</b> delete <b>nahi</b> ho sakta — iske {orders_c} order pehle se jude hain (unka record rakhna zaroori hai).\n\n"
                "Bechna band karna hai to <b>Hide</b> kar do — store se gayab ho jayega, purane customers ka data safe rahega.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Product Pe Wapas", callback_data=f"admin:prod:{pid}")]]),
            )
        else:
            await q.edit_message_text(
                f"🗑️ <b>Pakka delete kar dun?</b>\n\nProduct: <b>{prod['name']}</b>\nYe wapas nahi aayega.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🗑️ Haan, Pakka Delete", callback_data=f"admin:proddelok:{pid}")],
                        [InlineKeyboardButton("↩️ Nahi, Wapas", callback_data=f"admin:prod:{pid}")],
                    ]
                ),
            )

    elif data.startswith("admin:prodmode:"):
        pid = int(data.split(":")[2])
        prod = get_product(pid)
        if not prod:
            await q.answer("Product nahi mila.", show_alert=True)
            return
        new_mode = "count" if (prod.get("stock_mode") or "count") == "unlimited" else "unlimited"
        db_exec("UPDATE products SET stock_mode=? WHERE id=?", (new_mode, pid))
        await q.answer("♾️ Unlimited mode ON — ab ye product humesha Available rahega!" if new_mode == "unlimited" else "🔢 Number mode ON — ab stock gina jayega.")
        await _show_product_detail(update, context, pid)

    elif data == "input:cancel":
        context.user_data.pop("awaiting", None)
        context.user_data.pop("prod_draft", None)
        await q.answer("❌ Cancel ho gaya.")
        await send_admin_panel(update, context)

    elif data == "prodwiz:skip":
        if context.user_data.get("awaiting", {}).get("kind") == "pw_photo":
            await _finalize_product_wizard(update, context, None)

    elif data == "admin:broadcast":
        context.user_data["awaiting"] = {"kind": "broadcast"}
        await context.bot.send_message(
            q.from_user.id,
            "📣 <b>Broadcast</b> — jo message (text/photo) bhejoge, woh <b>sab users</b> ko jayega. Dhyaan se bhejna!",
            parse_mode="HTML", reply_markup=cancel_kb(),
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
        rows.append(admin_nav_row("admin:panel"))
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
                admin_nav_row("admin:reports"),
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
        await context.bot.send_message(q.from_user.id, f"💬 <b>{report_code(rid)}</b> — customer ko bhejne wala jawab likho:", parse_mode="HTML", reply_markup=cancel_kb())

    elif data.startswith("admin:rreplace:"):
        rid = int(data.split(":")[2])
        context.user_data["awaiting"] = {"kind": "report_replace", "report_id": rid}
        await context.bot.send_message(
            q.from_user.id,
            f"🔄 <b>{report_code(rid)}</b> — replacement access/details bhejo, bot customer ko bhej dega aur report band kar dega:",
            parse_mode="HTML", reply_markup=cancel_kb(),
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
    context.user_data.pop("prod_draft", None)
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

    # ---- Payment proof: screenshot ----
    if kind == "pay_shot":
        if msg.photo:
            context.user_data["pay_shot"] = msg.photo[-1].file_id
            product = get_product(context.user_data.get("buy_pid")) or default_product()
            context.user_data["awaiting"] = {"kind": "pay_utr"}
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Skip — UTR Nahi Hai", callback_data="pay:utrskip")]])
            amt = int(product["price_inr"]) if product else "?"
            await msg.reply_text(
                f"✅ Screenshot mil gaya! (₹{amt} wala payment)\n\n"
                "🧾 Ab <b>UTR / Transaction ID</b> bhejo — 12 digit ka number hota hai, payment ke receipt/screenshot me dikhta hai. Isse verify turant hoga.\n"
                "Nahi mil raha to <b>Skip</b> dabao:",
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            await msg.reply_text("📸 Photo (screenshot) bhejo — sirf text se payment verify nahi hoga.")
        return

    # ---- Payment proof: UTR ----
    if kind == "pay_utr":
        if not msg.text:
            await msg.reply_text("✍️ UTR number likh ke bhejo — ya upar Skip dabao.")
            return
        await finalize_pay_review(context, uid, msg.text.strip()[:40])
        return

    # ---- Neeche wale sirf ADMIN ke liye ----
    if not is_admin(uid):
        context.user_data.pop("awaiting", None)
        return

    # ---- Settings: Payment QR photo ----
    if kind == "set_payqr":
        if msg.photo:
            set_setting("pay_qr_file_id", msg.photo[-1].file_id)
            context.user_data.pop("awaiting", None)
            await msg.reply_photo(
                msg.photo[-1].file_id,
                caption="✅ <b>Ye QR set ho gaya!</b>\nAb har customer ko payment screen pe yahi QR dikhega.",
                parse_mode="HTML",
            )
        else:
            await msg.reply_text("🖼️ QR wali photo bhejo.")
        return

    # ---- Naya Product Wizard (step-by-step, sab bot se) ----
    if kind == "pw_name":
        if not (msg.text and msg.text.strip()):
            await msg.reply_text("✍️ Product ka naam likh ke bhejo.")
            return
        context.user_data.setdefault("prod_draft", {})["name"] = msg.text.strip()[:60]
        context.user_data["awaiting"] = {"kind": "pw_price"}
        await msg.reply_text("🆕 <b>Step 2/8 — Daam</b>\n\nKitne <b>₹ rupaye</b> me bechna hai? Sirf number bhejo (jaise: 199)", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_price":
        n = _to_int(msg.text) if msg.text else None
        if n is None or n < 1 or n > 100000:
            await msg.reply_text("🔢 Sirf sahi number bhejo (1 se 100000 ke beech).")
            return
        context.user_data.setdefault("prod_draft", {})["price"] = n
        context.user_data["awaiting"] = {"kind": "pw_mrp"}
        await msg.reply_text("🆕 <b>Step 3/8 — Asli Daam (cut wala)</b>\n\nMRP jaisa dikhana hai to bhejo (jaise: ₹599).\nNahi dikhana to sirf <b>-</b> bhejo.", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_mrp":
        t = (msg.text or "").strip()
        context.user_data.setdefault("prod_draft", {})["mrp"] = "" if t == "-" else t[:30]
        context.user_data["awaiting"] = {"kind": "pw_validity"}
        await msg.reply_text("🆕 <b>Step 4/8 — Validity</b>\n\nValidity kitne <b>din</b> ki hai? Sirf number bhejo (jaise: 30)", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_validity":
        n = _to_int(msg.text) if msg.text else None
        if n is None or n < 0 or n > 3650:
            await msg.reply_text("🔢 Sirf number bhejo (0 se 3650 ke beech).")
            return
        context.user_data.setdefault("prod_draft", {})["validity"] = n
        context.user_data["awaiting"] = {"kind": "pw_warranty"}
        await msg.reply_text("🆕 <b>Step 5/8 — Warranty</b>\n\nWarranty kitne <b>din</b> ki hai? Sirf number bhejo (jaise: 25)", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_warranty":
        n = _to_int(msg.text) if msg.text else None
        if n is None or n < 0 or n > 3650:
            await msg.reply_text("🔢 Sirf number bhejo (0 se 3650 ke beech).")
            return
        context.user_data.setdefault("prod_draft", {})["warranty"] = n
        context.user_data["awaiting"] = {"kind": "pw_stock"}
        await msg.reply_text("🆕 <b>Step 6/8 — Stock</b>\n\nDo tareeke hain:\n🔢 <b>Number</b> bhejo (jaise: 10) — utna hi bikega, khatm hone pe Out of Stock\n♾️ <b>unlimited</b> likho — bina ginti ke humesha Available rahega (number wala jhanjhat hi nahi)\n\nJo chaahe bhejo:", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_stock":
        t = (msg.text or "").strip().lower()
        if t in ("unlimited", "humesha", "infinite", "♾️"):
            context.user_data.setdefault("prod_draft", {})["stock"] = 0
            context.user_data["prod_draft"]["stock_mode"] = "unlimited"
            n = 0
        else:
            n = _to_int(msg.text) if msg.text else None
            if n is None or n < 0 or n > 1000000:
                await msg.reply_text("🔢 Number bhejo (0 ya zyada) — ya <b>unlimited</b> likho.", parse_mode="HTML")
                return
            context.user_data.setdefault("prod_draft", {})["stock"] = n
            context.user_data["prod_draft"]["stock_mode"] = "count"
        context.user_data["awaiting"] = {"kind": "pw_desc"}
        await msg.reply_text("🆕 <b>Step 7/8 — Details</b>\n\nProduct ki <b>details/description</b> likho — features wagera jo customer ko dikhana hai (kai line bhi chalega).\nKuch nahi likhna to sirf <b>-</b> bhejo.", parse_mode="HTML", reply_markup=cancel_kb())
        return

    if kind == "pw_desc":
        t = (msg.text or "").strip()
        context.user_data.setdefault("prod_draft", {})["desc"] = "" if t == "-" else t[:1500]
        context.user_data["awaiting"] = {"kind": "pw_photo"}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Photo Nahi Hai — Skip Karo", callback_data="prodwiz:skip")],
            [InlineKeyboardButton("❌ Cancel Karo", callback_data="input:cancel")],
        ])
        draft_name = context.user_data.get("prod_draft", {}).get("name", "is product")
        await msg.reply_text(
            f"🆕 <b>Step 8/8 — Photo</b>\n\n<b>{draft_name}</b> ki <b>photo bhejo</b> 📸 — jo photo bhejoge wahi isi product pe lagegi.\nPhoto nahi hai to Skip dabao:",
            parse_mode="HTML", reply_markup=kb,
        )
        return

    if kind == "pw_photo":
        if msg.photo:
            await _finalize_product_wizard(update, context, msg.photo[-1].file_id)
        else:
            await msg.reply_text("📸 Photo bhejo, ya upar wala Skip button dabao.")
        return

    if kind == "approve_id":
        n = _to_int(msg.text) if msg.text else None
        if n is None or n <= 0:
            await msg.reply_text("🔢 Sahi Telegram ID (number) bhejo.")
            return
        ids = get_approved_ids()
        ids.add(n)
        set_approved_ids(ids)
        context.user_data.pop("awaiting", None)
        await msg.reply_text(f"✅ User <code>{n}</code> approve ho gaya — ab woh bina group-check ke store use kar sakta hai. Usko bolo /start dobara kare.", parse_mode="HTML")
        return

    if kind == "set_field":
        field = state.get("field")
        t = (msg.text or "").strip()
        if field == "store_name":
            if not t:
                await msg.reply_text("✍️ Naam khali nahi ho sakta.")
                return
            set_setting("store_name", t[:40])
            context.user_data.pop("awaiting", None)
            await msg.reply_text(f"✅ Store ka naam ab: <b>{t[:40]}</b> — turant LIVE hai.", parse_mode="HTML")
        elif field == "welcome_text":
            set_setting("welcome_text", "" if t == "-" else t[:600])
            context.user_data.pop("awaiting", None)
            await msg.reply_text("✅ Welcome text " + ("default pe wapas ho gaya." if t == "-" else "badal gaya — Home pe turant dikhega."), parse_mode="HTML")
        elif field == "upi_id":
            if "@" in t and " " not in t and 6 <= len(t) <= 80:
                set_setting("upi_id", t)
                context.user_data.pop("awaiting", None)
                await msg.reply_text(f"✅ UPI ID set ho gaya: <code>{t}</code> — ab customer isi pe payment karega.", parse_mode="HTML")
            else:
                await msg.reply_text("💳 Sahi UPI ID bhejo (jaise 9876543210@paytm) — @ hona chahiye, space nahi.")
                return
        elif field == "group_link":
            if t == "-":
                set_setting("group_link", "")
                context.user_data.pop("awaiting", None)
                await msg.reply_text("✅ Group link purane (env) wale pe wapas ho gaya.")
            elif t.startswith("http"):
                set_setting("group_link", t[:200])
                context.user_data.pop("awaiting", None)
                await msg.reply_text("✅ Group link badal gaya — Join/Home buttons pe turant LIVE hai.")
            else:
                await msg.reply_text("🔗 Sahi link bhejo (https:// se shuru) — ya - bhejo.")
                return
        else:
            context.user_data.pop("awaiting", None)
        return

    if kind == "order_search":
        raw = (msg.text or "").strip().upper().replace(" ", "")
        num = None
        if raw.startswith("EL") and raw[2:].isdigit():
            num = int(raw[2:]) - 1000
        elif raw.isdigit():
            v = int(raw)
            num = v - 1000 if v >= 1000 else v
        o = db_one("SELECT * FROM orders WHERE id=?", (num,)) if num else None
        context.user_data.pop("awaiting", None)
        if not o:
            await msg.reply_text("❌ Aisa koi order nahi mila. Order ID check karke dobara try karo (Admin Panel → 🔍 Order Search).")
            return
        u = db_one("SELECT * FROM users WHERE user_id=?", (o["user_id"],)) or {}
        rows = []
        if o["status"] == "pending":
            rows.append([InlineKeyboardButton(f"✅ Deliver {order_code(o['id'])}", callback_data=f"admin:deliver:{o['id']}")])
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin:panel")])
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
        await msg.reply_text(
            f"🧾 <b>Order {order_code(o['id'])}</b>\n\n"
            f"Product: {o['product']}\n"
            f"User: {u.get('first_name', '?')} (@{u.get('username', '-')}) • <code>{o['user_id']}</code>\n"
            f"Paid: ₹{o['amount_inr']}\n"
            f"Status: <b>{o['status'].title()}</b>\n"
            f"Order Time: {fmt_dt(o['created_at'])}"
            + (f"\nDelivered: {fmt_dt(o['delivered_at'])}" if o.get("delivered_at") else ""),
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    # ---- Product Edit (per-product) ----
    if kind == "pe_field":
        field = state.get("field")
        pid = state.get("pid")
        prod = get_product(pid)
        if not prod or field not in ("name", "price_inr", "mrp_text", "description", "validity_days", "warranty_days"):
            context.user_data.pop("awaiting", None)
            return
        if field in ("price_inr", "validity_days", "warranty_days"):
            n = _to_int(msg.text) if msg.text else None
            lo, hi = (1, 100000) if field == "price_inr" else (0, 3650)
            if n is None or n < lo or n > hi:
                await msg.reply_text(f"🔢 Sirf sahi number bhejo ({lo} se {hi} ke beech).")
                return
            val = n
        else:
            t = (msg.text or "").strip()
            if field == "name":
                if not t:
                    await msg.reply_text("✍️ Naam khali nahi ho sakta — naam likh ke bhejo.")
                    return
                val = t[:60]
            elif field == "mrp_text":
                val = "" if t == "-" else t[:30]
            else:  # description
                val = "" if t == "-" else t[:1500]
        db_exec(f"UPDATE products SET {field}=? WHERE id=?", (val, pid))
        context.user_data.pop("awaiting", None)
        labels = {"name": "Naam", "price_inr": "Daam", "mrp_text": "Asli daam", "description": "Details", "validity_days": "Validity", "warranty_days": "Warranty"}
        await msg.reply_text(f"✅ <b>{prod['name']}</b> ka <b>{labels[field]}</b> badal gaya — store me turant LIVE hai. 🎉", parse_mode="HTML")
        return

    if kind == "pe_stock":
        pid = state.get("pid")
        prod = get_product(pid)
        if not prod:
            context.user_data.pop("awaiting", None)
            return
        if (msg.text or "").strip().lower() in ("unlimited", "humesha", "infinite", "♾️"):
            db_exec("UPDATE products SET stock_mode='unlimited' WHERE id=?", (pid,))
            context.user_data.pop("awaiting", None)
            await msg.reply_text(f"♾️ <b>{prod['name']}</b> ab <b>Unlimited mode</b> me hai — humesha Available rahega, number ki zaroorat nahi.", parse_mode="HTML")
            return
        n = _to_int(msg.text) if msg.text else None
        if n is None or n < 0 or n > 1000000:
            await msg.reply_text("🔢 Number bhejo (0 ya zyada) — ya <b>unlimited</b> likho.", parse_mode="HTML")
            return
        db_exec("UPDATE products SET stock_mode='count' WHERE id=?", (pid,))
        new_stock = int(prod["stock"]) + n if state.get("mode") == "add" else n
        set_product_stock(pid, new_stock)
        context.user_data.pop("awaiting", None)
        await msg.reply_text(f"✅ <b>{prod['name']}</b> ka stock ab: <b>{new_stock}</b>", parse_mode="HTML")
        return

    if kind == "pe_photo":
        pid = state.get("pid")
        prod = get_product(pid)
        if not prod:
            context.user_data.pop("awaiting", None)
            return
        if msg.photo:
            fid = msg.photo[-1].file_id
            db_exec("UPDATE products SET photo_file_id=? WHERE id=?", (fid, pid))
            context.user_data.pop("awaiting", None)
            await context.bot.send_photo(
                msg.chat_id, photo=fid,
                caption=f"✅ <b>Photo lag gayi!</b>\n\nProduct: <b>{prod['name']}</b>\nAb product page pe yahi photo dikhegi — sirf isi product pe. 👍",
                parse_mode="HTML",
            )
        else:
            await msg.reply_text("🖼️ Photo bhejo.")
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
                    f"👋 Welcome {m.first_name}! <b>{get_store_name()} Public Group</b> me swagat hai.\n"
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
# Global error handler — koi bhi handler fisle to user ko khabar milegi,
# screen chup-chaap gayab nahi hogi.
# --------------------------------------------------------------------------- #
async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Handler error: %s", context.error)
    try:
        if update is not None and getattr(update, "callback_query", None):
            try:
                await update.callback_query.answer(
                    "⚠️ Kuch gadbad ho gayi — ek baar dobara try karo. Dikkat rahe to /start dabao.",
                    show_alert=True,
                )
                return
            except Exception:
                pass  # query purana/answer ho chuka — neeche chat message bhej do
        if update is not None and getattr(update, "effective_chat", None):
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ Kuch gadbad ho gayi — dobara try karo, ya /start dabake shuru se aao.",
            )
    except Exception:
        pass


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
                    f"Renew karna ho to bot me My Subscription se Renew dabao — ₹{(get_product(s.get('product_id')) or get_product_by_name(s.get('product')) or {}).get('price_inr', PRICE_INR)}.",
                    parse_mode="HTML",
                )
            except Exception:
                pass


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    today = now_utc().astimezone(IST).strftime("%Y-%m-%d")
    todays = [o for o in db_all("SELECT * FROM orders") if (from_iso(o["created_at"]) or now_utc()).astimezone(IST).strftime("%Y-%m-%d") == today]
    kamai = sum((o["amount_inr"] or 0) for o in todays if o["status"] in ("pending", "delivered"))
    pending = db_one("SELECT COUNT(*) AS c FROM orders WHERE status='pending'")["c"]
    text = (
        f"🌙 <b>Daily Report — {today}</b>\n\n"
        f"🧾 Aaj ke Orders: <b>{len(todays)}</b>\n"
        f"💰 Aaj ki Kamai: <b>₹{kamai}</b>\n"
        f"⏳ Abhi Pending: <b>{pending}</b>\n"
        f"📦 Kul Stock Bacha: <b>{total_stock()}</b>"
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
    # ---- Self-check: users ke Verify ke liye group config sabse zaroori hai.
    # Gadbad ho to bot chup nahi rahega — admin ko turant khabar jayegi.
    problems = []
    if not GROUP_ID:
        problems.append("GROUP_ID set hi nahi hai — force-join gate band hai, koi bhi user Verify nahi kar payega.")
    else:
        try:
            chat = await app.bot.get_chat(GROUP_ID)
            me = await app.bot.get_me()
            mem = await app.bot.get_chat_member(GROUP_ID, me.id)
            if mem.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                problems.append(
                    f"Bot group '{getattr(chat, 'title', '')}' me admin nahi hai (status: {mem.status}) — "
                    "membership check fail hoga. Bot ko group me admin banao."
                )
        except Exception as e:
            problems.append(
                f"Bot GROUP_ID <code>{GROUP_ID}</code> wale group tak pahunch nahi pa raha ({e}). "
                "Render → Environment me GROUP_ID bilkul sahi hona chahiye: <code>-1003961533075</code> (aage minus ke saath, bina space/quotes ke)."
            )
    if problems:
        log.warning("SELF-CHECK problems: %s", problems)
        alert = (
            "⚠️ <b>Bot Self-Check — kuch gadbad mili hai!</b>\n\n"
            + "\n".join("• " + x for x in problems)
            + "\n\nUsers ka Verify isiliye atak sakta hai. Poori jaanch: Admin Panel → 🩺 Health Check"
        )
        for aid in ADMIN_IDS:
            try:
                await app.bot.send_message(aid, alert, parse_mode="HTML")
            except Exception:
                pass


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
    app.add_handler(CallbackQueryHandler(cb_admin, pattern="^(admin:|prodwiz:|input:)"))
    app.add_handler(CallbackQueryHandler(cb_nav, pattern="^(nav:|noop$)"))

    app.add_handler(CallbackQueryHandler(cb_pay, pattern=r"^(pay:|payok:|payno:)"))

    # Pending-input (private chat) — commands ke alawa
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.ATTACHMENT) & ~filters.COMMAND,
            handle_input,
        )
    )
    # Public group guard
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_guard))
    app.add_error_handler(error_handler)

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
        lines.append(f"• <b>{order_code(o['id'])}</b> — ₹{o['amount_inr']} • {status_label(o['status'])} • {fmt_dt(o['created_at'])}")
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

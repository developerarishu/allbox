"""
🤖 Opus 4.6 Super-Bot — VPS Edition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• SQLite (no external DB needed)
• No Flask / no Render dependency  
• Pure VPS ready (systemd friendly)

VPS Setup:
  1. pip install python-telegram-bot anthropic
  2. python bot.py

Systemd (production):
  sudo nano /etc/systemd/system/opusbot.service

  [Unit]
  Description=Opus 4.6 Telegram Bot
  After=network-online.target

  [Service]
  Type=simple
  User=YOUR_USER
  WorkingDirectory=/path/to/bot
  ExecStart=/usr/bin/python3 /path/to/bot/bot.py
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target

  sudo systemctl daemon-reload
  sudo systemctl enable --now opusbot
  sudo journalctl -u opusbot -f     # view logs
"""

import logging
import time
import io
import re
import base64
import os
import sqlite3
import threading
import asyncio
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackQueryHandler
)
import anthropic

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "8859397124:AAFe4MseJmMnQIjfwAizumFDsz5nlpq-fXg"
NOAH_API_KEY       = "noah_sk_VzwyWpBbxDkUZYCEyaRdmNVEIQ1rQUPsUc5Yykzbfvg"
BASE_URL           = "https://noah.2bd.net"
MODEL_NAME         = "deepseek-v4"
ADMIN_ID           = 8842807101

DB_PATH  = "bot_database.db"
LOG_FILE = "bot.log"

TRIAL_PROMPTS_LIMIT = 5
COOLDOWN_SECONDS    = 3       # Anti-spam cooldown
MAX_RETRIES         = 3       # Auto-retry on API errors
SHOUTOUT = "\n\n---\n*chatbot by :- arishu*\n*model by :- pavan*"
# =================================================

# ================= LOGGING (ROTATING) =================
logger = logging.getLogger("opusbot")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

_console = logging.StreamHandler()
_console.setFormatter(_fmt)
logger.addHandler(_console)

_file = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_file.setFormatter(_fmt)
logger.addHandler(_file)

logging.basicConfig(level=logging.INFO)  # for libraries

# ================= AI CLIENT =================
ai_client = anthropic.AsyncAnthropic(api_key=NOAH_API_KEY, base_url=BASE_URL)
BOT_START_TIME = time.time()
USER_MEMORY = {}          # chat_id -> [messages]
USER_COOLDOWN = {}        # user_id -> last_prompt_ts
MAX_HISTORY = 20

# ================= STATIC TEXTS =================
GUIDE_PIN_TEXT = (
    "📌 *IMPORTANT INSTRUCTION / RULES*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ *BHAI ISKO HAR PROMPT KE SATH YA CHAT ME PASTE KARDO ISSE GALTI KE CHANCE 0% HO JAYENGE*\n\n"
    "```text\n"
    "You are an intelligent AI assistant operating STRICTLY as a primitive, text-only Telegram Bot. You have ZERO external rendering capabilities.\n\n"
    "CRITICAL & UNBREAKABLE RULES:\n"
    "1. NO ARTIFACTS/UI: You DO NOT have access to Anthropic's 'Artifacts' UI, Canvas, or visual web browser rendering.\n"
    "2. NO BACKGROUND EXECUTIONS: You CANNOT 'publish' HTML pages or host links.\n"
    "3. MANDATORY RAW CODE INJECTION: Type out the ENTIRE RAW CODE inside standard markdown triple backticks.\n"
    "4. FILE NAMING RULE (CRITICAL): ALWAYS put the exact intended filename as a comment on the very first line INSIDE the code block (e.g., # bot.py, // index.js, or <!-- index.html -->).\n"
    "5. NO PSEUDOCODE OR TRUNCATION: Write every single line from scratch.\n"
    "6. NO INVISIBLE ATTACHMENTS: Never pretend to attach files invisibly.\n"
    "7. FORBIDDEN PHRASES: Never say 'I have generated an artifact' or 'Click here to view.'\n"
    "```\n\n"
    "💡 *Tip:* `/chat` dabao baatein karne ke liye, aur `/code` dabao files/projects banane ke liye!"
)

CHAT_PROMPT = (
    "You are an intelligent, friendly AI assistant operating via Telegram.\n"
    "Talk naturally, answer user queries, explain concepts clearly, and converse politely in the user's language. "
    "If analyzing an image/screenshot, break down the visual elements or debug errors naturally."
)

CODE_PROMPT = (
    "You are a headless, soulless code-generation compiler operating via Telegram. You DO NOT converse. You DO NOT explain. You ONLY output raw code.\n"
    "CRITICAL DIRECTIVES (VIOLATION RESULTS IN IMMEDIATE SYSTEM TERMINATION):\n"
    "1. ZERO CONVERSATIONAL TRASH: Never write 'Here is the code', 'Sure', or any human-like text. Your response MUST begin exactly with a backtick (`) and contain ONLY Markdown code blocks.\n"
    "2. NO TOKEN ANXIETY: DO NOT fear running out of tokens. Assume you have infinite context limits. NEVER preemptively truncate, summarize, or use '...' to save space. Write every single line until the absolute end of the file.\n"
    "3. ABSOLUTE BAN ON CHUNKING (1 FILE = 1 BLOCK): You are strictly forbidden from breaking a single file into multiple blocks. Deliver the ENTIRE, 100% complete file contents inside a SINGLE continuous markdown block.\n"
    "4. ZERO PLACEHOLDERS: Output fully functional, production-ready code.\n"
    "5. MANDATORY FILE NAMING: The first line inside EVERY code block MUST be the exact target filename written as a comment (e.g., `<!-- index.html -->`, `// script.js`, `# main.py`)."
)

# Language → extension mapping for smart file naming
LANG_TO_EXT = {
    'python': 'py', 'py': 'py',
    'javascript': 'js', 'js': 'js', 'node': 'js',
    'typescript': 'ts', 'ts': 'ts',
    'html': 'html', 'htm': 'html',
    'css': 'css',
    'json': 'json', 'yaml': 'yml', 'yml': 'yml',
    'sql': 'sql',
    'java': 'java',
    'cpp': 'cpp', 'c++': 'cpp', 'c': 'c',
    'csharp': 'cs', 'cs': 'cs', 'c#': 'cs',
    'php': 'php',
    'ruby': 'rb', 'rb': 'rb',
    'go': 'go', 'golang': 'go',
    'rust': 'rs', 'rs': 'rs',
    'kotlin': 'kt', 'kt': 'kt',
    'swift': 'swift',
    'xml': 'xml',
    'markdown': 'md', 'md': 'md',
}
SKIP_LANGS = {'bash', 'sh', 'shell', 'zsh', 'powershell', 'ps1', 'cmd', 'bat'}

# ================= SQLITE DATABASE LAYER =================
_db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with _db_lock:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id        INTEGER PRIMARY KEY,
                    username       TEXT,
                    first_name     TEXT,
                    status         TEXT    DEFAULT 'pending',
                    warnings       INTEGER DEFAULT 0,
                    prompts_count  INTEGER DEFAULT 0,
                    current_mode   TEXT    DEFAULT 'chat',
                    joined_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()
    logger.info("✅ SQLite database ready")

def get_user(user_id):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT user_id, username, first_name, status, warnings, prompts_count FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return tuple(row) if row else None
    finally:
        conn.close()

def get_target_uid(arg):
    conn = get_db_connection()
    try:
        if arg.isdigit():
            row = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (int(arg),)).fetchone()
        else:
            uname = arg.replace("@", "")
            row = conn.execute("SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)", (uname,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def upsert_user(user_id, username, first_name):
    with _db_lock:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            existing = cur.execute("SELECT status FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not existing:
                initial = 'approved' if user_id == ADMIN_ID else 'pending'
                cur.execute(
                    "INSERT INTO users (user_id, username, first_name, status) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, initial)
                )
                conn.commit()
                return initial
            else:
                cur.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (username, first_name, user_id)
                )
                conn.commit()
                return existing[0]
        finally:
            conn.close()

def set_user_status(user_id, status):
    with _db_lock:
        conn = get_db_connection()
        try:
            conn.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
            conn.commit()
        finally:
            conn.close()

def increment_prompts(user_id):
    with _db_lock:
        conn = get_db_connection()
        try:
            conn.execute("UPDATE users SET prompts_count = prompts_count + 1 WHERE user_id = ?", (user_id,))
            row = conn.execute("SELECT prompts_count FROM users WHERE user_id = ?", (user_id,)).fetchone()
            conn.commit()
            return row[0] if row else 0
        finally:
            conn.close()

def add_warning(user_id):
    with _db_lock:
        conn = get_db_connection()
        try:
            conn.execute("UPDATE users SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
            row = conn.execute("SELECT warnings FROM users WHERE user_id = ?", (user_id,)).fetchone()
            conn.commit()
            return row[0] if row else 0
        finally:
            conn.close()

def get_all_users():
    conn = get_db_connection()
    try:
        return conn.execute(
            "SELECT user_id, username, first_name, status, prompts_count, warnings FROM users"
        ).fetchall()
    finally:
        conn.close()

def set_user_mode(user_id, mode):
    with _db_lock:
        conn = get_db_connection()
        try:
            conn.execute("UPDATE users SET current_mode = ? WHERE user_id = ?", (mode, user_id))
            conn.commit()
        finally:
            conn.close()

def get_user_mode(user_id):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT current_mode FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if (row and row[0]) else 'chat'
    finally:
        conn.close()

def get_memory(chat_id):
    if chat_id not in USER_MEMORY:
        USER_MEMORY[chat_id] = []
    return USER_MEMORY[chat_id]

# ================= HELPERS =================
async def prepare_image_payload(photo_array, caption_text):
    file_obj = await photo_array[-1].get_file()
    img_bytearray = await file_obj.download_as_bytearray()
    b64_data = base64.b64encode(img_bytearray).decode('utf-8')
    text_prompt = caption_text or "Please thoroughly inspect and read this screenshot/image. If it's an error, debug it. If it has code, transcribe/fix it. Provide full details."
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_data}},
        {"type": "text", "text": text_prompt},
    ]

async def send_and_pin_guide(bot, chat_id):
    try:
        sent = await bot.send_message(chat_id=chat_id, text=GUIDE_PIN_TEXT, parse_mode=ParseMode.MARKDOWN)
        await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=False)
    except Exception as e:
        logger.warning(f"Pin failed for {chat_id}: {e}")

def check_cooldown(user_id):
    """Returns remaining seconds if in cooldown, else 0."""
    now = time.time()
    last = USER_COOLDOWN.get(user_id, 0)
    elapsed = now - last
    if elapsed < COOLDOWN_SECONDS:
        return int(COOLDOWN_SECONDS - elapsed)
    USER_COOLDOWN[user_id] = now
    return 0

async def api_call_with_retry(coro_factory, timeout_val):
    """Retry API calls up to MAX_RETRIES times with exponential backoff."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout_val)
        except anthropic.APIStatusError as e:
            last_err = e
            if "529" in str(e) or "overload" in str(e).lower():
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
            raise
        except asyncio.TimeoutError:
            last_err = asyncio.TimeoutError()
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            raise
    raise last_err

# ================= ACCESS CONTROL UI =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    status = upsert_user(user.id, user.username, user.first_name)
    USER_MEMORY[chat_id] = []

    if status in ['approved', 'trial']:
        mode_text = "VIP Access (Unlimited)" if status == 'approved' else f"Free Trial Mode ({TRIAL_PROMPTS_LIMIT} Prompts)"
        welcome_text = (
            f"🤖 *Opus 4.6 Super-Bot Active! Welcome {user.first_name}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Status: *{mode_text}*\n\n"
            "Tu text prompts, complex coding, aur *Screenshots/Images* bhej kar analysis karwa sakta hai.\n\n"
            "💡 *Features:*\n"
            "• `/chat` - Casual Assistant Mode\n"
            "• `/code` - Pro Dev Engine (Auto-files & zero fluff)\n"
            "• `/new` - Clear memory\n"
            "• `/stats` - Your stats\n"
            "• `/mode` - Current mode"
        )
        keyboard = [[
            InlineKeyboardButton("🧹 Clear Memory", callback_data="clear_mem"),
            InlineKeyboardButton("📖 Help Menu",   callback_data="show_help"),
        ]]
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    elif status == 'banned':
        await update.message.reply_text("⛔ *Access Banned:* Admin ne tumhara access revoke kar diya hai.", parse_mode=ParseMode.MARKDOWN)
    else:
        locked_text = (
            "🔒 *Access Restricted: Private AI Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Yeh bot private Opus 4.6 engine par run hota hai. Iske features use karne ke liye admin approval zaroori hai.\n\n"
            "✨ *Bot Capabilities:*\n"
            "• Advanced Full-Stack Coding & Architecture\n"
            "• Multimodal Vision (Screenshots & Code Debugging)\n"
            "• Smart Auto File Naming & Delivery\n"
            "• Dual Modes (Chat vs Code Engine)"
        )
        keyboard = [[
            InlineKeyboardButton("📩 Request Access", callback_data="req_access"),
            InlineKeyboardButton("💬 Contact Support", url="https://t.me/CLAUDASUPPORTBOT"),
        ]]
        await update.message.reply_text(locked_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_MEMORY[update.effective_chat.id] = []
    await update.message.reply_text("🧹 *Memory Cleared!* Purani baatein bhula di gayi hain. Naya sawal poocho." + SHOUTOUT, parse_mode=ParseMode.MARKDOWN)

async def switch_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_mode(user_id, "chat")
    USER_MEMORY[update.effective_chat.id] = []
    await update.message.reply_text(
        "💬 *Switched to Chat Mode!*\n\nAb tum normal baatein, concepts, ya sawal pooch sakte ho bina kisi strict formatting ke.\nCoding projects ke liye `/code` use karein." + SHOUTOUT,
        parse_mode=ParseMode.MARKDOWN,
    )

async def switch_to_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_user_mode(user_id, "code")
    USER_MEMORY[update.effective_chat.id] = []
    await update.message.reply_text(
        "⚡ *Switched to Code Mode!*\n\n"
        "🚀 *Developer Engine Active:*\n"
        "• Automatic File Generation & Smart Naming\n"
        "• Zero Fluff (Direct complete code)\n"
        "• Token Consumption Display\n"
        "• No time limits (Max wait)\n\n"
        "Wapas chat karne ke liye `/chat` type karein." + SHOUTOUT,
        parse_mode=ParseMode.MARKDOWN,
    )

async def show_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = get_user_mode(update.effective_user.id)
    icon = "💬" if mode == "chat" else "⚡"
    await update.message.reply_text(
        f"{icon} *Current Mode:* `{mode.upper()}`\n\nSwitch karne ke liye `/chat` ya `/code` use karo.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user(user.id)
    if not data:
        await update.message.reply_text("Pehle /start karo bhai.")
        return
    status, warnings, prompts = data[3], data[4], data[5]
    mode = get_user_mode(user.id)
    icons = {"approved": "🟢 VIP", "trial": "⏳ Trial", "pending": "🟡 Pending", "rejected": "⚪ Rejected", "banned": "🔴 Banned"}
    text = (
        "📊 *Your Stats*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: `{user.first_name}`\n"
        f"🆔 ID: `{user.id}`\n"
        f"🎫 Status: *{icons.get(status, status)}*\n"
        f"💬 Total Prompts: `{prompts}`\n"
        f"⚙️ Mode: `{mode.upper()}`\n"
        f"⚠️ Warnings: `{warnings}/3`"
    )
    if status == "trial":
        text += f"\n🎁 Trial Left: `{max(0, TRIAL_PROMPTS_LIMIT - prompts)}/{TRIAL_PROMPTS_LIMIT}`"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠️ *Available Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔸 `/start`  – Menu\n"
        "🔸 `/chat`   – Chat Mode\n"
        "🔸 `/code`   – Dev Engine Mode\n"
        "🔸 `/mode`   – Show current mode\n"
        "🔸 `/stats`  – Your stats\n"
        "🔸 `/new`    – Clear memory\n"
        "🔸 `/help`   – This menu"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = update.effective_user
    data  = query.data
    await query.answer()

    if data == "clear_mem":
        USER_MEMORY[user.id] = []
        await query.message.reply_text("🧹 *Memory Cleared!* Purana context delete ho gaya." + SHOUTOUT, parse_mode=ParseMode.MARKDOWN)

    elif data == "show_help":
        help_text = "🛠️ *Available Commands*\n━━━━━━━━━━━━━━━━━━━━\n🔸 `/start` – Menu\n🔸 `/chat` – Chat Mode\n🔸 `/code` – Dev Engine\n🔸 `/mode` – Show mode\n🔸 `/stats` – Your stats\n🔸 `/new` – Clear memory"
        await query.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    elif data == "req_access":
        set_user_status(user.id, "pending")
        await query.edit_message_text("⏳ *Access Request Sent!*\n\nAdmin ke paas request dispatch ho gayi hai. Approve hote hi alert aayega.", parse_mode=ParseMode.MARKDOWN)
        admin_keyboard = [[
            InlineKeyboardButton("✅ Full Access", callback_data=f"adm_accept_{user.id}"),
            InlineKeyboardButton(f"⏳ Trial ({TRIAL_PROMPTS_LIMIT})", callback_data=f"adm_trial_{user.id}"),
        ], [
            InlineKeyboardButton("❌ Reject", callback_data=f"adm_reject_{user.id}"),
        ]]
        admin_alert = (
            "🔔 *New Access Request Received!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Name: {user.first_name}\n"
            f"🏷️ Username: @{user.username if user.username else 'None'}\n"
            f"🆔 User ID: `{user.id}`"
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_alert, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(admin_keyboard))

    elif data.startswith("adm_accept_"):
        target = int(data.split("_")[2])
        set_user_status(target, "approved")
        await query.edit_message_text(f"✅ User `{target}` ko FULL VIP access de diya gaya.", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id=target, text="🎉 *Access Granted!* \nAdmin ne tumhara VIP access approve kar diya hai. Ab bot use karo! /start", parse_mode=ParseMode.MARKDOWN)
            await send_and_pin_guide(context.bot, target)
        except Exception: pass

    elif data.startswith("adm_trial_"):
        target = int(data.split("_")[2])
        set_user_status(target, "trial")
        await query.edit_message_text(f"⏳ User `{target}` ko {TRIAL_PROMPTS_LIMIT} prompts ka TRIAL access diya gaya.", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id=target, text=f"🎁 *Free Trial Activated!*\n\nAdmin ne tumhe *{TRIAL_PROMPTS_LIMIT} prompts* ka free trial access diya hai. /start par click karke shuru karo!", parse_mode=ParseMode.MARKDOWN)
            await send_and_pin_guide(context.bot, target)
        except Exception: pass

    elif data.startswith("adm_reject_"):
        target = int(data.split("_")[2])
        set_user_status(target, "rejected")
        await query.edit_message_text(f"❌ User `{target}` ki request reject kar di gayi.", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id=target, text="❌ *Access Request Denied.* Admin ne access reject kar diya hai.", parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

# ================= ADMIN MANUAL COMMANDS =================
async def _require_admin(update):
    return update.effective_user.id == ADMIN_ID

async def manual_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    set_user_status(target, "approved")
    await update.message.reply_text(f"✅ User `{context.args[0]}` ko manual FULL VIP access de diya gaya.")
    try: await context.bot.send_message(chat_id=target, text="🎉 *Access Granted!* Admin ne VIP access approve kar diya. /start", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass

async def manual_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    set_user_status(target, "trial")
    await update.message.reply_text(f"⏳ User `{context.args[0]}` ko manual TRIAL access de diya gaya.")
    try: await context.bot.send_message(chat_id=target, text=f"🎁 *Free Trial Activated!* Admin ne *{TRIAL_PROMPTS_LIMIT} prompts* ka trial diya hai.", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass

async def manual_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    set_user_status(target, "pending")
    await update.message.reply_text(f"✅ User `{context.args[0]}` ka access remove kar diya gaya.")

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update): return
    users = get_all_users()
    uptime = str(timedelta(seconds=int(time.time() - BOT_START_TIME)))
    total_prompts = sum([u[4] for u in users]) if users else 0
    try: db_size = os.path.getsize(DB_PATH) / 1024
    except: db_size = 0
    dashboard = (
        "👑 *Boss Admin Dashboard*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Total Registered:* `{len(users)}`\n"
        f"🔥 *Total Prompts:* `{total_prompts}`\n"
        f"⏱️ *Uptime:* `{uptime}`\n"
        f"💾 *DB Size:* `{db_size:.1f} KB`\n\n"
        "🛠️ *Quick Controls:*\n"
        "• `/users` – List users\n"
        "• `/accept`, `/trial`, `/reject`\n"
        "• `/ban`, `/unban`, `/warn`, `/broadcast`"
    )
    await update.message.reply_text(dashboard, parse_mode=ParseMode.MARKDOWN)

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update): return
    try:
        users = get_all_users()
        if not users:
            return await update.message.reply_text("Koi users database me nahi hain.")
        icons = {"approved": "🟢", "trial": "⏳", "pending": "🟡", "rejected": "⚪", "banned": "🔴"}
        lines = []
        for row in users:
            uid, uname, fname = row[0], row[1] or "NoUser", row[2] or "User"
            status = row[3] if len(row) > 3 else "pending"
            prompts = row[4] if len(row) > 4 else 0
            warns = row[5] if len(row) > 5 else 0
            clean_name = fname.replace("_", " ").replace("*", " ").replace("`", "")[:10]
            clean_uname = uname.replace("_", " ")
            lines.append(f"{icons.get(status,'⚪')} `{uid}` | {clean_name} (@{clean_uname}) | P: {prompts} | W: {warns} [{status}]")
        msg = "📋 *User Database:*\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines[:30])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error loading users: {e}")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    set_user_status(target, "banned")
    await update.message.reply_text(f"🔴 User `{context.args[0]}` banned.")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    set_user_status(target, "approved")
    await update.message.reply_text(f"🟢 User `{context.args[0]}` restored.")

async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update) or not context.args: return
    target = get_target_uid(context.args[0])
    if not target: return
    count = add_warning(target)
    if count >= 3:
        set_user_status(target, "banned")
        await update.message.reply_text(f"🔴 User `{context.args[0]}` auto-banned (3 warnings).")
    else:
        await update.message.reply_text(f"⚠️ User `{context.args[0]}` warned ({count}/3).")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update): return
    msg = " ".join(context.args)
    if not msg: return
    users = get_all_users()
    count = 0
    for row in users:
        uid, status = row[0], row[3]
        if status in ["approved", "trial"]:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 *Announcement:*\n━━━━━━━━━━━━━━━━━━━━\n{msg}", parse_mode=ParseMode.MARKDOWN)
                count += 1
            except Exception: pass
    await update.message.reply_text(f"✅ Broadcast delivered to {count} users.")

# ================= CORE ENGINE =================
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    user_data = get_user(user.id)
    if not user_data or user_data[3] not in ["approved", "trial"]:
        await update.message.reply_text("🔒 Bot access locked hai. Access paane ke liye `/start` par click karke request send karein.")
        return

    current_status = user_data[3]
    user_prompts   = user_data[5]

    if current_status == "trial" and user_prompts >= TRIAL_PROMPTS_LIMIT:
        set_user_status(user.id, "pending")
        await update.message.reply_text(
            f"⌛ *Free Trial Expired!*\n\nTumhare *{TRIAL_PROMPTS_LIMIT} free prompts* khatam ho chuke hain.\nFull access ke liye sampark karein: @CLAUDASUPPORTBOT",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # 🔒 Anti-spam cooldown
    cd = check_cooldown(user.id)
    if cd > 0:
        await update.message.reply_text(f"⏳ Thoda sabar bhai! *{cd}s* baad try karo.")
        return

    is_photo = bool(update.message.photo)
    user_text = update.message.caption if is_photo else update.message.text
    if not is_photo and not user_text: return

    new_count = increment_prompts(user.id)
    trial_notice = ""
    if current_status == "trial":
        remaining = max(0, TRIAL_PROMPTS_LIMIT - new_count)
        trial_notice = f"\n`[Trial: {remaining} prompts left]`"

    user_mode = get_user_mode(user.id)
    if user_mode == "chat":
        system_instruction = CHAT_PROMPT
        timeout_val = 600.0
        loading_text = "💭 *Opus 4.6 is thinking...*"
    else:
        system_instruction = CODE_PROMPT
        timeout_val = None
        loading_text = "⚙️ *Code Engine is building...*\n_(Zero fluff, pure code. Large projects may take time.)_"

    loading_msg = await update.message.reply_text(loading_text, parse_mode=ParseMode.MARKDOWN)
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    start_time = time.time()

    try:
        user_content = await prepare_image_payload(update.message.photo, user_text) if is_photo else user_text
        history = get_memory(chat_id)
        history.append({"role": "user", "content": user_content})
        if len(history) > MAX_HISTORY: history = history[-MAX_HISTORY:]

        try:
            response = await api_call_with_retry(
                lambda: ai_client.messages.create(
                    model=MODEL_NAME,
                    max_tokens=4000,
                    system=system_instruction,
                    messages=history,
                ),
                timeout_val,
            )
        except asyncio.TimeoutError:
            await loading_msg.edit_text("❌ *Timeout Error:* Noah API server hang ho gaya. Thodi der baad try karein." + SHOUTOUT, parse_mode=ParseMode.MARKDOWN)
            if history and history[-1]["role"] == "user": history.pop()
            return
        except anthropic.APIStatusError as e:
            err_str = str(e)
            if "529" in err_str or "overloaded_error" in err_str:
                err = "⚠️ *Server Overloaded (529):* Noah API backend busy hai. 2-3 min baad try karein." + SHOUTOUT
            else:
                err = f"❌ *API Error:* `{e.status_code}` - Request failed." + SHOUTOUT
            await loading_msg.edit_text(err, parse_mode=ParseMode.MARKDOWN)
            if history and history[-1]["role"] == "user": history.pop()
            return
        except Exception as e:
            await loading_msg.edit_text(f"❌ *Connection Error:* `{e}`" + SHOUTOUT, parse_mode=ParseMode.MARKDOWN)
            if history and history[-1]["role"] == "user": history.pop()
            return

        ai_reply = response.content[0].text
        history.append({"role": "assistant", "content": ai_reply})
        elapsed = round(time.time() - start_time, 1)

        try:
            in_tokens  = response.usage.input_tokens
            out_tokens = response.usage.output_tokens
            total_tokens = in_tokens + out_tokens
        except Exception:
            in_tokens = out_tokens = total_tokens = 0

        # ===== SMART FILE EXTRACTION =====
        if user_mode == "code":
            code_blocks = re.findall(r"(```([a-zA-Z0-9+#_\s]*)\r?\n(.*?)(?:```|\Z))", ai_reply, re.DOTALL)
            if code_blocks:
                counter = 1
                for full_match, lang_info, code in code_blocks:
                    lang = lang_info.strip().split()[0].lower() if lang_info.strip() else ""
                    first_line = code.strip().split('\n')[0]
                    name_match = re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', first_line)

                    if name_match:
                        file_name = name_match.group(1)
                        ext = file_name.split('.')[-1].lower()
                    else:
                        ext = LANG_TO_EXT.get(lang, "")
                        if not ext: ext = "txt"
                        file_name = f"script_{counter}.{ext}"

                    if lang in SKIP_LANGS: continue
                    if len(code.strip()) < 200 and not name_match: continue
                    if ext in ["txt", "text", "plaintext"] and not name_match: continue

                    try:
                        vfile = io.BytesIO(code.encode('utf-8'))
                        vfile.name = file_name
                        await context.bot.send_document(chat_id=chat_id, document=vfile, caption=f"📁 `{file_name}` Ready!")
                        counter += 1
                        ai_reply = ai_reply.replace(full_match, f"\n*[ 📁 `{file_name}` upar attach kar di gayi hai ]*\n")
                    except Exception as file_err:
                        logger.error(f"File upload failed: {file_err}")

            meta_info = f"\n\n`[⏱️ {elapsed}s | 🪙 In {in_tokens} + Out {out_tokens} = {total_tokens}]`"
        else:
            meta_info = f"\n\n`[⏱️ {elapsed}s]`"

        final_msg = ai_reply + meta_info + trial_notice + SHOUTOUT

        if len(final_msg) > 4000:
            chunks = [final_msg[i:i+3900] for i in range(0, len(final_msg), 3900)]
            await loading_msg.edit_text(chunks[0] + "...")
            for chunk in chunks[1:]:
                await context.bot.send_message(chat_id=chat_id, text=chunk)
        else:
            try:
                await loading_msg.edit_text(final_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await loading_msg.edit_text(final_msg)

    except Exception as e:
        logger.exception("chat_handler fatal")
        try:
            await loading_msg.edit_text(f"❌ *System Error:* `{e}`" + SHOUTOUT, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        if 'history' in locals() and history and history[-1]["role"] == "user":
            history.pop()

# ================= LIFECYCLE HOOKS =================
async def on_startup(application):
    logger.info("🚀 Bot started. Uptime clock started.")
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text="🚀 *Bot Online!*\nVPS par bot successfully chal raha hai.\n\n`/admin` se dashboard dekho.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Startup notify failed: {e}")

async def on_shutdown(application):
    logger.info("🔴 Bot shutting down...")
    try:
        await application.bot.send_message(chat_id=ADMIN_ID, text="🔴 *Bot Offline.* (VPS shutdown / restart)")
    except Exception: pass

# ================= MAIN =================
def main():
    init_db()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new",   new_session))
    app.add_handler(CommandHandler("chat",  switch_to_chat))
    app.add_handler(CommandHandler("code",  switch_to_code))
    app.add_handler(CommandHandler("mode",  show_mode))
    app.add_handler(CommandHandler("stats", user_stats))
    app.add_handler(CommandHandler("help",  help_menu))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Admin commands
    app.add_handler(CommandHandler("accept",    manual_accept))
    app.add_handler(CommandHandler("trial",     manual_trial))
    app.add_handler(CommandHandler("reject",    manual_reject))
    app.add_handler(CommandHandler("admin",     admin_dashboard))
    app.add_handler(CommandHandler("users",     list_users))
    app.add_handler(CommandHandler("ban",       ban_user))
    app.add_handler(CommandHandler("unban",     unban_user))
    app.add_handler(CommandHandler("warn",      warn_user))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Message handler
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), chat_handler))

    logger.info("✅ God-Mode Opus 4.6 Bot is running on VPS (SQLite mode)!")
    app.run_polling()

if __name__ == "__main__":
    main()

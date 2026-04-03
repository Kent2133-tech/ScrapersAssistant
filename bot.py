"""
⛏ TAMBANG BOT — Telegram Bot untuk Tambang Pasir
Fitur: Laporan harian, notifikasi, input operator, tanya data (AI)
"""

import os, logging, asyncio, json
from datetime import datetime, date
from dotenv import load_dotenv
import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    ConversationHandler
)

from ai_tools import chat_with_claude

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("tambang-bot")

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL      = os.getenv("SUPABASE_URL")
SUPA_KEY      = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_raw_owners = os.getenv("OWNER_CHAT_ID", "")
OWNER_CHATS  = set(int(x.strip()) for x in _raw_owners.split(",") if x.strip())

_raw_ops     = os.getenv("OPERATOR_IDS", "")
OPERATOR_IDS = set(int(x.strip()) for x in _raw_ops.split(",") if x.strip())

HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

(ASK_UNIT, ASK_SOLAR_L, ASK_SOLAR_HARGA, ASK_SERVICE_UNIT,
 ASK_SERVICE_JENIS, ASK_SERVICE_BIAYA, ASK_SPARE_NAMA,
 ASK_SPARE_QTY, ASK_SPARE_SATUAN) = range(9)

async def supa_get(table: str, params: str = "") -> list:
    url = f"{SUPA_URL}/rest/v1/{table}?{params}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=HEADERS)
            data = r.json()
            if r.status_code == 200 and isinstance(data, list):
                return data
            return []
    except Exception as e:
        log.error(f"supa_get error {table}: {e}")
        return []

async def supa_post(table: str, data: dict) -> dict | None:
    url = f"{SUPA_URL}/rest/v1/{table}"
    async with httpx.AsyncClient() as c:
        r = await c.post(url, headers=HEADERS, json=data)
        return r.json()[0] if r.status_code in [200, 201] else None

async def supa_patch(table: str, match: str, data: dict) -> bool:
    url = f"{SUPA_URL}/rest/v1/{table}?{match}"
    async with httpx.AsyncClient() as c:
        r = await c.patch(url, headers=HEADERS, json=data)
        return r.status_code in [200, 204]

def is_owner(uid: int) -> bool: return uid in OWNER_CHATS
def is_authorized(uid: int) -> bool: return uid in OWNER_CHATS or uid in OPERATOR_IDS

def owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Laporan Hari Ini"), KeyboardButton("🔔 Cek Maintenance")],
        [KeyboardButton("💰 Ringkasan Biaya"),  KeyboardButton("🚛 Status Unit")],
        [KeyboardButton("📦 Cek Stok Spare"),   KeyboardButton("🤖 Tanya AI")],
    ], resize_keyboard=True)

def operator_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⛽ Input Solar"),      KeyboardButton("🔧 Input Service")],
        [KeyboardButton("📦 Input Spare Part"), KeyboardButton("📊 Laporan Hari Ini")],
    ], resize_keyboard=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text("🚫 Akses ditolak.")
        return
    markup = owner_keyboard() if is_owner(uid) else operator_keyboard()
    await update.message.reply_text("⛏ *Selamat datang!*\nPilih menu di bawah 👇", parse_mode="Markdown", reply_markup=markup)

async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🤖 *Mode Tanya AI aktif!*\nKetik pertanyaan apapun tentang tambang kamu.\nKetik /done untuk kembali.", parse_mode="Markdown")
    ctx.user_data["ai_mode"] = True

async def handle_ai_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not ctx.user_data.get("ai_mode"): return

    question = update.message.text
    if not ANTHROPIC_KEY:
        await update.message.reply_text("⚠️ API key AI belum dikonfigurasi.")
        return

    msg = await update.message.reply_text("🤖 Sebentar bos, lagi ngecek data...")
    try:
        answer = await asyncio.to_thread(chat_with_claude, question, ANTHROPIC_KEY)
        await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error AI: {e}")
        await msg.edit_text("⚠️ Waduh bos, AI-nya lagi pusing.")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text("✅ Kembali ke menu utama.")

async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if ctx.user_data.get("ai_mode") and is_owner(update.effective_user.id):
        await handle_ai_query(update, ctx)
        return

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ai", cmd_ai))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

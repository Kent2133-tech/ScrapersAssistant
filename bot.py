import os, logging, asyncio
from datetime import datetime, date
from dotenv import load_dotenv
import httpx
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from ai_tools import chat_with_claude

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tambang-bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_raw_owners = os.getenv("OWNER_CHAT_ID", "")
OWNER_CHATS = set(int(x.strip()) for x in _raw_owners.split(",") if x.strip())

HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json"
}

def rp(n): return f"Rp {int(n):,}".replace(",", ".")

async def supa_get(table: str, params: str = "") -> list:
    url = f"{SUPA_URL}/rest/v1/{table}?{params}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=HEADERS)
            if r.status_code == 200: return r.json()
    except Exception as e:
        log.error(f"Error Supabase: {e}")
    return []

async def build_daily_report() -> str:
    today = date.today().isoformat()
    solar = await supa_get("solar_logs", f"select=*&created_at=gte.{today}")
    services = await supa_get("service_logs", f"select=*&created_at=gte.{today}")
    costs = await supa_get("cost_logs", f"select=*&created_at=gte.{today}")

    total_solar_l = sum(s.get("liter", 0) for s in solar)
    total_solar_rp = sum(s.get("liter", 0) * s.get("harga_per_liter", 9800) for s in solar)
    total_service = sum(s.get("biaya", 0) for s in services)
    total_biaya = sum(c.get("amount", 0) for c in costs)
    grand_total = total_solar_rp + total_service + total_biaya

    return (
        f"⛏ *LAPORAN HARIAN TAMBANG*\n"
        f"📅 {date.today().strftime('%d %B %Y')}\n"
        f"────────────────────────\n\n"
        f"⛽ *SOLAR*: {total_solar_l:,.0f} L ({rp(total_solar_rp)})\n"
        f"🔧 *SERVICE*: {len(services)} kali ({rp(total_service)})\n"
        f"📋 *BIAYA LAIN*: {rp(total_biaya)}\n\n"
        f"💰 *TOTAL HARI INI: {rp(grand_total)}*\n"
    )

def owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Laporan Hari Ini")],
        [KeyboardButton("🤖 Tanya AI")]
    ], resize_keyboard=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_CHATS:
        await update.message.reply_text("🚫 Akses ditolak.")
        return
    await update.message.reply_text("⛏ *Bot Online!*", parse_mode="Markdown", reply_markup=owner_keyboard())

async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_CHATS: return
    ctx.user_data["ai_mode"] = True
    await update.message.reply_text("🤖 *Mode AI Aktif*\nKetik pertanyaan lu.\n(Ketik /done buat keluar)", parse_mode="Markdown")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text("✅ Keluar dari mode AI.", reply_markup=owner_keyboard())

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in OWNER_CHATS: return
    text = update.message.text

    if ctx.user_data.get("ai_mode"):
        if not ANTHROPIC_KEY:
            await update.message.reply_text("⚠️ API Key AI belum diisi di Railway.")
            return
        msg = await update.message.reply_text("🤖 Bentar bos, mikir dulu...")
        try:
            answer = await asyncio.to_thread(chat_with_claude, text, ANTHROPIC_KEY)
            await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")
        except Exception as e:
            log.error(f"Error AI: {e}")
            await msg.edit_text("⚠️ AI lagi pusing. Coba tanya lagi.")
        return

    if text == "📊 Laporan Hari Ini":
        msg = await update.message.reply_text("⏳ Narik data...")
        report = await build_daily_report()
        await msg.edit_text(report, parse_mode="Markdown")
    elif text == "🤖 Tanya AI":
        await cmd_ai(update, ctx)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ai", cmd_ai))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

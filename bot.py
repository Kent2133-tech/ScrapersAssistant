import os, logging
from datetime import datetime, date
from dotenv import load_dotenv
import httpx
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tambang-bot")

# CONFIG DASAR
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

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

# BIKIN LAPORAN
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
        f"📋 *LAINNYA*: {rp(total_biaya)}\n\n"
        f"💰 *TOTAL HARI INI: {rp(grand_total)}*\n"
    )

# MENU UTAMA
def owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Laporan Hari Ini"), KeyboardButton("💰 Ringkasan Biaya")],
    ], resize_keyboard=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in OWNER_CHATS:
        await update.message.reply_text("🚫 Akses ditolak.")
        return
    await update.message.reply_text("⛏ *Sistem Online!*\nPilih menu di bawah 👇", parse_mode="Markdown", reply_markup=owner_keyboard())

async def cmd_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_CHATS: return
    msg = await update.message.reply_text("⏳ Narik data dari server...")
    report = await build_daily_report()
    await msg.edit_text(report, parse_mode="Markdown")

async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📊 Laporan Hari Ini" or text == "💰 Ringkasan Biaya":
        await cmd_laporan(update, ctx)

# JADWAL OTOMATIS
async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHATS: return
    report = await build_daily_report()
    for oc in OWNER_CHATS:
        try: await ctx.bot.send_message(oc, report, parse_mode="Markdown")
        except: pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    
    # Laporan jam 23:00 UTC (06:00 WIB)
    app.job_queue.run_daily(job_daily_report, time=__import__("datetime").time(23, 0, 0))
    
    log.info("BOT REBORN STARTING...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

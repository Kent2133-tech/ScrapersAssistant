"""
⛏ TAMBANG BOT — Versi Final (Gemini AI Edition)
Fitur: Laporan harian, Maintenance Alert, Stok Alert, AI Agent
Jadwal: 06:00 (Laporan), 06:10 (Maint), 06:20 (Stok) WIB
"""

import os, logging, asyncio, json
from datetime import datetime, date, time
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

from ai_tools import chat_with_gemini

load_dotenv()
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tambang-bot")

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL   = os.getenv("SUPABASE_URL")
SUPA_KEY   = os.getenv("SUPABASE_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

_raw_owners = os.getenv("OWNER_CHAT_ID", "")
OWNER_CHATS  = set(int(x.strip()) for x in _raw_owners.split(",") if x.strip())

HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

async def supa_get(table: str, params: str = "") -> list:
    url = f"{SUPA_URL}/rest/v1/{table}?{params}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=HEADERS)
            if r.status_code == 200: return r.json()
            return []
    except: return []

def rp(n): return f"Rp {int(n):,}".replace(",", ".")
def today_str(): return date.today().strftime("%d %B %Y")

async def build_daily_report() -> str:
    today = date.today().isoformat()
    solar = await supa_get("solar_logs", f"select=*&created_at=gte.{today}")
    services = await supa_get("service_logs", f"select=*&created_at=gte.{today}")
    costs = await supa_get("cost_logs", f"select=*&created_at=gte.{today}")

    total_solar_l = sum(s.get("liter", 0) for s in solar)
    total_solar_rp = sum(s.get("liter", 0) * s.get("harga_per_liter", 9800) for s in solar)
    total_service = sum(s.get("biaya", 0) for s in services)
    total_biaya = sum(c.get("amount", 0) or c.get("jumlah", 0) for c in costs)
    
    return (
        f"⛏ *LAPORAN HARIAN TAMBANG*\n"
        f"📅 {today_str()}\n"
        f"────────────────────────\n\n"
        f"⛽ *SOLAR*: {total_solar_l:,.0f} L ({rp(total_solar_rp)})\n"
        f"🔧 *SERVICE*: {len(services)} kali ({rp(total_service)})\n"
        f"📋 *BIAYA LAIN*: {rp(total_biaya)}\n\n"
        f"💰 *TOTAL: {rp(total_solar_rp + total_service + total_biaya)}*"
    )

async def build_maintenance_alerts() -> str:
    units = await supa_get("units", "select=id,name,jam_operasi,next_service_jam")
    alerts = []
    for u in units:
        sisa = (u.get("next_service_jam", 0) or 0) - (u.get("jam_operasi", 0) or 0)
        if sisa <= 0: alerts.append(f"🔴 *{u['name']}* — OVERDUE!")
        elif sisa <= 50: alerts.append(f"⚠️ *{u['name']}* — sisa `{int(sisa)} jam` lagi")
    return "\n".join(alerts) if alerts else "✅ Semua unit aman."

async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    report = await build_daily_report()
    for oc in OWNER_CHATS:
        try: await ctx.bot.send_message(oc, report, parse_mode="Markdown")
        except: pass

async def job_maintenance_check(ctx: ContextTypes.DEFAULT_TYPE):
    alerts = await build_maintenance_alerts()
    if "🔴" in alerts or "⚠️" in alerts:
        for oc in OWNER_CHATS:
            try: await ctx.bot.send_message(oc, f"🔧 *ALERT MAINTENANCE*\n\n{alerts}", parse_mode="Markdown")
            except: pass

async def job_stok_check(ctx: ContextTypes.DEFAULT_TYPE):
    spares = await supa_get("spare_stock", "select=*&qty=lt.3")
    if spares:
        text = "⚠️ *STOK KRITIS*\n" + "\n".join([f"• {s['nama']}: {s['qty']} {s['satuan']}" for s in spares])
        for oc in OWNER_CHATS:
            try: await ctx.bot.send_message(oc, text, parse_mode="Markdown")
            except: pass

def owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Laporan Hari Ini"), KeyboardButton("🔔 Cek Maintenance")],
        [KeyboardButton("💰 Ringkasan Biaya"),  KeyboardButton("🤖 Tanya AI")],
    ], resize_keyboard=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_CHATS: return
    await update.message.reply_text("⛏ *Bot Online!*", reply_markup=owner_keyboard())

async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in OWNER_CHATS: return
    text = update.message.text

    if ctx.user_data.get("ai_mode"):
        if not GEMINI_KEY:
            await update.message.reply_text("⚠️ API Key Gemini belum diisi di Railway.")
            return
        msg = await update.message.reply_text("🤖 Sebentar bos...")
        answer = await asyncio.to_thread(chat_with_gemini, text, GEMINI_KEY)
        await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")
        return

    if text == "📊 Laporan Hari Ini":
        await update.message.reply_text(await build_daily_report(), parse_mode="Markdown")
    elif text == "🔔 Cek Maintenance":
        await update.message.reply_text(await build_maintenance_alerts(), parse_mode="Markdown")
    elif text == "🤖 Tanya AI":
        ctx.user_data["ai_mode"] = True
        await update.message.reply_text("🤖 Mode AI aktif. Tanya apa aja bos! (Ketik /done buat keluar)")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("done", lambda u, c: (c.user_data.update({"ai_mode": False}), u.message.reply_text("✅ Keluar mode AI"))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    jq = app.job_queue
    jq.run_daily(job_daily_report,      time=time(23, 0, 0)) # 06:00 WIB
    jq.run_daily(job_maintenance_check, time=time(23, 10, 0)) # 06:10 WIB
    jq.run_daily(job_stok_check,        time=time(23, 20, 0)) # 06:20 WIB

    log.info("🤖 Bot Gemini Version Started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

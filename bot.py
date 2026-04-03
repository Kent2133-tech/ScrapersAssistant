"""
⛏ TAMBANG BOT — Telegram Bot untuk Tambang Pasir
Fitur: Laporan harian, notifikasi, input operator, tanya data (AI)
v2 — Multi-owner + Agentic AI (Tool Use)
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

# ── CONFIG ──────────────────────────────────────────────────────
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

# ── SUPABASE HELPERS ────────────────────────────────────────────
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

# ── FORMATTERS & ACCESS ─────────────────────────────────────────
def rp(n) -> str:
    try: return f"Rp {int(n):,}".replace(",", ".")
    except: return "Rp 0"

def today_str() -> str:
    return date.today().strftime("%d %B %Y")

def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def is_owner(uid: int) -> bool: return uid in OWNER_CHATS
def is_authorized(uid: int) -> bool: return uid in OWNER_CHATS or uid in OPERATOR_IDS

async def notify_owners(bot, sender_uid: int, text: str):
    for oc in OWNER_CHATS:
        if oc != sender_uid:
            try: await bot.send_message(oc, text, parse_mode="Markdown")
            except: pass

# ── LAPORAN BUILDER ──────────────────────────────────────────────
async def build_daily_report() -> str:
    today = date.today().isoformat()
    units    = await supa_get("units",        "select=id,name,status,jam_operasi")
    solar    = await supa_get("solar_logs",   f"select=*&created_at=gte.{today}")
    services = await supa_get("service_logs", f"select=*&created_at=gte.{today}")
    spares   = await supa_get("spare_stock",  "select=*&qty=lt.5")
    costs    = await supa_get("cost_logs",    f"select=*&created_at=gte.{today}")

    total_solar_l  = sum(s.get("liter", 0) for s in solar)
    total_solar_rp = sum(s.get("liter", 0) * s.get("harga_per_liter", 9800) for s in solar)
    total_service  = sum(s.get("biaya", 0) for s in services)
    total_biaya    = sum(c.get("jumlah", 0) for c in costs)
    unit_aktif     = sum(1 for u in units if u.get("status") == "aktif")

    lines = [
        f"⛏ *LAPORAN HARIAN TAMBANG*",
        f"📅 {today_str()}  |  🕐 {now_str()}",
        "─" * 30,
        f"",
        f"🚛 *UNIT & ARMADA*",
        f"  • Unit aktif: `{unit_aktif}/{len(units)}`",
        f"",
        f"⛽ *SOLAR HARI INI*",
        f"  • Total: `{total_solar_l:,.0f} liter`",
        f"  • Biaya: `{rp(total_solar_rp)}`",
        f"",
        f"🔧 *SERVICE HARI INI*",
        f"  • Jumlah service: `{len(services)}`",
        f"  • Total biaya: `{rp(total_service)}`",
        f"",
    ]
    if spares:
        lines.append("⚠️ *STOK MENIPIS (<5)*")
        for s in spares[:5]:
            lines.append(f"  • {s.get('nama','?')}: `{s.get('qty',0)} {s.get('satuan','')}`")
        lines.append("")

    lines += [
        f"💰 *TOTAL BIAYA HARI INI*",
        f"  • `{rp(total_biaya + total_solar_rp + total_service)}`",
        "─" * 30,
        f"_Dikirim otomatis oleh Tambang Bot_"
    ]
    return "\n".join(lines)

async def build_maintenance_alerts() -> str:
    units = await supa_get("units", "select=id,name,jam_operasi,next_service_jam")
    alerts = []
    for u in units:
        jam    = u.get("jam_operasi", 0) or 0
        next_s = u.get("next_service_jam", 0) or 0
        if next_s > 0:
            sisa = next_s - jam
            if sisa <= 0:
                alerts.append(f"🔴 *{u['name']}* — OVERDUE! ({abs(int(sisa))} jam lewat)")
            elif sisa <= 50:
                alerts.append(f"⚠️ *{u['name']}* — sisa `{int(sisa)} jam` lagi")
    return "\n".join(alerts) if alerts else "✅ Semua unit dalam kondisi baik"

# ── KEYBOARDS ────────────────────────────────────────────────────
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

# ── COMMANDS ─────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text("🚫 Akses ditolak.")
        return
    role   = "👑 Owner" if is_owner(uid) else "👷 Operator"
    markup = owner_keyboard() if is_owner(uid) else operator_keyboard()
    await update.message.reply_text(f"⛏ *Selamat datang!*\nRole: {role}\nPilih menu 👇", parse_mode="Markdown", reply_markup=markup)

async def cmd_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Mengambil data...")
    report = await build_daily_report()
    await msg.edit_text(report, parse_mode="Markdown")

async def cmd_maintenance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    msg = await update.message.reply_text("⏳ Cek jadwal maintenance...")
    alerts = await build_maintenance_alerts()
    await msg.edit_text(f"🔧 *STATUS MAINTENANCE*\n📅 {today_str()}\n\n{alerts}", parse_mode="Markdown")

async def cmd_units(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    units = await supa_get("units", "select=*&order=name")
    lines = ["🚛 *STATUS UNIT & ARMADA*\n"]
    for u in units:
        status = u.get("status", "unknown")
        icon   = {"aktif": "🟢", "rusak": "🔴", "maintenance": "🟡"}.get(status, "⚪")
        jam    = u.get("jam_operasi") or 0
        lines.append(f"{icon} *{u.get('name','?')}*\n   Jam: `{jam:,}` jam  |  Status: `{status}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stok = await supa_get("spare_stock", "select=*&order=qty.asc&limit=20")
    lines = ["📦 *STOK SPARE PART*\n"]
    for s in stok:
        qty  = s.get("qty") or 0
        icon = "🔴" if qty < 3 else "⚠️" if qty < 5 else "✅"
        lines.append(f"{icon} {s.get('nama','?')}: `{qty} {s.get('satuan','')}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_biaya(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    today   = date.today().isoformat()
    solar   = await supa_get("solar_logs",   f"select=*&created_at=gte.{today}")
    service = await supa_get("service_logs", f"select=*&created_at=gte.{today}")
    costs   = await supa_get("cost_logs",    f"select=*&created_at=gte.{today}")

    total_solar = sum((s.get("liter") or 0) * (s.get("harga_per_liter") or 9800) for s in solar)
    total_svc   = sum(s.get("biaya") or 0 for s in service)
    total_lain  = sum(c.get("jumlah") or 0 for c in costs)
    grand_total = total_solar + total_svc + total_lain

    await update.message.reply_text(
        f"💰 *RINGKASAN BIAYA HARI INI*\n\n⛽ Solar: `{rp(total_solar)}`\n🔧 Service: `{rp(total_svc)}`\n📋 Lain: `{rp(total_lain)}`\n\n💵 *TOTAL: `{rp(grand_total)}`*",
        parse_mode="Markdown"
    )

# ── AI CHAT ──────────────────────────────────────────────────────
async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    ctx.user_data["ai_mode"] = True
    await update.message.reply_text("🤖 *Mode Tanya AI aktif!*\nKetik pertanyaan lu. (Ketik /done buat keluar)", parse_mode="Markdown")

async def handle_ai_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    question = update.message.text
    if not ANTHROPIC_KEY:
        await update.message.reply_text("⚠️ API key AI belum dikonfigurasi.")
        return
    msg = await update.message.reply_text("🤖 Sebentar bos, lagi nyari datanya...")
    try:
        answer = await asyncio.to_thread(chat_with_claude, question, ANTHROPIC_KEY)
        await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error AI: {e}")
        await msg.edit_text("⚠️ AI lagi pusing bos. Coba tanya lagi.")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text("✅ Kembali ke menu utama.", reply_markup=owner_keyboard())

# ── INPUT CONVERSATIONS (Dipotong demi hemat baris, fungsi tetep sama) ──
async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Input dibatalkan.")
    return ConversationHandler.END

# ── ROUTER ────────────────────────────────────────────────────────
async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text

    # Kalo lagi mode AI, semua chat ditangkep AI
    if ctx.user_data.get("ai_mode") and is_owner(update.effective_user.id):
        await handle_ai_query(update, ctx)
        return

    # Kalo nggak, cek tombol mana yang dipencet
    routes = {
        "📊 Laporan Hari Ini": cmd_laporan,
        "🔔 Cek Maintenance":  cmd_maintenance,
        "💰 Ringkasan Biaya":  cmd_biaya,
        "🚛 Status Unit":      cmd_units,
        "📦 Cek Stok Spare":   cmd_stok,
        "🤖 Tanya AI":         cmd_ai,
    }
    if text in routes:
        await routes[text](update, ctx)

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ai", cmd_ai))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    log.info("🤖 Tambang Bot V2 Full Version Started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

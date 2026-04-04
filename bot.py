"""
⛏ TAMBANG BOT — Telegram Bot untuk Tambang Pasir
Fitur: Laporan harian, notifikasi, input operator, AI Gemini
v4 — Full Feature + Gemini Auto-Detect Edition
"""

import os, logging, asyncio, json
from datetime import datetime, date, timedelta, time
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
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("tambang-bot")

# ── CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL   = os.getenv("SUPABASE_URL")
SUPA_KEY   = os.getenv("SUPABASE_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

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

# ── FORMATTERS ──────────────────────────────────────────────────
def rp(n) -> str:
    try: return f"Rp {int(n):,}".replace(",", ".")
    except: return "Rp 0"

def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def today_str() -> str:
    return date.today().strftime("%d %B %Y")

def is_owner(uid: int) -> bool:
    return uid in OWNER_CHATS

def is_authorized(uid: int) -> bool:
    return uid in OWNER_CHATS or uid in OPERATOR_IDS

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
    name = update.effective_user.first_name
    if not is_authorized(uid):
        await update.message.reply_text(f"🚫 Akses ditolak. Chat ID kamu: `{uid}`", parse_mode="Markdown")
        return
    role   = "👑 Owner" if is_owner(uid) else "👷 Operator"
    markup = owner_keyboard() if is_owner(uid) else operator_keyboard()
    await update.message.reply_text(f"⛏ *Selamat datang, {name}!*\nRole: {role}\n\nPilih menu di bawah 👇", parse_mode="Markdown", reply_markup=markup)

async def cmd_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    msg    = await update.message.reply_text("⏳ Mengambil data...")
    report = await build_daily_report()
    await msg.edit_text(report, parse_mode="Markdown")

async def cmd_maintenance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    msg    = await update.message.reply_text("⏳ Cek jadwal maintenance...")
    alerts = await build_maintenance_alerts()
    await msg.edit_text(f"🔧 *STATUS MAINTENANCE*\n📅 {today_str()}\n\n{alerts}", parse_mode="Markdown")

async def cmd_units(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    units = await supa_get("units", "select=*&order=name")
    if not units:
        await update.message.reply_text("🚛 Belum ada unit terdaftar.")
        return
    lines = ["🚛 *STATUS UNIT & ARMADA*\n"]
    for u in units:
        status = u.get("status", "unknown")
        icon   = {"aktif": "🟢", "rusak": "🔴", "maintenance": "🟡"}.get(status, "⚪")
        jam    = u.get("jam_operasi") or 0
        lines.append(f"{icon} *{u.get('name','?')}*\n   Jam: `{jam:,}` jam  |  Status: `{status}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    stok = await supa_get("spare_stock", "select=*&order=qty.asc&limit=20")
    if not stok:
        await update.message.reply_text("📦 Stok spare part masih kosong.")
        return
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
        f"💰 *RINGKASAN BIAYA HARI INI*\n📅 {today_str()}\n\n"
        f"⛽ Solar/BBM:        `{rp(total_solar)}`\n"
        f"🔧 Service & Spare:  `{rp(total_svc)}`\n"
        f"📋 Biaya Lain:       `{rp(total_lain)}`\n"
        f"─────────────────────\n"
        f"💵 *TOTAL:           `{rp(grand_total)}`*",
        parse_mode="Markdown"
    )

# ── INPUT SOLAR ───────────────────────────────────────────────────
async def solar_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return ConversationHandler.END
    units = await supa_get("units", "select=id,name&order=name")
    ctx.user_data["units"] = units
    buttons = [[InlineKeyboardButton(u["name"], callback_data=f"unit_{u['id']}")] for u in units]
    await update.message.reply_text("⛽ *INPUT SOLAR*\n\nPilih unit yang diisi:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ASK_UNIT

async def solar_got_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    unit_id = q.data.replace("unit_", "")
    unit    = next((u for u in ctx.user_data.get("units", []) if str(u["id"]) == unit_id), None)
    ctx.user_data["solar_unit_id"]   = unit_id
    ctx.user_data["solar_unit_name"] = unit["name"] if unit else "?"
    await q.edit_message_text(f"✅ Unit: *{ctx.user_data['solar_unit_name']}*\n\nBerapa liter solar yang diisi?", parse_mode="Markdown")
    return ASK_SOLAR_L

async def solar_got_liter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["solar_liter"] = float(update.message.text.replace(",", "."))
        await update.message.reply_text("✅ Liter tercatat\n\nHarga per liter? (tekan /skip untuk Rp 9.800)")
        return ASK_SOLAR_HARGA
    except:
        await update.message.reply_text("❌ Masukkan angka valid.")
        return ASK_SOLAR_L

async def solar_got_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: harga = float(update.message.text.replace(",", ".").replace(".", ""))
    except: harga = 9800
    await _save_solar(update, ctx, harga)
    return ConversationHandler.END

async def solar_skip_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _save_solar(update, ctx, 9800)
    return ConversationHandler.END

async def _save_solar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, harga: float):
    liter = ctx.user_data.get("solar_liter", 0)
    await supa_post("solar_logs", {
        "unit_id": ctx.user_data.get("solar_unit_id"), "liter": liter,
        "harga_per_liter": harga, "operator_id": str(update.effective_user.id),
        "created_at": datetime.now().isoformat()
    })
    total = liter * harga
    await update.message.reply_text(f"✅ *Tersimpan!*\nUnit: {ctx.user_data.get('solar_unit_name')}\nLiter: `{liter:,.0f} L`\nTotal: `{rp(total)}`", parse_mode="Markdown")
    await notify_owners(ctx.bot, update.effective_user.id, f"🔔 *Input Solar Baru*\nUnit: {ctx.user_data.get('solar_unit_name')}\nSolar: {liter:,.0f} L = {rp(total)}")

# ── INPUT SERVICE ─────────────────────────────────────────────────
async def service_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return ConversationHandler.END
    units = await supa_get("units", "select=id,name&order=name")
    ctx.user_data["units"] = units
    buttons = [[InlineKeyboardButton(u["name"], callback_data=f"svc_{u['id']}")] for u in units]
    await update.message.reply_text("🔧 *INPUT SERVICE*\n\nPilih unit:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ASK_SERVICE_UNIT

async def service_got_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    unit_id = q.data.replace("svc_", "")
    unit    = next((u for u in ctx.user_data.get("units", []) if str(u["id"]) == unit_id), None)
    ctx.user_data["svc_unit_id"]   = unit_id
    ctx.user_data["svc_unit_name"] = unit["name"] if unit else "?"
    buttons = [[InlineKeyboardButton(j, callback_data=f"jenis_{j}")] for j in ["Ringan", "Sedang", "Besar", "Overhaul", "Lainnya"]]
    await q.edit_message_text(f"✅ Unit: *{ctx.user_data['svc_unit_name']}*\n\nJenis service:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ASK_SERVICE_JENIS

async def service_got_jenis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["svc_jenis"] = q.data.replace("jenis_", "")
    await q.edit_message_text(f"✅ Jenis: *{ctx.user_data['svc_jenis']}*\n\nTotal biaya (Rp)?", parse_mode="Markdown")
    return ASK_SERVICE_BIAYA

async def service_got_biaya(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: biaya = float(update.message.text.replace(".", "").replace(",", "").replace("rp", "").replace("Rp", "").strip())
    except:
        await update.message.reply_text("❌ Format salah. Contoh: 500000")
        return ASK_SERVICE_BIAYA
    await supa_post("service_logs", {
        "unit_id": ctx.user_data.get("svc_unit_id"), "jenis": ctx.user_data.get("svc_jenis"),
        "biaya": biaya, "operator_id": str(update.effective_user.id), "created_at": datetime.now().isoformat()
    })
    await update.message.reply_text(f"✅ *Tersimpan!*\nUnit: {ctx.user_data.get('svc_unit_name')}\nBiaya: `{rp(biaya)}`", parse_mode="Markdown")
    await notify_owners(ctx.bot, update.effective_user.id, f"🔔 *Input Service Baru*\nUnit: {ctx.user_data.get('svc_unit_name')}\nBiaya: {rp(biaya)}")
    return ConversationHandler.END

# ── INPUT SPARE PART ──────────────────────────────────────────────
async def spare_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("📦 *INPUT SPARE PART*\n\nNama spare part:", parse_mode="Markdown")
    return ASK_SPARE_NAMA

async def spare_got_nama(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["spare_nama"] = update.message.text
    await update.message.reply_text("Jumlah? (angka)")
    return ASK_SPARE_QTY

async def spare_got_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["spare_qty"] = int(update.message.text)
        await update.message.reply_text("Satuan? (pcs/liter/dll)")
        return ASK_SPARE_SATUAN
    except:
        await update.message.reply_text("❌ Masukkan angka.")
        return ASK_SPARE_QTY

async def spare_got_satuan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    satuan = update.message.text
    nama, qty = ctx.user_data.get("spare_nama"), ctx.user_data.get("spare_qty", 0)
    existing = await supa_get("spare_stock", f"select=*&nama=eq.{nama}&limit=1")
    
    if existing:
        new_qty = (existing[0].get("qty") or 0) + qty
        await supa_patch("spare_stock", f"nama=eq.{nama}", {"qty": new_qty})
    else:
        await supa_post("spare_stock", {"nama": nama, "qty": qty, "satuan": satuan})
        
    await update.message.reply_text(f"✅ *Stok tersimpan!*\nItem: {nama} ({qty} {satuan})", parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Input dibatalkan.")
    return ConversationHandler.END

# ── AI CHAT ───────────────────────────────────────────────────────
async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🤖 *Mode Tanya AI aktif!*\nKetik pertanyaan lu.\nKetik /done untuk keluar.", parse_mode="Markdown")
    ctx.user_data["ai_mode"] = True

async def handle_ai_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not ctx.user_data.get("ai_mode"): return
    if not GEMINI_KEY:
        await update.message.reply_text("⚠️ API key Gemini belum dipasang.")
        return

    msg = await update.message.reply_text("🤖 Sebentar bos, lagi ngecek data...")
    try:
        answer = await asyncio.to_thread(chat_with_gemini, update.message.text, GEMINI_KEY)
        await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text("⚠️ AI lagi pusing bos.")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text("✅ Keluar dari mode AI.")

# ── MESSAGE ROUTER ────────────────────────────────────────────────
async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text

    if ctx.user_data.get("ai_mode") and is_owner(update.effective_user.id):
        await handle_ai_query(update, ctx)
        return

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

# ── SCHEDULED JOBS ────────────────────────────────────────────────
async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHATS: return
    report = await build_daily_report()
    for oc in OWNER_CHATS:
        try: await ctx.bot.send_message(oc, report, parse_mode="Markdown")
        except: pass

async def job_maintenance_check(ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHATS: return
    alerts = await build_maintenance_alerts()
    if "🔴" in alerts or "⚠️" in alerts:
        for oc in OWNER_CHATS:
            try: await ctx.bot.send_message(oc, f"🔔 *PERINGATAN MAINTENANCE*\n\n{alerts}", parse_mode="Markdown")
            except: pass

async def job_stok_check(ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHATS: return
    spares = await supa_get("spare_stock", "select=*&qty=lt.3")
    if spares:
        lines = ["⚠️ *STOK HABIS / KRITIS*\n"] + [f"🔴 {s.get('nama')}: `{s.get('qty')} {s.get('satuan')}`" for s in spares]
        for oc in OWNER_CHATS:
            try: await ctx.bot.send_message(oc, "\n".join(lines), parse_mode="Markdown")
            except: pass

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    solar_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⛽ Input Solar$"), solar_start)],
        states={
            ASK_UNIT:        [CallbackQueryHandler(solar_got_unit, pattern="^unit_")],
            ASK_SOLAR_L:     [MessageHandler(filters.TEXT & ~filters.COMMAND, solar_got_liter)],
            ASK_SOLAR_HARGA: [CommandHandler("skip", solar_skip_harga), MessageHandler(filters.TEXT & ~filters.COMMAND, solar_got_harga)],
        }, fallbacks=[CommandHandler("cancel", cancel_conv)])

    service_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔧 Input Service$"), service_start)],
        states={
            ASK_SERVICE_UNIT:  [CallbackQueryHandler(service_got_unit,  pattern="^svc_")],
            ASK_SERVICE_JENIS: [CallbackQueryHandler(service_got_jenis, pattern="^jenis_")],
            ASK_SERVICE_BIAYA: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_got_biaya)],
        }, fallbacks=[CommandHandler("cancel", cancel_conv)])

    spare_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Input Spare Part$"), spare_start)],
        states={
            ASK_SPARE_NAMA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_nama)],
            ASK_SPARE_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_qty)],
            ASK_SPARE_SATUAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_satuan)],
        }, fallbacks=[CommandHandler("cancel", cancel_conv)])

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("done",        cmd_done))
    app.add_handler(solar_conv)
    app.add_handler(service_conv)
    app.add_handler(spare_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    jq = app.job_queue
    jq.run_daily(job_daily_report,      time=time(23, 0, 0))  # 06:00 WIB
    jq.run_daily(job_maintenance_check, time=time(23, 10, 0)) # 06:10 WIB
    jq.run_daily(job_stok_check,        time=time(23, 20, 0)) # 06:20 WIB

    log.info("🤖 Bot V4 Running!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

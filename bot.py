"""
⛏ TAMBANG BOT — Telegram Bot untuk Tambang Pasir
Fitur: Laporan harian, notifikasi, input operator, tanya data (AI)
v2 — Multi-owner support
"""

import os, logging, asyncio, json
from datetime import datetime, date, timedelta
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
from anthropic import Anthropic

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger("tambang-bot")

# ── CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
SUPA_URL      = os.getenv("SUPABASE_URL", "https://tqmqdrifrbvupkrufecc.supabase.co")
SUPA_KEY      = os.getenv("SUPABASE_KEY", "sb_publishable_bQTJDIyQYhx6P3Wljt82JA_gJmnFud1")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Multi-owner: OWNER_CHAT_ID bisa diisi lebih dari 1, pisah koma
# Contoh: "1953642141,8117718091"
_raw_owners = os.getenv("OWNER_CHAT_ID", "")
OWNER_CHATS  = set(int(x.strip()) for x in _raw_owners.split(",") if x.strip())

# Operator IDs (non-owner)
_raw_ops     = os.getenv("OPERATOR_IDS", "")
OPERATOR_IDS = set(int(x.strip()) for x in _raw_ops.split(",") if x.strip())

HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Conversation states
(ASK_UNIT, ASK_SOLAR_L, ASK_SOLAR_HARGA, ASK_SERVICE_UNIT,
 ASK_SERVICE_JENIS, ASK_SERVICE_BIAYA, ASK_SPARE_NAMA,
 ASK_SPARE_QTY, ASK_SPARE_SATUAN) = range(9)

# ── SUPABASE HELPERS ────────────────────────────────────────────
async def supa_get(table: str, params: str = "") -> list:
    url = f"{SUPA_URL}/rest/v1/{table}?{params}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=HEADERS)
        return r.json() if r.status_code == 200 else []

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

# ── ACCESS CHECK ─────────────────────────────────────────────────
def is_owner(uid: int) -> bool:
    return uid in OWNER_CHATS

def is_authorized(uid: int) -> bool:
    return uid in OWNER_CHATS or uid in OPERATOR_IDS

async def notify_owners(bot, sender_uid: int, text: str):
    """Kirim notifikasi ke semua owner kecuali si pengirim sendiri."""
    for oc in OWNER_CHATS:
        if oc != sender_uid:
            try:
                await bot.send_message(oc, text, parse_mode="Markdown")
            except Exception as e:
                log.warning(f"Gagal notif owner {oc}: {e}")

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

# ── OWNER KEYBOARD ───────────────────────────────────────────────
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
        await update.message.reply_text(
            f"🚫 Akses ditolak. Chat ID kamu: `{uid}`\n"
            f"Hubungi owner untuk didaftarkan.",
            parse_mode="Markdown"
        )
        return

    role   = "👑 Owner" if is_owner(uid) else "👷 Operator"
    markup = owner_keyboard() if is_owner(uid) else operator_keyboard()

    await update.message.reply_text(
        f"⛏ *Selamat datang, {name}!*\n"
        f"Role: {role}\n\n"
        f"Pilih menu di bawah 👇",
        parse_mode="Markdown",
        reply_markup=markup
    )

async def cmd_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg    = await update.message.reply_text("⏳ Mengambil data...")
    report = await build_daily_report()
    await msg.edit_text(report, parse_mode="Markdown")

async def cmd_maintenance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Fitur ini hanya untuk owner.")
        return
    msg    = await update.message.reply_text("⏳ Cek jadwal maintenance...")
    alerts = await build_maintenance_alerts()
    await msg.edit_text(
        f"🔧 *STATUS MAINTENANCE*\n📅 {today_str()}\n\n{alerts}",
        parse_mode="Markdown"
    )

async def cmd_units(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    units = await supa_get("units", "select=*&order=name")
    if not units:
        await update.message.reply_text("❌ Tidak ada data unit.")
        return
    lines = ["🚛 *STATUS UNIT & ARMADA*\n"]
    for u in units:
        status = u.get("status", "unknown")
        icon   = {"aktif": "🟢", "rusak": "🔴", "maintenance": "🟡"}.get(status, "⚪")
        lines.append(
            f"{icon} *{u.get('name','?')}*\n"
            f"   Jam: `{u.get('jam_operasi',0):,}` jam  |  Status: `{status}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    stok = await supa_get("spare_stock", "select=*&order=qty.asc&limit=20")
    if not stok:
        await update.message.reply_text("📦 Tidak ada data stok spare part.")
        return
    lines = ["📦 *STOK SPARE PART*\n"]
    for s in stok:
        qty  = s.get("qty", 0)
        icon = "🔴" if qty < 3 else "⚠️" if qty < 5 else "✅"
        lines.append(f"{icon} {s.get('nama','?')}: `{qty} {s.get('satuan','')}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_biaya(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Fitur ini hanya untuk owner.")
        return
    today   = date.today().isoformat()
    solar   = await supa_get("solar_logs",   f"select=*&created_at=gte.{today}")
    service = await supa_get("service_logs", f"select=*&created_at=gte.{today}")
    costs   = await supa_get("cost_logs",    f"select=*&created_at=gte.{today}")

    total_solar = sum(s.get("liter", 0) * s.get("harga_per_liter", 9800) for s in solar)
    total_svc   = sum(s.get("biaya", 0) for s in service)
    total_lain  = sum(c.get("jumlah", 0) for c in costs)
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

# ── INPUT SOLAR (Conversation) ────────────────────────────────────
async def solar_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return ConversationHandler.END
    units = await supa_get("units", "select=id,name&order=name")
    if not units:
        await update.message.reply_text("❌ Tidak ada unit terdaftar.")
        return ConversationHandler.END
    ctx.user_data["units"] = units
    buttons = [[InlineKeyboardButton(u["name"], callback_data=f"unit_{u['id']}")] for u in units]
    await update.message.reply_text(
        "⛽ *INPUT SOLAR*\n\nPilih unit yang diisi solar:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return ASK_UNIT

async def solar_got_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    unit_id = q.data.replace("unit_", "")
    units   = ctx.user_data.get("units", [])
    unit    = next((u for u in units if str(u["id"]) == unit_id), None)
    ctx.user_data["solar_unit_id"]   = unit_id
    ctx.user_data["solar_unit_name"] = unit["name"] if unit else "?"
    await q.edit_message_text(
        f"✅ Unit: *{ctx.user_data['solar_unit_name']}*\n\nBerapa liter solar yang diisi?",
        parse_mode="Markdown"
    )
    return ASK_SOLAR_L

async def solar_got_liter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        liter = float(update.message.text.replace(",", "."))
        ctx.user_data["solar_liter"] = liter
        await update.message.reply_text(
            f"✅ {liter:,.0f} liter\n\nHarga per liter? (tekan /skip untuk pakai Rp 9.800)"
        )
        return ASK_SOLAR_HARGA
    except:
        await update.message.reply_text("❌ Masukkan angka yang valid, contoh: 150")
        return ASK_SOLAR_L

async def solar_got_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        harga = float(update.message.text.replace(",", ".").replace(".", ""))
    except:
        harga = 9800
    await _save_solar(update, ctx, harga)
    return ConversationHandler.END

async def solar_skip_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _save_solar(update, ctx, 9800)
    return ConversationHandler.END

async def _save_solar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, harga: float):
    liter  = ctx.user_data.get("solar_liter", 0)
    uid    = str(update.effective_user.id)
    await supa_post("solar_logs", {
        "unit_id":         ctx.user_data.get("solar_unit_id"),
        "liter":           liter,
        "harga_per_liter": harga,
        "operator_id":     uid,
        "created_at":      datetime.now().isoformat()
    })
    total = liter * harga
    await update.message.reply_text(
        f"✅ *Solar tersimpan!*\n\n"
        f"🚛 Unit: {ctx.user_data.get('solar_unit_name')}\n"
        f"⛽ Liter: `{liter:,.0f} L`\n"
        f"💵 Harga: `{rp(harga)}/L`\n"
        f"💰 Total: `{rp(total)}`\n\n"
        f"_Data tersimpan ke cloud_ ✓",
        parse_mode="Markdown"
    )
    name = update.effective_user.first_name
    await notify_owners(
        ctx.bot,
        update.effective_user.id,
        f"🔔 *Input Solar Baru*\n"
        f"Oleh: {name}\n"
        f"Unit: {ctx.user_data.get('solar_unit_name')}\n"
        f"Solar: {liter:,.0f} L = {rp(total)}"
    )

# ── INPUT SERVICE (Conversation) ──────────────────────────────────
async def service_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return ConversationHandler.END
    units = await supa_get("units", "select=id,name&order=name")
    ctx.user_data["units"] = units
    buttons = [[InlineKeyboardButton(u["name"], callback_data=f"svc_{u['id']}")] for u in units]
    await update.message.reply_text(
        "🔧 *INPUT SERVICE*\n\nPilih unit yang di-service:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return ASK_SERVICE_UNIT

async def service_got_unit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    unit_id = q.data.replace("svc_", "")
    units   = ctx.user_data.get("units", [])
    unit    = next((u for u in units if str(u["id"]) == unit_id), None)
    ctx.user_data["svc_unit_id"]   = unit_id
    ctx.user_data["svc_unit_name"] = unit["name"] if unit else "?"
    buttons = [
        [InlineKeyboardButton("Servis Ringan (250h)",  callback_data="jenis_ringan")],
        [InlineKeyboardButton("Servis Sedang (1000h)", callback_data="jenis_sedang")],
        [InlineKeyboardButton("Servis Besar (2000h)",  callback_data="jenis_besar")],
        [InlineKeyboardButton("Overhaul (5000h)",      callback_data="jenis_overhaul")],
        [InlineKeyboardButton("Perbaikan/Lainnya",     callback_data="jenis_lain")],
    ]
    await q.edit_message_text(
        f"✅ Unit: *{ctx.user_data['svc_unit_name']}*\n\nJenis service:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )
    return ASK_SERVICE_JENIS

async def service_got_jenis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    jenis_map = {
        "ringan": "Servis Ringan", "sedang": "Servis Sedang",
        "besar":  "Servis Besar",  "overhaul": "Overhaul", "lain": "Perbaikan/Lain"
    }
    jenis = q.data.replace("jenis_", "")
    ctx.user_data["svc_jenis"] = jenis_map.get(jenis, jenis)
    await q.edit_message_text(
        f"✅ Jenis: *{ctx.user_data['svc_jenis']}*\n\nBerapa total biaya service? (Rp)",
        parse_mode="Markdown"
    )
    return ASK_SERVICE_BIAYA

async def service_got_biaya(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        biaya = float(
            update.message.text
            .replace(".", "").replace(",", "")
            .replace("rp", "").replace("Rp", "").strip()
        )
    except:
        await update.message.reply_text("❌ Format salah. Contoh: 500000")
        return ASK_SERVICE_BIAYA

    uid = str(update.effective_user.id)
    await supa_post("service_logs", {
        "unit_id":    ctx.user_data.get("svc_unit_id"),
        "jenis":      ctx.user_data.get("svc_jenis"),
        "biaya":      biaya,
        "operator_id": uid,
        "created_at": datetime.now().isoformat()
    })
    await update.message.reply_text(
        f"✅ *Service tersimpan!*\n\n"
        f"🚛 Unit: {ctx.user_data.get('svc_unit_name')}\n"
        f"🔧 Jenis: {ctx.user_data.get('svc_jenis')}\n"
        f"💰 Biaya: `{rp(biaya)}`\n\n"
        f"_Data tersimpan ke cloud_ ✓",
        parse_mode="Markdown"
    )
    name = update.effective_user.first_name
    await notify_owners(
        ctx.bot,
        update.effective_user.id,
        f"🔔 *Input Service Baru*\n"
        f"Oleh: {name}\n"
        f"Unit: {ctx.user_data.get('svc_unit_name')}\n"
        f"Jenis: {ctx.user_data.get('svc_jenis')}\n"
        f"Biaya: {rp(biaya)}"
    )
    return ConversationHandler.END

# ── INPUT SPARE PART ──────────────────────────────────────────────
async def spare_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text(
        "📦 *INPUT SPARE PART*\n\nNama spare part yang dipakai/dibeli:",
        parse_mode="Markdown"
    )
    return ASK_SPARE_NAMA

async def spare_got_nama(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["spare_nama"] = update.message.text
    await update.message.reply_text("Jumlah? (angka)")
    return ASK_SPARE_QTY

async def spare_got_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["spare_qty"] = int(update.message.text)
        await update.message.reply_text("Satuan? (pcs / buah / set / liter / kg / dll)")
        return ASK_SPARE_SATUAN
    except:
        await update.message.reply_text("❌ Masukkan angka. Contoh: 2")
        return ASK_SPARE_QTY

async def spare_got_satuan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    satuan = update.message.text
    nama   = ctx.user_data.get("spare_nama")
    qty    = ctx.user_data.get("spare_qty", 0)

    existing = await supa_get("spare_stock", f"select=*&nama=eq.{nama}&limit=1")
    if existing:
        new_qty = (existing[0].get("qty") or 0) + qty
        await supa_patch("spare_stock", f"nama=eq.{nama}", {"qty": new_qty})
        action = f"Stok diperbarui: {existing[0].get('qty',0)} → {new_qty}"
    else:
        await supa_post("spare_stock", {"nama": nama, "qty": qty, "satuan": satuan})
        action = "Item baru ditambahkan"

    await update.message.reply_text(
        f"✅ *Spare part tersimpan!*\n\n"
        f"📦 Item: {nama}\n"
        f"🔢 Qty: `{qty} {satuan}`\n"
        f"📝 {action}\n\n_Data tersimpan ke cloud_ ✓",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Input dibatalkan.")
    return ConversationHandler.END

# ── AI CHAT ──────────────────────────────────────────────────────
async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Fitur AI hanya untuk owner.")
        return
    await update.message.reply_text(
        "🤖 *Mode Tanya AI aktif!*\n\n"
        "Ketik pertanyaan apapun tentang tambang kamu.\n"
        "Contoh:\n"
        "• _Berapa total biaya solar minggu ini?_\n"
        "• _Unit mana yang paling sering service?_\n"
        "• _Stok apa yang hampir habis?_\n\n"
        "Ketik /done untuk kembali ke menu.",
        parse_mode="Markdown"
    )
    ctx.user_data["ai_mode"] = True

async def handle_ai_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not ctx.user_data.get("ai_mode"): return

    question = update.message.text
    if not ANTHROPIC_KEY:
        await update.message.reply_text("⚠️ API key AI belum dikonfigurasi.")
        return

    msg = await update.message.reply_text("🤖 Menganalisa data...")

    units   = await supa_get("units",        "select=*")
    solar   = await supa_get("solar_logs",   "select=*&order=created_at.desc&limit=50")
    service = await supa_get("service_logs", "select=*&order=created_at.desc&limit=50")
    stok    = await supa_get("spare_stock",  "select=*")

    context = f"""
Kamu adalah asisten keuangan dan operasional untuk tambang pasir bernama SCRAPERS.
Berikut data real-time dari sistem:

UNITS: {json.dumps(units, ensure_ascii=False)}
SOLAR LOGS (50 terbaru): {json.dumps(solar, ensure_ascii=False)}
SERVICE LOGS (50 terbaru): {json.dumps(service, ensure_ascii=False)}
SPARE STOCK: {json.dumps(stok, ensure_ascii=False)}

Jawab pertanyaan owner dengan ringkas, dalam Bahasa Indonesia.
Gunakan format yang mudah dibaca di Telegram (tanpa markdown kompleks).
Berikan insight yang berguna jika relevan.
"""
    client   = Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=context,
        messages=[{"role": "user", "content": question}]
    )
    answer = response.content[0].text
    await msg.edit_text(f"🤖 *AI:*\n\n{answer}", parse_mode="Markdown")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ai_mode"] = False
    await update.message.reply_text("✅ Kembali ke menu utama.")

# ── MESSAGE ROUTER ────────────────────────────────────────────────
async def route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
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
    """Laporan harian ke semua owner — 06:00 WIB"""
    if not OWNER_CHATS: return
    report = await build_daily_report()
    for oc in OWNER_CHATS:
        try:
            await ctx.bot.send_message(oc, report, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"Gagal kirim laporan ke {oc}: {e}")
    log.info(f"Daily report sent to {len(OWNER_CHATS)} owner(s)")

async def job_maintenance_check(ctx: ContextTypes.DEFAULT_TYPE):
    """Alert maintenance — 07:00 WIB"""
    if not OWNER_CHATS: return
    alerts = await build_maintenance_alerts()
    if "🔴" in alerts or "⚠️" in alerts:
        for oc in OWNER_CHATS:
            try:
                await ctx.bot.send_message(
                    oc,
                    f"🔔 *PERINGATAN MAINTENANCE*\n\n{alerts}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                log.warning(f"Gagal kirim maintenance alert ke {oc}: {e}")

async def job_stok_check(ctx: ContextTypes.DEFAULT_TYPE):
    """Alert stok kritis — 08:00 WIB"""
    if not OWNER_CHATS: return
    spares = await supa_get("spare_stock", "select=*&qty=lt.3")
    if spares:
        lines = ["⚠️ *STOK HABIS / KRITIS*\n"]
        for s in spares:
            lines.append(f"🔴 {s.get('nama')}: `{s.get('qty')} {s.get('satuan')}`")
        text = "\n".join(lines)
        for oc in OWNER_CHATS:
            try:
                await ctx.bot.send_message(oc, text, parse_mode="Markdown")
            except Exception as e:
                log.warning(f"Gagal kirim stok alert ke {oc}: {e}")

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN tidak ditemukan di .env!")

    log.info(f"Owner IDs: {OWNER_CHATS}")
    log.info(f"Operator IDs: {OPERATOR_IDS}")

    app = Application.builder().token(BOT_TOKEN).build()

    solar_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⛽ Input Solar$"), solar_start)],
        states={
            ASK_UNIT:        [CallbackQueryHandler(solar_got_unit, pattern="^unit_")],
            ASK_SOLAR_L:     [MessageHandler(filters.TEXT & ~filters.COMMAND, solar_got_liter)],
            ASK_SOLAR_HARGA: [
                CommandHandler("skip", solar_skip_harga),
                MessageHandler(filters.TEXT & ~filters.COMMAND, solar_got_harga)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )

    service_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔧 Input Service$"), service_start)],
        states={
            ASK_SERVICE_UNIT:  [CallbackQueryHandler(service_got_unit,  pattern="^svc_")],
            ASK_SERVICE_JENIS: [CallbackQueryHandler(service_got_jenis, pattern="^jenis_")],
            ASK_SERVICE_BIAYA: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_got_biaya)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )

    spare_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Input Spare Part$"), spare_start)],
        states={
            ASK_SPARE_NAMA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_nama)],
            ASK_SPARE_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_qty)],
            ASK_SPARE_SATUAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, spare_got_satuan)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)]
    )

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("laporan",     cmd_laporan))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("units",       cmd_units))
    app.add_handler(CommandHandler("stok",        cmd_stok))
    app.add_handler(CommandHandler("biaya",       cmd_biaya))
    app.add_handler(CommandHandler("ai",          cmd_ai))
    app.add_handler(CommandHandler("done",        cmd_done))
    app.add_handler(solar_conv)
    app.add_handler(service_conv)
    app.add_handler(spare_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    # Scheduled — WIB = UTC+7
    jq = app.job_queue
    jq.run_daily(job_daily_report,      time=__import__("datetime").time(23, 0, 0))  # 06:00 WIB
    jq.run_daily(job_maintenance_check, time=__import__("datetime").time(0,  0, 0))  # 07:00 WIB
    jq.run_daily(job_stok_check,        time=__import__("datetime").time(1,  0, 0))  # 08:00 WIB

    log.info("🤖 Tambang Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

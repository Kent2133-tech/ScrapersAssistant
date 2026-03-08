# ⛏ TAMBANG BOT — Telegram Bot untuk Tambang Pasir

Bot Telegram gratis selamanya yang connect ke Supabase tambang kamu.

---

## ✨ FITUR

| Fitur | Owner | Operator |
|-------|-------|----------|
| 📊 Laporan harian otomatis (jam 06.00) | ✅ | ✅ lihat |
| 🔔 Notifikasi maintenance overdue | ✅ | ❌ |
| ⚠️ Alert stok spare habis | ✅ | ❌ |
| ⛽ Input solar via chat | ✅ | ✅ |
| 🔧 Input service via chat | ✅ | ✅ |
| 📦 Input spare part via chat | ✅ | ✅ |
| 🤖 Tanya AI natural language | ✅ | ❌ |
| 🚛 Cek status semua unit | ✅ | ✅ |
| 💰 Ringkasan biaya hari ini | ✅ | ❌ |

---

## 🚀 CARA SETUP (30 menit)

### LANGKAH 1 — Buat Bot Telegram

1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot`
3. Beri nama: `Tambang Pasir Bot` (atau apapun)
4. Beri username: `tambangpasirku_bot` (harus unik, diakhiri `_bot`)
5. Copy **TOKEN** yang diberikan BotFather → simpan

### LANGKAH 2 — Dapat Chat ID Kamu

1. Cari **@userinfobot** di Telegram
2. Ketik `/start`
3. Copy **Id** yang muncul → ini OWNER_CHAT_ID kamu
4. Minta operator lakukan hal yang sama → catat ID mereka

### LANGKAH 3 — Deploy ke Railway (GRATIS)

Railway gratis untuk usage normal bot (500 jam/bulan = cukup untuk 1 bot).

1. Daftar di **railway.app** (gratis, pakai GitHub)
2. Klik **New Project** → **Deploy from GitHub repo**
3. Upload folder `tambang-bot` ini ke GitHub kamu dulu:
   ```
   git init
   git add .
   git commit -m "tambang bot"
   git push origin main
   ```
4. Di Railway, pilih repo tersebut
5. Setelah deploy, buka tab **Variables** dan tambahkan:

| Variable | Nilai |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | token dari BotFather |
| `OWNER_CHAT_ID` | chat ID kamu |
| `SUPABASE_URL` | https://tqmqdrifrbvupkrufecc.supabase.co |
| `SUPABASE_KEY` | sb_publishable_bQTJDIyQYhx6P3Wljt82JA_gJmnFud1 |
| `ANTHROPIC_API_KEY` | (opsional, untuk fitur AI) |
| `OPERATOR_IDS` | ID operator pisah koma: `111,222,333` |

6. Railway otomatis restart bot setelah variables diisi
7. Done! Cari bot kamu di Telegram, ketik `/start`

---

## 🔄 ALTERNATIF DEPLOY — Lokal di Laptop

Kalau tidak mau pakai Railway, jalankan di laptop/PC yang selalu nyala:

```bash
# Install Python 3.10+
pip install -r requirements.txt

# Copy dan isi file .env
cp .env.example .env
# Edit .env dengan text editor, isi semua nilai

# Jalankan bot
python bot.py
```

---

## 📱 CARA PAKAI

### Untuk Owner:
- `/start` — munculkan menu
- Keyboard menu muncul otomatis di bawah chat
- **Laporan harian** dikirim otomatis jam 06:00 WIB
- **Alert maintenance** dikirim jam 07:00 WIB (kalau ada yang overdue)
- **Alert stok** dikirim jam 08:00 WIB (kalau stok < 3)
- **Tanya AI**: ketik pertanyaan bebas setelah tap 🤖 Tanya AI

### Untuk Operator:
- `/start` — munculkan menu operator
- Tap **⛽ Input Solar** → pilih unit → isi liter → isi harga (atau /skip)
- Tap **🔧 Input Service** → pilih unit → pilih jenis → isi biaya
- Tap **📦 Input Spare Part** → isi nama → jumlah → satuan
- Setiap input langsung sync ke Supabase & notif ke owner

---

## 💬 CONTOH TANYA AI

```
Berapa total solar bulan ini?
Unit mana yang maintenance paling banyak?
Stok apa yang perlu segera dibeli?
Bandingkan biaya unit EXC-01 vs DT-01
Prediksi kapan Excavator perlu overhaul?
```

---

## 🔧 TROUBLESHOOTING

**Bot tidak merespons:**
- Cek Railway dashboard — pastikan service Running
- Cek Variables sudah terisi semua

**"Akses ditolak":**
- Chat ID operator belum ditambahkan ke `OPERATOR_IDS`
- Suruh operator chat @userinfobot untuk dapat ID mereka

**Data tidak masuk Supabase:**
- Pastikan project Supabase tidak di-pause
- Cek Supabase URL dan Key sudah benar

---

## 💡 TIPS

- Tambah operator baru: edit variable `OPERATOR_IDS` di Railway, tambah ID baru dengan koma
- Ganti jam laporan: edit file `bot.py` bagian `job_queue.run_daily`
- WIB = UTC+7, jadi jam 6 WIB = 23:00 UTC (hari sebelumnya)

---

*Dibuat untuk Tambang Pasir — powered by python-telegram-bot + Supabase*

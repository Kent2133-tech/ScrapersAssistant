import os
import json
import httpx
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# Setup Supabase
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json"
}

# Setup Google Sheets
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def fetch_supabase_data(table_name: str, limit: int = 50):
    url = f"{SUPA_URL}/rest/v1/{table_name}?select=*&order=created_at.desc&limit={limit}"
    try:
        with httpx.Client() as c:
            r = c.get(url, headers=HEADERS)
            if r.status_code == 200:
                data = r.json()
                return data if data else [{"info": f"Tabel {table_name} saat ini kosong."}]
            return [{"error": f"Akses ke {table_name} gagal. Status: {r.status_code}"}]
    except Exception as e:
        return [{"error": f"Sistem error di {table_name}: {str(e)}"}]

def get_sheets_data():
    try:
        creds_json = os.getenv("GOOGLE_CREDS")
        if not creds_json:
            return [{"error": "Waduh, variabel GOOGLE_CREDS belum dipasang di Railway."}]

        sheet_url = os.getenv("SPREADSHEET_URL")
        if not sheet_url:
            return [{"error": "Variabel SPREADSHEET_URL belum dipasang di Railway."}]

        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        client = gspread.authorize(creds)

        # Buka file berdasarkan URL dan ambil Sheet pertama (Sheet1)
        sheet = client.open_by_url(sheet_url).sheet1
        
        # Ambil maksimal 100 baris biar otak AI nggak keberatan bacanya
        records = sheet.get_all_records()
        return records[:100] if records else [{"info": "Data di Google Sheets kosong."}]
    except Exception as e:
        return [{"error": f"Gagal baca Google Sheets (Mungkin email bot belum di-invite jadi Editor): {str(e)}"}]

def chat_with_gemini(question: str, api_key: str):
    try:
        genai.configure(api_key=api_key)
        
        # Cari otomatis model yang tersedia
        tersedia = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if not tersedia:
            return "Error: API Key lu nggak dapet akses model teks."
            
        model = genai.GenerativeModel(tersedia[0])
        
        # Sedot SEMUA data (Supabase + Google Sheets)
        units = fetch_supabase_data("units", 10)
        solar = fetch_supabase_data("solar_logs", 20)
        service = fetch_supabase_data("service_logs", 20)
        costs = fetch_supabase_data("cost_logs", 20)
        stok = fetch_supabase_data("spare_stock", 20)
        keuangan_sheets = get_sheets_data()

        context = f"""
        Kamu adalah asisten operasional dan keuangan tambang pasir SCRAPERS. 
        Jawab pertanyaan bos dengan bahasa santai (lo/gue) dan ringkas.
        Pertanyaan: "{question}"
        
        Gunakan gabungan data real-time ini untuk menjawab:
        === DATA OPERASIONAL (SUPABASE) ===
        - Status Unit: {json.dumps(units)}
        - Solar Log: {json.dumps(solar)}
        - Service Log: {json.dumps(service)}
        - Cost/Biaya Log: {json.dumps(costs)}
        - Stok Gudang: {json.dumps(stok)}
        
        === DATA KEUANGAN (GOOGLE SHEETS) ===
        - Laporan Sheets: {json.dumps(keuangan_sheets)}

        PENTING:
        - Jika ada "error" dari Google Sheets, laporkan ke bos (biasanya karena bos lupa nge-share file Sheets-nya ke email bot).
        """
        
        response = model.generate_content(context)
        return response.text
    except Exception as e:
        return f"Waduh error AI bos: {str(e)}"

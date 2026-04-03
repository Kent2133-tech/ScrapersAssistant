import os
import json
import httpx
import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

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

def get_sheets_client():
    try:
        creds = Credentials.from_service_account_file("kredensial.json", scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error Google Sheets: {e}")
        return None

# Fungsi narik data dari Supabase yang bakal dijalanin sama AI
def fetch_supabase_data(table_name: str, limit: int = 50):
    url = f"{SUPA_URL}/rest/v1/{table_name}?select=*&order=created_at.desc&limit={limit}"
    try:
        with httpx.Client() as c:
            r = c.get(url, headers=HEADERS)
            if r.status_code == 200:
                return r.json()
            return {"error": f"Gagal narik data dari {table_name}"}
    except Exception as e:
        return {"error": str(e)}

# Definisi Alat (Tools) buat Claude
TOOLS = [
    {
        "name": "cek_database_tambang",
        "description": "Gunakan alat ini untuk mencari data spesifik dari database jika owner bertanya tentang log operasional, bbm, kas, atau maintenance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabel": {
                    "type": "string",
                    "description": "Nama tabel database yang tepat: 'solar_logs', 'cost_logs', 'service_logs', atau 'spare_stock'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Berapa baris data terbaru yang ingin dilihat (maksimal 100)"
                }
            },
            "required": ["tabel"]
        }
    }
]

# Engine Utama AI
async def chat_with_claude(question: str, anthropic_key: str):
    client = Anthropic(api_key=anthropic_key)
    system_prompt = "Kamu adalah asisten direktur operasional tambang pasir. Gunakan alat (tools) yang tersedia untuk mencari data real-time, lalu jawab dengan ringkas, tajam, dan gunakan bahasa santai layaknya asisten pribadi bos."

    # Tahap 1: Claude menganalisa pertanyaan dan milih alat
    response = client.messages.create(
        model="claude-3-haiku-20240307", # Versi Haiku yang bener & valid
        max_tokens=1000,
        system=system_prompt,
        tools=TOOLS,
        messages=[{"role": "user", "content": question}]
    )

    # Kalau Claude ngerasa gak butuh tool (misal cuma disapa "Halo"), langsung bales
    if response.stop_reason != "tool_use":
        return response.content[0].text

    # Tahap 2: Kalau Claude minta tool, kita tangkep permintaannya
    tool_use = next(block for block in response.content if block.type == "tool_use")
    tool_name = tool_use.name
    tool_input = tool_use.input
    tool_id = tool_use.id

    # Kita eksekusi alatnya di script Python kita
    if tool_name == "cek_database_tambang":
        hasil_data = fetch_supabase_data(tool_input.get("tabel"), tool_input.get("limit", 50))
    else:
        hasil_data = {"error": "Alat tidak ditemukan"}

    # Tahap 3: Balikin datanya ke Claude biar dia baca dan simpulin buat lo
    final_response = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=1000,
        system=system_prompt,
        tools=TOOLS,
        messages=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": response.content}, # Konteks bawaan
            {
                "role": "user", 
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(hasil_data)
                    }
                ]
            }
        ]
    )
    
    return final_response.content[0].text
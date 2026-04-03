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
        creds_json = os.getenv("GOOGLE_CREDS")
        if not creds_json:
            print("Error: Variabel GOOGLE_CREDS belum dipasang di Railway!")
            return None
            
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error Google Sheets: {e}")
        return None

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

TOOLS = [
    {
        "name": "cek_database_tambang",
        "description": "Gunakan alat ini untuk mencari data spesifik dari database jika owner bertanya tentang log operasional, bbm, kas, atau maintenance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabel": {
                    "type": "string",
                    "description": "Nama tabel database: 'solar_logs', 'cost_logs', 'service_logs', atau 'spare_stock'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Berapa baris data terbaru (maksimal 100)"
                }
            },
            "required": ["tabel"]
        }
    }
]

def chat_with_claude(question: str, anthropic_key: str):
    client = Anthropic(api_key=anthropic_key)
    system_prompt = "Kamu adalah asisten direktur operasional tambang pasir. Gunakan alat (tools) yang tersedia untuk mencari data real-time, lalu jawab dengan ringkas, tajam, dan gunakan bahasa santai layaknya asisten pribadi bos."

    response = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=1000,
        system=system_prompt,
        tools=TOOLS,
        messages=[{"role": "user", "content": question}]
    )

    if response.stop_reason != "tool_use":
        return response.content[0].text

    tool_use = next(block for block in response.content if block.type == "tool_use")
    
    if tool_use.name == "cek_database_tambang":
        hasil_data = fetch_supabase_data(tool_use.input.get("tabel"), tool_use.input.get("limit", 50))
    else:
        hasil_data = {"error": "Alat tidak ditemukan"}

    final_response = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=1000,
        system=system_prompt,
        tools=TOOLS,
        messages=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": response.content},
            {
                "role": "user", 
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(hasil_data)
                    }
                ]
            }
        ]
    )
    return final_response.content[0].text

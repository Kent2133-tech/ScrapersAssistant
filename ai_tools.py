import os
import json
import httpx
import google.generativeai as genai

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json"
}

def fetch_supabase_data(table_name: str, limit: int = 50):
    url = f"{SUPA_URL}/rest/v1/{table_name}?select=*&order=created_at.desc&limit={limit}"
    try:
        with httpx.Client() as c:
            r = c.get(url, headers=HEADERS)
            if r.status_code == 200:
                data = r.json()
                if not data: # Kalau kosong melompong, kasih tau AI biar nggak bingung
                    return [{"info": f"Tabel {table_name} saat ini kosong (belum ada data)."}]
                return data
            # Kalau koneksi gagal/typo
            return [{"error": f"Akses ke tabel {table_name} gagal. Status Code: {r.status_code}"}]
    except Exception as e:
        return [{"error": f"Sistem error saat ngebaca {table_name}: {str(e)}"}]

def chat_with_gemini(question: str, api_key: str):
    try:
        genai.configure(api_key=api_key)
        
        # Cari otomatis model yang tersedia buat akun ini
        tersedia = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        if not tersedia:
            return "Waduh bos error: API Key lu nggak dapet akses ke model teks Google satupun."
            
        model = genai.GenerativeModel(tersedia[0])
        
        # Sedot data dari database
        units = fetch_supabase_data("units", 10)
        solar = fetch_supabase_data("solar_logs", 30)
        service = fetch_supabase_data("service_logs", 30)
        costs = fetch_supabase_data("cost_logs", 30)
        stok = fetch_supabase_data("spare_stock", 30)

        context = f"""
        Kamu adalah asisten operasional tambang pasir SCRAPERS. 
        Jawab pertanyaan bos dengan bahasa santai (lo/gue) dan ringkas.
        Pertanyaan: "{question}"
        
        Gunakan data operasional real-time ini untuk menjawab:
        - Status Unit: {json.dumps(units)}
        - Solar Log: {json.dumps(solar)}
        - Service Log: {json.dumps(service)}
        - Cost/Biaya Log: {json.dumps(costs)}
        - Stok Gudang: {json.dumps(stok)}

        PENTING:
        - Jika data berisi "info" bahwa tabel kosong, beri tahu bos dengan sopan bahwa datanya memang belum diinput hari ini.
        - Jika data berisi "error", laporkan error tersebut ke bos.
        """
        
        response = model.generate_content(context)
        return response.text
    except Exception as e:
        return f"Waduh error AI bos: {str(e)}"

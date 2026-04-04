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
            if r.status_code == 200: return r.json()
            return []
    except Exception as e:
        return []

def chat_with_gemini(question: str, api_key: str):
    try:
        genai.configure(api_key=api_key)
        
        # JURUS SAKTI: Suruh sistem nyari sendiri model apa yang tersedia di API Key ini
        tersedia = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        if not tersedia:
            return "Waduh error: API Key lu nggak punya akses ke model teks Google satupun."
            
        # Pake model pertama yang ketemu dari daftar resmi Google
        model = genai.GenerativeModel(tersedia[0])
        
        # AI bakal nyedot data ini dulu sebelum ngejawab pertanyaan lo
        units = fetch_supabase_data("units", 10)
        solar = fetch_supabase_data("solar_logs", 30)
        service = fetch_supabase_data("service_logs", 30)
        costs = fetch_supabase_data("cost_logs", 30)
        stok = fetch_supabase_data("spare_stock", 30)

        context = f"""
        Kamu asisten operasional tambang pasir SCRAPERS. 
        Jawab pertanyaan bos dengan bahasa santai (lo/gue) dan ringkas.
        Pertanyaan: "{question}"
        
        Gunakan data operasional terbaru ini untuk menjawab:
        - Status Unit: {json.dumps(units)}
        - Solar Log: {json.dumps(solar)}
        - Service Log: {json.dumps(service)}
        - Cost/Biaya Log: {json.dumps(costs)}
        - Stok Gudang: {json.dumps(stok)}
        """
        
        response = model.generate_content(context)
        return response.text
    except Exception as e:
        return f"Waduh error AI: {str(e)}"

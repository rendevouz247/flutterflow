# app.py (publicado no Render)

from flask import Flask, request
from supabase import create_client
from datetime import datetime
from groq import Groq
from deep_translator import GoogleTranslator
import os, json, logging

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

def detectar_idioma(texto):
    try:
        return GoogleTranslator(source='auto', target='en').detect(texto)
    except:
        return 'pt'

@app.route("/ia", methods=["POST"])
def ia_from_supabase():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        mensagem = data.get("mensagem")
        agendamento_id = data.get("agendamento_id")

        # 🔍 Apenas debug
        app.logger.info(f"📩 Recebido na /ia: {data}")

        # Geração de resposta (pode ser mais elaborada depois)
        resposta = f"Recebi sua mensagem: {mensagem}"

        # Grava no Supabase se quiser
        supabase.table("mensagens_chat").insert({
            "user_id": "ia",
            "mensagem": resposta,
            "agendamento_id": agendamento_id
        }).execute()

        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.error(f"❌ Erro em /ia: {e}")
        return {"erro": "Falha ao processar"}, 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


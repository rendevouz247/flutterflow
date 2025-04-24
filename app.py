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

TRUNCATE_LIMIT = 500

def detectar_idioma(texto):
    try:
        return GoogleTranslator(source='auto', target='en').detect(texto)
    except:
        return 'pt'

def traduzir(texto: str, destino: str) -> str:
    try:
        return GoogleTranslator(source='pt', target=destino).translate(texto)
    except:
        return texto

@app.route("/ia", methods=["POST"])
def ia_reply():
    try:
        data = request.get_json()
        app.logger.info(f"📩 Recebido na /ia: {data}")
        
        user_id = data.get("user_id")
        mensagem = data.get("mensagem")
        agendamento_id = data.get("agendamento_id")

        if not user_id or not mensagem or agendamento_id is None:
            return {"erro": "Dados incompletos"}, 400

        idioma = detectar_idioma(mensagem)

        resposta = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    "Você é um assistente educado e direto. "
                    "Ajude o cliente com perguntas claras. "
                    "Se ele enviar 'R', pergunte qual data prefere. "
                    "Se ele disser uma data, verifique se é válida. "
                    "Se ele disser 'Y', confirme o agendamento. "
                    "Se disser 'N', cancele."
                )},
                {"role": "user", "content": mensagem}
            ]
        ).choices[0].message.content.strip()

        # Grava resposta da IA na tabela mensagens_chat com tipo IA
        supabase.table("mensagens_chat").insert({
            "user_id": "ia",
            "mensagem": resposta,
            "agendamento_id": agendamento_id,
            "data_envio": datetime.utcnow().isoformat(),
            "tipo": "IA"
        }).execute()

        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.info(f"❌ Erro na função IA: {e}")
        return {"erro": str(e)}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


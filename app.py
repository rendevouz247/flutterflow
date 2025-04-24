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
def ia_reply():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        mensagem = data.get("mensagem")
        agendamento_id = data.get("agendamento_id")

        if not user_id or not mensagem:
            return {"erro": "Faltam dados."}, 400

        idioma = detectar_idioma(mensagem)

        resposta = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    f"Você é um assistente poliglota, atenda bem o cliente com respostas diretas, educadas e naturais, na língua que ele usar."
                )},
                {"role": "user", "content": mensagem}
            ]
        ).choices[0].message.content.strip()

        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.info(f"❌ Erro na função ia_reply: {e}")
        return {"erro": str(e)}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


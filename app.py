from flask import Flask, request
from supabase import create_client
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
from groq import Groq
from deep_translator import GoogleTranslator
import os, json, re, logging

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

TRUNCATE_LIMIT = 500
HORA_FLAG = "HORA_SELECIONADA"

# Funções utilitárias
def truncate(text: str, limit: int = TRUNCATE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

def detectar_idioma(texto):
    try:
        return GoogleTranslator(source='auto', target='en').detect(texto)
    except:
        return 'fr'

def traduzir(texto: str, destino: str) -> str:
    try:
        return GoogleTranslator(source='fr', target=destino).translate(texto)
    except:
        return texto

def send_message(resp: MessagingResponse, text: str, lang: str = "fr"):
    translated = traduzir(text, lang) if lang != "fr" else text
    resp.message(truncate(translated))

def format_date(date_str: str) -> str:
    return datetime.fromisoformat(date_str).strftime("%d/%m/%Y")

def get_available_times(date: str, company_id: str) -> list:
    rows = (
        supabase
        .from_("view_horas_disponiveis")
        .select("horas_disponiveis")
        .eq("company_id", company_id)
        .eq("date", date)
        .execute()
        .data
    )
    times = []
    for r in rows:
        j = r.get("horas_disponiveis") or {}
        times += j.get("disponiveis", [])
    return sorted(set(times))

def parse_date_from_text(text):
    try:
        hora_match = re.search(r"\\b(\\d{1,2}[:h]\\d{2})(:\\d{2})?\\b", text)
        if hora_match:
            return HORA_FLAG

        idioma = detectar_idioma(text)
        hoje_fmt = datetime.now().strftime("%Y-%m-%d")

        nlu = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    f"Tu es un assistant JSON. Aujourd'hui, nous sommes le {hoje_fmt}. "
                    f"Ta tâche est d'extraire une date future à partir d'une phrase dans la langue '{idioma}' "
                    f"(ex: 'le 19 mai', 'demain', 'terça-feira'). Si la date extraite est dans le passé, tu dois automatiquement "
                    f"ajouter des années jusqu'à ce qu'elle soit dans le futur. Réponds uniquement avec ce JSON: "
                    f"{{ \"date\": \"YYYY-MM-DD\" }}. Ne réponds rien d'autre."
                )},
                {"role": "user", "content": text}
            ]
        )
        raw = nlu.choices[0].message.content.strip()
        app.logger.info(f"🧠 Resposta IA bruta: {raw}")
        result = json.loads(raw)
        value = result.get("date")
        if value in [None, "YYYY-MM-DD"]:
            return None
        value = value.split("T")[0] if "T" in value else value

        now = datetime.now()
        dt = datetime.fromisoformat(value)
        if dt.year < now.year:
            dt = dt.replace(year=now.year)
        if dt < now:
            dt = dt.replace(year=dt.year + 1)

        return dt.date().isoformat()
    except Exception as e:
        app.logger.info(f"❌ Erro ao extrair data: {e}")
        return None

# Rota para integrar com FlutterFlow ou outra origem
@app.route("/functions/v1/quick-handler", methods=["POST"])
def quick_handler():
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
                    f"Você é um assistente poliglota, atenda bem o cliente com respostas diretas e naturais."
                )},
                {"role": "user", "content": mensagem}
            ]
        ).choices[0].message.content.strip()

        supabase.table("mensagens_chat").insert({
            "user_id": user_id,
            "mensagem": resposta,
            "agendamento_id": agendamento_id
        }).execute()

        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.info(f"❌ Erro na função quick_handler: {e}")
        return {"erro": str(e)}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

from flask import Flask, request
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
from groq import Groq
import os, json, re
import logging

# CONFIG
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
TWILIO_SID    = os.getenv("TWILIO_SID")
TWILIO_AUTH   = os.getenv("TWILIO_AUTH")
TWILIO_PHONE  = os.getenv("TWILIO_PHONE")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")

supabase     = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client= TwilioClient(TWILIO_SID, TWILIO_AUTH)
groq_client  = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

TRUNCATE_LIMIT = 500

def truncate(text: str, limit: int = TRUNCATE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

def send_message(resp: MessagingResponse, text: str):
    resp.message(truncate(text))

def format_date(date_str: str) -> str:
    return datetime.fromisoformat(date_str).strftime("%d/%m/%Y")

def parse_date_from_text(text):
    try:
        nlu = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    "Tu es un assistant JSON. Ta tâche est d'extraire une date future à partir d'une phrase en français (ex: 'le 19 mai', 'demain', 'lundi prochain'). "
                    "Si l'année n'est pas mentionnée, utilise l'année actuelle. Si la date est passée, utilise l'année suivante. "
                    "Réponds uniquement en JSON comme ceci: { \"date\": \"2025-05-03\" } avec une vraie date future. Jamais retourner \"YYYY-MM-DD\"."
                    "Si tu ne trouves aucune date, réponds: { \"date\": null }. "
                    "Ne parle pas, ne fais aucun commentaire, ne formate pas le JSON, retourne une ligne unique."
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
        return value)
    except Exception as e:
        app.logger.info(f"❌ Erro ao extrair data: {e}")
        return None

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg = request.form.get("Body", "").strip().lower()
    frm = request.form.get("From")
    resp = MessagingResponse()

    app.logger.info(f"📩 MSG RECEBIDA: {msg}")

    ag = (
        supabase
        .table("agendamentos")
        .select("*")
        .eq("user_phone", frm)
        .eq("status", "Agendado")
        .order("date", desc=True)
        .limit(1)
        .execute()
        .data
    )

    if not ag:
        send_message(resp, "Aucun rendez-vous trouvé pour ce numéro.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    a = ag[0]
    nome = a.get("name_user", "Client")
    cod_id = a["cod_id"]
    comp = a["company_id"]
    reagendando = a.get("reagendando", False)

    if msg == "y":
        supabase.table("agendamentos").update({"status": "Confirmado", "reagendando": False}).eq("cod_id", cod_id).execute()
        send_message(resp, f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "n":
        supabase.table("agendamentos").update({"status": "Annulé", "reagendando": False}).eq("cod_id", cod_id).execute()
        send_message(resp, f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "r":
        supabase.table("agendamentos").update({"reagendando": True}).eq("cod_id", cod_id).execute()
        reagendando = True
        send_message(resp, "Avez-vous un jour de préférence pour reprogrammer ? Vous pouvez répondre par 'demain', 'lundi', 'le 3 mai', etc.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "oui":
        ag = (
            supabase
            .table("agendamentos")
            .select("nova_data")
            .eq("user_phone", frm)
            .eq("cod_id", cod_id)
            .limit(1)
            .execute()
            .data
        )
        if ag and ag[0].get("nova_data"):
            nova_data = ag[0]["nova_data"]
            supabase.table("agendamentos").update({
                "date": nova_data,
                "status": "Confirmado",
                "reagendando": False,
                "nova_data": None
            }).eq("cod_id", cod_id).execute()
            send_message(resp, f"Parfait {nome}! Votre rendez-vous a été reprogrammé pour le {format_date(nova_data)}.")
            return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "non":
        supabase.table("agendamentos").update({"nova_data": None}).eq("cod_id", cod_id).execute()
        send_message(resp, "D'accord, dites-moi une nouvelle date pour reprogrammer.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    # AQUI sim roda IA, se ainda está em modo de reagendamento e msg for nova data
    if reagendando:
        preferred_date_raw = parse_date_from_text(msg)
        app.logger.info(f"📅 Data extraída: {preferred_date_raw}")

        # Corrige ano se IA retornou passado
        if preferred_date_raw:
            try:
                dt = datetime.fromisoformat(preferred_date_raw)
                now = datetime.now()
                if dt.year < now.year:
                    dt = dt.replace(year=now.year)
                    if dt < now:
                        dt = dt.replace(year=now.year + 1)
                preferred_date = dt.date().isoformat()
            except:
                preferred_date = None
        else:
            preferred_date = None

        if preferred_date:
            try:
                datetime.fromisoformat(preferred_date)
            except ValueError:
                send_message(resp, "Désolé, je n'ai pas compris la date. Essayez à nouveau en indiquant un jour précis (ex: 'demain', 'lundi', 'le 3 mai').")
                return str(resp), 200, {"Content-Type": "text/xml"}

            supabase.table("agendamentos").update({"nova_data": preferred_date}).eq("cod_id", cod_id).execute()
            send_message(resp, f"Souhaitez-vous reprogrammer pour le {format_date(preferred_date)} ? Répondez OUI pour confirmer ou NON pour choisir une autre date.")
            return str(resp), 200, {"Content-Type": "text/xml"}

    app.logger.info("⚠️ Caiu na message padrão final")
    send_message(resp, "Merci ! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer.")
    return str(resp), 200, {"Content-Type": "text/xml"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

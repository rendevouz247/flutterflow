from flask import Flask, request
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
from groq import Groq
import os, json, re

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

TRUNCATE_LIMIT = 500

def truncate(text: str, limit: int = TRUNCATE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

def send_message(resp: MessagingResponse, text: str):
    resp.message(truncate(text))

def format_slot(date_str: str, time_str: str) -> str:
    dt = datetime.fromisoformat(f"{date_str}T{time_str}")
    return dt.strftime("%d/%m/%Y %H:%M")

def parse_preferred_date(text):
    try:
        nlu = groq_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": (
                    "Você é um analisador de data. Extraia do texto abaixo a data referida (como 'amanhã', 'próxima sexta', etc.) e retorne no formato JSON: { \"date\": \"YYYY-MM-DD\" }. Se não encontrar data, retorne {\"date\": null}."
                )},
                {"role": "user", "content": text}
            ]
        )
        result = json.loads(nlu.choices[0].message.content)
        return result.get("date")
    except:
        return None

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg = request.form.get("Body", "").strip().lower()
    frm = request.form.get("From")
    resp = MessagingResponse()

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

    if msg == "y":
        supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
        send_message(resp, f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "n":
        supabase.table("agendamentos").update({"status": "Annulé"}).eq("cod_id", cod_id).execute()
        send_message(resp, f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "r":
        send_message(resp, "Avez-vous un jour de préférence pour reprogrammer ? Vous pouvez répondre par 'demain', 'lundi', 'le 3 mai', etc.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    # Se cliente respondeu com data (depois do "R")
    preferred_date = parse_preferred_date(msg)
    if preferred_date:
        def get_available_times(date):
            rows = (
                supabase
                .from_("view_horas_disponiveis")
                .select("horas_disponiveis")
                .eq("company_id", comp)
                .eq("date", date)
                .execute()
                .data
            )
            times = []
            for r in rows:
                j = r.get("horas_disponiveis") or {}
                times += j.get("disponiveis", [])
            return sorted(set(times))

        current = preferred_date
        options = get_available_times(current)

        if options:
            send_message(resp, f"Voici les horaires disponibles pour le {datetime.fromisoformat(current).strftime('%d/%m/%Y')}:\n" + ", ".join(options))
        else:
            prev_day = (datetime.fromisoformat(current) - timedelta(days=1)).strftime("%Y-%m-%d")
            next_day = (datetime.fromisoformat(current) + timedelta(days=1)).strftime("%Y-%m-%d")
            prev_options = get_available_times(prev_day)
            next_options = get_available_times(next_day)

            reply = f"Désolé, aucun horaire disponible le {datetime.fromisoformat(current).strftime('%d/%m/%Y')}"
            if prev_options:
                reply += f". Mais il y en a le {datetime.fromisoformat(prev_day).strftime('%d/%m/%Y')}: {', '.join(prev_options)}"
            if next_options:
                reply += f". Et aussi le {datetime.fromisoformat(next_day).strftime('%d/%m/%Y')}: {', '.join(next_options)}"
            send_message(resp, reply)
            print(resp, reply)

        return str(resp), 200, {"Content-Type": "text/xml"}

    # Caso não seja reconhecido nada
    send_message(
        resp,
        "Merci ! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer."
    )
    return str(resp), 200, {"Content-Type": "text/xml"}
    print(resp, reply)
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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

def format_date(date_str: str) -> str:
    return datetime.fromisoformat(date_str).strftime("%d/%m/%Y")

def parse_date_from_text(text):
    try:
        nlu = groq_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": (
                    "Tu es un assistant qui extrait une date future mentionnée dans un texte (ex: 'le 19 mai', 'demain', 'lundi prochain'). "
                    "Retourne seulement en JSON comme ceci: { \"date\": \"YYYY-MM-DD\" } ou { \"date\": null } si aucune date n'est trouvée."
                )},
                {"role": "user", "content": text}
            ]
        )
        result = json.loads(nlu.choices[0].message.content)
        return result.get("date")
    except Exception as e:
        print("❌ Erro ao extrair data:", e)
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

    # IA entra aqui — quando cliente respondeu após o "R"
    preferred_date = parse_date_from_text(msg)

    if preferred_date:
            try:
                current_fmt = datetime.fromisoformat(preferred_date).strftime('%d/%m/%Y')
            except ValueError:
                send_message(resp, "Désolé, je n'ai pas compris la date. Essayez à nouveau en indiquant un jour précis (ex: 'demain', 'lundi', 'le 3 mai').")
                return str(resp), 200, {"Content-Type": "text/xml"}
                
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
                send_message(resp, f"Voici les horaires disponibles pour le {format_date(current)}:\n" + ", ".join(options))
            else:
                prev_day = (datetime.fromisoformat(current) - timedelta(days=1)).strftime("%Y-%m-%d")
                next_day = (datetime.fromisoformat(current) + timedelta(days=1)).strftime("%Y-%m-%d")
                prev_options = get_available_times(prev_day)
                next_options = get_available_times(next_day)
    
                reply = f"Désolé, aucun horaire disponible le {format_date(current)}"
                if prev_options:
                    reply += f". Mais il y en a le {format_date(prev_day)}: {', '.join(prev_options)}"
                if next_options:
                    reply += f". Et aussi le {format_date(next_day)}: {', '.join(next_options)}"
                send_message(resp, reply)
    
            return str(resp), 200, {"Content-Type": "text/xml"}

    # Se não entendeu a resposta
    send_message(resp, "Merci ! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer.")
    return str(resp), 200, {"Content-Type": "text/xml"}
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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

def format_slot(date_str, time_str):
    dt = datetime.fromisoformat(f"{date_str}T{time_str}")
    return dt.strftime("%d/%m/%Y %H:%M")

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg  = request.form.get("Body", "").strip().lower()
    frm  = request.form.get("From")
    resp = MessagingResponse()

    # 1) Busca último agendamento “Agendado”
    ag = supabase.table("agendamentos") \
        .select("*") \
        .eq("user_phone", frm) \
        .eq("status", "Agendado") \
        .order("date", desc=True) \
        .limit(1) \
        .execute().data

    if not ag:
        resp.message("Aucun rendez-vous trouvé pour ce numéro.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    a      = ag[0]
    nome   = a.get("name_user", "Client")
    cod_id = a["cod_id"]
    comp   = a["company_id"]

    # 2) Fluxo estrito: só aceita Y, N ou R
    if msg == "y":
        supabase.table("agendamentos") \
            .update({"status": "Confirmado"}) \
            .eq("cod_id", cod_id) \
            .execute()
        resp.message(f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "n":
        supabase.table("agendamentos") \
            .update({"status": "Annulé"}) \
            .eq("cod_id", cod_id) \
            .execute()
        resp.message(f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "r":
        # 3) Uso de IA apenas aqui
        # 3a) Tenta NLU para checar se veio data/hora no próprio 'R'
        try:
            nlu = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[
                    {"role": "system", "content": (
                        "Você é um analisador de intenções. Retorne apenas JSON com:\n"
                        "- action: 'reschedule' ou 'check_availability'\n"
                        "- datetime (YYYY-MM-DD HH:MM) se o usuário enviou data+hora\n"
                        "- date (YYYY-MM-DD) se ele só enviou data\n"
                    )},
                    {"role": "user", "content": msg}
                ]
            )
            intent = json.loads(nlu.choices[0].message.content)
        except:
            intent = {"action": "list_slots"}

        # 3b) Se veio datetime completo, aplica alteração imediata
        if intent.get("action") == "reschedule" and intent.get("datetime"):
            date_new, time_new = intent["datetime"].split(" ")
            time_new += ":00"
            supabase.table("agendamentos") \
                .update({
                    "date": date_new,
                    "horas": time_new,
                    "status": "Confirmado"
                }) \
                .eq("cod_id", cod_id) \
                .execute()
            rep_date = datetime.fromisoformat(f"{date_new}T{time_new}") \
                           .strftime("%d/%m/%Y à %H:%M")
            resp.message(f"Parfait {nome}! Reprogrammé pour le {rep_date}.")
            return str(resp), 200, {"Content-Type": "text/xml"}

        # 3c) Se veio só date, checa disponibilidade naquela data
        if intent.get("action") == "check_availability" and intent.get("date"):
            date_q = intent["date"]
            slots = supabase.from_("view_horas_disponiveis") \
                .select("horas") \
                .eq("company_id", comp) \
                .eq("date", date_q) \
                .order("horas", desc=False) \
                .execute().data
            horas = [s["horas"][:5] for s in slots]

            # IA formata resposta
            try:
                rep = groq_client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=[
                        {"role": "system", "content": (
                            "Você é Luna, responda em francês listando horários ou dizendo que não há."
                        )},
                        {"role": "user", "content": (
                            f"Data: {date_q}. Disponibilidades: {', '.join(horas) or 'nenhuma'}"
                        )}
                    ]
                ).choices[0].message.content.strip()
            except:
                rep = (
                    f"Horaires disponibles le {datetime.fromisoformat(date_q).strftime('%d/%m/%Y')}: "
                    + (", ".join(horas) if horas else "aucun")
                )

            resp.message(rep)
            return str(resp), 200, {"Content-Type": "text/xml"}

        # 3d) Sem data antecipada, lista próximos 9 slots
        amanhã = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        slots = supabase.from_("view_horas_disponiveis") \
            .select("date, horas") \
            .eq("company_id", comp) \
            .gte("date", amanhã) \
            .order("date", desc=False) \
            .limit(9) \
            .execute().data

        if not slots:
            resp.message("Désolé, aucune date disponible pour le moment.")
        else:
            opts = [
                datetime.fromisoformat(f"{s['date']}T{s['horas']}") \
                    .strftime("%d/%m/%Y %H:%M")
                for s in slots
            ]
            menu = "\n".join(f"{i+1}) {opt}" for i, opt in enumerate(opts))
            resp.message(
                "Veuillez choisir une nouvelle date :\n\n" + menu
            )

        return str(resp), 200, {"Content-Type": "text/xml"}

    # 4) Qualquer outra mensagem
    resp.message(
        "Merci ! Répondez avec Y pour confirmer, N pour annuler ou R pour reprogrammer."
    )
    return str(resp), 200, {"Content-Type": "text/xml"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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
    if len(text) <= limit:
        return text
    return text[: limit-3] + "..."

def send_message(resp: MessagingResponse, text: str):
    resp.message(truncate(text))

def format_slot(date_str: str, time_str: str) -> str:
    dt = datetime.fromisoformat(f"{date_str}T{time_str}")
    return dt.strftime("%d/%m/%Y %H:%M")

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg  = request.form.get("Body", "").strip().lower()
    frm  = request.form.get("From")
    resp = MessagingResponse()

    # 1) Busca último agendamento “Agendado”
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

    a      = ag[0]
    nome   = a.get("name_user", "Client")
    cod_id = a["cod_id"]
    comp   = a["company_id"]

    # 2) Fluxo estrito: só Y, N ou R
    if msg == "y":
        supabase.table("agendamentos") \
            .update({"status": "Confirmado"}) \
            .eq("cod_id", cod_id) \
            .execute()
        send_message(resp, f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "n":
        supabase.table("agendamentos") \
            .update({"status": "Annulé"}) \
            .eq("cod_id", cod_id) \
            .execute()
        send_message(resp, f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "r":
        # 3) IA NLU para 'R'
        try:
            nlu = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[
                    {"role": "system", "content": (
                        "Você é analisador de intenções. Retorne JSON com:\n"
                        "- action: 'reschedule' ou 'check_availability'\n"
                        "- datetime (YYYY-MM-DD HH:MM) se houver data+hora\n"
                        "- date (YYYY-MM-DD) se houver apenas data\n"
                    )},
                    {"role": "user", "content": msg}
                ]
            )
            intent = json.loads(nlu.choices[0].message.content)
        except:
            intent = {"action": "list_slots"}

        # 3a) Reschedule com datetime
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
            rd = datetime.fromisoformat(f"{date_new}T{time_new}") \
                     .strftime("%d/%m/%Y à %H:%M")
            send_message(resp, f"Parfait {nome}! Reprogrammé pour le {rd}.")
            return str(resp), 200, {"Content-Type": "text/xml"}

        # 3b) Check availability em data específica
        if intent.get("action") == "check_availability" and intent.get("date"):
            date_q = intent["date"]
            rows = (
                supabase
                .from_("view_horas_disponiveis")
                .select("horas_disponiveis")
                .eq("company_id", comp)
                .eq("date", date_q)
                .order("date", desc=False)
                .execute()
                .data
            )
            # extrai e unifica horários
            times = []
            for r in rows:
                j = r.get("horas_disponiveis") or {}
                times += j.get("disponiveis", [])
            times = sorted(set(times))

            # IA formata a resposta
            try:
                rep = groq_client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=[
                        {"role": "system", "content": (
                            "Você é Luna. Responda em francês listando horários ou dizendo que não há."
                        )},
                        {"role": "user", "content": (
                            f"Data: {date_q}. Disponibilités: {', '.join(times) or 'aucune'}"
                        )}
                    ]
                ).choices[0].message.content.strip()
            except:
                date_fmt = datetime.fromisoformat(date_q).strftime("%d/%m/%Y")
                rep = (
                    f"Horaires disponibles le {date_fmt}: "
                    + (", ".join(times) if times else "aucun")
                )

            send_message(resp, rep)
            return str(resp), 200, {"Content-Type": "text/xml"}

        # 3c) Lista próximos 5 slots
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        rows = (
            supabase
            .from_("view_horas_disponiveis")
            .select("date, horas_disponiveis")
            .eq("company_id", comp)
            .gte("date", tomorrow)
            .order("date", desc=False)
            .execute()
            .data
        )
        slots = []
        for r in rows:
            d = r["date"]
            for h in r.get("horas_disponiveis", {}).get("disponiveis", []):
                slots.append((d, h))
                if len(slots) >= 5:
                    break
            if len(slots) >= 5:
                break

        if not slots:
            send_message(resp, "Désolé, aucune date disponible pour le moment.")
        else:
            menu = "\n".join(
                f"{i+1}) {format_slot(d, h)}"
                for i, (d, h) in enumerate(slots)
            )
            send_message(resp, "Veuillez choisir une nouvelle date :\n\n" + menu)

        return str(resp), 200, {"Content-Type": "text/xml"}
        print(send_message)

    # 4) Qualquer outra mensagem
    send_message(
        resp,
        "Merci ! Répondez avec Y pour confirmer, N pour annuler ou R pour reprogrammer."
    )
    return str(resp), 200, {"Content-Type": "text/xml"}
    print(send_message)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

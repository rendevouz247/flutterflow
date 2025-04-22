from flask import Flask, request, Response
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
from groq import Groq
from deep_translator import GoogleTranslator
import os, json, re


# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
def format_slot(date_str, time_str):
    dt = datetime.fromisoformat(f"{date_str}T{time_str}")
    return dt.strftime("%d/%m/%Y %H:%M")

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg   = request.form.get("Body", "").strip()
    frm   = request.form.get("From")
    resp  = MessagingResponse()

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
    nome   = a.get("name_user") or "Client"
    cod_id = a["cod_id"]

    # 2) Chama a IA para extrair intent
    intent = {}
    try:
        nlu = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    "Você é um analisador de intenções. "
                    "Retorne apenas JSON com: action (confirm|cancel|reschedule|check_availability), "
                    "datetime (YYYY-MM-DD HH:MM) e/ou date (YYYY-MM-DD) quando aplicável."
                )},
                {"role": "user", "content": msg}
            ]
        )
        content = nlu.choices[0].message.content.strip()
        intent = json.loads(content)
    except Exception as e:
        print("⚠️ NLU fallback:", e)
        # fallback manual
        if msg.lower() == "y":
            intent = {"action": "confirm"}
        elif msg.lower() == "n":
            intent = {"action": "cancel"}
        elif msg.lower() == "r":
            intent = {"action": "reschedule"}
        elif re.match(r"\d{1,2}/\d{1,2}(/(\d{2}|\d{4}))?$", msg):
            # cliente enviou algo como "30/04" ou "30/04/2025"
            parts = msg.split("/")
            day, month = parts[0], parts[1]
            year = parts[2] if len(parts) == 3 else str(datetime.utcnow().year)
            if len(year) == 2:
                year = "20" + year
            date_iso = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            intent = {"action": "check_availability", "date": date_iso}
        else:
            intent = {"action": "unknown"}

    # 3) Roteia pelos casos
    act = intent.get("action")

    if act == "confirm":
        supabase.table("agendamentos") \
            .update({"status": "Confirmado"}) \
            .eq("cod_id", cod_id).execute()
        resp.message(f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if act == "cancel":
        supabase.table("agendamentos") \
            .update({"status": "Annulé"}) \
            .eq("cod_id", cod_id).execute()
        resp.message(f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    # Reschedule com datetime completo
    if act == "reschedule" and intent.get("datetime"):
        dt_str = intent["datetime"]  # "YYYY-MM-DD HH:MM"
        date_new, time_new = dt_str.split(" ")
        time_new += ":00"
        supabase.table("agendamentos") \
            .update({"date": date_new, "horas": time_new, "status": "Confirmado"}) \
            .eq("cod_id", cod_id).execute()
        rd = datetime.fromisoformat(f"{date_new}T{time_new}").strftime("%d/%m/%Y à %H:%M")
        resp.message(f"Parfait {nome}! Votre rendez-vous a été reprogrammé pour le {rd}.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    # Verifica disponibilidade em data específica
    if act == "check_availability" and intent.get("date"):
        date_q = intent["date"]
        slots = supabase.from_("view_horas_disponiveis") \
            .select("horas") \
            .eq("company_id", a["company_id"]) \
            .eq("date", date_q) \
            .order("horas", asc=True) \
            .execute().data
        horas_list = [s["horas"][:5] for s in slots]

        # Formatação final pela IA
        try:
            reply = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[
                    {"role":"system","content":(
                        "Você é Luna, assistente virtual de clínica. "
                        "Responda em francês, listando horários ou dizendo que não há." 
                    )},
                    {"role":"user","content":(
                        f"Data: {date_q}. Disponibilidades: {', '.join(horas_list) or 'nenhuma'}"
                    )}
                ]
            ).choices[0].message.content.strip()
        except:
            # fallback simples
            if horas_list:
                reply = "Horaires disponibles le " + datetime.fromisoformat(date_q).strftime("%d/%m/%Y") + ": " + ", ".join(horas_list)
            else:
                reply = "Désolé, aucun horaire disponible le " + datetime.fromisoformat(date_q).strftime("%d/%m/%Y") + "."
        resp.message(reply)
        return str(resp), 200, {"Content-Type": "text/xml"}

    # Fallback genérico
    resp.message(
        "Merci ! Répondez avec Y pour confirmer, N pour annuler, R pour reprogrammer, "
        "ou indiquez une date (ex: 30/04) pour vérifier la disponibilité."
    )
    return str(resp), 200, {"Content-Type": "text/xml"}


    # Se nenhum dos casos acima, manda instrução padrão
    resp.message("Merci ! Répondez avec Y pour confirmer, N pour annuler, ou indiquez une date (ex: 30/04) pour vérifier la disponibilité.")
    return Response(str(resp), mimetype="text/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

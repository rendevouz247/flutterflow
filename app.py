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
        return Response(str(resp), mimetype="text/xml")

    a      = ag[0]
    nome   = a.get("name_user") or "Client"
    cod_id = a["cod_id"]

    # 2) Usa IA para extrair “intent” e parâmetros do cliente
    intent_chat = groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": (
                "Você é um analisador de intenções. "
                "Recebe a mensagem do cliente e retorna apenas um JSON com campos:\n"
                "- action: um dos ['confirm', 'cancel', 'reschedule', 'check_availability']\n"
                "- date: no formato 'YYYY-MM-DD' (quando action for check_availability)\n"
                "- datetime: 'YYYY-MM-DD HH:MM' (quando action for reschedule com data completa)\n"
                "Nada além do JSON." )},
            {"role": "user", "content": msg}
        ]
    )
    intent = json.loads(intent_chat.choices[0].message.content)

    # 3) Roteia de acordo com a intent
    # 3a) Confirmação simples
    if intent["action"] == "confirm":
        supabase.table("agendamentos") \
            .update({"status": "Confirmado"}) \
            .eq("cod_id", cod_id).execute()
        resp.message(f"Merci {nome}! Votre rendez-vous est confirmé.")
        return Response(str(resp), mimetype="text/xml")

    # 3b) Cancelamento
    if intent["action"] == "cancel":
        supabase.table("agendamentos") \
            .update({"status": "Annulé"}) \
            .eq("cod_id", cod_id).execute()
        resp.message(f"D'accord {nome}, votre rendez-vous a été annulé.")
        return Response(str(resp), mimetype="text/xml")

    # 3c) Remarcar com data completa na própria mensagem
    if intent["action"] == "reschedule" and intent.get("datetime"):
        dt_str = intent["datetime"]  # "YYYY-MM-DD HH:MM"
        date_new, time_new = dt_str.split(" ")
        time_new = f"{time_new}:00"
        supabase.table("agendamentos") \
            .update({"date": date_new, "horas": time_new, "status": "Confirmado"}) \
            .eq("cod_id", cod_id).execute()
        rd = datetime.fromisoformat(f"{date_new}T{time_new}").strftime("%d/%m/%Y à %H:%M")
        resp.message(f"Parfait {nome}! Votre rendez-vous a été reprogrammé pour le {rd}.")
        return Response(str(resp), mimetype="text/xml")

    # 3d) Verificar disponibilidade em data específica
    if intent["action"] == "check_availability" and intent.get("date"):
        date_q = intent["date"]  # "YYYY-MM-DD"
        # consulta view de horários disponíveis
        slots = supabase.from_("view_horas_disponiveis") \
            .select("horas") \
            .eq("company_id", a["company_id"]) \
            .eq("date", date_q) \
            .order("horas", asc=True) \
            .execute().data

        horas_list = [s["horas"][:5] for s in slots]
        # 3d1) pergunta à IA como responder naturalmente
        reply_chat = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role":"system","content":(
                    "Você é Luna, assistente virtual de clínica. "
                    "Informe ao cliente se há horários disponíveis ou não, "
                    "listando-os de forma clara em francês.")},
                {"role":"user","content":(
                    f"Data: {date_q}. Disponibilidades: {', '.join(horas_list) or 'nenhuma'}")}
            ]
        )
        reply = reply_chat.choices[0].message.content.strip()
        resp.message(reply)
        return Response(str(resp), mimetype="text/xml")

    # Se nenhum dos casos acima, manda instrução padrão
    resp.message("Merci ! Répondez avec Y pour confirmer, N pour annuler, ou indiquez une date (ex: 30/04) pour vérifier la disponibilité.")
    return Response(str(resp), mimetype="text/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

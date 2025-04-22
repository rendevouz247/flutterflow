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
    msg  = request.form.get("Body", "").strip().lower()
    frm  = request.form.get("From")
    resp = MessagingResponse()

    # 1) Busca último agendamento “Agendado”
    ag = supabase.table("agendamentos") \
        .select("*") \
        .eq("user_phone", frm) \
        .eq("status", "Agendado") \
        .order("date", desc=True) \
        .limit(1).execute().data

    if not ag:
        resp.message("Aucun rendez-vous trouvé pour ce numéro.")
        response = str(resp)
        headers  = {"Content-Type": "text/xml"}
        return response, 200, headers


    a      = ag[0]
    nome   = a.get("name_user", "Client")
    cod_id = a["cod_id"]
    comp   = a["company_id"]

    # 2) Fluxo estrito: só aceita Y, N ou R
    if msg == "y":
        supabase.table("agendamentos") \
            .update({"status":"Confirmado"}) \
            .eq("cod_id",cod_id).execute()
        resp.message(f"Merci {nome}! Votre rendez-vous est confirmé.")
        response = str(resp)
        headers  = {"Content-Type": "text/xml"}
        return response, 200, headers


    if msg == "n":
        supabase.table("agendamentos") \
            .update({"status":"Annulé"}) \
            .eq("cod_id",cod_id).execute()
        resp.message(f"D'accord {nome}, votre rendez-vous a été annulé.")
        response = str(resp)
        headers  = {"Content-Type": "text/xml"}
        return response, 200, headers


    if msg == "r":
        # 3) Aqui sim chamamos a IA para interpretar ou listar opções
        # Primeiro, tentamos extrair se o cliente já enviou data/hora
        # Caso contrário, retornamos as próximas datas disponíveis
        # (mesma lógica que vimos antes)

        # Exemplo: uso da NLU para extrair JSON com ação e parâmetros
        try:
            nlu = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[
                    {"role":"system","content":(
                        "Você é analisador de intenções. Retorne apenas JSON com:\n"
                        "- action: 'reschedule' ou 'check_availability'\n"
                        "- datetime (YYYY-MM-DD HH:MM) se o usuário enviou data+hora\n"
                        "- date (YYYY-MM-DD) se ele só enviou data\n"
                    )},
                    {"role":"user","content": msg}
                ]
            )
            intent = json.loads(nlu.choices[0].message.content)
        except:
            intent = {"action": "list_slots"}

        # Se veio datetime completo, aplica alteração
        if intent.get("action") == "reschedule" and intent.get("datetime"):
            date_new, time_new = intent["datetime"].split(" ")
            time_new += ":00"
            supabase.table("agendamentos") \
                .update({
                    "date": date_new,
                    "horas": time_new,
                    "status": "Confirmado"
                }).eq("cod_id", cod_id).execute()
            rd = datetime.fromisoformat(f"{date_new}T{time_new}") \
                   .strftime("%d/%m/%Y à %H:%M")
            resp.message(f"Parfait {nome}! Reprogrammé pour le {rd}.")
            response = str(resp)
            headers  = {"Content-Type": "text/xml"}
            return response, 200, headers


        # Se veio apenas date, checa disponibilidade naquela data
        if intent.get("action") == "check_availability" and intent.get("date"):
            date_q = intent["date"]
            slots = supabase.from_("view_horas_disponiveis") \
                .select("horas") \
                .eq("company_id", comp) \
                .eq("date", date_q) \
                .order("horas", asc=True).execute().data
            horas = [s["horas"][:5] for s in slots]
            # IA formata a resposta
            try:
                rep = groq_client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=[
                        {"role":"system","content":(
                            "Você é Luna, responda em francês listando horários ou dizendo que não há."
                        )},
                        {"role":"user","content":(
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
            response = str(resp)
            headers  = {"Content-Type": "text/xml"}
            return response, 200, headers


        # Caso não tenha data/hora no JSON, listamos as próximas 9 slots
        amanhã = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        slots = supabase.from_("view_horas_disponiveis") \
            .select("date, horas") \
            .eq("company_id", comp) \
            .gte("date", amanhã) \
            .order("date", asc=True) \
            .limit(9).execute().data

        if not slots:
            resp.message("Désolé, aucune date disponible pour le moment.")
        else:
            opts = [
                datetime.fromisoformat(f"{s['date']}T{s['horas']}") \
                    .strftime("%d/%m/%Y %H:%M")
                for s in slots
            ]
            texto = "\n".join(f"{i+1}) {opt}" for i,opt in enumerate(opts))
            resp.message(
                "Veuillez choisir une nouvelle date en répondant par le numéro :\n\n" + texto
            )
        response = str(resp)
        headers  = {"Content-Type": "text/xml"}
        return response, 200, headers


    # 4) Qualquer outra mensagem cai aqui
    resp.message(
        "Merci ! Répondez avec Y pour confirmer, N pour annuler ou R pour reprogrammer."
    )
    response = str(resp)
    headers  = {"Content-Type": "text/xml"}
    return response, 200, headers
    

    # Se nenhum dos casos acima, manda instrução padrão
    resp.message("Merci ! Répondez avec Y pour confirmer, N pour annuler, ou indiquez une date (ex: 30/04) pour vérifier la disponibilité.")
    response = str(resp)
    headers  = {"Content-Type": "text/xml"}
    return response, 200, headers



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

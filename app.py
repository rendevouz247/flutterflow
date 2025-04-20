from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from datetime import datetime, timedelta, date
import os

app = Flask(__name__)

# Configs
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg_body = request.form.get("Body", "").strip()
    from_number = request.form.get("From")
    resp = MessagingResponse()
    agora = datetime.utcnow()

    result = supabase.table("agendamentos") \
        .select("*") \
        .eq("user_phone", from_number) \
        .eq("convite_ativo", True) \
        .order("tentativa_convite_em", desc=True) \
        .limit(1) \
        .execute()

    if result.data:
        agendamento = result.data[0]
        cod_id = agendamento["cod_id"]
        tentativa_em = datetime.fromisoformat(agendamento["tentativa_convite_em"])
        tempo_limite = tentativa_em + timedelta(hours=2)

        if msg_body.lower() == "yes":
            if agora <= tempo_limite:
                supabase.table("agendamentos").update({
                    "status": "Confirmado",
                    "convite_ativo": False
                }).eq("cod_id", cod_id).execute()
                resp.message("Perfeito! Você foi confirmado no encaixe. Até lá! 😊")
            else:
                supabase.table("agendamentos").update({
                    "convite_ativo": False
                }).eq("cod_id", cod_id).execute()
                resp.message("Esse encaixe não está mais disponível. Seu agendamento original continua reservado.")
        elif msg_body.lower() == "no":
            supabase.table("agendamentos").update({
                "convite_ativo": False
            }).eq("cod_id", cod_id).execute()
            resp.message("Tudo bem, seu agendamento original continua reservado.")
        else:
            resposta = client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "Você é um atendente multilíngue simpático que ajuda clientes a remarcar consultas e esclarecer dúvidas."},
                    {"role": "user", "content": msg_body}
                ]
            )
            texto_ia = resposta.choices[0].message.content.strip()
            resp.message(texto_ia)

        return Response(str(resp), mimetype="application/xml")

    agendamento_padrao = supabase.table("agendamentos") \
        .select("*") \
        .eq("user_phone", from_number) \
        .eq("status", "Agendado") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()

    if agendamento_padrao.data:
        agendamento = agendamento_padrao.data[0]
        cod_id = agendamento["cod_id"]
        company_id = agendamento["company_id"]

        if msg_body.lower() == "no":
            supabase.table("agendamentos").update({"status": "Cancelado"}).eq("cod_id", cod_id).execute()
            resp.message("Consulta cancelada. Obrigado por avisar!")

            fila = supabase.table("agendamentos") \
                .select("*") \
                .eq("company_id", company_id) \
                .eq("lista_espera", True) \
                .eq("status", "Agendado") \
                .eq("convite_ativo", False) \
                .order("date") \
                .limit(1) \
                .execute()

            if fila.data:
                novo = fila.data[0]
                user_id = novo["user_id"]
                user_info = supabase.table("tab_user").select("name, phone").eq("user_id", user_id).limit(1).execute()
                nome = user_info.data[0]["name"]
                telefone = user_info.data[0]["phone"]
                data = novo["date"]
                hora = novo["horas"]

                mensagem = (
                    f"Olá {nome}, surgiu uma vaga para antecipar sua consulta para {data} às {hora}. "
                    "Responda YES para aceitar ou NO para manter seu horário original."
                )

                twilio_client.messages.create(
                    body=mensagem,
                    from_=TWILIO_PHONE,
                    to=telefone
                )

                supabase.table("agendamentos").update({
                    "convite_ativo": True,
                    "tentativa_convite_em": agora.isoformat(),
                    "user_phone": telefone
                }).eq("cod_id", novo["cod_id"]).execute()
        elif msg_body.lower() == "yes":
            supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
            resp.message("Perfeito! Consulta confirmada. Nos vemos em breve! 🩺")
        else:
            horarios_disponiveis = supabase.table("view_horas_disponiveis").select("date, horas_disponiveis").eq("company_id", company_id).order("date").limit(3).execute()
            sugestoes = []
            for item in horarios_disponiveis.data:
                data_label = item["date"]
                horas = item["horas_disponiveis"]["disponiveis"][:3]
                sugestoes.append(f"{data_label}: {', '.join(horas)}")

            texto = "Encontrei alguns horários disponíveis para você:\n\n"
            texto += "\n".join(sugestoes)
            texto += "\n\nDeseja escolher um desses ou prefere outro dia/hora específico?"
            print(texto)
            
            resp.message(texto)

        return Response(str(resp), mimetype="application/xml")

    resp.message("Não encontramos um agendamento ou convite ativo para esse número.")
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



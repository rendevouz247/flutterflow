from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from datetime import datetime, timedelta
import os
from flask import Response

# CONFIGURAÇÕES
app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg_body = request.form.get("Body", "").strip().lower()
    from_number = request.form.get("From")
    resp = MessagingResponse()

    print(f"📨 Mensagem recebida: {msg_body} de {from_number}")

    agora = datetime.utcnow()

    # 1️⃣ Tenta encontrar um encaixe ativo
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

        if msg_body == "yes":
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
        elif msg_body == "no":
            supabase.table("agendamentos").update({
                "convite_ativo": False
            }).eq("cod_id", cod_id).execute()
            resp.message("Tudo bem, seu agendamento original continua reservado.")
        else:
            resp.message("Por favor, responda com 'Yes' ou 'No'.")
        return Response(str(resp), mimetype="application/xml")

    # 2️⃣ Se não for encaixe, tenta pegar agendamento comum
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

        print("📌 Agendamento normal detectado:", agendamento)

        if msg_body == "yes":
            supabase.table("agendamentos").update({
                "status": "Confirmado"
            }).eq("cod_id", cod_id).execute()
            resp.message("Agendamento confirmado com sucesso. Até lá! 😉")

        elif msg_body == "no":
            # 1. Cancela o agendamento original
            supabase.table("agendamentos").update({
                "status": "Cancelado"
            }).eq("cod_id", cod_id).execute()
            resp.message("Consulta cancelada. Obrigado por avisar!")

            # 2. Busca próximo da lista de espera
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

                # 🔍 Buscar nome e telefone na tab_user
                user_info = supabase.table("tab_user") \
                    .select("name, phone") \
                    .eq("user_id", user_id) \
                    .limit(1) \
                    .execute()

                nome = user_info.data[0]["name"]
                telefone = user_info.data[0]["phone"]
                data = novo["date"]
                hora = novo["horas"]

                mensagem = (
                    f"Olá {nome}, surgiu uma vaga para antecipar sua consulta para {data} às {hora}. "
                    "Responda YES para aceitar ou NO para manter seu horário original."
                )

                print(f"📤 Enviando SMS para {nome} - {telefone}")

                twilio_client.messages.create(
                    body=mensagem,
                    from_=TWILIO_PHONE,
                    to=telefone
                )

                # ✅ Atualiza o novo agendamento com os dados
                supabase.table("agendamentos").update({
                    "convite_ativo": True,
                    "tentativa_convite_em": agora.isoformat(),
                    "user_phone": telefone
                }).eq("cod_id", novo["cod_id"]).execute()
            else:
                print("⚠️ Nenhum cliente na fila de espera.")
        else:
            resp.message("Por favor, responda com 'Yes' para confirmar ou 'No' para cancelar.")

        return Response(str(resp), mimetype="application/xml")

    # 3️⃣ Se nada for encontrado
    resp.message("Não encontramos um agendamento ou convite ativo para esse número.")
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


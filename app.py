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
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if result.data:
        agendamento = result.data[0]
        cod_id = agendamento["cod_id"]
        status = agendamento["status"]
        company_id = agendamento["company_id"]

        # Se for resposta direta de confirmação ou cancelamento
        if msg_body.lower() == "yes":
            supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
            resp.message("Perfeito! Consulta confirmada. Nos vemos em breve! 🩺")

        elif msg_body.lower() == "no":
            supabase.table("agendamentos").update({"status": "Cancelado"}).eq("cod_id", cod_id).execute()
            resp.message("Consulta cancelada. Obrigado por avisar!")

        else:
            # Qualquer outra pergunta será interpretada pela IA
            resposta = client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "Você é um atendente multilíngue simpático que ajuda clientes a remarcar consultas, esclarecer dúvidas e sugerir novos horários."},
                    {"role": "user", "content": msg_body}
                ]
            )
            texto_ia = resposta.choices[0].message.content.strip()

            # Sugerir horários disponíveis automaticamente
            horarios_disponiveis = supabase.table("view_horas_disponiveis") \
                .select("date, horas_disponiveis") \
                .eq("company_id", company_id) \
                .order("date") \
                .limit(3) \
                .execute()

            sugestoes = []
            for item in horarios_disponiveis.data:
                data_label = item["date"]
                horas = item["horas_disponiveis"]["disponiveis"][:3]
                sugestoes.append(f"{data_label}: {', '.join(horas)}")

            texto = f"{texto_ia}\n\nAqui estão alguns horários disponíveis para você:\n\n"
            texto += "\n".join(sugestoes)
            texto += "\n\nDeseja escolher um desses ou prefere outro dia/hora específico?"
            resp.message(texto)

        return Response(str(resp), mimetype="application/xml")

    # Se não encontrou nenhum agendamento
    resp.message("Não encontramos um agendamento ou convite ativo para esse número.")
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



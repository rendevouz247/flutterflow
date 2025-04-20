from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from datetime import datetime
import os
import sys
import re
from deep_translator import GoogleTranslator

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

    if not result.data:
        resp.message("Não encontramos um agendamento ativo para esse número.")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    agendamento = result.data[0]
    cod_id = agendamento["cod_id"]
    status = agendamento["status"]
    company_id = agendamento["company_id"]
    nome_cliente = agendamento.get("nome_cliente", "Cliente")
    nome_atendente = agendamento.get("nome_atendente", "atendente")

    if msg_body.lower() == "yes":
        novo_agendamento = agendamento.get("nova_data_confirmacao")
        if novo_agendamento:
            data_nova = novo_agendamento["data"]
            hora_nova = novo_agendamento["hora"]
            supabase.table("agendamentos").update({
                "status": "Confirmado",
                "date": data_nova,
                "horas": hora_nova,
                "nova_data_confirmacao": None
            }).eq("cod_id", cod_id).execute()
            resp.message(f"Confirmado para {data_nova} às {hora_nova[:5]} com {nome_atendente}! 😉")
            return Response(str(resp), content_type="text/xml; charset=utf-8")

        supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
        resp.message(f"Perfeito, {nome_cliente}! Consulta confirmada com {nome_atendente}. Até lá! 🩺")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    if msg_body.lower() == "no":
        supabase.table("agendamentos").update({"status": "Cancelado", "nova_data_confirmacao": None}).eq("cod_id", cod_id).execute()
        resp.message("Consulta cancelada. Obrigado por avisar!")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    padrao_data = re.search(r"(\d{1,2})[\/-](\d{1,2})", msg_body)
    padrao_hora = re.search(r"(\d{1,2})[:h](\d{2})", msg_body)

    horarios_disponiveis = supabase.table("view_horas_disponiveis") \
        .select("date, horas_disponiveis") \
        .eq("company_id", company_id) \
        .order("date") \
        .limit(3) \
        .execute()

    sugestoes = []
    horario_encontrado = None
    for item in horarios_disponiveis.data:
        data_label = item["date"]
        horas = item["horas_disponiveis"].get("disponiveis", [])[:3]
        sugestoes.append(f"📅 {data_label[8:10]}/{data_label[5:7]}: {', '.join(horas)}")

        if padrao_data and padrao_hora:
            dia, mes = padrao_data.groups()
            hora, minuto = padrao_hora.groups()
            data_candidata = f"2025-{int(mes):02d}-{int(dia):02d}"
            hora_candidata = f"{int(hora):02d}:{int(minuto):02d}:01"

            if data_label == data_candidata and hora_candidata in item["horas_disponiveis"].get("disponiveis", []):
                horario_encontrado = {"data": data_candidata, "hora": hora_candidata}

    if horario_encontrado:
        supabase.table("agendamentos").update({"nova_data_confirmacao": horario_encontrado}).eq("cod_id", cod_id).execute()
        texto = f"Achei o horário {horario_encontrado['data'][8:10]}/{horario_encontrado['data'][5:7]} às {horario_encontrado['hora'][:5]}. Posso confirmar pra você? Responda YES pra confirmar."
        resp.message(texto)
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    try:
        system_prompt = "Você é um atendente virtual profissional, claro, simpático e direto."
        resposta = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": msg_body}
            ]
        )
        texto_ia = resposta.choices[0].message.content.strip()
        print("🧠 IA RESPONDEU:", texto_ia, flush=True)
    except Exception as e:
        print("❌ ERRO COM IA:", e, file=sys.stderr, flush=True)
        texto_ia = "Oi! Tudo bem? Aqui estão alguns horários pra você escolher."

    texto = f"{texto_ia}"
    texto += "\n\nAqui vão uns horários disponíveis pra você:\n\n"
    texto += "\n".join(sugestoes)
    texto += "\n\nQuer que eu reserve um desses? Ou prefere outro? 😊"

    mensagem_final = texto.replace("\n", " • ").strip()[:800]
    print("📦 MENSAGEM ENVIADA AO TWILIO:", mensagem_final, flush=True)

    resp.message(mensagem_final)
    return Response(str(resp), content_type="text/xml; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from datetime import datetime
import os
import sys
import re
import random
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
    nome_cliente = agendamento.get("user_name") or agendamento.get("nome_cliente") or "Cliente"
    nome_atendente = agendamento.get("nome_atendente", "atendente")

    if msg_body.lower() == "yes":
        supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
        resp.message(f"Perfeito, {nome_cliente}! Consulta confirmada com {nome_atendente}. Até lá! 🩺")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    if msg_body.lower() == "no":
        supabase.table("agendamentos").update({"status": "Cancelado"}).eq("cod_id", cod_id).execute()
        resp.message("Consulta cancelada. Obrigado por avisar!")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    padrao_data = re.search(r"(\d{2})[\/\-](\d{2})", msg_body)
    padrao_hora = re.search(r"(\d{1,2})[:h](\d{2})", msg_body)

    if padrao_data and padrao_hora:
        try:
            dia, mes = padrao_data.groups()
            hora, minuto = padrao_hora.groups()
            ano = datetime.now().year
            data_formatada = datetime.strptime(f"{dia}/{mes}/{ano}", "%d/%m/%Y").date()
            hora_formatada = f"{hora.zfill(2)}:{minuto.zfill(2)}:01"

            horarios = supabase.table("view_horas_disponiveis") \
                .select("*") \
                .eq("company_id", company_id) \
                .eq("date", data_formatada.isoformat()) \
                .execute()

            for linha in horarios.data:
                if hora_formatada in linha["horas_disponiveis"].get("disponiveis", []):
                    msg = (
                        f"Legal! Posso agendar então para o dia {data_formatada.strftime('%d/%m/%Y')} "
                        f"às {hora_formatada[:5]} com {nome_atendente}. Pode ser?\n\nResponda com YES para confirmar ou NO para manter como está."
                    )
                    resp.message(msg)
                    return Response(str(resp), content_type="text/xml; charset=utf-8")

            resp.message("Este horário não está mais disponível. Deseja que eu sugira outros?")
            return Response(str(resp), content_type="text/xml; charset=utf-8")

        except Exception as e:
            print("⚠️ Erro ao processar nova data/hora:", e, file=sys.stderr, flush=True)

    try:
        system_prompt = (
            "Você é um assistente virtual que responde de forma natural e direta, sem enrolação. "
            "Use linguagem casual e amigável, como se fosse uma pessoa real conversando por WhatsApp. "
            "Evite perguntas repetidas e mensagens longas. Responda como um atendente experiente, gentil e objetivo."
        )
        resposta = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": msg_body}
            ]
        )
        texto_ia = resposta.choices[0].message.content.strip()
        print("🧠 IA RESPONDEU:", texto_ia, flush=True)

        if len(texto_ia.split()) < 6 or ("?" in texto_ia and len(texto_ia) < 50):
            texto_ia = "Claro! Aqui estão alguns horários que podemos te oferecer 👇"

    except Exception as e:
        print("❌ ERRO COM IA:", e, file=sys.stderr, flush=True)
        texto_ia = "Claro! Me diz melhor o que você precisa e te mostro os horários disponíveis."

    texto = f"{texto_ia}"

    if not re.search(r"\d{2}/\d{2}|\d{2}:\d{2}", texto_ia):
        horarios_disponiveis = supabase.table("view_horas_disponiveis") \
            .select("date, horas_disponiveis") \
            .eq("company_id", company_id) \
            .order("date") \
            .limit(3) \
            .execute()

        sugestoes = []
        for item in horarios_disponiveis.data:
            data_label = item["date"]
            horas = item["horas_disponiveis"].get("disponiveis", [])[:3]
            sugestoes.append(f"{data_label}: {', '.join(horas)}")

        texto += "\n\nAqui estão alguns horários disponíveis:\n\n"
        texto += "\n".join(sugestoes)
        texto += "\n\nQuer escolher um desses ou prefere outro dia/hora? 😊"

    mensagem_final = texto.replace("\n", " • ").strip()[:800]
    print("📦 MENSAGEM ENVIADA AO TWILIO:", mensagem_final, flush=True)

    resp.message(mensagem_final)
    return Response(str(resp), content_type="text/xml; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

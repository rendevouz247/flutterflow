from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from datetime import datetime, timedelta
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
        resp.message("Numéro introuvable dans notre système. Veuillez contacter le support.")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    agendamento = result.data[0]
    cod_id = agendamento["cod_id"]
    status = agendamento["status"]
    company_id = agendamento["company_id"]
    nome_cliente = agendamento.get("nome_cliente") or "Client"
    nome_atendente = agendamento.get("nome_atendente") or "un assistant"
    empresa = agendamento.get("company_name") or "notre clinique"
    data_consulta = datetime.strptime(agendamento["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora_consulta = agendamento["horas"][:5]

    if msg_body.lower() in ["y", "yes", "oui"]:
        if agendamento.get("nova_data_confirmacao"):
            nova_data = agendamento["nova_data_confirmacao"]
            data, hora = nova_data.split(" ")
            supabase.table("agendamentos").update({
                "date": data,
                "horas": hora,
                "status": "Confirmado",
                "nova_data_confirmacao": None
            }).eq("cod_id", cod_id).execute()
            resp.message(f"Parfait {nome_cliente}! Votre rendez-vous a été reprogrammé pour le {datetime.strptime(data, '%Y-%m-%d').strftime('%d/%m')} à {hora[:5]}. ✅")
            return Response(str(resp), content_type="text/xml; charset=utf-8")
        else:
            supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
            resp.message(f"Parfait {nome_cliente}, votre rendez-vous avec {nome_atendente} est confirmé pour le {data_consulta} à {hora_consulta}. ✅")
            return Response(str(resp), content_type="text/xml; charset=utf-8")

    if msg_body.lower() in ["n", "no", "non"]:
        supabase.table("agendamentos").update({"status": "Cancelado"}).eq("cod_id", cod_id).execute()
        resp.message(f"Votre rendez-vous du {data_consulta} à {hora_consulta} a été annulé. Merci!")
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    if msg_body.lower() in ["r", "remarquer", "remarcar"]:
        mensagem = (
            f"Bonjour {nome_cliente}, je suis Luna, l'assistante de {empresa}.\n"
            f"Vous souhaitez une nouvelle date en particulier ou voulez-vous que je vous propose quelques créneaux disponibles ?"
        )
        resp.message(mensagem)
        return Response(str(resp), content_type="text/xml; charset=utf-8")

    padrao_data = re.search(r"(\d{2}/\d{2})", msg_body)
    padrao_hora = re.search(r"(\d{1,2}[:h]\d{2})", msg_body)

    if padrao_data and padrao_hora:
        try:
            data_str = padrao_data.group(1) + f"/{datetime.now().year}"
            data_formatada = datetime.strptime(data_str, "%d/%m/%Y").date()
            hora_bruta = padrao_hora.group(1).replace("h", ":") + ":01"

            horarios = supabase.table("view_horas_disponiveis") \
                .select("*") \
                .eq("company_id", company_id) \
                .eq("date", data_formatada.isoformat()) \
                .execute()

            for linha in horarios.data:
                if hora_bruta in linha["horas_disponiveis"].get("disponiveis", []):
                    supabase.table("agendamentos").update({
                        "nova_data_confirmacao": f"{data_formatada} {hora_bruta}"
                    }).eq("cod_id", cod_id).execute()

                    resp.message(
                        f"Je peux reprogrammer pour le {data_formatada.strftime('%d/%m')} à {hora_bruta[:5]}. C’est bon pour vous? Répondez avec Y pour confirmer."
                    )
                    return Response(str(resp), content_type="text/xml; charset=utf-8")

            resp.message("Désolé, cet horaire n’est plus disponible. Souhaitez-vous que je vous propose d’autres options ?")
            return Response(str(resp), content_type="text/xml; charset=utf-8")

        except Exception as e:
            print("⚠️ Erreur lors du traitement de la date/heure :", e, file=sys.stderr, flush=True)

    try:
        system_prompt = (
            "Você é Luna, a assistente virtual de agendamentos simpática, eficiente e objetiva.\n"
            "Ajude o cliente a confirmar ou remarcar sua consulta com clareza e naturalidade.\n"
            "Não invente datas. Se o cliente quiser sugestões, liste as próximas datas disponíveis.\n"
            "Se ele sugerir uma data, verifique se há horários disponíveis e ofereça uma confirmação."
        )
        resposta = client.chat.completions.create(
            model="gemma-7b-it",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": msg_body}
            ]
        )
        texto_ia = resposta.choices[0].message.content.strip()
        print("🧠 IA RESPONDEU:", texto_ia, flush=True)
    except Exception as e:
        print("❌ ERRO COM IA:", e, file=sys.stderr, flush=True)
        texto_ia = "Je suis Luna, votre assistante virtuelle. Voici les horaires disponibles :"

    horarios_disponiveis = supabase.table("view_horas_disponiveis") \
        .select("date, horas_disponiveis") \
        .eq("company_id", company_id) \
        .order("date") \
        .limit(3) \
        .execute()

    sugestoes = []
    for item in horarios_disponiveis.data:
        data_formatada = datetime.strptime(item["date"], "%Y-%m-%d").strftime("%d/%m")
        horas = item["horas_disponiveis"].get("disponiveis", [])[:3]
        sugestoes.append(f"📅 {data_formatada}: {', '.join(horas)}")

    texto = f"{texto_ia}\n\nVoici quelques horaires disponibles pour vous :\n\n"
    texto += "\n".join(sugestoes)
    texto += "\n\nSouhaitez-vous que je réserve l’un de ces horaires ? 😊"

    mensagem_final = texto.replace("\n", " • ").strip()[:800]
    print("📦 MENSAGEM ENVIADA AO TWILIO:", mensagem_final, flush=True)

    resp.message(mensagem_final)
    return Response(str(resp), content_type="text/xml; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

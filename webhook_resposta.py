import os
from datetime import date
from openai import OpenAI
from supabase import create_client, Client
from twilio.rest import Client as TwilioClient

# Configs
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
openai = OpenAI(api_key=OPENAI_KEY)

ATENDENTE_VIRTUAL = "Assistant Virtuel de la Clinique"

# Busca agendamentos com data em 3 dias e sms_3dias = false
response = supabase.table("agendamentos") \
    .select("*") \
    .eq("sms_3dias", False) \
    .execute()

hoje = date.today()
data_alvo = hoje.toordinal() + 3

for agendamento in response.data:
    agendamento_data = date.fromisoformat(agendamento["date"]).toordinal()

    if agendamento_data == data_alvo:
        nome_cliente = agendamento.get("user_name", "client")
        telefone = agendamento["user_phone"]
        data = agendamento["date"]
        hora = agendamento["horas"]
        nome_atendente = agendamento.get("nome_atendente", "")
        company_name = agendamento.get("company_name", "notre entreprise")

        # Gera mensagem em francês com aviso multilíngue
        prompt = f"""
Tu es un assistant virtuel nommé {ATENDENTE_VIRTUAL}, travaillant pour {company_name}.

Ton client s'appelle {nome_cliente} et a un rendez-vous prévu le {data} à {hora} avec {nome_atendente}.
Rédige un message courtois en français rappelant le rendez-vous et demandant une confirmation.

À la fin, ajoute une phrase indiquant que le client peut répondre dans n'importe quelle langue, car tu parles 2335 langues.
Le message doit contenir 3 à 4 lignes maximum.
"""

        completion = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )

        mensagem_ia = completion.choices[0].message.content.strip()

        print(f"✅ IA gerou a mensagem para {nome_cliente}: {mensagem_ia}")

        # Envia SMS
        twilio_client.messages.create(
            body=mensagem_ia,
            from_=TWILIO_PHONE,
            to=telefone
        )

        # Atualiza agendamento
        supabase.table("agendamentos").update({
            "sms_3dias": True,
            "user_phone": telefone
        }).eq("cod_id", agendamento["cod_id"]).execute()

        print(f"📤 SMS enviado para {telefone}")


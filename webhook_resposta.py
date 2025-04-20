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

# Nome padrão do atendente virtual
ATENDENTE_VIRTUAL = "Assistente da Equipe"

# Busca agendamentos com data em 3 dias
response = supabase.table("agendamentos") \
    .select("*") \
    .eq("sms_3dias", False) \
    .execute()

hoje = date.today()
data_alvo = hoje.toordinal() + 3

for agendamento in response.data:
    agendamento_data = date.fromisoformat(agendamento["date"]).toordinal()

    if agendamento_data == data_alvo:
        nome_cliente = agendamento.get("user_name", "cliente")
        telefone = agendamento["user_phone"]
        data = agendamento["date"]
        hora = agendamento["horas"]
        nome_atendente = agendamento.get("nome_atendente", "")
        company_name = agendamento.get("company_name", "nossa empresa")

        # Gera mensagem com IA multilíngue
        prompt = f"""
Você é um atendente virtual chamado {ATENDENTE_VIRTUAL}, da empresa {company_name}.

Seu cliente se chama {nome_cliente}, e tem uma consulta agendada para o dia {data} às {hora}, com {nome_atendente}.
Gere uma mensagem educada e simpática lembrando da consulta e pedindo confirmação.

Importante:
- A mensagem deve ter no máximo 3 linhas
- Escreva no idioma do cliente (baseado no nome se conseguir)
- Use tom amigável
- Peça para o cliente responder SIM para confirmar ou NÃO para cancelar (sem ser robótico)
"""

        completion = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )

        mensagem_ia = completion.choices[0].message.content.strip()

        print(f"✅ IA gerou a mensagem para {nome_cliente}: {mensagem_ia}")

        # Envia SMS com mensagem da IA
        twilio_client.messages.create(
            body=mensagem_ia,
            from_=TWILIO_PHONE,
            to=telefone
        )

        # Atualiza sms_3dias = true
        supabase.table("agendamentos").update({
            "sms_3dias": True,
            "user_phone": telefone
        }).eq("cod_id", agendamento["cod_id"]).execute()


        print(f"📤 SMS enviado para {telefone}")


from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
import os
from datetime import datetime

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)

# Consulta agendamentos em até 3 dias
hoje = datetime.utcnow().date()
fim = hoje + timedelta(days=3)

response = supabase.table("agendamentos") \
    .select("*") \
    .eq("status", "Agendado") \
    .eq("sms_3dias", False) \
    .gte("date", str(hoje)) \
    .lte("date", str(fim)) \
    .execute()

for agendamento in response.data:
    cod_id = agendamento["cod_id"]
    telefone = agendamento["user_phone"]
    nome_cliente = agendamento.get("name_user") or "Client"
    nome_atendente = agendamento.get("nome_atendente") or "notre assistant"
    empresa = agendamento.get("company_name") or "notre clinique"
    data = datetime.strptime(agendamento["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora = agendamento["horas"][:5]

    mensagem = (
        f"Bonjour {nome_cliente}, votre rendez-vous avec {nome_atendente} - {empresa} "
        f"est prévu pour le {data} à {hora}.\n"
        "Répondez avec Y pour confirmer ✅, N pour annuler ❌ ou R pour reprogrammer 🔁."
    )

    try:
        twilio_client.messages.create(
            body=mensagem,
            from_=TWILIO_PHONE,
            to=telefone
        )

        print(f"✅ SMS envoyé à {nome_cliente} - {telefone}")

        supabase.table("agendamentos").update({
            "sms_3dias": True
        }).eq("cod_id", cod_id).execute()

    except Exception as e:
        print(f"❌ Erreur d'envoi vers {telefone}: {e}")

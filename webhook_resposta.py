from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from datetime import datetime, timedelta
import os

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)

hoje = datetime.utcnow().date()
fim = hoje + timedelta(days=3)

agendamentos = supabase.table("agendamentos") \
    .select("cod_id, name_user, user_phone, date, horas, nome_atendente, company_name") \
    .eq("sms_3dias", False) \
    .eq("status", "Agendado") \
    .gte("date", hoje.isoformat()) \
    .lte("date", fim.isoformat()) \
    .execute()

for ag in agendamentos.data:
    nome = ag.get("name_user") or "Client"
    telefone = ag["user_phone"]
    cod_id = ag["cod_id"]
    data = datetime.strptime(ag["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora = ag["horas"][:5]
    nome_atendente = ag.get("nome_atendente") or "notre spécialiste"
    empresa = ag.get("company_name") or "notre clinique"

    mensagem = (
        f"Bonjour {nome}, votre rendez-vous avec {nome_atendente} - {empresa} est prévu pour le {data} à {hora}. "
        "Répondez par Y pour confirmer, N pour annuler ou R pour reprogrammer."
    )

    mensagem = mensagem.replace("\n", " ").strip()
    mensagem = mensagem[:800]

    try:
        twilio_client.messages.create(
            body=mensagem,
            from_=TWILIO_PHONE,
            to=telefone
        )
        print(f"✅ SMS envoyé à {nome} ({telefone})")

        supabase.table("agendamentos").update({
            "sms_3dias": True,
            "user_phone": telefone
        }).eq("cod_id", cod_id).execute()

    except Exception as e:
        print(f"❌ Erreur lors de l'envoi à {telefone}: {e}")


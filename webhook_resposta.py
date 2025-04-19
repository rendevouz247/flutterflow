from twilio.rest import Client
from supabase import create_client, Client as SupabaseClient
import os

# CONFIGS
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

# INICIALIZA CLIENTES
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = Client(TWILIO_SID, TWILIO_AUTH)

# BUSCA DADOS DA VIEW
response = supabase.table("view_sms_3_dias").select("*").execute()

for agendamento in response.data:
    cod_id = agendamento['cod_id']
    nome = agendamento['user_name']
    telefone = agendamento['user_phone']
    data_consulta = agendamento['date']
    horas = agendamento['horas']
    
    mensagem = (
        f"Olá {nome}, sua consulta e dia {data_consulta} às {horas}.\n"
        "Responda com 'Yes' para confirmar ou 'No' para cancelar."
    )

    try:
        # Envia o SMS
        message = twilio_client.messages.create(
            body=mensagem,
            from_=TWILIO_PHONE,
            to=telefone
        )

        print(f"✅ SMS enviado para {nome} - {telefone}")

        # Atualiza o campo sms_3dias = true na tabela agendamentos
        supabase.table("agendamentos").update({
            "sms_3dias": True,
            "user_phone": telefone
        }).eq("cod_id", cod_id).execute()

    except Exception as e:
        print(f"❌ Erro ao enviar para {telefone}: {e}")

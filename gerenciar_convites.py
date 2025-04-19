from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from datetime import datetime, timedelta
import os

# CONFIGS
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio = TwilioClient(TWILIO_SID, TWILIO_AUTH)

# Hora atual UTC
agora = datetime.utcnow()
limite_tempo = agora - timedelta(hours=2)

print("⏳ Verificando convites expirados...")

# Buscar convites ativos que passaram de 2h
convites_expirados = supabase.table("agendamentos") \
    .select("*") \
    .eq("convite_ativo", True) \
    .lt("tentativa_convite_em", limite_tempo.isoformat()) \
    .execute()

for convite in convites_expirados.data:
    cod_id = convite["cod_id"]
    company_id = convite["company_id"]

    print(f"⏰ Convite expirado para {convite['user_phone']} - cod_id {cod_id}")

    # Desativa o convite expirado
    supabase.table("agendamentos").update({
        "convite_ativo": False
    }).eq("cod_id", cod_id).execute()

    # Buscar o próximo da fila para essa empresa
    fila = supabase.table("agendamentos") \
        .select("*") \
        .eq("company_id", company_id) \
        .eq("lista_espera", True) \
        .eq("status", "Agendado") \
        .eq("convite_ativo", False) \
        .order("date") \
        .limit(1) \
        .execute()

    if not fila.data:
        print("❌ Nenhum cliente na fila de espera para essa empresa.")
        continue

    proximo = fila.data[0]
    user_id = proximo["user_id"]

    # Buscar nome e telefone na tab_user
    user_info = supabase.table("tab_user") \
        .select("name, phone") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if not user_info.data:
        print(f"⚠️ Não foi possível encontrar dados do usuário {user_id}.")
        continue

    nome = user_info.data[0]["name"]
    telefone = user_info.data[0]["phone"]
    data_consulta = proximo["date"]
    horas = proximo["horas"]

    mensagem = (
        f"Olá {nome}, surgiu uma vaga para antecipar sua consulta marcada para {data_consulta} às {horas}. "
        "Deseja antecipar? Responda com 'Yes' para aceitar ou 'No' para manter seu horário atual."
    )

    try:
        twilio.messages.create(
            body=mensagem,
            from_=TWILIO_PHONE,
            to=telefone
        )
        print(f"📲 Novo convite enviado para {nome} - {telefone}")

        # Atualizar novo agendamento com status de convite
        supabase.table("agendamentos").update({
            "convite_ativo": True,
            "tentativa_convite_em": agora.isoformat(),
            "user_phone": telefone
        }).eq("cod_id", proximo["cod_id"]).execute()

    except Exception as e:
        print(f"❌ Erro ao enviar SMS para {telefone}: {e}")
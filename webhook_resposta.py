from supabase import create_client, Client as SupabaseClient
from datetime import datetime, timedelta
import os

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)

hoje = datetime.utcnow().date()
fim = hoje + timedelta(days=3)

agendamentos = supabase.table("agendamentos") \
    .select("cod_id, name_user, user_id, date, horas, nome_atendente, company_name") \
    .eq("sms_3dias", False) \
    .eq("status", "Agendado") \
    .gte("date", hoje.isoformat()) \
    .lte("date", fim.isoformat()) \
    .execute()

for ag in agendamentos.data:
    nome = ag.get("name_user") or "Client"
    user_id = ag["user_id"]
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
        # 💬 Insere a mensagem diretamente no chat do FlutterFlow
        supabase.table("mensagens_chat").insert({
            "user_id": user_id,
            "mensagem": mensagem,
            "agendamento_id": cod_id,
            "data_envio": datetime.utcnow().isoformat()
        }).execute()

        # ✅ Marca que o lembrete foi enviado
        supabase.table("agendamentos").update({
            "sms_3dias": True
        }).eq("cod_id", cod_id).execute()

        print(f"✅ Mensagem enviada no chat do usuário {nome} ({user_id})")

    except Exception as e:
        print(f"❌ Erro ao enviar mensagem para o chat: {e}")



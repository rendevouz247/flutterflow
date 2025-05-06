from supabase import create_client, Client as SupabaseClient
from datetime import datetime, timedelta, timezone, time
import os, logging

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
logging.basicConfig(level=logging.INFO)

def formata_mensagem(nome, atd, empresa, data, hora):
    texto = (
        f"Bonjour {nome}, votre rendez-vous avec {atd} - {empresa} "
        f"est prévu pour le {data} à {hora}. "
        "Répondez par Y pour confirmer, N pour annuler ou R pour reprogrammer."
    )
    return texto.replace("\n", " ").strip()[:800]

def envia_lembretes():
    hoje = datetime.now(timezone.utc).date()
    fim = hoje + timedelta(days=3)

    # 1) Lista de usuários com chat ativo pendente
    pendentes = supabase.table("agendamentos") \
        .select("user_id") \
        .eq("chat_ativo", True) \
        .eq("status", "Agendado") \
        .execute()
    usuarios_com_chat = {r["user_id"] for r in (pendentes.data or [])}

    # 2) Busca agendamentos de 3 dias ainda não enviados
    resp = supabase.table("agendamentos") \
        .select("cod_id, name_user, user_id, date, horas, nome_atendente, company_name") \
        .eq("sms_3dias", False) \
        .eq("status", "Agendado") \
        .gte("date", hoje.isoformat()) \
        .lte("date", fim.isoformat()) \
        .execute()

    # 3) Agrupa por user_id
    by_user: dict[str, list[dict]] = {}
    for ag in resp.data or []:
        uid = ag["user_id"]
        # só guarda quem NÃO está com chat ativo pendente
        if uid in usuarios_com_chat:
            continue
        by_user.setdefault(uid, []).append(ag)

    # 4) Para cada usuário, envia só o próximo agendamento
    for user_id, ag_list in by_user.items():
        # Ordena pelo mais próximo (data + hora)
        ag_list.sort(key=lambda ag: datetime.combine(
            datetime.fromisoformat(ag["date"]),
            time(*map(int, ag["horas"][:5].split(":"))),
            tzinfo=timezone.utc
        ))
        ag = ag_list[0]

        try:
            # Dados
            nome = ag.get("name_user") or "Client"
            atd = ag.get("nome_atendente") or "notre spécialiste"
            empresa = ag.get("company_name") or "notre clinique"
            data_str = datetime.fromisoformat(ag["date"]).strftime("%d/%m/%Y")
            hora = ag["horas"][:5]
            cod_id = ag["cod_id"]

            # Monta e insere mensagem
            msg = formata_mensagem(nome, atd, empresa, data_str, hora)

            # 2) Insere no chat
            supabase.table("mensagens_chat").insert({
                "user_id": user_id,
                "mensagem": msg,
                "tipo": "IA",
                "agendamento_id": cod_id,
                "data_envio": datetime.now(timezone.utc).isoformat()
            }).execute()

            # 3) Marca o agendamento imediatamente
            supabase.table("agendamentos").update({
                "sms_3dias": True,
                "chat_ativo": True
            }).eq("cod_id", cod_id).execute()

            # 4) Insere no histórico, mas sem quebrar se falhar
            try:
                supabase.table("mensagens_chat_historico").insert({
                    "user_id": user_id,
                    "mensagem": msg,
                    "tipo": "IA",
                    "agendamento_id": cod_id,
                    "data_envio": datetime.now(timezone.utc).isoformat()
                }).execute()
            except Exception as hist_err:
                logging.warning(f"⚠️ Falha ao inserir histórico para ag. {cod_id}: {hist_err}")
                
            # 5) Confirma que tudo deu certo
            logging.info(f"✅ Lembrete (ag. {cod_id}) enviado para user {user_id}")
        
        except Exception as e:
            logging.error(f"❌ Erro no agendamento {cod_id} user {user_id}: {e}")

if __name__ == "__main__":
    envia_lembretes()


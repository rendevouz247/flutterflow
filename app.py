from flask import Flask, request
import os, logging, re
from datetime import datetime, timedelta
import dateparser
from dateparser.search import search_dates
from supabase import create_client
from groq import Groq

# ==== CONFIG ====
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

app.logger.info("🏁 IA rodando e aguardando requisições...")

# ==== FUNÇÕES UTILITÁRIAS ====

def normalizar_texto(texto):
    """Corrige expressões humanas como 'depois de amanhã', 'próxima sexta', etc."""
    hoje = datetime.now().date()
    substituicoes = {
        "depois de amanhã": (hoje + timedelta(days=2)).isoformat(),
        "amanhã": (hoje + timedelta(days=1)).isoformat(),
        "hoje": hoje.isoformat(),
        "semana que vem": (hoje + timedelta(days=7)).isoformat(),
        "daqui a três dias": (hoje + timedelta(days=3)).isoformat(),
        "daqui a tres dias": (hoje + timedelta(days=3)).isoformat(),  # erro comum (sem acento)
        "daqui a dois dias": (hoje + timedelta(days=2)).isoformat(),
        "próxima sexta": (hoje + timedelta((4 - hoje.weekday() + 7) % 7)).isoformat(),  # sexta-feira
        "sexta que vem": (hoje + timedelta((4 - hoje.weekday() + 7) % 7)).isoformat(),
        "próximo sábado": (hoje + timedelta((5 - hoje.weekday() + 7) % 7)).isoformat(),
        "sábado que vem": (hoje + timedelta((5 - hoje.weekday() + 7) % 7)).isoformat(),
    }
    for chave, valor in substituicoes.items():
        texto = re.sub(rf'\b{chave}\b', valor, texto, flags=re.IGNORECASE)
    return texto

def extrair_data_hora(texto):
    try:
        app.logger.info(f"🔍 Tentando extrair de: {texto}")

        texto = normalizar_texto(texto)
        texto = re.sub(r"\bdia\s+", "", texto, flags=re.IGNORECASE).strip()
        texto = re.sub(r"\s+as\s+", " às ", texto, flags=re.IGNORECASE)
        texto = re.sub(r"\s+à\s+", " às ", texto, flags=re.IGNORECASE)

        resultado = search_dates(
            texto,
            languages=["pt", "en", "fr"],
            settings={"PREFER_DATES_FROM": "future"}
        )

        if not resultado:
            app.logger.warning("⚠️ Nenhuma data encontrada.")
            return None, None

        data_detectada = resultado[0][1].date().isoformat()
        app.logger.info(f"📆 Data identificada: {data_detectada}")

        # ⚡ Nova regex melhorada para hora
        hora_match = re.search(r"\b(\d{1,2})(?:[:hH](\d{2}))?\b", texto)
        if hora_match:
            hora = hora_match.group(1).zfill(2)
            minuto = hora_match.group(2) if hora_match.group(2) else "00"
            hora_formatada = f"{hora}:{minuto}:01"
            app.logger.info(f"⏰ Hora identificada: {hora_formatada}")
            return data_detectada, hora_formatada

        app.logger.warning("⚠️ Nenhuma hora encontrada.")
        return data_detectada, None

    except Exception as e:
        app.logger.error(f"❌ Erro em extrair_data_hora: {e}")
        return None, None


def gravar_mensagem_chat(user_id, mensagem, agendamento_id, tipo="IA"):
    """Grava uma mensagem no chat."""
    try:
        supabase.table("mensagens_chat").insert({
            "user_id": user_id,
            "mensagem": mensagem,
            "agendamento_id": agendamento_id,
            "data_envio": datetime.utcnow().isoformat(),
            "tipo": tipo
        }).execute()
        app.logger.info(f"💬 Mensagem gravada no chat: {mensagem}")
    except Exception as e:
        app.logger.error(f"❌ Erro ao gravar mensagem no chat: {e}")

def buscar_agendamento(cod_id):
    """Busca informações básicas do agendamento."""
    try:
        dados = supabase.table("agendamentos") \
            .select("nova_data, nova_hora, company_id, atend_id") \
            .eq("cod_id", int(cod_id)) \
            .single().execute().data
        return dados
    except Exception as e:
        app.logger.error(f"❌ Erro ao buscar agendamento: {e}")
        return None

def consultar_disponibilidade(company_id, atend_id, nova_data):
    """Consulta horários disponíveis para uma data."""
    try:
        resultado = supabase.table("view_horas_disponiveis") \
            .select("horas_disponiveis") \
            .eq("company_id", company_id) \
            .eq("atend_id", atend_id) \
            .eq("date", nova_data) \
            .maybe_single().execute()

        if not resultado or not getattr(resultado, 'data', None):
            app.logger.warning(f"⚠️ Nenhuma disponibilidade encontrada para {nova_data}.")
            return {}

        return resultado.data or {}

    except Exception as e:
        app.logger.error(f"❌ Erro ao consultar disponibilidade: {e}")
        return {}


def gerar_resposta_ia(mensagens):
    """Gera uma resposta da IA usando Groq."""
    try:
        resposta = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=mensagens,
            temperature=0.7,
            max_tokens=400
        )
        return resposta.choices[0].message.content.strip()
    except Exception as e:
        app.logger.error(f"❌ Erro no modelo Groq: {e}")
        return "Tive um probleminha. Pode tentar novamente?"

# ==== ROTA PRINCIPAL ====

@app.route("/ia", methods=["POST"])
def handle_ia():
    data = request.get_json()
    user_id = data.get("user_id")
    mensagem = data.get("mensagem", "").strip().lower()
    agendamento_id = data.get("agendamento_id")

    app.logger.info(f"📩 Requisição recebida: {data}")

    if not user_id or not mensagem or not agendamento_id:
        return {"erro": "Dados incompletos"}, 400

    resposta = ""
    try:
        if mensagem in ["y", "yes", "sim", "oui"]:
            dados = buscar_agendamento(agendamento_id)
            if dados and dados.get("nova_data") and dados.get("nova_hora"):
                supabase.table("agendamentos").update({
                    "date": dados["nova_data"],
                    "horas": dados["nova_hora"],
                    "status": "Reagendado",
                    "reagendando": False,
                    "chat_ativo": False
                }).eq("cod_id", int(agendamento_id)).execute()
                resposta = f"✅ Perfeito! Sua consulta foi remarcada para {dados['nova_data']} às {dados['nova_hora']}. Te esperamos lá! 😄"
            else:
                resposta = "Hmm... não encontrei uma sugestão de horário. Pode me dizer novamente qual dia e hora você quer?"

        elif mensagem in ["n", "não", "no", "non"]:
            supabase.table("agendamentos").update({
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", int(agendamento_id)).execute()
            resposta = "Tranquilo! Qual outro dia e horário funcionam melhor pra você? 😉"

        elif mensagem == "r":
            supabase.table("agendamentos").update({
                "reagendando": True,
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", int(agendamento_id)).execute()
            resposta = "Claro! Qual dia é melhor pra você? Pode dizer: 'amanhã', 'segunda às 14h', ou algo assim."

        else:
            nova_data, nova_hora = extrair_data_hora(mensagem)
            dados = buscar_agendamento(agendamento_id)

            if nova_data and nova_hora and dados:
                disponibilidade = consultar_disponibilidade(dados["company_id"], dados["atend_id"], nova_data)
                disponiveis = disponibilidade.get("horas_disponiveis", {}).get("disponiveis", [])

                if not disponiveis:
                    resposta = (
                        f"⚠️ Infelizmente não há horários disponíveis para o dia {nova_data}.\n"
                        f"Por favor, envie outra data e horário para que eu possa verificar."
                    )
                else:
                    match_hora = next((h for h in disponiveis if nova_hora[:5] in h or h.startswith(nova_hora[:5])), None)
                    if match_hora:
                        supabase.table("agendamentos").update({
                            "nova_data": nova_data,
                            "nova_hora": match_hora
                        }).eq("cod_id", int(agendamento_id)).execute()
                        resposta = f"🔐 Posso confirmar sua remarcação para o dia {nova_data} às {match_hora}? Responda com *sim* ou *não*."
                    else:
                        sugestoes = disponiveis[:3]
                        sugestoes_texto = "\n".join([f"🔹 {h}" for h in sugestoes]) or "Nenhum horário disponível."
                        resposta = (
                            f"😕 O horário {nova_hora} no dia {nova_data} não está disponível.\n"
                            f"Aqui estão outras opções:\n{sugestoes_texto}"
                        )
            else:
                historico = supabase.table("mensagens_chat") \
                    .select("mensagem, tipo") \
                    .eq("agendamento_id", int(agendamento_id)) \
                    .order("data_envio", desc=False) \
                    .limit(10).execute().data

                mensagens_formatadas = [
                    {"role": "assistant" if m["tipo"] == "IA" else "user", "content": m["mensagem"]}
                    for m in historico
                ]
                mensagens_formatadas.append({"role": "user", "content": mensagem})
                mensagens_formatadas.insert(0, {
                    "role": "system",
                    "content": (
                        "Você é uma atendente virtual simpática. Nunca confirme horários sem o cliente dizer 'sim'. "
                        "Se o cliente disser um dia e hora, pergunte: 'Posso confirmar a remarcação para tal dia às tal hora?'"
                    )
                })
                resposta = gerar_resposta_ia(mensagens_formatadas)

        gravar_mensagem_chat(
            user_id="ia",
            mensagem=resposta,
            agendamento_id=agendamento_id,
            tipo="IA"
        )

        app.logger.info(f"💬 Resposta da IA: {resposta}")
        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.error(f"❌ Erro: {e}")
        return {"erro": "Erro interno ao processar"}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

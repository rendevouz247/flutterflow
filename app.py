from flask import Flask, request
import os, logging, re
from datetime import datetime, timedelta
import dateparser
from dateparser.search import search_dates
from supabase import create_client
from groq import Groq
from dateutil import tz

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
    texto = texto.lower()
    timezone = tz.gettz('America/Toronto')
    agora = datetime.now(tz=timezone)

    if "depois de amanhã" in texto:
        dois_dias = agora + timedelta(days=2)
        data_formatada = dois_dias.strftime("%d/%m/%Y")
        texto = texto.replace("depois de amanhã", data_formatada)

    if "amanhã" in texto and "depois de amanhã" not in texto:
        amanha = agora + timedelta(days=1)
        data_formatada = amanha.strftime("%d/%m/%Y")
        texto = texto.replace("amanhã", data_formatada)

    if "hoje" in texto:
        hoje = agora
        data_formatada = hoje.strftime("%d/%m/%Y")
        texto = texto.replace("hoje", data_formatada)

    # 🛠️ CORREÇÃO NOVA: adicionar o ano 2025 se cliente mandar "29/05" sem ano
    texto = re.sub(
        r"\b(\d{2})/(\d{2})\b(?!/)",  # detecta dd/mm que não tem /ano
        lambda m: f"{m.group(1)}/{m.group(2)}/2025",
        texto
    )

    return texto

def extrair_data_hora(texto):
    print(f"🔍 Buscando data e hora em: {texto}")

    data_encontrada = None
    hora_encontrada = None

    # Primeiro, buscamos padrões explícitos de hora no texto
    match_hora = re.search(r"\b(\d{1,2}):(\d{2})\b", texto)
    if match_hora:
        hora_texto = match_hora.group()
        try:
            hora_encontrada = datetime.strptime(hora_texto, "%H:%M").time()
            print(f"⏰ Hora extraída diretamente: {hora_encontrada}")
        except ValueError:
            print("❌ Formato de hora inválido encontrado.")

    # Agora buscamos a data usando o search_dates
    resultados = search_dates(texto, settings={
        'PREFER_DATES_FROM': 'future',
        'RELATIVE_BASE': datetime.now(),
        'TIMEZONE': 'America/Toronto',
        'RETURN_AS_TIMEZONE_AWARE': False
    })

    if resultados:
        for resultado in resultados:
            texto_detectado, data_detectada = resultado
            # Se o texto detectado for um horário isolado que já pegamos, ignorar
            if re.fullmatch(r"\d{1,2}:\d{2}", texto_detectado):
                continue
            data_encontrada = data_detectada.date()
            print(f"📅 Data extraída: {data_encontrada}")
            break

    if not data_encontrada:
        print("⚠️ Nenhuma data encontrada.")
    if not hora_encontrada:
        print("⚠️ Nenhuma hora encontrada.")

    return data_encontrada, hora_encontrada

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
                if isinstance(nova_data, datetime.date):
                    nova_data = nova_data.isoformat()
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
            if isinstance(nova_data, datetime.date):
                nova_data = nova_data.isoformat()
            supabase.table("agendamentos").update({
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", int(agendamento_id)).execute()
            resposta = "Tranquilo! Qual outro dia e horário funcionam melhor pra você? 😉"

        elif mensagem == "r":
            if isinstance(nova_data, datetime.date):
                nova_data = nova_data.isoformat()
            supabase.table("agendamentos").update({
                "reagendando": True,
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", int(agendamento_id)).execute()
            resposta = "Claro! Qual dia é melhor pra você? Pode dizer: 'amanhã', 'segunda às 14h', ou algo assim."

        else:
            dados = buscar_agendamento(agendamento_id)

            # Cliente mandou só hora? (ex: 09:00)
            if re.fullmatch(r"\d{1,2}[:hH]\d{2}", mensagem) and dados and dados.get("nova_data"):
                nova_data = dados["nova_data"][:10]
                hora_match = re.search(r"(\d{1,2})[:hH](\d{2})", mensagem)
                if hora_match:
                    hora = hora_match.group(1).zfill(2)
                    minuto = hora_match.group(2).zfill(2)
                    nova_hora = f"{hora}:{minuto}:01"
                app.logger.info(f"♻️ Cliente mandou só hora, usando nova_data {nova_data} e nova_hora {nova_hora}")

            else:
                nova_data, nova_hora = extrair_data_hora(mensagem)

                if not nova_data and dados and dados.get("nova_data"):
                    nova_data = dados["nova_data"][:10]
                    app.logger.info(f"♻️ Usando nova_data gravada anteriormente: {nova_data}")

            if nova_data and nova_hora:
                disponibilidade = consultar_disponibilidade(dados["company_id"], dados["atend_id"], nova_data)
                disponiveis = disponibilidade.get("horas_disponiveis", {}).get("disponiveis", [])
            
                if not disponiveis:
                    # ✅ Nenhuma disponibilidade: só gravar a nova_data (sem nova_hora)
                    if isinstance(nova_data, datetime.date):
                        nova_data = nova_data.isoformat()
                    supabase.table("agendamentos").update({
                        "nova_data": nova_data,
                        "nova_hora": None
                    }).eq("cod_id", int(agendamento_id)).execute()
                    app.logger.info(f"♻️ Gravado nova_data {nova_data} (sem hora) no agendamento.")
            
                    resposta = (
                        f"⚠️ Infelizmente não há horários disponíveis para o dia {nova_data}.\n"
                        f"Por favor, envie outra data e horário para que eu possa verificar."
                    )
            
                else:
                    # Verifica se a hora desejada existe nos horários disponíveis
                    match_hora = next((h for h in disponiveis if nova_hora[:5] in h or h.startswith(nova_hora[:5])), None)
            
                    if match_hora:
                        # ✅ Hora disponível: grava a nova_data e nova_hora
                        if isinstance(nova_data, datetime.date):
                            nova_data = nova_data.isoformat()
                        supabase.table("agendamentos").update({
                            "nova_data": nova_data,
                            "nova_hora": match_hora
                        }).eq("cod_id", int(agendamento_id)).execute()
                        app.logger.info(f"📝 Gravado nova_data {nova_data} e nova_hora {match_hora} no agendamento.")
            
                        resposta = f"🔐 Posso confirmar sua remarcação para o dia {nova_data} às {match_hora}? Responda com *sim* ou *não*."
            
                    else:
                        # ⚠️ Hora desejada não disponível: gravar nova_data mas sem hora
                        if isinstance(nova_data, datetime.date):
                            nova_data = nova_data.isoformat()
                        supabase.table("agendamentos").update({
                            "nova_data": nova_data,
                            "nova_hora": None
                        }).eq("cod_id", int(agendamento_id)).execute()
                        app.logger.info(f"♻️ Gravado nova_data {nova_data} (sem hora após horário indisponível) no agendamento.")
            
                        sugestoes = disponiveis[:3]
                        sugestoes_texto = "\n".join([f"🔹 {h}" for h in sugestoes]) or "Nenhum horário disponível."
                        resposta = (
                            f"😕 O horário {nova_hora[:5]} no dia {nova_data} não está disponível.\n"
                            f"Aqui estão outras opções:\n{sugestoes_texto}"
                        )


            elif nova_data:
                # Atualiza nova_data mesmo sem hora
                if isinstance(nova_data, datetime.date):
                    nova_data = nova_data.isoformat()
                supabase.table("agendamentos").update({
                    "nova_data": nova_data,
                    "nova_hora": None
                }).eq("cod_id", int(agendamento_id)).execute()
                app.logger.info(f"♻️ Gravado nova_data {nova_data} (sem hora) no agendamento.")

                resposta = f"Posso confirmar a remarcação para o dia {nova_data}? Se sim, por favor, informe também o horário. 😉"

            else:
                # Nenhuma data entendida
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

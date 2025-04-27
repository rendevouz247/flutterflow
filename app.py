from flask import Flask, request
import os, logging, re, random
from datetime import datetime, timedelta, date
from dateparser.search import search_dates
from supabase import create_client
from groq import Groq
from dateutil import tz

# ==== CONFIGURAÇÃO ====  
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.logger.info("🏁 IA rodando e aguardando requisições...")

# ==== CONSTS PRECOMPILADAS ====  
RE_HORA = re.compile(r"\b(\d{1,2}):(\d{2})\b")
RE_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})(/(\d{2,4}))?\b")
MESES_PT = [None, "janeiro", "fevereiro", "março", "abril", "maio", "junho",
           "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

CONFIRM_TEMPLATES = [
    "Perfeito! Sua consulta foi remarcada para {date} às {time}. 🙂",
    "Ótimo, confirmei o dia {date} às {time}. Nos vemos lá!",
    "Beleza! Agendado para {date} às {time}. Até breve!"
]
NO_SLOTS_TEMPLATES = [
    "⚠️ Infelizmente não há horários disponíveis para o dia {date}.",
    "Poxa, não encontrei vagas em {date}."
]
ASK_TIME_TEMPLATES = [
    "Posso confirmar sua remarcação para {date}? Se sim, informe também o horário. 😉",
]

# ==== FUNÇÕES AUXILIARES ====  

def fmt_data(dt: date) -> str:
    """Formata data para '29 de maio'"""
    return f"{dt.day} de {MESES_PT[dt.month]}"

def extrair_data_hora(texto: str):
    """
    Extrai data e hora do texto:
    1) Tenta hora explícita via regex RE_HORA.
    2) Tenta data via dateparser (DMY).
    3) Fallback manual para padrões dd/mm ou dd/mm/aaaa.
    """
    timezone = tz.gettz('America/Toronto')
    agora = datetime.now(tz=timezone)

    # 1) Extrair hora explícita
    match_hora = RE_HORA.search(texto)
    hora_encontrada = None
    if match_hora:
        try:
            hora_encontrada = datetime.strptime(match_hora.group(), "%H:%M").time()
        except ValueError:
            app.logger.info(f"❌ Formato de hora inválido: {match_hora.group()}")

    # 2) Tentar extrair data com dateparser (DMY)
    settings = {
        'PREFER_DATES_FROM': 'future',
        'RELATIVE_BASE': agora,
        'TIMEZONE': 'America/Toronto',
        'RETURN_AS_TIMEZONE_AWARE': False,
        'DATE_ORDER': 'DMY'
    }
    resultados = search_dates(texto, languages=['pt'], settings=settings) or []
    data_encontrada = None
    for txt, dt in resultados:
        if not RE_HORA.fullmatch(txt):
            data_encontrada = dt.date()
            break

    # 3) Fallback manual para dd/mm (com ou sem ano)
    if not data_encontrada:
        m = RE_DATA.search(texto)
        if m:
            day_str, month_str, _, year_str = m.groups()
            day = int(day_str)
            month = int(month_str)
            # se não veio ano, assume o ano atual ou próximo se já passou
            yr = int(year_str) if year_str else agora.year
            if not year_str:
                tentativa = date(agora.year, month, day)
                if tentativa < agora.date():
                    yr += 1
            try:
                data_encontrada = date(yr, month, day)
            except ValueError:
                app.logger.info(f"❌ Data inválida no fallback: {day}/{month}/{yr}")

    app.logger.info(f"🔎 extrair_data_hora -> data: {data_encontrada}, hora: {hora_encontrada}")
    return data_encontrada, hora_encontrada



def gravar_mensagem_chat(user_id, mensagem, agendamento_id, tipo="IA"):
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
        app.logger.error(f"❌ Erro ao gravar chat: {e}")


def buscar_agendamento(cod_id):
    try:
        res = supabase.table("agendamentos") \
            .select("nova_data, nova_hora, company_id, atend_id") \
            .eq("cod_id", int(cod_id)) \
            .single().execute()
        dados = res.data or {}
        app.logger.info(f"🔍 Dados do agendamento: {dados}")
        return dados
    except Exception as e:
        app.logger.error(f"❌ Erro ao buscar agendamento: {e}")
        return {}


def consultar_disponibilidade(company_id, atend_id, nova_data):
    try:
        app.logger.info(f"🔍 consultando disponibilidade para company_id={company_id}, atend_id={atend_id}, date={nova_data}")
        res = supabase.table("view_horas_disponiveis") \
            .select("horas_disponiveis") \
            .eq("company_id", company_id) \
            .eq("atend_id", atend_id) \
            .eq("date", nova_data) \
            .maybe_single().execute()
        dispo = res.data or {}
        slots = dispo.get("horas_disponiveis", {}).get("disponiveis", [])
        app.logger.info(f"✅ disponibilidade retornada: {slots}")
        return dispo
    except Exception as e:
        app.logger.error(f"❌ Erro na disponibilidade: {e}")
        return {}


def gerar_resposta_ia(mensagens):
    try:
        app.logger.info(f"💭 Prompt IA:\n{mensagens}")
        resp = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=mensagens,
            temperature=0.7,
            max_tokens=400
        )
        resposta = resp.choices[0].message.content.strip()
        app.logger.info(f"💡 Resposta do Groq: {resposta}")
        return resposta
    except Exception as e:
        app.logger.error(f"❌ Erro no Groq: {e}")
        return "Desculpe, ocorreu um problema. Pode tentar novamente?"

# ==== ROTA PRINCIPAL ====  
@app.route("/ia", methods=["POST"])
def handle_ia():
    data = request.get_json() or {}
    user_id = data.get("user_id")
    mensagem = data.get("mensagem", "").strip().lower()
    agendamento_id = data.get("agendamento_id")

    app.logger.info(f"📩 Requisição: {data}")

    if not user_id or not mensagem or not agendamento_id:
        return {"erro": "Dados incompletos"}, 400

    dados = buscar_agendamento(agendamento_id)
    nova_data = None
    nova_hora = None
    resposta = ""

    # 1) Intenção: disponibilidade
    if any(k in mensagem for k in ["disponível", "vagas"]):
        dispo = consultar_disponibilidade(dados.get("company_id"), dados.get("atend_id"), dados.get("nova_data"))
        slots = dispo.get("horas_disponiveis", {}).get("disponiveis", [])[:3]
        if slots:
            resposta = "Tenho vagas nestes horários:\n" + "\n".join(f"– {h}" for h in slots)
        else:
            date_str = fmt_data(date.fromisoformat(dados.get("nova_data"))) if dados.get("nova_data") else "essa data"
            tpl = random.choice(NO_SLOTS_TEMPLATES)
            resposta = tpl.format(date=date_str)

    # 2) Confirmação positiva
    elif mensagem in ["y", "yes", "sim", "oui"]:
        if dados.get("nova_data") and dados.get("nova_hora"):
            d_obj = date.fromisoformat(dados["nova_data"])
            t_str = dados["nova_hora"][0:5]
            resposta = random.choice(CONFIRM_TEMPLATES).format(date=fmt_data(d_obj), time=t_str)
            app.logger.info(f"♻️ Gravando confirmação: date={dados['nova_data']} horas={dados['nova_hora']}")
            supabase.table("agendamentos").update({
                "date": dados["nova_data"],
                "horas": dados["nova_hora"],
                "status": "Reagendado",
                "reagendando": False,
                "chat_ativo": False
            }).eq("cod_id", int(agendamento_id)).execute()
        else:
            resposta = "Hmm... não encontrei sugestão de horário. Pode dizer dia e hora?"

    # 3) Confirmação negativa
    elif mensagem in ["n", "não", "no", "non"]:
        resposta = "Tranquilo! Qual outro dia e horário funcionam melhor pra você? 😉"
        supabase.table("agendamentos").update({"nova_data": None, "nova_hora": None}) \
            .eq("cod_id", int(agendamento_id)).execute()
        app.logger.info(f"♻️ Reset slots no agendamento {agendamento_id}")

    # 4) Iniciar reagendamento
    elif mensagem == "r":
        resposta = "Claro! Qual dia é melhor pra você?"
        supabase.table("agendamentos").update({"reagendando": True, "nova_data": None, "nova_hora": None}) \
            .eq("cod_id", int(agendamento_id)).execute()
        app.logger.info(f"♻️ Iniciando reagendamento no agendamento {agendamento_id}")

    # 5) Cliente forneceu data/hora
    else:
        nova_data, nova_hora = extrair_data_hora(mensagem)
        if nova_data and nova_hora:
            app.logger.info(f"🔍 Cliente enviou data e hora: {nova_data}, {nova_hora}")
            dispo = consultar_disponibilidade(dados.get("company_id"), dados.get("atend_id"), nova_data.isoformat())
            slots = dispo.get("horas_disponiveis", {}).get("disponiveis", [])
            if nova_hora.strftime("%H:%M") in [h[:5] for h in slots]:
                app.logger.info(f"♻️ Gravando nova_data {nova_data} e nova_hora {nova_hora} no agendamento.")
                supabase.table("agendamentos").update({"nova_data": nova_data.isoformat(), "nova_hora": nova_hora.isoformat()}) \
                    .eq("cod_id", int(agendamento_id)).execute()
                resposta = f"🔐 Posso confirmar a remarcação para o dia {fmt_data(nova_data)} às {nova_hora.strftime('%H:%M')}? Responda com sim ou não."
            else:
                app.logger.info(f"⚠️ Hora {nova_hora} não disponível em {slots}")
                tpl = random.choice(NO_SLOTS_TEMPLATES)
                resposta = tpl.format(date=fmt_data(nova_data)) + " Por favor, escolha outro horário."
        elif nova_data:
            app.logger.info(f"♻️ Gravando apenas nova_data {nova_data} (sem hora) no agendamento.")
            supabase.table("agendamentos").update({"nova_data": nova_data.isoformat(), "nova_hora": None}) \
                .eq("cod_id", int(agendamento_id)).execute()
            tpl = ASK_TIME_TEMPLATES[0]
            resposta = tpl.format(date=fmt_data(nova_data))
        else:
            app.logger.info("💬 Fallback IA acionado")
            historico = supabase.table("mensagens_chat") \
                .select("mensagem,tipo") \
                .eq("agendamento_id", int(agendamento_id)) \
                .order("data_envio", desc=False).limit(10).execute().data
            msgs = [
                {"role": "assistant" if m['tipo']=='IA' else 'user', "content": m['mensagem']}
                for m in historico
            ]
            msgs.append({"role": "user", "content": mensagem})
            msgs.insert(0, {"role":"system","content":
                         "Você é uma atendente virtual simpática. Nunca confirme horários sem o cliente for sim."})
            resposta = gerar_resposta_ia(msgs)

    gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
    app.logger.info(f"💬 Resposta final: {resposta}")
    return {"resposta": resposta}, 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


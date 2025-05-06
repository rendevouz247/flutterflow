from flask import Flask, request, jsonify
from flask_cors import CORS
import os, logging, re, random
from datetime import datetime, timedelta, date, time
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
# Permite chamadas CORS ao endpoint /ia
CORS(app, resources={r"/ia": {"origins": "*"}})
app.logger.setLevel(logging.INFO)
app.logger.info("🏁 IA rodando e aguardando requisições...")

@app.route("/ping", methods=["GET"])
def ping():
    print("🏓 PING RECEBIDO")           # sempre aparece no stdout
    app.logger.info("🏓 PING RECEBIDO")  # e nos logs
    return "pong", 200

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

REMINDER_TEMPLATES = [
    "Claro! No dia {date} vou te lembrar de {task}.",
    "Combinado! Em {date}, você receberá um lembrete para {task}.",
    "Perfeito! Lembrarei você em {date} de {task}. 😉",
    "Sem problemas! Te aviso em {date} para não esquecer de {task}."
]

# ==== FUNÇÕES AUXILIARES ====  

def fmt_data(dt: date) -> str:
    """Formata data para '29 de maio'"""
    return f"{dt.day} de {MESES_PT[dt.month]}"

def extrair_data_hora(texto: str):
    """
    Extrai data e hora do texto, tratando:
      1) Expressões relativas: 'hoje', 'amanhã', 'depois de amanhã'
      2) Hora em formatos “HH:MM”, “15h” ou “15hs”
      3) “próxima <weekday>”
      4) Data via dateparser (DATE_ORDER='DMY')
      5) “<dia> de <mês> [de <ano>]”
      6) Fallback numérico “dd/mm” ou “dd/mm/aaaa”
    """
    from datetime import datetime, date, time, timedelta
    import re
    from dateparser.search import search_dates
    from dateutil import tz

    # globais já definidos no módulo:
    # RE_HORA, RE_DATA, MESES_PT

    # base de datas
    timezone = tz.gettz('America/Toronto')
    agora_dt = datetime.now(tz=timezone)
    hoje = agora_dt.date()

    # 1) Expressões relativas
    if re.search(r"\bdepois de amanhã\b", texto, re.IGNORECASE):
        data_encontrada = hoje + timedelta(days=2)
        app.logger.info(f"🗓️ Fallback 'depois de amanhã' -> {data_encontrada}")
    elif re.search(r"\bamanhã\b", texto, re.IGNORECASE):
        data_encontrada = hoje + timedelta(days=1)
        app.logger.info(f"🗓️ Fallback 'amanhã' -> {data_encontrada}")
    elif re.search(r"\bhoje\b", texto, re.IGNORECASE):
        data_encontrada = hoje
        app.logger.info(f"🗓️ Fallback 'hoje' -> {data_encontrada}")
    else:
        data_encontrada = None

    # 2) Extrair hora (HH:MM ou 15h/15hs)
    match_hora = RE_HORA.search(texto)
    hora_encontrada = None
    if match_hora:
        h = int(match_hora.group(1))
        m = int(match_hora.group(2)) if match_hora.group(2) else 0
        hora_encontrada = time(h, m)
        app.logger.info(f"⏰ Hora extraída: {hora_encontrada}")

    # Se já capturamos data relativa, retornamos
    if data_encontrada:
        return data_encontrada, hora_encontrada

    # 3) Próxima <weekday>
    m_w = re.search(
        r"\bpróxima\s+(segunda|terça|quarta|quinta|sexta|sábado|domingo)(?:-feira)?\b",
        texto, re.IGNORECASE
    )
    if m_w:
        WEEKDAY = {"segunda":0,"terça":1,"quarta":2,"quinta":3,"sexta":4,"sábado":5,"domingo":6}
        alvo = WEEKDAY[m_w.group(1).lower()]
        delta = (alvo - hoje.weekday() + 7) % 7 or 7
        data_encontrada = hoje + timedelta(days=delta)
        app.logger.info(f"🗓️ Próxima semana detectada: {data_encontrada}")
        return data_encontrada, hora_encontrada

    # 4) Tentar via dateparser (DMY)
    settings = {
        'PREFER_DATES_FROM': 'future',
        'RELATIVE_BASE': agora_dt,
        'TIMEZONE': 'America/Toronto',
        'RETURN_AS_TIMEZONE_AWARE': False,
        'DATE_ORDER': 'DMY'
    }
    resultados = search_dates(texto, languages=['pt'], settings=settings) or []
    for txt, dt in resultados:
        if not RE_HORA.fullmatch(txt):
            data_encontrada = dt.date()
            app.logger.info(f"📅 dateparser extraído: {data_encontrada}")
            return data_encontrada, hora_encontrada

    # 5) Fallback “<dia> de <mês> [de <ano>]”
    meses_regex = "|".join(MESES_PT[1:])
    m_m = re.search(
        rf"\b(\d{{1,2}})\s+de\s+({meses_regex})(?:\s+de\s+(\d{{4}}))?\b",
        texto, re.IGNORECASE
    )
    if m_m:
        d, mes_nome, ano_str = m_m.groups()
        month = MESES_PT.index(mes_nome.lower())
        year = int(ano_str) if ano_str else hoje.year
        dt_tmp = date(year, month, int(d))
        if not ano_str and dt_tmp < hoje:
            dt_tmp = date(year+1, month, int(d))
        data_encontrada = dt_tmp
        app.logger.info(f"📅 mês-name fallback: {data_encontrada}")
        return data_encontrada, hora_encontrada

    # 6) Fallback numérico “dd/mm[/aaaa]”
    m = RE_DATA.search(texto)
    if m:
        day_str, month_str, _, year_str = m.groups()
        day, month = int(day_str), int(month_str)
        yr = int(year_str) if year_str else hoje.year
        if not year_str:
            tentativa = date(hoje.year, month, day)
            if tentativa < hoje:
                yr += 1
        try:
            data_encontrada = date(yr, month, day)
            app.logger.info(f"📅 numeric fallback: {data_encontrada}")
        except ValueError:
            app.logger.info(f"❌ Data inválida fallback: {day}/{month}/{yr}")

    app.logger.info(f"🔎 extrair_data_hora -> data: {data_encontrada}, hora: {hora_encontrada}")
    return data_encontrada, hora_encontrada


def gravar_mensagem_chat(user_id, mensagem, agendamento_id, tipo="IA"):
    # Define o timezone de Toronto
    timezone = tz.gettz('America/Toronto')
    # Usa a hora local com microssegundos
    agora = datetime.now(tz=timezone).isoformat()
    try:
        supabase.table("mensagens_chat").insert({
            "user_id":        user_id,
            "mensagem":       mensagem,
            "agendamento_id": agendamento_id,
            "data_envio":     agora,
            "tipo":           tipo
        }).execute()
        app.logger.info(f"💬 Mensagem gravada no chat: '{mensagem}' às {agora}")

        supabase.table("mensagens_chat_historico").insert({
            "user_id":        user_id,
            "mensagem":       mensagem,
            "agendamento_id": agendamento_id,
            "data_envio":     agora,
            "tipo":           tipo
        }).execute()
        app.logger.info(f"💬 Mensagem gravada no chat: '{mensagem}' às {agora}")
    
    except Exception as e:
        app.logger.error(f"❌ Erro ao gravar chat: {e}")


def buscar_agendamento(cod_id):
    try:
        res = supabase.table("agendamentos") \
            .select(
                "date, "          # data original
                "horas, "         # hora original
                "nova_data, "     # nova data, se houver
                "nova_hora, "     # nova hora, se houver
                "reagendando, "   # flag de reagendamento em curso
                "status, "        # status atual (Agendado/Confirmado/etc)
                "company_id, "    # empresa do agendamento
                "atend_id, "      # atendente do agendamento
                "chat_ativo"      # controla fluxo de chat
            ) \
            .eq("cod_id", int(cod_id)) \
            .maybe_single() \
            .execute()

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
    # Responde ao preflight CORS
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(force=True) or {}
    app.logger.info("🚀 handle_ia chamado com payload: %s", data)
    user_id = data.get("user_id")
    mensagem = data.get("mensagem", "").strip().lower()
    agendamento_id = data.get("agendamento_id")

    # Validação básica
    if not user_id or not mensagem or not agendamento_id:
        return {"erro": "Dados incompletos"}, 400

    # ← Guard: ignora mensagens enviadas pela própria IA para evitar loop
    if user_id == "ia":
        return {}, 200

    app.logger.info("🔍 Mensagem recebida para override de lembrete: %s", mensagem)

    # ─── OVERRIDE DE LEMBRETES ──────────────────────────────────────
    if any(kw in mensagem for kw in ["lembra", "avisa"]):
        # 1) Extrai data
        dates = search_dates(mensagem, languages=["pt"])
        if dates:
            date_str, date_dt = dates[0]

            # 2) Remove a data da mensagem
            text_wo_date = re.sub(re.escape(date_str), "", mensagem, flags=re.IGNORECASE)

            # 3) Remove palavras de gatilho e preposições comuns
            reminder_msg = re.sub(
                r"\b(me lembra( me)?|avisa( me)?|lembra de|lembra)\b",
                "",
                text_wo_date,
                flags=re.IGNORECASE
            )
            # 4) Remove “dia”, “em”, “para” e pontuações soltas
            reminder_msg = re.sub(r"\b(dia|em|para)\b", "", reminder_msg, flags=re.IGNORECASE)
            reminder_msg = reminder_msg.replace("?", "").strip()

            # 5) Se ficar vazio, define mensagem genérica
            if not reminder_msg:
                reminder_msg = "seu lembrete"

            # 6) Grava no banco
            res = supabase.table("user_reminders").insert({
                "user_id":  user_id,
                "due_date": date_dt.isoformat(),
                "message":  reminder_msg
            }).execute()

            # 7) Monta resposta usando template genérico
            tpl = random.choice(REMINDER_TEMPLATES)
            resposta = tpl.format(
                date=date_dt.strftime("%d/%m/%Y"),
                task=reminder_msg
            )

            gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
            return {"resposta": resposta}, 200

            # … após o bloco de override de lembretes …

    # 1) Busca agendamento atual
    dados = buscar_agendamento(agendamento_id)

    # ← Se o agendamento NÃO está ativo, redireciona para o ativo mais próximo
    if mensagem in ["y","yes","sim","oui","ok","n","não","no","non","r"] \
       and not dados.get("chat_ativo"):
        ativo = supabase.table("agendamentos") \
            .select("cod_id") \
            .eq("user_id", user_id) \
            .eq("status", "Agendado") \
            .eq("chat_ativo", True) \
            .order("date", asc=True) \
            .order("horas", asc=True) \
            .maybe_single() \
            .execute().data

        if ativo and ativo.get("cod_id"):
            app.logger.info(
                f"🔀 Redirecionando do agendamento {agendamento_id} para o ativo {ativo['cod_id']}"
            )
            agendamento_id = ativo["cod_id"]
            dados = buscar_agendamento(agendamento_id)
        else:
            resposta = (
                "Não encontrei nenhum agendamento aberto para processar. "
                "Por favor, responda à mensagem do agendamento correto."
            )
            gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
            return {"resposta": resposta}, 200

    # 2) Intenção: disponibilidade (caso seja esse o caso)
    if any(k in mensagem for k in ["disponível", "vagas"]):
        disponiveis = consultar_disponibilidade(
            dados["company_id"], dados["atend_id"], dados.get("nova_data")
        ).get("horas_disponiveis", {}).get("disponiveis", [])
        if disponiveis:
            resposta = "Tenho vagas nestes horários:\n" + "\n".join(f"– {h[:5]}" for h in disponiveis)
        else:
            tpl = random.choice(NO_SLOTS_TEMPLATES)
            resposta = tpl.format(date=fmt_data(date.fromisoformat(dados["nova_data"][:10])))
        gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
        return {"resposta": resposta}, 200

    # 3) Confirmação positiva (Y / yes / sim / oui / ok)
    if mensagem in ["y", "yes", "sim", "oui", "ok"]:
        # 3.1) Se NÃO estivermos remarcando, confirmamos o original
        if not dados.get("reagendando"):
            orig_date = datetime.fromisoformat(dados["date"]).date()
            orig_hora = dados["horas"][:5]
            resposta = random.choice(CONFIRM_TEMPLATES).format(
                date=fmt_data(orig_date), time=orig_hora
            )
            supabase.table("agendamentos").update({
                "status":     "Confirmado",
                "chat_ativo": False
            }).eq("cod_id", int(agendamento_id)).execute()
            gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
            return {"resposta": resposta}, 200

        # 3.2) Caso estejamos no fluxo de remarcação:
        if not dados.get("nova_data") or not dados.get("nova_hora"):
            resposta = (
                "Ops, não encontrei a nova data ou horário. "
                "Por favor, diga a data e hora desejadas (ex: '25/05 às 14:00')."
            )
            gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
            return {"resposta": resposta}, 200

        d_obj = datetime.fromisoformat(dados["nova_data"]).date()
        t_str = dados["nova_hora"][:5]
        resposta = random.choice(CONFIRM_TEMPLATES).format(
            date=fmt_data(d_obj), time=t_str
        )
        supabase.table("agendamentos").update({
            "date":        dados["nova_data"],
            "horas":       dados["nova_hora"],
            "status":      "Reagendado",
            "reagendando": False,
            "chat_ativo":  False
        }).eq("cod_id", int(agendamento_id)).execute()
        gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
        return {"resposta": resposta}, 200


    # 4) Confirmação negativa (N / não / no / non)
    elif mensagem in ["n", "não", "no", "non"]:
        resposta = "Tranquilo! Qual outro dia e horário funcionam melhor pra você? 😉"
        supabase.table("agendamentos").update({
            "nova_data": None,
            "nova_hora": None
        }).eq("cod_id", int(agendamento_id)).execute()
        app.logger.info(f"♻️ Reset slots no agendamento {agendamento_id}")
        gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
        return {"resposta": resposta}, 200

    # 5) Iniciar reagendamento (R)
    elif mensagem.strip().lower() == "r":
        resposta = "Claro! Qual dia funciona melhor para marcarmos?"
        supabase.table("agendamentos").update({
            "reagendando": True,
            "nova_data": None,
            "nova_hora": None,
            "chat_ativo": True
        }).eq("cod_id", int(agendamento_id)).execute()
        app.logger.info(f"♻️ Iniciando reagendamento no agendamento {agendamento_id}")
        gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
        return {"resposta": resposta}, 200

    # 6) Processamento de data/hora informada
    else:
        from datetime import date, time

        # 6a) Apenas hora, mas já temos nova_data
        if re.fullmatch(r"\d{1,2}:\d{2}", mensagem) and dados.get("nova_data"):
            h, m = map(int, mensagem.split(":"))
            nova_data = date.fromisoformat(dados["nova_data"][:10])
            nova_hora = time(h, m)
            app.logger.info(f"⏰ Hora isolada detectada; usando {nova_data} {nova_hora}")

        # 6b) Extrai data e hora juntos
        else:
            nova_data, nova_hora = extrair_data_hora(mensagem)

        # 7) Se vier data+hora, grava e pergunta confirmação
        if nova_data and nova_hora:
            supabase.table("agendamentos").update({
                "nova_data": nova_data.isoformat(),
                "nova_hora": nova_hora.strftime("%H:%M:%S")
            }).eq("cod_id", int(agendamento_id)).execute()
            resposta = (
                f"🔐 Posso confirmar a remarcação para {fmt_data(nova_data)} "
                f"às {nova_hora.strftime('%H:%M')}? Responda com sim ou não."
            )
            app.logger.info(f"♻️ Gravado nova_data {nova_data} e nova_hora {nova_hora}")

        # 8) Se vier só data, grava e lista horários disponíveis
        elif nova_data:
            supabase.table("agendamentos").update({
                "nova_data": nova_data.isoformat(),
                "nova_hora": None
            }).eq("cod_id", int(agendamento_id)).execute()
            app.logger.info(f"♻️ Gravado nova_data {nova_data} (sem hora)")

            disponiveis = consultar_disponibilidade(
                dados["company_id"], dados["atend_id"], nova_data.isoformat()
            ).get("horas_disponiveis", {}).get("disponiveis", [])
            if disponiveis:
                resposta = "Tenho vagas nestes horários:\n" + "\n".join(f"– {h[:5]}" for h in disponiveis)
            else:
                tpl = random.choice(NO_SLOTS_TEMPLATES)
                resposta = tpl.format(date=fmt_data(nova_data))
            app.logger.info(f"💬 Listando slots para {nova_data}: {disponiveis}")

        # 9) Quando não for lembrete, nem reagendamento…
        else:
            if not dados.get("chat_ativo"):
                resposta = (
                    "No momento só posso ajudar com lembretes e reagendamentos. "
                    "Alterar agendamento, somente 3 dias antes da data agendada. Se quiser pode ir na Home e cancelar seu agendamento e fazer outro."
                )
                app.logger.info("🚫 Bloqueado fallback IA pois chat_ativo=False")
                gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
                return {"resposta": resposta}, 200

            # 10) Se estivermos em reagendamento (chat_ativo == True), cai no LLM
            historico = supabase.table("mensagens_chat") \
                .select("mensagem,tipo") \
                .eq("agendamento_id", int(agendamento_id)) \
                .order("data_envio", desc=False).limit(10).execute().data
            msgs = [
                {"role": "assistant" if m['tipo']=='IA' else 'user', "content": m['mensagem']}
                for m in historico
            ]
            msgs.append({"role": "user", "content": mensagem})
            msgs.insert(0, {
                "role": "system",
                "content": "Você é uma atendente virtual simpática. Nunca confirme horários sem o cliente for sim."
            })
            resposta = gerar_resposta_ia(msgs)
            app.logger.info("💬 Fallback IA para reagendamento em curso")

        # 11) Grava e retorna (fall-through)
        gravar_mensagem_chat(user_id="ia", mensagem=resposta, agendamento_id=agendamento_id)
        return {"resposta": resposta}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

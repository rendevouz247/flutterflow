from flask import Flask, request
from supabase import create_client
from datetime import datetime
from groq import Groq
import os, logging
import dateparser
import re

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

gatilhos = ["quero", "pode ser", "remarcar", "agendar", "agenda", "pra", "para", "às", "as", "dia"]

def contem_gatilhos(texto):
    tem_data = dateparser.parse(texto, languages=["pt", "en", "fr"]) is not None
    tem_hora = re.search(r"\d{1,2}[:h]\d{0,2}", texto)
    return tem_data and (tem_hora or any(g in texto.lower() for g in gatilhos))

def extrair_data_hora(texto):
    data = dateparser.parse(texto, languages=["pt", "en", "fr"])
    hora_match = re.search(r"(\d{1,2})[:h](\d{0,2})", texto)
    if data and hora_match:
        hora = hora_match.group(1).zfill(2)
        minuto = hora_match.group(2).zfill(2) if hora_match.group(2) else "00"
        hora_formatada = f"{hora}:{minuto}"
        return data.date().isoformat(), hora_formatada
    return None, None

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
        # COMANDO DE CONFIRMAÇÃO
        if mensagem in ["y", "yes", "sim", "oui"]:
            dados = supabase.table("agendamentos") \
                .select("nova_data, nova_hora") \
                .eq("cod_id", agendamento_id) \
                .single().execute().data

            nova_data = dados.get("nova_data")
            nova_hora = dados.get("nova_hora")

            if nova_data and nova_hora:
                supabase.table("agendamentos").update({
                    "date": nova_data,
                    "horas": nova_hora,
                    "status": "Reagendado",
                    "reagendando": False,
                    "chat_ativo": False
                }).eq("cod_id", agendamento_id).execute()

                resposta = f"✅ Perfeito! Sua consulta foi remarcada com sucesso para {nova_data} às {nova_hora}. Te esperamos lá! 😄"
            else:
                resposta = "Hmm... não encontrei uma data pendente para confirmar. Pode me dizer de novo o dia e horário?"

        elif mensagem in ["n", "não", "no", "non"]:
            resposta = "Sem problema! Qual dia e horário seria melhor pra você? 😊"

        elif mensagem == "r":
            supabase.table("agendamentos").update({
                "reagendando": True,
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", agendamento_id).execute()

            resposta = "Claro! Qual dia é melhor pra você? Pode dizer: 'amanhã', 'segunda às 14h', ou algo assim."

        else:
            dados_agendamento = supabase.table("agendamentos") \
                .select("company_id, atend_id") \
                .eq("cod_id", agendamento_id) \
                .single().execute().data

            company_id = dados_agendamento.get("company_id")
            atendente_id = dados_agendamento.get("atend_id")

            if contem_gatilhos(mensagem):
                nova_data, nova_hora = extrair_data_hora(mensagem)
                app.logger.info(f"📅 Data extraída: {nova_data} | ⏰ Hora extraída: {nova_hora}")

                if nova_data and nova_hora:
                    resultado = supabase.table("view_horas_disponiveis") \
                        .select("disponiveis") \
                        .eq("company_id", company_id) \
                        .eq("atend_id", atendente_id) \
                        .eq("date", nova_data) \
                        .single().execute().data

                    app.logger.info(f"📊 Resultado da view: {resultado}")

                    if resultado and nova_hora in resultado.get("disponiveis", []):
                        supabase.table("agendamentos").update({
                            "nova_data": nova_data,
                            "nova_hora": nova_hora
                        }).eq("cod_id", agendamento_id).execute()

                        resposta = f"📆 Posso confirmar sua remarcação para {nova_data} às {nova_hora}? Responda com *sim* ou *não* 😉"
                    else:
                        horarios = resultado.get("disponiveis", []) if resultado else []
                        horarios_sugestao = "\n".join([f"🔹 {h}" for h in horarios[:3]]) or "Nenhum horário disponível."
                        resposta = (
                            f"😕 Esse horário não está disponível.\n"
                            f"Aqui estão outras opções:\n{horarios_sugestao}\n"
                            f"Qual prefere?"
                        )
                else:
                    resposta = "Não consegui entender bem a data e hora. Pode dizer algo como: 'Quero remarcar pra amanhã às 15h'."
            else:
                historico = supabase.table("mensagens_chat") \
                    .select("mensagem, tipo") \
                    .eq("agendamento_id", agendamento_id) \
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
                        "Você é uma atendente virtual simpática e multilíngue. "
                        "Ajude clientes a remarcar serviços como consultas, estética, pet shop, mecânica, etc. "
                        "Sempre pergunte se o cliente quer confirmar a data sugerida. "
                        "Se ele disser sim, finalize com simpatia. Se disser não, pergunte por outra opção."
                    )
                })

                nlu = groq_client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=mensagens_formatadas,
                    temperature=0.7,
                    max_tokens=400
                )
                resposta = nlu.choices[0].message.content.strip()

        supabase.table("mensagens_chat").insert({
            "user_id": "ia",
            "mensagem": resposta,
            "agendamento_id": agendamento_id,
            "data_envio": datetime.utcnow().isoformat(),
            "tipo": "IA"
        }).execute()

        app.logger.info(f"💬 Resposta da IA: {resposta}")
        return {"resposta": resposta}, 200

    except Exception as e:
        app.logger.error(f"❌ Erro: {e}")
        return {"erro": "Erro interno ao processar"}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


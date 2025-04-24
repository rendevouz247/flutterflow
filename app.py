from flask import Flask, request
from supabase import create_client
from datetime import datetime
from groq import Groq
import os, json, logging

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

@app.route("/ia", methods=["POST"])
def handle_ia():
    data = request.get_json()
    user_id = data.get("user_id")
    mensagem = data.get("mensagem", "").strip().lower()
    agendamento_id = data.get("agendamento_id")

    print(f"📩 Recebido na /ia: {data}")

    if not user_id or not mensagem or not agendamento_id:
        return {"erro": "Dados incompletos"}, 400

    resposta = ""

    try:
        if mensagem in ["y", "yes", "sim", "oui"]:
            supabase.table("agendamentos").update({
                "status": "Confirmado",
                "reagendando": False
            }).eq("cod_id", agendamento_id).execute()
            resposta = "Perfeito! Sua consulta está confirmada ✅"

        elif mensagem in ["n", "não", "no", "non"]:
            supabase.table("agendamentos").update({
                "status": "Cancelado",
                "reagendando": False
            }).eq("cod_id", agendamento_id).execute()
            resposta = "Entendido! Sua consulta foi cancelada ❌"

        elif mensagem in ["r"]:
            supabase.table("agendamentos").update({
                "reagendando": True,
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", agendamento_id).execute()
            resposta = (
                "Ok! Qual data é melhor pra remarcar? Você pode escrever algo como:\n"
                "➡️ 'amanhã', 'próxima segunda', 'dia 15 de maio', etc."
            )

        else:
            # IA entra em ação
            nlu = groq_client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[
                    {"role": "system", "content": (
                        "Você é uma IA de reagendamento de consultas, seja simpática e ajude o cliente com naturalidade. "
                        "Pergunte qual data ele prefere, sugira horários disponíveis se possível e confirme se ele quer remarcar."
                    )},
                    {"role": "user", "content": mensagem}
                ]
            )
            resposta = nlu.choices[0].message.content.strip()

        # Grava a resposta da IA no banco
        print(f"💬 Resposta da IA: {resposta}")
        supabase.table("mensagens_chat").insert({
            "user_id": "ia",
            "mensagem": resposta,
            "agendamento_id": agendamento_id,
            "data_envio": datetime.utcnow().isoformat(),
            "tipo": "IA"
        }).execute()

        return {"resposta": resposta}, 200

    except Exception as e:
        print(f"❌ Erro no app.py: {e}")
        return {"erro": "Erro interno ao processar a IA"}, 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)



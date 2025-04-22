from flask import Flask, request
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
from groq import Groq
import os, json, re
import logging

# CONFIG
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
TWILIO_SID    = os.getenv("TWILIO_SID")
TWILIO_AUTH   = os.getenv("TWILIO_AUTH")
TWILIO_PHONE  = os.getenv("TWILIO_PHONE")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")

supabase     = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client= TwilioClient(TWILIO_SID, TWILIO_AUTH)
groq_client  = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)


TRUNCATE_LIMIT = 500
HORA_FLAG = "HORA_SELECIONADA"

def truncate(text: str, limit: int = TRUNCATE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

def send_message(resp: MessagingResponse, text: str, lang: str = "fr"):
    translated = traduzir(text, lang) if lang != "fr" else text
    resp.message(truncate(translated))

def format_date(date_str: str) -> str:
    return datetime.fromisoformat(date_str).strftime("%d/%m/%Y")

def get_available_times(date: str, company_id: str) -> list:
    rows = (
        supabase
        .from_("view_horas_disponiveis")
        .select("horas_disponiveis")
        .eq("company_id", company_id)
        .eq("date", date)
        .execute()
        .data
    )
    times = []
    for r in rows:
        j = r.get("horas_disponiveis") or {}
        times += j.get("disponiveis", [])
    return sorted(set(times))

def detectar_idioma(texto):
    try:
        return GoogleTranslator(source='auto', target='en').detect(texto)
    except:
        return 'fr'

def traduzir(texto: str, destino: str) -> str:
    try:
        return GoogleTranslator(source='fr', target=destino).translate(texto)
    except:
        return texto

def parse_date_from_text(text):
    try:
        # Normaliza strings como "às 10:00", "as 10:00", "de manhã", "à tarde", "no fim do dia"
        text = text.strip().lower()
        text = re.sub(r"(às|as|a|à|ao|no|na|de|em|por|ao\s+)?", "", text)

        # Interpretação direta de partes do dia
        if "manhã" in text:
            return "09:00:00"
        if "tarde" in text:
            return "14:00:00"
        if "noite" in text:
            return "19:00:00"
        if re.match(r"^\d{1,2}[:h]\d{2}(:\d{2})?$", text.strip()):
            return HORA_FLAG

        idioma = detectar_idioma(text)
        hoje = datetime.now().strftime("%d %B %Y")

        nlu = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": (
                    f"Tu es un assistant JSON. Ta tâche est d'extraire une date future à partir d'une phrase dans la langue '{idioma}' (ex: 'le 19 mai', 'demain', 'segunda-feira'). "
                    f"Aujourd'hui, c'est le {hoje}. Si l'année ou la semaine n'est pas mentionnée, choisis toujours la prochaine occurrence future à partir de cette date. "
                    "Réponds uniquement en JSON comme { \"date\": \"2025-05-03\" }. Si aucune date n'est trouvée, retourne { \"date\": null }. Ne retourne aucun texte ou commentaire."
                )},
                {"role": "user", "content": text}
            ]
        )
        raw = nlu.choices[0].message.content.strip()
        app.logger.info(f"\U0001F9E0 Resposta IA bruta: {raw}")
        result = json.loads(raw)
        value = result.get("date")
        if value in [None, "YYYY-MM-DD"]:
            return None
        value = value.split("T")[0] if "T" in value else value

        now = datetime.now()
        dt = datetime.fromisoformat(value)

        if dt.year < now.year:
            dt = dt.replace(year=now.year)
        if dt < now:
            dt = dt.replace(year=dt.year + 1)

        return dt.date().isoformat()
    except Exception as e:
        app.logger.info(f"❌ Erro ao extrair data: {e}")
        return None


@app.route("/sms", methods=["POST"])
def sms_reply():
    msg = request.form.get("Body", "").strip().lower()
    frm = request.form.get("From")
    resp = MessagingResponse()

    app.logger.info(f"📩 MSG RECEBIDA: {msg}")

    ag = (
        supabase
        .table("agendamentos")
        .select("*")
        .eq("user_phone", frm)
        .eq("status", "Agendado")
        .order("date", desc=True)
        .limit(1)
        .execute()
        .data
    )

    if not ag:
        send_message(resp, "Aucun rendez-vous trouvé pour ce numéro.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    a = ag[0]
    nome = a.get("name_user", "Client")
    cod_id = a["cod_id"]
    comp = a["company_id"]
    reagendando = a.get("reagendando", False)
    nova_data = a.get("nova_data")

    if msg == "y":
        supabase.table("agendamentos").update({"status": "Confirmado", "reagendando": False}).eq("cod_id", cod_id).execute()
        send_message(resp, f"Merci {nome}! Votre rendez-vous est confirmé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "n":
        supabase.table("agendamentos").update({"status": "Annulé", "reagendando": False}).eq("cod_id", cod_id).execute()
        send_message(resp, f"D'accord {nome}, votre rendez-vous a été annulé.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "r":
        supabase.table("agendamentos").update({"reagendando": True}).eq("cod_id", cod_id).execute()
        send_message(resp, "Avez-vous un jour de préférence pour reprogrammer ? Vous pouvez répondre par 'demain', 'lundi', 'le 3 mai', etc.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if reagendando and msg and re.match(r"^\d{1,2}[:h]\d{2}(:\d{2})?$", msg):
        if not nova_data:
            send_message(resp, "Veuillez d'abord m'indiquer une date avant de choisir une heure 😉")
            return str(resp), 200, {"Content-Type": "text/xml"}

        hora_formatada = msg.replace("h", ":")
        if len(hora_formatada.split(":")) == 2:
            hora_formatada += ":00"

        horarios_disponiveis = get_available_times(nova_data, company_id=comp)
        horarios_simplificados = [h[:5] for h in horarios_disponiveis]

        if hora_formatada[:5] not in horarios_simplificados:
            send_message(resp, f"Désolé, l'heure {hora_formatada[:5]} n'est pas disponible pour le {format_date(nova_data)}.")
            return str(resp), 200, {"Content-Type": "text/xml"}

        supabase.table("agendamentos").update({"nova_hora": hora_formatada}).eq("cod_id", cod_id).execute()
        send_message(resp, f"Confirmez-vous le nouveau rendez-vous pour le {format_date(nova_data)} à {hora_formatada[:5]} ? Répondez OUI ou NON.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "oui" and reagendando:
        dados = supabase.table("agendamentos").select("nova_data, nova_hora").eq("cod_id", cod_id).execute().data[0]
        nova_data = dados.get("nova_data")
        nova_hora = dados.get("nova_hora")
        if nova_data and nova_hora:
            supabase.table("agendamentos").update({
                "date": nova_data,
                "horas": nova_hora,
                "status": "Confirmado",
                "reagendando": False,
                "nova_data": None,
                "nova_hora": None
            }).eq("cod_id", cod_id).execute()
            send_message(resp, f"Parfait {nome}! Votre rendez-vous a été reprogrammé pour le {format_date(nova_data)} à {nova_hora[:5]}.")
            return str(resp), 200, {"Content-Type": "text/xml"}

    if msg == "non" and reagendando:
        supabase.table("agendamentos").update({"nova_data": None, "nova_hora": None}).eq("cod_id", cod_id).execute()
        send_message(resp, "D'accord, dites-moi une nouvelle date pour reprogrammer.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    preferred_date_raw = parse_date_from_text(msg)
    app.logger.info(f"📅 Data extraída: {preferred_date_raw}")

    if reagendando and preferred_date_raw and preferred_date_raw != HORA_FLAG:
        try:
            datetime.fromisoformat(preferred_date_raw)
        except ValueError:
            send_message(resp, "Désolé, je n'ai pas compris la date. Essayez à nouveau en indiquant un jour précis (ex: 'demain', 'lundi', 'le 3 mai').")
            return str(resp), 200, {"Content-Type": "text/xml"}

        horaires = get_available_times(preferred_date_raw, company_id=comp)
        if horaires:
            texto = f"Voici les horaires disponibles pour le {format_date(preferred_date_raw)}:\n" + ", ".join(horaires)
        else:
            # procura próximos 3 dias com disponibilidade
            for i in range(1, 4):
                proxima_data = (datetime.fromisoformat(preferred_date_raw) + timedelta(days=i)).date().isoformat()
                prox_horarios = get_available_times(proxima_data, company_id=comp)
                if prox_horarios:
                    texto = (
                        f"Aucun horaire disponible pour le {format_date(preferred_date_raw)}. "
                        f"Mais voici les horaires pour le {format_date(proxima_data)}:\n" + ", ".join(prox_horarios)
                    )
                    break
            else:
                texto = f"Aucun horaire disponible pour le {format_date(preferred_date_raw)} ni les jours suivants."


        supabase.table("agendamentos").update({"nova_data": preferred_date_raw, "nova_hora": None}).eq("cod_id", cod_id).execute()
        send_message(resp, texto + "\n\nRépondez avec l'heure souhaitée (ex: 09:00) ou un autre jour.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    if not reagendando:
        send_message(resp, "Merci ! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer.")
        return str(resp), 200, {"Content-Type": "text/xml"}

    app.logger.info("⚠️ Caiu na message padrão final")
    send_message(resp, "Merci ! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer.")
    return str(resp), 200, {"Content-Type": "text/xml"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

from flask import Flask, request, Response
from supabase import create_client, Client as SupabaseClient
from twilio.rest import Client as TwilioClient
from datetime import datetime
from groq import Groq
import os, json, re

# CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

@app.route("/sms", methods=["POST"])
def sms_reply():
    msg_body = request.form.get("Body", "").strip()
    from_number = request.form.get("From")
    agora = datetime.utcnow()

    agendamento = supabase.table("agendamentos") \
        .select("*") \
        .eq("user_phone", from_number) \
        .eq("status", "Agendado") \
        .order("date", desc=True) \
        .limit(1) \
        .execute()

    if not agendamento.data:
        return Response("<Response><Message>Aucun rendez-vous trouvé pour ce numéro.</Message></Response>", content_type="text/xml; charset=utf-8")

    dados = agendamento.data[0]
    nome = dados.get("name_user") or "Client"
    company_id = dados.get("company_id")
    company_name = dados.get("company_name") or "notre clinique"
    atendente = dados.get("nome_atendente") or "notre spécialiste"
    cod_id = dados.get("cod_id")
    telefone = dados.get("user_phone")
    data_original = dados.get("date")
    hora_original = dados.get("horas")[:5]

    print(f"📩 Message de {from_number}: {msg_body}")

    if msg_body.lower() == "y":
        supabase.table("agendamentos").update({"status": "Confirmado"}).eq("cod_id", cod_id).execute()
        return Response(f"<Response><Message>Merci {nome}! Votre rendez-vous est confirmé pour le {data_original} à {hora_original}.</Message></Response>", content_type="text/xml; charset=utf-8")

    if msg_body.lower() == "n":
        supabase.table("agendamentos").update({"status": "Annulé"}).eq("cod_id", cod_id).execute()
        return Response(f"<Response><Message>D'accord {nome}, votre rendez-vous du {data_original} à {hora_original} a été annulé.</Message></Response>", content_type="text/xml; charset=utf-8")

    if msg_body.lower() == "r":
        prompt = (
            f"Tu es Luna, une assistante virtuelle de la clinique {company_name}. "
            f"Un client nommé {nome} souhaite reprogrammer son rendez-vous prévu le {data_original} à {hora_original}. "
            f"Propose-lui 3 dates avec horaires disponibles à partir d'aujourd'hui en te basant sur les disponibilités de la vue 'view_horas_disponiveis' pour la company_id {company_id}. "
            f"Sois claire et directe, pose une seule question à la fin : "
            f"'Souhaitez-vous que je programme le {data_original} à {hora_original} ?'"
        )

        try:
            
            chat = groq_client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "Tu es une assistante virtuelle efficace pour la prise de rendez-vous médicaux."},
                    {"role": "user", "content": prompt}
                ]
            )

            reponse = chat.choices[0].message.content.strip()
            print("🧠 IA LUNA:", reponse)

            # Verifica se há confirmação implícita para agendamento direto
            match = re.search(r"(\d{2}/\d{2}/\d{4}).*?(\d{2}:\d{2})", msg_body)
            if match:
                nova_data = match.group(1).replace("/", "-")
                nova_hora = match.group(2) + ":00"
                print("🕓 Tentando reservar:", nova_data, "às", nova_hora)

                # Atualiza agendamento
                supabase.table("agendamentos").update({
                    "date": nova_data,
                    "horas": nova_hora,
                    "status": "Confirmado"
                }).eq("cod_id", cod_id).execute()

                confirmacao = f"Parfait {nome}! Votre rendez-vous a été reprogrammé pour le {nova_data} à {nova_hora[:5]}."
                return Response(f"<Response><Message>{confirmacao}</Message></Response>", content_type="text/xml; charset=utf-8")

            reponse = reponse.replace("\n", " ")[:800]
            return Response(f"<Response><Message>{reponse}</Message></Response>", content_type="text/xml; charset=utf-8")

        except Exception as e:
            print("❌ ERREUR GROQ:", e)
            return Response("<Response><Message>Désolé, une erreur est survenue avec Luna.</Message></Response>", content_type="text/xml; charset=utf-8")

    return Response("<Response><Message>Merci! Répondez avec Y pour confirmer, N pour annuler, ou R pour reprogrammer.</Message></Response>", content_type="text/xml; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

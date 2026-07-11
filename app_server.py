import os
from flask import Flask, request, jsonify
from auto_finance_updater import przetworz_powiadomienie_push

app = Flask(__name__)


@app.route('/webhook/push', methods=['POST'])
def odbierz_powiadomienie():
    # Pobieramy parametry z zapytania URL
    app_id = request.args.get("app_id")
    app_name = request.args.get("app_name")
    text = request.args.get("text")

    # Zabezpieczenie: jeśli telefon wysłał dane inną metodą
    if not app_id:
        data = request.form if request.form else request.json or {}
        app_id = data.get("app_id")
        app_name = data.get("app_name")
        text = data.get("text")

    # Jeśli tekst jest pusty, ignorujemy bezpiecznie bez błędu 400
    if not text:
        print("⚠️ Serwer odebrał powiadomienie, ale parametr 'text' był pusty.")
        return jsonify({"status": "ignored", "message": "Pusty tekst"}), 200

    print(f"\n📥 Serwer odebrał powiadomienie od: {app_name} ({app_id})")
    print(f"💬 Treść: {text}")

    sformatowana_linia = f"{app_name}: {text}"

    try:
        # Przekazujemy do Twojego głównego silnika finansowego
        przetworz_powiadomienie_push(sformatowana_linia)
        return jsonify({"status": "success", "message": "Przetworzono pomyślnie"}), 200
    except Exception as e:
        print(f"❌ Błąd podczas przetwarzania przez silnik AI: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


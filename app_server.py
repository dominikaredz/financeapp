import os
import urllib.parse  # <-- DODAJ TEN IMPORT NA SAMĄ GÓRĘ PLIKU
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# --- WYMUSZENIE ŁADOWANIA KLUCZY NA SAMYM STARCIE ---
folder_projektu = r'/home/domiredz00/FinanceApp'
load_dotenv(dotenv_path=os.path.join(folder_projektu, 'env.txt'))

from auto_finance_updater import przetworz_powiadomienie_push

app = Flask(__name__)


@app.route('/webhook/push', methods=['POST'])
def odbierz_powiadomienie():
    app_id = request.args.get("app_id")
    app_name = request.args.get("app_name")
    text = request.args.get("text")

    if not app_id:
        data = request.form if request.form else request.json or {}
        app_id = data.get("app_id")
        app_name = data.get("app_name")
        text = data.get("text")

    if not text:
        return jsonify({"status": "ignored", "message": "Pusty tekst"}), 200

    # 🔥 ODKODOWANIE ZNAKÓW %20, %C5%82, %C4%85 na normalne spacje i polskie litery:
    text_czysty = urllib.parse.unquote(text)
    app_name_czysty = urllib.parse.unquote(app_name) if app_name else "NieznanaAplikacja"

    sformatowana_linia = f"{app_name_czysty}: {text_czysty}"

    try:
        przetworz_powiadomienie_push(sformatowana_linia)
        return jsonify({"status": "success", "message": "Przetworzono pomyślnie"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
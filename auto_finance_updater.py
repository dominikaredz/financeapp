import os
import sqlite3
import json
import warnings
from dotenv import load_dotenv
from ai_categorized import rozpoznaj_kategorie_zbiorczo

# Uciszamy ostrzeżenia o datach w nowym Pythonie
warnings.filterwarnings("ignore", category=DeprecationWarning)

# --- KONFIGURACJA ---
folder_projektu = r'E:\Programowanie\FinanceApp'
load_dotenv(dotenv_path=os.path.join(folder_projektu, '.env'))
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

# Ładowanie czarnej listy z config.json
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        czarna_lista = config["blokowane_slowa"]
except FileNotFoundError:
    czarna_lista = {}

# --- FUNKCJA ZAPISU DO BAZY ---
def dodaj_transakcje_do_bazy(kategoria_id, kwota, data, opis, typ):
    try:
        conn = sqlite3.connect(path_to_db)
        cursor = conn.cursor()
        query = """
                INSERT INTO transakcje (uzytkownik_id, kategoria_id, kwota, data, opis_sklepu, typ)
                VALUES (1, ?, ?, ?, ?, ?);
                """
        cursor.execute(query, (kategoria_id, kwota, data, opis, typ))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"❌ Błąd bazy danych: {e}")

# Tutaj w przyszłości wejdzie nasza funkcja czytająca powiadomienia!
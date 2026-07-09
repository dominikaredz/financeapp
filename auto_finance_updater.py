import os
import sqlite3
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from ai_categorized import rozpoznaj_kategorie_zbiorczo

# --- KONFIGURACJA I ŁADOWANIE ŚRODOWISKA ---
folder_projektu = r'E:\Programowanie\FinanceApp'
sciezka_env = os.path.join(folder_projektu, '.env')
load_dotenv(dotenv_path=sciezka_env)

path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

# Ładowanie kluczy Plaid
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")

configuration = plaid.Configuration(
    host=plaid.Environment.Sandbox,
    api_key={'clientId': PLAID_CLIENT_ID, 'secret': PLAID_SECRET}
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

# Ładowanie czarnej listy z config.json
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        czarna_lista = config["blokowane_slowa"]
except FileNotFoundError:
    print("⚠️ Brak pliku config.json! Program przejdzie w tryb bez filtrów użytkownika.")
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


# --- ETAP 1: POBIERANIE I FILTROWANIE DANYCH Z API ---
ACCESS_TOKEN = "access-sandbox-2e41401b-d94b-4e2f-93b8-94b3693205c3"

start_date = (datetime.now() - timedelta(days=30)).date()
end_date = datetime.now().date()

transakcje_do_zapisu = []
opisy_dla_ai = []

try:
    print(f"📡 Pobieram transakcje z Plaid ({start_date} do {end_date})...")
    request = TransactionsGetRequest(
        access_token=ACCESS_TOKEN,
        start_date=start_date,
        end_date=end_date,
        options=TransactionsGetRequestOptions(count=15)
    )
    response = client.transactions_get(request)
    plaid_transactions = response['transactions']
    print(f"✅ Pomyślnie pobrano {len(plaid_transactions)} transakcji z banku.")

    for t in plaid_transactions:
        kwota_raw = t['amount']
        data = t['date']
        pelny_opis = t['name'].strip()

        typ = "WYDATEK" if kwota_raw > 0 else "PRZYCHOD"
        kwota = abs(kwota_raw)

        # Filtry bezpieczeństwa
        czy_to_przelew = "PRZELEW" in pelny_opis.upper() or "TRANSFER" in pelny_opis.upper() or typ == 'PRZYCHOD'

        wykryte_id_z_pliku = None
        if not czy_to_przelew:
            # Sprawdzamy słowa z config.json
            for slowo, kat_id in czarna_lista.items():
                if slowo.upper() in pelny_opis.upper():
                    wykryte_id_z_pliku = kat_id
                    break

        indeks_ai = None
        # Do AI trafia tylko to, co nie jest przelewem i czego nie ma w config.json
        if not czy_to_przelew and wykryte_id_z_pliku is None:
            opisy_dla_ai.append(pelny_opis)
            indeks_ai = len(opisy_dla_ai) - 1

        transakcje_do_zapisu.append({
            'kwota': kwota,
            'data': data,
            'opis': pelny_opis,
            'typ': typ,
            'indeks_ai': indeks_ai,
            'sztywne_id': wykryte_id_z_pliku
        })

except plaid.ApiException as e:
    print(f"❌ Błąd API Plaid: {e.body}")
    exit()

# --- ETAP 2: JEDNO ZAPYTANIE DO AI ---
słownik_kategorii = {}
if opisy_dla_ai:
    print(f"🚀 Wysyłam zbiorcze zapytanie do AI dla {len(opisy_dla_ai)} transakcji...")
    słownik_kategorii = rozpoznaj_kategorie_zbiorczo(opisy_dla_ai)

# --- ETAP 3: MASOWY ZAPIS DO BAZY ---
print("💾 Zapisuję skategoryzowane dane do bazy SQL...")
licznik = 0

for t in transakcje_do_zapisu:
    if t['indeks_ai'] is not None and t['indeks_ai'] in słownik_kategorii:
        kategoria_id = słownik_kategorii[t['indeks_ai']]
    elif t['sztywne_id'] is not None:
        kategoria_id = t['sztywne_id']
    else:
        kategoria_id = 12  # Domyślnie 'Inne'

    dodaj_transakcje_do_bazy(kategoria_id, t['kwota'], t['data'], t['opis'], t['typ'])
    licznik += 1

print(f"\n✅ SUKCES! Dodano {licznik} transakcji. Zużyto 1 zapytanie AI. Pliki CSV odeszły do lamusa!")
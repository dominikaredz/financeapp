import os
import sqlite3
import json
import warnings
import re
from datetime import datetime
from dotenv import load_dotenv
from ai_categorized import analizuj_powiadomienie_przez_ai

warnings.filterwarnings("ignore", category=DeprecationWarning)

# --- KONFIGURACJA ---
folder_projektu = r'E:\Programowanie\FinanceApp'
load_dotenv(dotenv_path=os.path.join(folder_projektu, '.env'))
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        prywatne_slowa = config["blokowane_slowa"]
except FileNotFoundError:
    prywatne_slowa = {}


# --- FUNKCJA INTEGRACJI BAZY DANYCH (Z DUPLIKATAMI) ---
def zapisz_lub_scal_transakcje(kategoria_id, kwota, data, opis, typ, zrodlo):
    try:
        conn = sqlite3.connect(path_to_db)
        cursor = conn.cursor()

        # 1. Szukamy transakcji o tej samej kwocie z dzisiaj (inteligentne okno duplikatów)
        query_check = """
                      SELECT id, opis_sklepu, zrodlo \
                      FROM transakcje
                      WHERE kwota = ? \
                        AND data = ? \
                        AND typ = ?; \
                      """
        cursor.execute(query_check, (kwota, data, typ))
        istniejaca_transakcja = cursor.fetchone()

        if istniejaca_transakcja:
            t_id, stary_opis, stare_zrodlo = istniejaca_transakcja

            # Jeśli stare to Millennium, a nowe to Google -> Google ma lepszą nazwę, aktualizujemy!
            if stare_zrodlo == "MILLENNIUM" and zrodlo == "GOOGLE":
                query_update = "UPDATE transakcje SET opis_sklepu = ?, zrodlo = 'GOOGLE_SCALONE' WHERE id = ?;"
                cursor.execute(query_update, (opis, t_id))
                conn.commit()
                print(f"🔄 Scalono duplikaty! Zastąpiono suchy wpis z banku ładną nazwą z Google: {opis} ({kwota} PLN)")
            else:
                print(f"⚠️ Wykryto duplikat z {zrodlo} dla kwoty {kwota} PLN. Ignoruję (mamy już lepsze dane).")

        else:
            # Brak duplikatów – wpisujemy jako nową transakcję
            query_insert = """
                           INSERT INTO transakcje (uzytkownik_id, kategoria_id, kwota, data, opis_sklepu, typ, zrodlo)
                           VALUES (1, ?, ?, ?, ?, ?, ?); \
                           """
            cursor.execute(query_insert, (kategoria_id, kwota, data, opis, typ, zrodlo))
            conn.commit()
            print(f"💾 Nowy wpis ({zrodlo}): {opis} | {kwota} PLN | Kategoria ID: {kategoria_id}")

        conn.close()
    except sqlite3.Error as e:
        print(f"❌ Błąd bazy danych: {e}")


# --- CENZOR ---
def cenzuruj_wrazliwe_dane(tekst_paczy):
    tekst_oczyszczony = tekst_paczy
    for slowo in prywatne_slowa.keys():
        if slowo.upper() in tekst_oczyszczony.upper():
            insensitivity_compiler = re.compile(re.escape(slowo), re.IGNORECASE)
            tekst_oczyszczony = insensitivity_compiler.sub("[UKRYTE]", tekst_oczyszczony)
    return tekst_oczyszczony


# --- PROCESOR PACZEK ---
def przetworz_powiadomienie_push(tresc_pusha):
    print(f"\n📱 Rozpoczynam przetwarzanie paczki powiadomień...")
    dzisiejsza_data = datetime.now().strftime("%Y-%m-%d")

    # --- NOWOŚĆ: AUTOMATYCZNE FILTROWANIE SUROWYCH PRZELEWÓW MILLENNIUM ---
    if "MILLENNIUM" in tresc_pusha.upper() and "PRZELEW" in tresc_pusha.upper():
        print("ℹ️ System: Wykryto surowy przelew z Millennium. Przetwarzam automatycznie (bez AI)...")

        # Wyciągamy kwotę z tekstu powiadomienia
        kwota_match = re.search(r"Kwota:\s*([0-9.,]+)", tresc_pusha)
        if not kwota_match:
            print("⚠️ Nie udało się wyciągnąć kwoty z powiadomienia Millennium. Przerywam.")
            return

        kwota = float(kwota_match.group(1).replace(',', '.'))

        # Ustalamy kierunek, opis i nową kategorię
        if "PRZYCHODZĄCY" in tresc_pusha.upper():
            typ_transakcji = "PRZYCHOD"
            kategoria_id = 13  # Twoja nowa kategoria 'Przychody' z DataGripa
            sklep = "Przelew Przychodzący Millennium"
        else:
            typ_transakcji = "WYDATEK"
            kategoria_id = 12  # Kategoria 'Inne'
            sklep = "Przelew Wychodzący Millennium"

        # Wywołujemy Twoją standardową funkcję zapisu (ona sama sprawdzi duplikaty)
        zapisz_lub_scal_transakcje(kategoria_id, kwota, dzisiejsza_data, sklep, typ_transakcji, "MILLENNIUM")
        return  # Wychodzimy z funkcji! Nie idziemy do AI.

    # --- STANDARDOWA ŚCIEŻKA DLA INNYCH POWIADOMIEŃ (NP. PORTFEL GOOGLE) ---
    # Lokalna cenzura
    bezpieczna_tresc_dla_ai = cenzuruj_wrazliwe_dane(tresc_pusha)

    # Wysyłanie do AI Groq
    print("🚀 Przekazuję bezpieczną paczkę do AI...")
    dane_z_ai = analizuj_powiadomienie_przez_ai(bezpieczna_tresc_dla_ai)

    if dane_z_ai and isinstance(dane_z_ai, dict) and "transakcje" in dane_z_ai:
        lista_transakcji = dane_z_ai["transakcje"]

        # Potrzebujemy też oryginalnych linii, żeby wykryć źródło (Google/Millennium)
        linie = tresc_pusha.strip().split('\n')

        for transakcja in lista_transakcji:
            kwota = transakcja.get("kwota")
            sklep = transakcja.get("sklep")
            kategoria_id = transakcja.get("kategoria_id", 12)
            typ_transakcji = transakcja.get("typ", "WYDATEK")

            # --- SUPERELASTYCZNA DETEKCJA ŹRÓDŁA ---
            zrodlo = "NIEZNANE"
            for linia in linie:
                if str(kwota).replace('.', ',') in linia or str(kwota) in linia:
                    if "MILLENNIUM" in linia.upper():
                        zrodlo = "MILLENNIUM"
                        break
                    elif "PORTFEL" in linia.upper() or "GOOGLE" in linia.upper():
                        zrodlo = "GOOGLE"
                        break

            if kwota is not None and sklep:
                zapisz_lub_scal_transakcje(kategoria_id, kwota, dzisiejsza_data, sklep, typ_transakcji, zrodlo)
    else:
        print("❌ Nie udało się sparsować paczki wiadomości przez AI.")


if __name__ == "__main__":
    sciezka_pusha = os.path.join(folder_projektu, 'test_push_notification.txt')
    if os.path.exists(sciezka_pusha):
        with open(sciezka_pusha, 'r', encoding='utf-8') as f:
            surowy_push = f.read().strip()
        przetworz_powiadomienie_push(surowy_push)
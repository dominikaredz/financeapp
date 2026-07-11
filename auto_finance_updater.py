import os
import sqlite3
import json
import warnings
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from ai_categorized import analizuj_powiadomienie_przez_ai

# --- KONFIGURACJA ŚCIEŻEK ---
folder_projektu = r'/home/domiredz00/FinanceApp'

# --- KONFIGURACJA LOGOWANIA ---
log_file_path = os.path.join(folder_projektu, 'finance_app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()  # Logi będą widoczne jednocześnie w pliku i konsoli serwera
    ]
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

# --- KONFIGURACJA BAZY I DOTENV ---
load_dotenv(dotenv_path=os.path.join(folder_projektu, 'env.txt'))
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

try:
    with open(os.path.join(folder_projektu, 'config.json'), 'r', encoding='utf-8') as f:
        config = json.load(f)
        prywatne_slowa = config["blokowane_slowa"]
except FileNotFoundError:
    prywatne_slowa = {}


# --- FUNKCJA INTEGRACJI BAZY DANYCH (Z DUPLIKATAMI) ---
def zapisz_lub_scal_transakcje(kategoria_id, kwota, data, opis, typ, zrodlo):
    try:
        conn = sqlite3.connect(path_to_db)
        cursor = conn.cursor()

        # Szukamy transakcji o tej samej kwocie z dzisiaj
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

            # Jeśli stare to Millennium, a nowe to Google -> Aktualizujemy nazwę
            if stare_zrodlo == "MILLENNIUM" and zrodlo == "GOOGLE":
                query_update = "UPDATE transakcje SET opis_sklepu = ?, zrodlo = 'GOOGLE_SCALONE' WHERE id = ?;"
                cursor.execute(query_update, (opis, t_id))
                conn.commit()
                logging.info(
                    f"🔄 Scalono duplikaty! Zastąpiono suchy wpis z banku ładną nazwą z Google: {opis} ({kwota} PLN)")
            else:
                logging.warning(
                    f"⚠️ Wykryto duplikat z {zrodlo} dla kwoty {kwota} PLN. Ignoruję (mamy już lepsze dane).")

        else:
            # Brak duplikatów – wpisujemy jako nową transakcję
            query_insert = """
                           INSERT INTO transakcje (uzytkownik_id, kategoria_id, kwota, data, opis_sklepu, typ, zrodlo)
                           VALUES (1, ?, ?, ?, ?, ?, ?); \
                           """
            cursor.execute(query_insert, (kategoria_id, kwota, data, opis, typ, zrodlo))
            conn.commit()
            logging.info(f"💾 Nowy wpis ({zrodlo}): {opis} | {kwota} PLN | Kategoria ID: {kategoria_id}")

        conn.close()
    except sqlite3.Error as e:
        logging.error(f"❌ Błąd bazy danych: {e}")


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
    logging.info("📱 Rozpoczynam przetwarzanie paczki powiadomień...")

    try:  # --- PEŁNY BLOK OCHRONNY ---
        dzisiejsza_data = datetime.now().strftime("%Y-%m-%d")

        # --- 1. AUTOMATYCZNE FILTROWANIE SUROWYCH PRZELEWÓW MILLENNIUM ---
        if "MILLENNIUM" in tresc_pusha.upper() and "PRZELEW" in tresc_pusha.upper():
            logging.info("ℹ️ System: Wykryto surowy przelew z Millennium. Przetwarzam automatycznie (bez AI)...")

            # Wyciągamy kwotę z tekstu powiadomienia
            kwota_match = re.search(r"Kwota:\s*([0-9.,]+)", tresc_pusha)
            if not kwota_match:
                logging.error("⚠️ Nie udało się wyciągnąć kwoty z powiadomienia Millennium. Przerywam.")
                return

            kwota = float(kwota_match.group(1).replace(',', '.'))

            # Ustalamy kierunek, opis i nową kategorię
            if "PRZYCHODZĄCY" in tresc_pusha.upper():
                typ_transakcji = "PRZYCHOD"
                kategoria_id = 13  # Kategoria 'Przychody' z bazy
                sklep = "Przelew Przychodzący Millennium"
            else:
                typ_transakcji = "WYDATEK"
                kategoria_id = 12  # Kategoria 'Inne'
                sklep = "Przelew Wychodzący Millennium"

            zapisz_lub_scal_transakcje(kategoria_id, kwota, dzisiejsza_data, sklep, typ_transakcji, "MILLENNIUM")
            return  # Kończymy przetwarzanie sukcesem

        # --- 2. STANDARDOWA ŚCIEŻKA DLA INNYCH POWIADOMIEŃ (NP. PORTFEL GOOGLE) ---
        bezpieczna_tresc_dla_ai = cenzuruj_wrazliwe_dane(tresc_pusha)

        logging.info("🚀 Przekazuję bezpieczną paczkę do AI...")
        dane_z_ai = analizuj_powiadomienie_przez_ai(bezpieczna_tresc_dla_ai)

        if dane_z_ai and isinstance(dane_z_ai, dict) and "transakcje" in dane_z_ai:
            lista_transakcji = dane_z_ai["transakcje"]
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
            logging.error("❌ Nie udało się sparsować paczki wiadomości przez AI.")

    except Exception as e:
        # Ten fragment złapie KAŻDY błąd i precyzyjnie opisze go w logu
        logging.exception(f"💥 Krytyczny błąd wewnątrz procesora pushy: {e}")


if __name__ == "__main__":
    sciezka_pusha = os.path.join(folder_projektu, 'test_push_notification.txt')
    if os.path.exists(sciezka_pusha):
        with open(sciezka_pusha, 'r', encoding='utf-8') as f:
            surowy_push = f.read().strip()
        przetworz_powiadomienie_push(surowy_push)
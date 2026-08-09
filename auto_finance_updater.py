import os
import sqlite3
import json
import warnings
import re
import logging
from datetime import datetime
from dotenv import load_dotenv

# --- KONFIGURACJA ŚCIEŻEK I DOTENV ---
# WAŻNE: to musi być wykonane PRZED importem ai_categorized, bo ten moduł
# tworzy klienta Groq już na poziomie importu (client = Groq(api_key=os.getenv(...))).
# Jeśli GROQ_API_KEY nie jest jeszcze wczytany z env.txt, import ai_categorized
# rzuci groq.GroqError.
folder_projektu = r'/home/domiredz00/FinanceApp'
load_dotenv(dotenv_path=r'/home/domiredz00/.env')
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

from ai_categorized import analizuj_powiadomienie_przez_ai

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

try:
    with open(os.path.join(folder_projektu, 'config.json'), 'r', encoding='utf-8') as f:
        config = json.load(f)
        prywatne_slowa = config["blokowane_slowa"]
except FileNotFoundError:
    prywatne_slowa = {}


# --- FUNKCJA INTEGRACJI BAZY DANYCH (Z DUPLIKATAMI) ---
# ZMIANA: Dodajemy argument uzytkownik_id na początku funkcji
def zapisz_lub_scal_transakcje(uzytkownik_id, kategoria_id, kwota, data, opis, typ, zrodlo):
    try:
        if kwota is not None:
            kwota = abs(float(str(kwota).replace(',', '.')))

        conn = sqlite3.connect(path_to_db)
        cursor = conn.cursor()

        # Szukamy transakcji o tej samej kwocie z dzisiaj DLA TEGO KONKRETNEGO UŻYTKOWNIKA
        query_check = """
                      SELECT id, opis_sklepu, zrodlo
                      FROM transakcje
                      WHERE kwota = ?
                        AND data = ?
                        AND typ = ?
                        AND uzytkownik_id = ?;
                      """
        cursor.execute(query_check, (kwota, data, typ, uzytkownik_id))
        istniejaca_transakcja = cursor.fetchone()

        if istniejaca_transakcja:
            t_id, stary_opis, stare_zrodlo = istniejaca_transakcja

            # UNIWERSALNE scalanie: zamiast sprawdzać KONKRETNE nazwy banków/appek
            # (co wymagałoby dopisywania każdego nowego banku ręcznie), patrzymy na
            # JAKOŚĆ opisu. Jeśli istniejący wpis ma generyczną nazwę ("Płatność",
            # "BLIK", "Przelew" - czyli appka bankowa nie znała sprzedawcy), a nowe
            # powiadomienie przynosi konkretną nazwę (np. z portfela Google/Apple,
            # który zna sprzedawcę) - nadpisujemy ładniejszą wersją. Działa identycznie
            # dla dowolnej kombinacji: Millennium+Google, mBank+Apple Pay, cokolwiek.
            czy_stary_generyczny = stary_opis.strip().lower() in OPISY_GENERYCZNE
            czy_nowy_konkretny = opis.strip().lower() not in OPISY_GENERYCZNE

            if czy_stary_generyczny and czy_nowy_konkretny:
                query_update = "UPDATE transakcje SET opis_sklepu = ?, kategoria_id = ?, zrodlo = ? WHERE id = ?;"
                cursor.execute(query_update, (opis, kategoria_id, f"{zrodlo}_SCALONE", t_id))
                conn.commit()
                logging.info(f"🔄 Scalono duplikaty dla user_id {uzytkownik_id}: '{stary_opis}' -> '{opis}'")
            else:
                logging.warning(f"⚠️ Wykryto duplikat ('{opis}' vs istniejące '{stary_opis}'). Ignoruję.")
        else:
            # ZMIANA: Przekazujemy dynamiczne uzytkownik_id zamiast sztywnej jedynki
            query_insert = """
                           INSERT INTO transakcje (uzytkownik_id, kategoria_id, kwota, data, opis_sklepu, typ, zrodlo)
                           VALUES (?, ?, ?, ?, ?, ?, ?);
                           """
            cursor.execute(query_insert, (uzytkownik_id, kategoria_id, kwota, data, opis, typ, zrodlo))
            conn.commit()
            logging.info(f"💾 Nowy wpis (User {uzytkownik_id}): {opis} | {kwota} PLN")

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


# Opisy uznawane za "generyczne" (appka nie znała nazwy sprzedawcy) - używane przy
# scalaniu duplikatów (patrz zapisz_lub_scal_transakcje) do decyzji, czy nowsze
# powiadomienie ma konkretniejszą nazwę, którą warto nadpisać starą.
OPISY_GENERYCZNE = {"płatność", "przelew", "blik", "wydatek", "przychód", "nieznany", "nieznana", "transakcja"}

# Uniwersalny wzorzec kwoty - pasuje do dowolnego zapisu typu "19,18", "-13.00", "3,49 zł"
# niezależnie od banku/appki. Używany jako filtr antyspamowy: powiadomienie push BEZ
# takiego wzorca to prawie na pewno nie jest prawdziwa transakcja (tylko marketing/info),
# więc odrzucamy je PRZED wysłaniem czegokolwiek do AI - oszczędza zapytanie i chroni
# przed zmyślonymi/fałszywymi wpisami w bazie.
WZORZEC_KWOTY = re.compile(r"-?[0-9]+[.,][0-9]{2}")


# --- PROCESOR PACZEK ---
def przetworz_powiadomienie_push(tresc_pusha, uzytkownik_id):
    logging.info(f"📱 Przetwarzanie pusha dla użytkownika ID: {uzytkownik_id}")
    try:
        dzisiejsza_data = datetime.now().strftime("%Y-%m-%d")

        # --- FILTR ANTYSPAMOWY (uniwersalny, dla DOWOLNEJ appki/banku): powiadomienie
        # bez rozpoznawalnej kwoty to prawie na pewno marketing/info, nie transakcja
        # (np. "Odzyskaj nawet do 40 zł zwrotu w sklepie Allegro" - liczba tam jest,
        # ale to reklama, nie płatność - dlatego to tylko pierwsza linia obrony,
        # kwota=0 z AI jest odrzucana osobno niżej).
        if not WZORZEC_KWOTY.search(tresc_pusha):
            logging.info(f"🚫 Zignorowano powiadomienie bez rozpoznawalnej kwoty (prawdopodobnie nie-transakcyjne): {tresc_pusha!r}")
            return

        bezpieczna_tresc_dla_ai = cenzuruj_wrazliwe_dane(tresc_pusha)
        dane_z_ai = analizuj_powiadomienie_przez_ai(bezpieczna_tresc_dla_ai)

        if dane_z_ai and "transakcje" in dane_z_ai:
            lista_transakcji = dane_z_ai["transakcje"]

            for transakcja in lista_transakcji:
                kwota = transakcja.get("kwota")
                sklep = transakcja.get("sklep")
                kategoria_id = transakcja.get("kategoria_id", 12)
                typ_transakcji = transakcja.get("typ", "WYDATEK")

                # Odporne parsowanie kwoty - AI czasem zwraca "13,00" jako string z przecinkiem
                # (bo tak wygląda w oryginalnym tekście powiadomienia), a float() na przecinku
                # rzuca ValueError. Bez tego jedna zła transakcja wywalała WYJĄTKIEM CAŁĄ funkcję
                # (łapany cicho przez zewnętrzny except, bez żadnego śladu poza logiem).
                try:
                    kwota_num = float(str(kwota).replace(',', '.')) if kwota is not None else None
                    if kwota_num is not None:
                        # WAŻNE: wartość bezwzględna - AI (Gemini/Groq) mimo instrukcji w prompcie
                        # ("kwota ZAWSZE DODATNIA") czasem i tak przepisuje minus 1:1 z surowego
                        # tekstu powiadomienia banku ("Kwota: -49,00 USD" -> -49.0). Kierunek
                        # transakcji i tak wyraża wyłącznie pole "typ", więc znak liczby jest
                        # zbędny - a bez normalizacji taka transakcja wpadała w warunek
                        # "kwota_num > 0" poniżej i była CAŁKOWICIE ODRZUCANA zamiast zapisana.
                        kwota_num = abs(kwota_num)
                except (TypeError, ValueError):
                    kwota_num = None

                # Odrzucamy transakcje z zerową/brakującą/niepoprawną kwotą (zmyślone przez AI z niejasnego tekstu)
                if kwota_num is not None and kwota_num > 0 and sklep:
                    # zrodlo jest teraz UNIWERSALNE - zawsze "POWIADOMIENIE", bez rozróżniania
                    # konkretnych appek/banków. Scalanie duplikatów (lepsza nazwa nadpisuje
                    # generyczną) działa teraz przez jakość opisu, nie przez nazwę źródła -
                    # patrz zapisz_lub_scal_transakcje.
                    zapisz_lub_scal_transakcje(uzytkownik_id, kategoria_id, kwota_num, dzisiejsza_data, sklep, typ_transakcji, "POWIADOMIENIE")
                else:
                    logging.warning(f"🚫 Odrzucono transakcję z niewiarygodnymi danymi (kwota={kwota!r}, sklep={sklep!r}): {tresc_pusha!r}")
        else:
            # WAŻNE: to jest przypadek, który wcześniej ginął całkowicie po cichu -
            # zarówno Gemini, jak i zapasowy Groq nie zwróciły użytecznego JSON-a
            # (np. AI się pogubiło, limit, błąd sieci) - HTTP i tak zwracał 200 "success"
            # do telefonu, ale transakcja NIGDY nie trafiała do bazy. Teraz przynajmniej
            # widać to jednoznacznie w finance_app.log.
            logging.error(f"❌ AI (Gemini + zapasowy Groq) nie zwróciło użytecznych danych dla powiadomienia: {tresc_pusha!r}")
    except Exception as e:
        logging.exception(f"💥 Błąd wewnątrz procesora pushy: {e}")


if __name__ == "__main__":
    sciezka_pusha = os.path.join(folder_projektu, 'test_push_notification.txt')
    if os.path.exists(sciezka_pusha):
        with open(sciezka_pusha, 'r', encoding='utf-8') as f:
            surowy_push = f.read().strip()
        # Test ręczny z konsoli - podmień 1 na prawdziwe id użytkownika z bazy jeśli trzeba
        przetworz_powiadomienie_push(surowy_push, 1)
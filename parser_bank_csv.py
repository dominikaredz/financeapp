"""
=====================================================================================
 TYMCZASOWY SKRYPT TESTOWY – parser_bank_csv.py
=====================================================================================
Cel: jednorazowy/testowy import historycznych transakcji z pliku wyciag.csv
     do tej samej bazy danych, z której korzysta strona (finance_db.sqlite).

To NIE jest część głównej aplikacji Flask (app_server.py) ani stałego
przepływu powiadomień push (auto_finance_updater.py) – to osobne narzędzie,
które można uruchomić ręcznie z konsoli bash na PythonAnywhere, np.:

    python3 parser_bank_csv.py

Po zakończeniu testów ten plik można bezpiecznie usunąć – nic innego go nie importuje.

LOGIKA (celowo skopiowana/wspólna z resztą projektu, żeby nie duplikować kodu):
  1. Wczytanie CSV – struktura kolumn jak w starym parser_bank.py
     (Data transakcji, Odbiorca/Zleceniodawca, Opis, Obciążenia, Uznania).
  2. Sprawdzenie słów z config.json (blokowane_slowa) NA OPISIE – pozwala twardo
     przypisać kategorię i pominąć AI dla wpisów, które są rozpoznawane po
     słowach kluczowych (bez cenzurowania - opis trafia do bazy bez zmian).
  3. Transakcje bez twardego dopasowania z config.json i niebędące przelewem
     idą do AI POJEDYNCZO - jedno zapytanie na wiersz, z 3-sekundową pauzą
     po każdym zapytaniu (żeby nie przekroczyć limitu API Groq).
  4. Zapis do bazy przez zapisz_lub_scal_transakcje() z auto_finance_updater –
     ta sama funkcja, której używa obsługa powiadomień push, więc automatycznie
     dostajemy tę samą logikę wykrywania duplikatów (np. gdy ta sama transakcja
     została już wcześniej dodana przez push z telefonu).
=====================================================================================
"""

import os
import csv
import sys
import time
import json
import logging

# WAŻNA KOLEJNOŚĆ: auto_finance_updater MUSI być zaimportowany PRZED ai_categorized.
# To auto_finance_updater woła load_dotenv() i ładuje GROQ_API_KEY z env.txt.
# ai_categorized tworzy klienta Groq już w momencie importu modułu (na poziomie
# top-level: client = Groq(api_key=os.getenv("GROQ_API_KEY"))), więc jeśli
# GROQ_API_KEY nie jest jeszcze w środowisku, import ai_categorized rzuci błąd
# groq.GroqError: "The api_key client option must be set...".
#
# Reużywamy configu i zapisu z głównego modułu – bez przepisywania kodu.
from auto_finance_updater import (
    folder_projektu,
    zapisz_lub_scal_transakcje,
    prywatne_slowa,
    cenzuruj_wrazliwe_dane,
)

from ai_categorized import (
    rozpoznaj_i_zredaguj_pojedynczo_gemini,
    rozpoznaj_i_zredaguj_pojedynczo_groq,
    GeminiKwotaWyczerpana,
)

# Pauza między kolejnymi zapytaniami do Groq, żeby nie nadwerężyć limitu API.
PAUZA_MIEDZY_ZAPYTANIAMI_SEK = 5

# Ten sam słownik "słowo -> kategoria_id" z config.json, którego auto_finance_updater
# używa do cenzury powiadomień push, tutaj wykorzystujemy WYŁĄCZNIE do twardego
# przypisywania kategorii (tak jak robił to stary parser_bank.py) - bez cenzury.
czarna_lista = prywatne_slowa

# Domyślne kategorie zapasowe (spójne z auto_finance_updater: 13 = Przychody, 12 = Inne)
KATEGORIA_PRZYCHOD_DOMYSLNA = 13
KATEGORIA_WYDATEK_DOMYSLNA = 12

ZRODLO_IMPORTU = "CSV"


def wczytaj_csv(sciezka):
    """Wczytuje i wstępnie przetwarza wiersze z wyciągu bankowego CSV."""
    transakcje_do_zapisu = []
    opisy_dla_ai = []

    if not os.path.exists(sciezka):
        logging.error(f"❌ Nie znaleziono pliku CSV: {sciezka}")
        return transakcje_do_zapisu, opisy_dla_ai

    with open(sciezka, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file, delimiter=',')

        for row in reader:
            try:
                data = row['Data transakcji']
                odbiorca = row['Odbiorca/Zleceniodawca'].strip()
                opis_transakcji = row['Opis'].strip()
                pelny_opis_surowy = f"{odbiorca} - {opis_transakcji}" if odbiorca else opis_transakcji

                obciazenie = row['Obciążenia'].strip()
                uznanie = row['Uznania'].strip()

                if obciazenie:
                    kwota_raw = obciazenie
                    typ = 'WYDATEK'
                elif uznanie:
                    kwota_raw = uznanie
                    typ = 'PRZYCHOD'
                else:
                    continue

                kwota = abs(float(kwota_raw.replace(' ', '')))

                # Czy to przelew / przychód -> traktujemy jako potencjalnie prywatne
                czy_to_przelew = (
                    "PRZELEW" in pelny_opis_surowy.upper()
                    or "TRANSFER" in pelny_opis_surowy.upper()
                    or typ == 'PRZYCHOD'
                )

                # --- 1. Sprawdzenie config.json - twarde dopasowanie kategorii ---
                wykryte_id_z_pliku = None
                for slowo, kat_id in czarna_lista.items():
                    if slowo.upper() in pelny_opis_surowy.upper():
                        wykryte_id_z_pliku = kat_id
                        break

                # --- 2. Decyzja: AI, czy pomijamy AI ---
                # WERSJA "ZERO AI DLA PRZELEWÓW": przelewy/przychody bez twardego
                # dopasowania w config.json W OGÓLE nie dostają żadnego fragmentu
                # oryginalnego tekstu - ani przez AI, ani przez regexy próbujące
                # "sprytnie" wyciąć adres (te zawsze coś przeoczą). Zamiast tego
                # dostają czysto generyczną etykietę zależną WYŁĄCZNIE od kierunku
                # transakcji. To najsilniejsza gwarancja prywatności - nic z pola
                # Opis/Odbiorca nigdy nie trafia nigdzie dalej niż ta funkcja,
                # kosztem utraty informacji "kto/za co" dla takich wierszy.
                indeks_ai = None
                opis_generyczny_przelewu = None

                if czy_to_przelew and wykryte_id_z_pliku is None:
                    opis_generyczny_przelewu = "Przelew przychodzący" if typ == 'PRZYCHOD' else "Przelew wychodzący"
                elif wykryte_id_z_pliku is None:
                    # Zwykłe zakupy (nie-przelewy) nadal idą przez AI - tu ryzyko
                    # adresu/nazwiska jest dużo mniejsze (to opisy sklepów, nie osób).
                    opisy_dla_ai.append(pelny_opis_surowy)
                    indeks_ai = len(opisy_dla_ai) - 1

                transakcje_do_zapisu.append({
                    'kwota': kwota,
                    'data': data,
                    'opis': pelny_opis_surowy,
                    'typ': typ,
                    'czy_to_przelew': czy_to_przelew,
                    'indeks_ai': indeks_ai,
                    'sztywne_id': wykryte_id_z_pliku,
                    'opis_generyczny_przelewu': opis_generyczny_przelewu,
                })
            except Exception as e:
                logging.warning(f"⚠️ Pominięto wiersz CSV z powodu błędu parsowania: {e}")
                continue

    return transakcje_do_zapisu, opisy_dla_ai


def ustal_kategorie(transakcja, słownik_wynikow_z_ai):
    """Ustala finalne (kategoria_id, opis_do_zapisu) dla pojedynczej transakcji, wg priorytetu:
    1) przelew bez dopasowania -> generyczna etykieta, ZERO AI/regexów (patrz komentarz wyżej),
    2) trafienie w AI (jeśli wysłana) - AI zwraca już zredagowany krótki opis,
    3) sztywne ID z config.json - opis cenzurowany, ale niezredagowany do 1-2 słów,
    4) domyślna kategoria zależna od typu (rzadki przypadek zapasowy)."""
    if transakcja.get('opis_generyczny_przelewu'):
        domyslna = KATEGORIA_PRZYCHOD_DOMYSLNA if transakcja['typ'] == 'PRZYCHOD' else KATEGORIA_WYDATEK_DOMYSLNA
        return domyslna, transakcja['opis_generyczny_przelewu']

    if transakcja['indeks_ai'] is not None and transakcja['indeks_ai'] in słownik_wynikow_z_ai:
        wynik = słownik_wynikow_z_ai[transakcja['indeks_ai']]
        return wynik['kategoria_id'], wynik['opis_krotki']

    if transakcja['sztywne_id'] is not None:
        return transakcja['sztywne_id'], cenzuruj_wrazliwe_dane(transakcja['opis'])

    domyslna = KATEGORIA_PRZYCHOD_DOMYSLNA if transakcja['typ'] == 'PRZYCHOD' else KATEGORIA_WYDATEK_DOMYSLNA
    return domyslna, cenzuruj_wrazliwe_dane(transakcja['opis'])


def kategoryzuj_z_fallbackiem(opis, stan_ai):
    """Próbuje Gemini; jeśli limit wyczerpany (GeminiKwotaWyczerpana) - TRWALE przełącza
    `stan_ai['na_groq']` na True, więc WSZYSTKIE kolejne wiersze w tym imporcie
    pójdą już od razu na Groq (bez ponownego, bezcelowego próbowania Gemini).
    Inne, jednorazowe błędy Gemini (np. chwilowy problem sieciowy) powodują tylko
    fallback na Groq DLA TEGO JEDNEGO wiersza, bez trwałego przełączenia."""
    if not stan_ai['na_groq']:
        try:
            return rozpoznaj_i_zredaguj_pojedynczo_gemini(opis)
        except GeminiKwotaWyczerpana as e:
            logging.warning(
                f"⚠️ Gemini: limit wyczerpany ({e}). Przełączam WSZYSTKIE pozostałe "
                f"wiersze tego importu na Groq."
            )
            stan_ai['na_groq'] = True
        except Exception as e:
            logging.warning(f"⚠️ Gemini zawiódł dla tego wiersza ({e}) - jednorazowy fallback na Groq.")
            return rozpoznaj_i_zredaguj_pojedynczo_groq(opis)

    return rozpoznaj_i_zredaguj_pojedynczo_groq(opis)


def zapisz_status_importu(uzytkownik_id, stan, przetworzone=0, razem=0, komunikat=None):
    """Zapisuje status trwającego importu CSV do pliku JSON per użytkownik, żeby panel
    webowy mógł go odpytywać (polling z przeglądarki) bez trzymania requestu HTTP
    otwartego przez cały czas importu - to właśnie powodowało zawieszanie się strony
    i wywalanie po limicie czasu serwera na PythonAnywhere."""
    sciezka_statusu = os.path.join(folder_projektu, f"import_status_{uzytkownik_id}.json")
    try:
        with open(sciezka_statusu, 'w', encoding='utf-8') as f:
            json.dump({
                "stan": stan,  # "w_toku" | "gotowe" | "blad"
                "przetworzone": przetworzone,
                "razem": razem,
                "komunikat": komunikat,
            }, f)
    except Exception as e:
        logging.error(f"❌ Nie udało się zapisać statusu importu: {e}")


def importuj_plik_csv(sciezka, uzytkownik_id):
    """Importuje wyciąg CSV DLA KONKRETNEGO UŻYTKOWNIKA. Wywoływane zarówno z panelu
    webowego (upload pliku, URUCHAMIANE W OSOBNYM WĄTKU - patrz app_server.py) jak
    i ręcznie z konsoli (patrz blok __main__ niżej). Zwraca liczbę zaimportowanych wierszy."""
    logging.info(f"📄 Rozpoczynam import z pliku CSV: {sciezka} (uzytkownik_id={uzytkownik_id})")

    try:
        transakcje_do_zapisu, opisy_dla_ai = wczytaj_csv(sciezka)

        if not transakcje_do_zapisu:
            logging.warning("⚠️ Brak transakcji do zaimportowania (pusty plik lub błąd odczytu).")
            zapisz_status_importu(uzytkownik_id, "blad", komunikat="Pusty plik lub błąd odczytu CSV.")
            return 0

        # --- Osobne zapytanie do AI dla KAŻDEGO wiersza z osobna, z pauzą między nimi.
        #     Domyślnie Gemini (lepsza redakcja danych osobowych); jeśli wyczerpie limit,
        #     WSZYSTKIE pozostałe wiersze automatycznie lecą na Groq (patrz kategoryzuj_z_fallbackiem). ---
        stan_ai = {'na_groq': False}
        słownik_wynikow = {}
        zapisz_status_importu(uzytkownik_id, "w_toku", przetworzone=0, razem=len(opisy_dla_ai))

        if opisy_dla_ai:
            logging.info(
                f"🚀 Kategoryzuję {len(opisy_dla_ai)} opisów (Gemini, z automatycznym fallbackiem "
                f"na Groq po wyczerpaniu limitu), pauza {PAUZA_MIEDZY_ZAPYTANIAMI_SEK}s po każdym..."
            )
            for i, opis in enumerate(opisy_dla_ai):
                wynik = kategoryzuj_z_fallbackiem(opis, stan_ai)
                słownik_wynikow[i] = wynik
                zapisz_status_importu(uzytkownik_id, "w_toku", przetworzone=i + 1, razem=len(opisy_dla_ai))
                time.sleep(PAUZA_MIEDZY_ZAPYTANIAMI_SEK)

        # --- Zapis do bazy przez wspólną funkcję z deduplikacją, DLA TEGO uzytkownik_id ---
        licznik_dodanych = 0
        for t in transakcje_do_zapisu:
            kategoria_id, opis_do_zapisu = ustal_kategorie(t, słownik_wynikow)
            zapisz_lub_scal_transakcje(
                uzytkownik_id,
                kategoria_id,
                t['kwota'],
                t['data'],
                opis_do_zapisu,
                t['typ'],
                ZRODLO_IMPORTU,
            )
            licznik_dodanych += 1

        zapisz_status_importu(uzytkownik_id, "gotowe", przetworzone=licznik_dodanych, razem=licznik_dodanych)
        logging.info(f"✅ Zakończono import. Przetworzono {licznik_dodanych} wierszy z CSV.")
        return licznik_dodanych

    except Exception as e:
        # KRYTYCZNE: import działa teraz w tle (osobny wątek), więc bez tego except
        # błąd zniknąłby bez śladu - użytkownik zobaczyłby tylko wiszący "w toku" na zawsze.
        logging.exception(f"💥 Błąd podczas importu CSV: {e}")
        zapisz_status_importu(uzytkownik_id, "blad", komunikat=str(e))
        return 0


if __name__ == "__main__":
    # Użycie ręczne z konsoli: python3 parser_bank_csv.py <sciezka_do_csv> <id_uzytkownika>
    # ID użytkownika sprawdzisz w bazie: sqlite3 finance_db.sqlite "SELECT id, imie, email FROM uzytkownicy;"
    if len(sys.argv) < 3:
        print("Użycie: python3 parser_bank_csv.py <sciezka_do_csv> <id_uzytkownika>")
        print("ID użytkownika sprawdzisz: sqlite3 finance_db.sqlite \"SELECT id, imie, email FROM uzytkownicy;\"")
        sys.exit(1)

    sciezka_arg = sys.argv[1]
    uzytkownik_id_arg = int(sys.argv[2])
    importuj_plik_csv(sciezka_arg, uzytkownik_id_arg)
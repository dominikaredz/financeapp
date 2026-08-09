import os
import json
import logging
from groq import Groq
from google import genai
from google.genai import types

# Klient Groq - używany do MASOWEGO importu CSV (rozpoznaj_kategorie_pojedynczo/zbiorczo),
# gdzie liczy się przepustowość/koszt przy wielu wierszach naraz.
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Klient Gemini - używany do CODZIENNYCH pojedynczych powiadomień push,
# gdzie liczy się jakość (lepsza redakcja danych osobowych) a wolumen jest niewielki,
# więc koszt praktycznie nie istnieje nawet na płatnym tierze.
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# UWAGA: "gemini-2.5-pro" ma w API limit:0 na darmowym tierze (wymaga włączonego billingu
# na projekcie Google Cloud - patrz aistudio.google.com). Dopóki nie włączysz billingu,
# zostajemy na Flash, który ma darmowy tier i w zupełności wystarcza do tego zadania
# (krótki tekst -> kategoria + 1-2 słowa opisu, bez potrzeby głębokiego rozumowania).
GEMINI_MODEL = "gemini-2.5-flash"

# --- SZYBKIE MAPOWANIE PYTHON (BEZ AI) ---
# Jeśli jakakolwiek transakcja zawiera poniższe słowa, przypiszemy kategorię natychmiast,
# oszczędzając czas i zapytania do Groq.
SZTYWNE_MAPOWANIE_DLA_AI = {
    "JOYFUL": 1,  # Jedzenie
    "SLIMAK": 1,  # Jedzenie

    # --- Opłaty i prowizje bankowe -> kategoria 4 (Bank) ---
    # Dodane po tym, jak AI (mały model 8B) systematycznie mylił te opisy
    # z kategorią 8 (Leki), przez zbyt mocno sformułowaną regułę "MED".
    "OPŁATA MIESIĘCZNA ZA OBSLUGĘ KARTY": 4,
    "OPŁATA MIESIĘCZNA ZA OBSŁUGĘ KARTY": 4,
    "PAKIET BEZPIECZEŃSTWA": 4,
    "OPŁATA ZA PROWADZENIE": 4,
    "PROWIZJA": 4,
    "OBSŁUGĘ KARTY": 4,

    # --- Stacje paliw -> kategoria 6 (Transport) ---
    # AI miał regułę na to, ale nie stosował jej konsekwentnie - wymuszamy w Pythonie.
    "HYPEROIL": 6,
    "ORLEN": 6,
    "CIRCLE K": 6,
    "SHELL": 6,
    "MOYA": 6,
    "AMIC": 6,
    "LOTOS": 6,

    "ZDROFIT": 3,
    "AUTOMATY": 1,

    # Tutaj możesz dopisywać kolejne uparte słowa kluczowe w przyszłości!
}

LISTA_KATEGORII = """
    1 - Jedzenie (sklepy spożywcze, supermarkety, restauracje, Żabka, Biedronka, Lidl, Kaufland, UberEats, kawiarnie)
    2 - Chemia gospodarcza (środki czystości, zakupy do domu, proszki itp.)
    3 - Subskrypcje (Netflix, Spotify, YouTube Premium, aplikacje, iCloud)
    4 - Bank (opłaty za prowadzenie konta, prowizje, odsetki)
    5 - Kosmetyki (Rossmann, Hebe, Sephora, drogerie, kosmetyczka, fryzjer)
    6 - Transport (paliwo, bilety komunikacji miejskiej, Uber, Bolt, PKP, taksówki, parkingi)
    7 - Rachunki (czynsz, prąd, gaz, internet, telephone)
    8 - Leki (apteki, lekarze, badania, medycyna)
    9 - Mieszkanie (meble, wyposażenie wnętrz, remont, IKEA, Castorama)
    10 - Ubrania (odzież, obuwie, sklepy sieciowe z ubraniami)
    11 - Rozrywka/Wyjazdy (kino, teatr, hotele, loty, wakacje, imprezy)
    12 - Inne (wydatki, które nie pasują do żadnej z powyższych kategorii ORAZ ogólne przelewy przychodzące)
"""


def analizuj_powiadomienie_przez_ai(tresc_pusha):
    """Przetwarzanie powiadomień push z telefonu.
    GŁÓWNA ŚCIEŻKA: Gemini (mocniejszy model) - kategoryzuje ORAZ redaguje opis do
    1-2 słów kluczowych, żeby do bazy nie trafiały pełne, surowe dane z banku.
    ZAPASOWA ŚCIEŻKA: jeśli Gemini zawiedzie (limit, sieć, błąd), wraca do starej
    logiki na Groq (bez redakcji opisu, ale przynajmniej transakcja się zapisze)."""

    # 1. Sprawdzenie szybkiego nadpisania w Pythonie (bez wysyłania czegokolwiek do AI)
    for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
        if slowo.upper() in tresc_pusha.upper():
            logging.info(f"🎯 Szybkie dopasowanie (PUSH): '{tresc_pusha}' zawiera '{slowo}' -> Kategoria {kat_id}")
            break

    prompt = f"""
    Jesteś precyzyjnym parserem danych finansowych, który jednocześnie dba o prywatność użytkownika.
    Twoim zadaniem jest przeanalizować powiadomienie push z banku/portfela płatniczego, wyciągnąć z niego
    dane transakcji ORAZ nadać jej krótki, oczyszczony opis do zapisania w bazie danych.

    Oto powiadomienie do analizy:
    "{tresc_pusha}"

    Musisz zwrócić wyłącznie JEDEN obiekt JSON zawierający klucz "transakcje", który jest listą obiektów.
    Każdy obiekt na liście musi zawierać klucze:
    - "kwota": liczba zmiennoprzecinkowa (np. 46.33)
    - "sklep": KRÓTKI (1-2 słowa) oczyszczony opis, który trafi do bazy danych. Zasady redakcji:
        * Jeśli to płatność w sklepie/firmie -> podaj samą nazwę marki, bez numerów sklepu, miast,
          kodów pocztowych, skrótów prawnych (SP. Z O.O., S.A.) (np. "Kaufland", "Żabka", "Orlen").
        * Jeśli w treści pojawia się IMIĘ I NAZWISKO osoby prywatnej (np. przelew od/do konkretnej
          osoby) -> podaj WYŁĄCZNIE IMIĘ, bez nazwiska i bez numeru konta.
        * ADRES (ulica, numer domu/mieszkania, miasto, kod pocztowy) -> NIGDY nie umieszczaj
          adresu w "sklep". Użyj ogólnego kontekstu (np. "Czynsz", "Najem"), a jeśli nic więcej
          nie wiadomo -> "Przelew".
        * NIGDY nie przepisuj surowego, pełnego opisu 1:1 - zawsze skróć do sedna (1-2 słowa).
        * Jeśli nie da się ustalić nic konkretnego, użyj ogólnego określenia typu "Przelew" lub "Płatność".
    - "kategoria_id": liczba całkowita reprezentująca ID kategorii dobraną na podstawie listy poniżej.
    - "typ": tekst, który musi przyjąć wyłącznie wartość "WYDATEK" lub "PRZYCHOD".

    Wybierz odpowiednie "kategoria_id" STRICTE na podstawie poniższej listy:
    {LISTA_KATEGORII}

    Zwróć wyłącznie sam surowy JSON, bez żadnego formatowania markdown, bez wstępów i podsumowań.

    Przykład struktury odpowiedzi:
    {{"transakcje": [{{"kwota": 46.33, "sklep": "Kaufland", "kategoria_id": 1, "typ": "WYDATEK"}}]}}
    """

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )

        dane_transakcji = json.loads(response.text.strip())

        # Zabezpieczenie: jeśli AI mimo wszystko pomyliło kategorię dla naszych pewniaków:
        if "transakcje" in dane_transakcji:
            for t in dane_transakcji["transakcje"]:
                for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
                    if slowo.upper() in str(t.get("sklep")).upper() or slowo.upper() in tresc_pusha.upper():
                        t["kategoria_id"] = kat_id

        logging.info(f"✨ [GEMINI] Skategoryzowano i zredagowano: {dane_transakcji}")
        return dane_transakcji

    except Exception as e:
        logging.warning(f"⚠️ Gemini zawiódł ({e}) - przełączam na zapasową ścieżkę Groq (bez redakcji opisu).")
        return _analizuj_powiadomienie_przez_ai_groq_fallback(tresc_pusha)


def _analizuj_powiadomienie_przez_ai_groq_fallback(tresc_pusha):
    """Wersja zapasowa (Groq) - używana WYŁĄCZNIE, gdy wywołanie do Gemini się nie powiedzie
    (np. limit, brak sieci). Logika identyczna jak wcześniej, bez redakcji opisu."""

    # 1. Sprawdzenie szybkiego nadpisania w Pythonie
    for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
        if slowo.upper() in tresc_pusha.upper():
            logging.info(f"🎯 Szybkie dopasowanie (PUSH): '{tresc_pusha}' zawiera '{slowo}' -> Kategoria {kat_id}")
            # Zwracamy sztuczną strukturę JSON, którą auto_finance_updater bez problemu zrozumie
            # Musimy wyciągnąć też orientacyjną kwotę ze strumienia, ale w pushach zajmuje się tym regex w updaterze.
            # Zwróćmy pustą strukturę, by updater wiedział, żeby szukać w tekście.
            # Dla bezpieczeństwa pozwólmy AI sparsować kwotę, ale wymuśmy kategorię w prompcie (zrobione niżej).
            break

    prompt = f"""
    Jesteś precyzyjnym parserem danych finansowych. Twoim zadaniem jest przeanalizować listę powiadomień push z banku/portfela płatniczego i wyciągnąć z nich dane.

    Oto powiadomienia do analizy:
    "{tresc_pusha}"

    Musisz zwrócić wyłącznie JEDEN obiekt JSON zawierający klucz "transakcje", który jest listą obiektów.
    Każdy obiekt na liście musi zawierać klucze:
    - "kwota": liczba zmiennoprzecinkowa (np. 46.33)
    - "sklep": KRÓTKI (1-2 słowa) oczyszczony opis do zapisania w bazie. Zasady redakcji:
        * sklep/firma -> sama oczyszczona nazwa marki, bez numerów/miast/kodów (np. "Kaufland", "Żabka")
        * imię i nazwisko osoby prywatnej -> WYŁĄCZNIE imię, bez nazwiska
        * ADRES (ulica, numer domu/mieszkania, miasto, kod pocztowy) -> NIGDY nie umieszczaj
          adresu w "sklep". Użyj ogólnego kontekstu (np. "Czynsz", "Najem"), a jeśli nic więcej
          nie wiadomo -> "Przelew"
    - "kategoria_id": liczba całkowita reprezentująca ID kategorii dobraną na podstawie bazy danych.
    - "typ": tekst, który musi przyjąć wyłącznie wartość "WYDATEK" lub "PRZYCHOD".

    Wybierz odpowiednie "kategoria_id" STRICTE na podstawie poniższej listy:
    {LISTA_KATEGORII}

    Zwróć wyłącznie sam surowy JSON, bez żadnego formatowania markdown (bez ```json), bez wstępów i podsumowań.

    Przykład struktury odpowiedzi:
    {{"transakcje": [{{"kwota": 46.33, "sklep": "Kaufland", "kategoria_id": 1, "typ": "WYDATEK"}}]}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0,
            response_format={"type": "json_object"}
        )

        odpowiedz_tekst = chat_completion.choices[0].message.content.strip()
        if odpowiedz_tekst.startswith("```"):
            odpowiedz_tekst = odpowiedz_tekst.split("```")[1]
            if odpowiedz_tekst.startswith("json"):
                odpowiedz_tekst = odpowiedz_tekst[4:]

        dane_transakcji = json.loads(odpowiedz_tekst.strip())

        # Drugie zabezpieczenie: Jeśli AI mimo wszystko się pomyliło w pushu dla tych słów:
        if "transakcje" in dane_transakcji:
            for t in dane_transakcji["transakcje"]:
                for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
                    if slowo.upper() in str(t.get("sklep")).upper() or slowo.upper() in tresc_pusha.upper():
                        t["kategoria_id"] = kat_id

        return dane_transakcji
    except Exception as e:
        logging.error(f"❌ Błąd komunikacji z Groq AI lub parsowania JSON: {e}")
        return None


class GeminiKwotaWyczerpana(Exception):
    """Rzucany, gdy Gemini zwróci 429 RESOURCE_EXHAUSTED (limit dzienny/minutowy).
    To sygnał dla parser_bank_csv.py, żeby TRWALE przełączyć się na Groq dla
    WSZYSTKICH pozostałych wierszy w tym imporcie (nie próbować Gemini ponownie)."""
    pass


def rozpoznaj_i_zredaguj_pojedynczo_gemini(opis):
    """Kategoryzuje ORAZ redaguje JEDEN opis transakcji z CSV przez Gemini Flash.
    Zwraca dict {"kategoria_id": int, "opis_krotki": str}.
    Rzuca GeminiKwotaWyczerpana przy limicie (429), zwykły Exception przy innych błędach."""

    prompt = f"""
    Jesteś ekspertem ds. analizy danych finansowych, który jednocześnie dba o prywatność użytkownika.
    Przeanalizuj poniższy surowy opis transakcji bankowej z wyciągu CSV.

    Opis transakcji: "{opis}"

    Zignoruj szum: nazwy miast (np. BIALYSTOK), kraje (POL), daty, numery terminali,
    skróty prawne ("SP. Z O.O.", "S.A.").

    Zwróć wyłącznie JEDEN obiekt JSON z kluczami:
    - "kategoria_id": liczba całkowita 1-12 wg listy kategorii poniżej.
    - "opis_krotki": KRÓTKI (1-2 słowa) oczyszczony opis do zapisania w bazie danych:
        * sklep/firma -> sama nazwa marki, bez numerów sklepu/miast/kodów prawnych (np. "Kaufland", "Orlen")
        * imię i nazwisko osoby prywatnej -> WYŁĄCZNIE imię, bez nazwiska
        * ADRES (ulica, numer domu/mieszkania, miasto, kod pocztowy) -> NIGDY nie umieszczaj
          adresu w opis_krotki. Zamiast tego użyj ogólnego kontekstu transakcji, jeśli się da
          go ustalić (np. "Czynsz", "Najem", "Rachunek"), a jeśli nic więcej nie wiadomo -> "Przelew"
        * NIGDY nie przepisuj surowego opisu 1:1 - zawsze skróć do sedna

    Reguły kategoryzacji (RÓWNY priorytet - żadna nie jest ważniejsza od innej):
    - Apteki, przychodnie, lekarze (APTEKA, LEKARZ, GROFARM, ZDROWIE, PRZYCHODNIA) -> 8 (Leki)
    - Stacje paliw (OIL, HYPEROIL, SHELL, ORLEN, BP, CIRCLE K, MOYA) -> 6 (Transport)
    - Sklepy spożywcze, gastronomia (KAUFLAND, BIEDRONKA, LIDL) -> 1 (Jedzenie)
    - Opłaty bankowe (obsługa karty, prowadzenie konta, prowizje, pakiety) -> 4 (Bank), NIGDY 8
    - Automaty bez jasnego kontekstu -> 12 (Inne), nie zgaduj 8 tylko bo brzmi "medycznie"

    --- DOSTĘPNE KATEGORIE ---
    {LISTA_KATEGORII}

    Zwróć wyłącznie surowy JSON, bez markdown, bez wstępów.
    Przykład: {{"kategoria_id": 1, "opis_krotki": "Kaufland"}}
    """

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        dane = json.loads(response.text.strip())

        for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
            if slowo.upper() in opis.upper():
                dane["kategoria_id"] = kat_id

        wynik = {
            "kategoria_id": int(dane.get("kategoria_id", 12)),
            "opis_krotki": str(dane.get("opis_krotki", "Inne"))[:60],
        }
        logging.info(f"✨ [GEMINI/CSV] '{opis}' -> {wynik}")
        return wynik

    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            raise GeminiKwotaWyczerpana(str(e))
        raise


def rozpoznaj_i_zredaguj_pojedynczo_groq(opis):
    """Wersja zapasowa (Groq) - kategoryzuje ORAZ redaguje opis. Używana gdy Gemini
    wyczerpał limit albo trwale przełączono się na Groq dla reszty importu CSV."""

    for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
        if slowo.upper() in opis.upper():
            logging.info(f"🎯 [PYTHON OVERRIDE] Opis '{opis}' zawiera '{slowo}' -> kategoria {kat_id} (bez AI)")
            return {"kategoria_id": kat_id, "opis_krotki": opis[:60]}

    prompt = f"""
    Jesteś ekspertem ds. analizy danych finansowych, który jednocześnie dba o prywatność użytkownika.
    Przeanalizuj poniższy surowy opis transakcji bankowej z wyciągu CSV.

    Opis transakcji: "{opis}"

    Zignoruj szum: nazwy miast, kraje (POL), daty, numery terminali, skróty prawne ("SP. Z O.O.", "S.A.").

    Zwróć wyłącznie JEDEN obiekt JSON z kluczami:
    - "kategoria_id": liczba całkowita 1-12 wg listy poniżej.
    - "opis_krotki": KRÓTKI (1-2 słowa) oczyszczony opis do zapisania w bazie:
        * sklep/firma -> sama nazwa marki, bez numerów/miast/kodów prawnych
        * imię i nazwisko osoby prywatnej -> WYŁĄCZNIE imię, bez nazwiska
        * ADRES (ulica, numer domu/mieszkania, miasto, kod pocztowy) -> NIGDY nie umieszczaj
          adresu w opis_krotki. Użyj ogólnego kontekstu (np. "Czynsz", "Najem"), a jeśli
          nic więcej nie wiadomo -> "Przelew"
        * NIGDY nie przepisuj surowego opisu 1:1

    Reguły kategoryzacji (RÓWNY priorytet):
    - Apteki, przychodnie, lekarze (APTEKA, LEKARZ, GROFARM, ZDROWIE, PRZYCHODNIA) -> 8 (Leki)
    - Stacje paliw (OIL, HYPEROIL, SHELL, ORLEN, BP, CIRCLE K, MOYA) -> 6 (Transport)
    - Sklepy spożywcze, gastronomia (KAUFLAND, BIEDRONKA, LIDL) -> 1 (Jedzenie)
    - Opłaty bankowe (obsługa karty, prowadzenie konta, prowizje, pakiety) -> 4 (Bank), NIGDY 8
    - Automaty bez jasnego kontekstu -> 12 (Inne), nie zgaduj 8

    {LISTA_KATEGORII}

    Zwróć wyłącznie surowy JSON, bez markdown.
    Przykład: {{"kategoria_id": 1, "opis_krotki": "Kaufland"}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0,
            response_format={"type": "json_object"}
        )
        dane = json.loads(chat_completion.choices[0].message.content.strip())
        wynik = {
            "kategoria_id": int(dane.get("kategoria_id", 12)),
            "opis_krotki": str(dane.get("opis_krotki", opis))[:60],
        }
        logging.info(f"🔎 [GROQ/CSV] '{opis}' -> {wynik}")
        return wynik
    except Exception as e:
        logging.error(f"❌ Błąd Groq dla opisu {opis!r}: {e}")
        return {"kategoria_id": 12, "opis_krotki": opis[:60]}


def rozpoznaj_kategorie_pojedynczo(opis):
    """Kategoryzuje JEDEN opis transakcji z wyciągu CSV (z filtrem słów kluczowych).
    UWAGA: zachowana dla wstecznej kompatybilności - nowy kod (parser_bank_csv.py)
    używa rozpoznaj_i_zredaguj_pojedynczo_gemini / _groq zamiast tej funkcji."""

    # 1. Szybka ścieżka w Pythonie - jeśli słowo jest na naszej liście, nie pytamy AI!
    for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
        if slowo.upper() in opis.upper():
            logging.info(f"🎯 [PYTHON OVERRIDE] Opis '{opis}' zawiera '{slowo}' -> Automatycznie kategoria {kat_id} (Bez AI)")
            return kat_id

    prompt = f"""
    Jesteś ekspertem ds. analizy danych finansowych i księgowości. Twoim zadaniem jest przeanalizować surowy opis transakcji bankowej z wyciągu i przypisać mu idealne ID kategorii.

    Opis transakcji do analizy: "{opis}"

    --- INSTRUKCJA ANALIZY SEMANTYCZNEJ ORAZ RESEARCHU ---
    Opisy z wyciągów CSV zawierają mnóstwo szumu: nazwy miast (np. BIALYSTOK), kraje (POL), daty, numery terminali czy skróty prawne ("SP. Z O.O.", "S.A.").
    Zignoruj ten szum i skup się na profilu działalności podmiotu. Poniższe reguły mają RÓWNY priorytet - żadna nie jest ważniejsza od innej:

    - Apteki, przychodnie, lekarze (słowa: APTEKA, LEKARZ, GROFARM, ZDROWIE, PRZYCHODNIA) -> kategoria 8 (Leki).
    - Stacje paliw, paliwo (słowa: OIL, HYPEROIL, SHELL, ORLEN, BP, CIRCLE K, MOYA) -> kategoria 6 (Transport).
    - Sklepy spożywcze, supermarkety, gastronomia (KAUFLAND, BIEDRONKA, LIDL, restauracje, kawiarnie) -> kategoria 1 (Jedzenie).
    - Opłaty bankowe: obsługa karty, prowadzenie konta, prowizje, pakiety ubezpieczeniowe do karty -> kategoria 4 (Bank). UWAGA: to NIE jest kategoria 8, mimo że w opisie może pojawić się skrót "S.A." czy nazwa banku.
    - Automaty vendingowe (np. słowo AUTOMAT/AUTOMATY bez kontekstu apteki) -> jeśli nie wiadomo co sprzedają, wybierz kategorię 12 (Inne), NIE zgaduj kategorii 8 tylko dlatego, że nazwa brzmi medycznie.

    WAŻNE: Skrót "MED" sam w sobie NIE wystarcza do przypisania kategorii 8 - musi być częścią słowa wyraźnie związanego z medycyną/apteką (np. "MEDICOVER", "APTEKA"), a nie przypadkowym fragmentem innej nazwy. Jeśli nie masz pewności, wybierz kategorię 12 (Inne) zamiast zgadywać 8.

    --- DOSTĘPNE KATEGORIE ORAZ ICH ID ---
    {LISTA_KATEGORII}

    Musisz zwrócić wyłącznie JEDEN obiekt JSON w formacie:
    {{"kategoria_id": liczba_całkowita}}

    Zasady:
    - Wartość MUSI być liczbą całkowitą od 1 do 12. Jeśli nie masz pojęcia co to za firma, wybierz 12 (Inne).
    - Zwróć wyłącznie sam surowy JSON, bez żadnego formatowania markdown (bez ```json), bez wstępów i podsumowań.

    Przykład struktury odpowiedzi:
    {{"kategoria_id": 8}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0,
            response_format={"type": "json_object"}
        )

        odpowiedz_tekst = chat_completion.choices[0].message.content.strip()
        logging.info(f"🔎 DEBUG (pojedyncze): '{opis}' -> {odpowiedz_tekst}")

        if odpowiedz_tekst.startswith("```"):
            odpowiedz_tekst = odpowiedz_tekst.split("```")[1]
            if odpowiedz_tekst.startswith("json"):
                odpowiedz_tekst = odpowiedz_tekst[4:]

        dane = json.loads(odpowiedz_tekst.strip())
        return int(dane.get("kategoria_id", 12))
    except Exception as e:
        logging.error(f"❌ Błąd komunikacji z Groq AI dla opisu {opis!r}: {e}")
        return 12


def rozpoznaj_kategorie_zbiorczo(lista_opisow):
    """Kategoryzuje wiele opisów w jednym zapytaniu (z zapasowym filtrem)."""
    if not lista_opisow:
        return {}

    opisy_ponumerowane = "\n".join(f"{i}: {opis}" for i, opis in enumerate(lista_opisow))

    prompt = f"""
    Jesteś precyzyjnym parserem danych finansowych. Poniżej znajduje się ponumerowana lista opisów transakcji bankowych.
    {opisy_ponumerowane}

    Dla KAŻDEGO indeksu przypisz kategoria_id na podstawie poniższej listy:
    {LISTA_KATEGORII}

    Musisz zwrócić wyłącznie JEDEN obiekt JSON w formacie:
    {{"kategorie": {{"0": kategoria_id, "1": kategoria_id}}}}
    """
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0,
            response_format={"type": "json_object"}
        )
        dane = json.loads(chat_completion.choices[0].message.content.strip())
        kategorie_surowe = dane.get("kategorie", {})

        wynik = {}
        for indeks_tekst, kat_id_surowe in kategorie_surowe.items():
            idx = int(indeks_tekst)
            wynik[idx] = int(kat_id_surowe)

            # Zabezpieczenie przed błędami zbiorczymi dla naszych słów
            for slowo, kat_id in SZTYWNE_MAPOWANIE_DLA_AI.items():
                if slowo.upper() in lista_opisow[idx].upper():
                    wynik[idx] = kat_id
        return wynik
    except Exception as e:
        logging.error(f"❌ Błąd zapytania zbiorczego: {e}")
        return {}

def analizuj_eparagon_kaufland(tekst_paragonu=None, obraz_bytes=None, obraz_mime_type="image/jpeg"):
    """Parsuje e-paragon z aplikacji Kaufland przez Gemini Flash.
    Wyciąga pojedyncze pozycje zakupowe, kwoty, dopasowuje kategorie oraz wyciąga datę zakupu.

    Przyjmuje ALBO obraz (zdjęcie/screenshot paragonu - obraz_bytes + obraz_mime_type),
    ALBO wklejony tekst (tekst_paragonu, zachowane dla wstecznej zgodności/API).
    Gemini ma wbudowane rozpoznawanie obrazu, więc dla screena po prostu "patrzy"
    na zdjęcie zamiast analizować wyekstrahowany tekst - nie potrzeba żadnego OCR."""

    instrukcja = f"""
    Jesteś precyzyjnym skanerem paragonów zakupowych sieci Kaufland.
    Twoim zadaniem jest przeanalizować {"załączone zdjęcie/screenshot" if obraz_bytes else "wklejony tekst"}
    e-paragonu, wyodrębnić z niego wszystkie zakupione pozycje, ich rzeczywiste ceny,
    przypisać im odpowiednie kategoria_id oraz WYCIĄGNĄĆ DATĘ TRANSAKCJI.
    {f'Oto tekst paragonu do analizy: "{tekst_paragonu}"' if tekst_paragonu else ""}

    WAŻNE przy czytaniu ze zdjęcia: zignoruj wszystko, co NIE jest częścią paragonu (pasek
    powiadomień telefonu, ikony, tło aplikacji, elementy interfejsu). Skup się wyłącznie
    na tabeli pozycji zakupowych i danych paragonu.

    Musisz zwrócić wyłącznie JEDEN obiekt JSON zawierający klucze:
    - "data_paragonu": data wykonania zakupów w formacie "YYYY-MM-DD" (wyciągnięta z paragonu). Jeśli jej nie znajdziesz, wstaw null.
    - "pozycje": lista obiektów, gdzie każdy zawiera:
        * "nazwa": wyczyszczona z kodów, wag i skrótów nazwa produktu (np. "Chleb", "Mleko 3.2%")
        * "kwota": cena za tę pozycję jako liczba zmiennoprzecinkowa (np. 3.49)
        * "kategoria_id": liczba całkowita dobrana na podstawie poniższej listy.

    Zasady doboru kategorii:
    - Artykuły spożywcze, napoje, pieczywo, warzywa, słodycze -> 1 (Jedzenie)
    - Płyny do naczyń, proszki do prania, tabletki do zmywarki, ściereczki, filtry -> 2 (Chemia gospodarcza)
    - Szampony, żele pod prysznic, kremy, dezodoranty, pasty do zębów -> 5 (Kosmetyki)
    - Wszystko inne przypisuj logicznie do pozostałych kategorii. Jeśli nie pasuje do niczego -> 12 (Inne).

    --- DOSTĘPNE KATEGORIE ORAZ ICH ID ---
    {LISTA_KATEGORII}

    Zwróć wyłącznie surowy JSON, bez formatowania markdown, bez znaczników ```json, bez wstępów.

    Przykład oczekiwanej struktury:
    {{"data_paragonu": "2026-07-11", "pozycje": [{{"nazwa": "Chleb", "kwota": 3.00, "kategoria_id": 1}}, {{"nazwa": "Proszek", "kwota": 50.00, "kategoria_id": 2}}]}}
    """

    if obraz_bytes:
        contents = [instrukcja, types.Part.from_bytes(data=obraz_bytes, mime_type=obraz_mime_type)]
    else:
        contents = instrukcja

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logging.error(f"❌ Błąd AI przy rozbijaniu paragonu: {e}")
        return None
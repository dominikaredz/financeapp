import os
import json
from groq import Groq

# Tworzymy klienta Groq - teraz bez błędu, bo app_server zdążył już załadować env.txt!
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def analizuj_powiadomienie_przez_ai(tresc_pusha):
    """
    Funkcja wysyła pełną treść powiadomienia z telefonu do Llama 3.
    Model wyciąga z tekstu kwotę, sklep i dopasowuje ID kategorii zgodnie z bazą danych.
    """

    prompt = f"""
    Jesteś precyzyjnym parserem danych finansowych. Twoim zadaniem jest przeanalizować listę powiadomień push z banku/portfela płatniczego i wyciągnąć z nich dane.

    Oto powiadomienia do analizy:
    "{tresc_pusha}"

    Musisz zwrócić wyłącznie JEDEN obiekt JSON zawierający klucz "transakcje", który jest listą obiektów.
    Każdy obiekt na liście musi zawierać klucze:
    - "kwota": liczba zmiennoprzecinkowa (np. 46.33)
    - "sklep": oczyszczona z niepotrzebnych dopisków i numerów nazwa sprzedawcy/miejsca (np. "Kaufland", "Żabka"). Jeśli to przelew przychodzący bez danych nadawcy, wpisz "Przelew Przychodzący".
    - "kategoria_id": liczba całkowita reprezentująca ID kategorii dobraną na podstawie bazy danych.
    - "typ": tekst, który musi przyjąć wyłącznie wartość "WYDATEK" lub "PRZYCHOD".

    Wybierz odpowiednie "kategoria_id" STRICTE na podstawie poniższej listy:
    1 - Jedzenie (sklepy spożywcze, supermarkety, restauracje, Żabka, Biedronka, Lidl, Kaufland, UberEats, kawiarnie)
    2 - Chemia gospodarcza (środki czystości, zakupy do domu, proszki itp.)
    3 - Subskrypcje (Netflix, Spotify, YouTube Premium, aplikacje, iCloud)
    4 - Bank (opłaty za prowadzenie konta, prowizje, odsetki)
    5 - Kosmetyki (Rossmann, Hebe, Sephora, drogerie, kosmetyczka, fryzjer)
    6 - Transport (paliwo, bilety komunikacji miejskiej, Uber, Bolt, PKP, taksówki, parkingi)
    7 - Rachunki (czynsz, prąd, gaz, internet, telefon)
    8 - Leki (apteki, lekarze, badania, medycyna)
    9 - Mieszkanie (meble, wyposażenie wnętrz, remont, IKEA, Castorama)
    10 - Ubrania (odzież, obuwie, sklepy sieciowe z ubraniami)
    11 - Rozrywka/Wyjazdy (kino, teatr, hotele, loty, wakacje, imprezy)
    12 - Inne (wydatki, które nie pasują do żadnej z powyższych kategorii ORAZ ogólne przelewy przychodzące)

    Zwróć wyłącznie sam surowy JSON, bez żadnego formatowania markdown (bez ```json), bez wstępów i podsumowań.

    Dodatkowe uwagi:
    - JOYFUL kategoryzuj jako numer 1 - jedzenie

    Przykład struktury odpowiedzi:
    {{"transakcje": [{{"kwota": 46.33, "sklep": "Kaufland", "kategoria_id": 1, "typ": "WYDATEK"}}]}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.1-8b-instant",
            temperature=0,  # Zero kreatywności, zależy nam na sztywnych danych
            response_format={"type": "json_object"}
        )

        # Pobieramy czysty tekst z odpowiedzi AI
        odpowiedz_tekst = chat_completion.choices[0].message.content.strip()

        # 🔥 Pokaże nam prawdę w pliku logów:
        import logging
        logging.info(f"🔎 DEBUG: Model AI przesłał dokładnie taki tekst:\n{odpowiedz_tekst}\n-------------------")

        # Dodatkowy ratunek: gdyby model mimo wszystko wbił tam znaczniki markdownu
        if odpowiedz_tekst.startswith("```"):
            odpowiedz_tekst = odpowiedz_tekst.split("```")[1]
            if odpowiedz_tekst.startswith("json"):
                odpowiedz_tekst = odpowiedz_tekst[4:]
        odpowiedz_tekst = odpowiedz_tekst.strip()

        # Parsujemy tekst na słownik Pythona
        dane_transakcji = json.loads(odpowiedz_tekst)
        return dane_transakcji

    except Exception as e:
        import logging
        logging.error(f"❌ Błąd komunikacji z Groq AI lub parsowania JSON: {e}")
        if 'odpowiedz_tekst' in locals():
            logging.warning(f"⚠️ Surowa odpowiedź modelu to:\n{odpowiedz_tekst}")
        return None
import json
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=API_KEY)


def rozpoznaj_kategorie_zbiorczo(lista_opisow):
    # Przekształcamy listę w ponumerowany tekst, np. "0: KAUFLAND\n1: ROSSMANN"
    sklepy_tekst = "\n".join([f"{i}: {opis}" for i, opis in enumerate(lista_opisow)])

    prompt = f"""
    Jesteś ekspertem od finansów osobistych. Twoim zadaniem jest przypisanie poniższych opisów transakcji do odpowiednich kategorii.

    Dostępne kategorie (ID - Nazwa):
    1 - Jedzenie
    2 - Chemia gospodarcza
    3 - Subskrypcje
    4 - Bank
    5 - Kosmetyki
    6 - Transport
    7 - Rachunki
    8 - Leki
    9 - Mieszkanie
    10 - Ubrania
    11 - Rozrywka/Wyjazdy
    12 - Inne

    Przeanalizuj poniższą listę transakcji (format to INDEKS: OPIS):
    {sklepy_tekst}

    Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON, gdzie kluczem jest INDEKS (jako string lub liczba), a wartością jest ID kategorii (liczba).
    Nie pisz żadnych wstępów, wyjaśnień ani znaczników markdown typu ```json. Tylko czysty JSON.
    Przykład wyniku: {{"0": 1, "1": 5, "2": 12}}
    
    Dodatkowe uwagi:
    JOYFUL - kategoria 'jedzenie' - 1
    """

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )

        # Odpowiedź od AI zamieniamy bezpośrednio na słownik Pythona
        wynik_json = json.loads(response.choices[0].message.content.strip())

        # Zamieniamy klucze na liczby całkowite dla łatwiejszego dopasowania
        return {int(k): int(v) for k, v in wynik_json.items()}

    except Exception as e:
        print(f"❌ Błąd zbiorczego AI: {e}")
        return {}
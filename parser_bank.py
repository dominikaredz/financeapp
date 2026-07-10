import csv
import json  # 1. DODAJEMY IMPORT JSON NA SAMEJ GÓRZE
import sqlite3
from ai_categorized import rozpoznaj_kategorie_zbiorczo

path_to_db = r'E:\Programowanie\FinanceApp\finance_db.sqlite'
path_to_csv = r'E:\Programowanie\wyciag.csv'

# 2. TUTAJ ŁADUJEMY KONFIGURACJĘ (Robimy to raz, przed uruchomieniem pętli)
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        czarna_lista = config["blokowane_slowa"]
except FileNotFoundError:
    print("⚠️ Brak pliku config.json! Program przejdzie w tryb bez filtrów użytkownika.")
    czarna_lista = {}


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


# --- ETAP 1: CZYTANIE PLIKU I FILTROWANIE DANYCH ---
transakcje_do_zapisu = []
opisy_dla_ai = []

with open(path_to_csv, mode='r', encoding='utf-8') as file:
    reader = csv.DictReader(file, delimiter=',')

    for row in reader:
        try:
            data = row['Data transakcji']
            odbiorca = row['Odbiorca/Zleceniodawca'].strip()
            opis_transakcji = row['Opis'].strip()
            pelny_opis = f"{odbiorca} - {opis_transakcji}" if odbiorca else opis_transakcji

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

            # Podstawowy, twardy filtr na przelewy/przychody (nadal chroni wrażliwe przelewy)
            czy_to_przelew = "PRZELEW" in pelny_opis.upper() or "TRANSFER" in pelny_opis.upper() or typ == 'PRZYCHOD'

            # 3. NOWA DYNAMICZNA LOGIKA SPRAWDZANIA SŁÓW Z PLIKU JSON
            wykryte_id_z_pliku = None

            if not czy_to_przelew:
                # Przeszukujemy słowa kluczowe z czarnej listy użytkownika
                for slowo, kat_id in czarna_lista.items():
                    if slowo.upper() in pelny_opis.upper():
                        wykryte_id_z_pliku = kat_id
                        break  # Znaleźliśmy dopasowanie, przerywamy pętlę for dla słów kluczowych

            indeks_ai = None
            # Do AI wysyłamy tylko wtedy, gdy to nie przelew I gdy nie znaleźliśmy słowa w pliku config.json
            if not czy_to_przelew and wykryte_id_z_pliku is None:
                opisy_dla_ai.append(pelny_opis)
                indeks_ai = len(opisy_dla_ai) - 1

            # Pakujemy dane do słownika, zapamiętując wykryte ID (lub None, jeśli ma iść do AI)
            transakcje_do_zapisu.append({
                'kwota': kwota,
                'data': data,
                'opis': pelny_opis,
                'typ': typ,
                'indeks_ai': indeks_ai,
                'sztywne_id': wykryte_id_z_pliku  # Tu trzymamy np. 3 dla Spotify, 7 dla PGE albo None
            })
        except Exception:
            continue

# --- ETAP 2: JEDNO ZAPYTANIE DO AI ---
słownik_kategorii = {}
if opisy_dla_ai:
    print(f"🚀 Wysyłam zbiorcze zapytanie do AI dla {len(opisy_dla_ai)} transakcji kartowych...")
    słownik_kategorii = rozpoznaj_kategorie_zbiorczo(opisy_dla_ai)

# --- ETAP 3: MASOWY ZAPIS DO BAZY ---
print("\n💾 Zapisuję skategoryzowane dane do bazy SQL...")
licznik = 0

for t in transakcje_do_zapisu:
    # Decydujemy o kategorii:
    if t['indeks_ai'] is not None and t['indeks_ai'] in słownik_kategorii:
        # Jeśli transakcja poszła do AI i dostała odpowiedź:
        kategoria_id = słownik_kategorii[t['indeks_ai']]
    elif t['sztywne_id'] is not None:
        # Jeśli transakcja została wyłapana przez plik config.json użytkownika:
        kategoria_id = t['sztywne_id']
    else:
        # Przelewy prywatne oraz awaryjne sytuacje błędów AI:
        kategoria_id = 12

    dodaj_transakcje_do_bazy(kategoria_id, t['kwota'], t['data'], t['opis'], t['typ'])
    licznik += 1

print(f"\n✅ Sukces! Wydałaś tylko 1 zapytanie do AI. Dodano {licznik} transakcji.")
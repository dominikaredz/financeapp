"""
=====================================================================================
 JEDNORAZOWY SKRYPT MIGRACYJNY - migrate_add_auth.py
=====================================================================================
Dodaje do tabeli `uzytkownicy` kolumny potrzebne do logowania (haslo_hash) oraz
unikalny token webhooka per użytkownik (api_token) - używany do rozróżniania,
z którego konta przyszło powiadomienie push z telefonu.

UŻYCIE:

  1) Samo dodanie kolumn + podgląd obecnej zawartości tabeli:
         python3 migrate_add_auth.py

  2) Jeśli tabela `uzytkownicy` jest PUSTA (najpewniej Twój przypadek - do tej
     pory nikt się nigdzie nie rejestrował, uzytkownik_id=1 był tylko wpisywany
     na sztywno w kodzie) - NIC WIĘCEJ NIE RÓB. Po prostu zarejestruj się normalnie
     przez stronę /register. Nowe konto automatycznie dostanie id=1 (SQLite
     przydziela pierwszy wolny numer w pustej tabeli), więc WSZYSTKIE dotychczasowe
     transakcje (uzytkownik_id=1) automatycznie staną się Twoje - bez żadnej
     dodatkowej migracji danych w tabeli transakcje.

  3) Jeśli w tabeli JUŻ JEST wiersz o id=1 (np. ktoś go kiedyś ręcznie dodał) -
     nadaj mu e-mail, hasło i token tym samym skryptem:
         python3 migrate_add_auth.py twoj@email.pl TwojeHaslo123

Bezpieczne do wielokrotnego uruchomienia - sprawdza, czy kolumny już istnieją.
=====================================================================================
"""
import os
import sqlite3
import secrets
import sys
from werkzeug.security import generate_password_hash

folder_projektu = r'/home/domiredz00/FinanceApp'
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

conn = sqlite3.connect(path_to_db)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(uzytkownicy);")
kolumny = {row[1] for row in cursor.fetchall()}

if "haslo_hash" not in kolumny:
    cursor.execute("ALTER TABLE uzytkownicy ADD COLUMN haslo_hash TEXT;")
    print("✅ Dodano kolumnę haslo_hash")
else:
    print("ℹ️ Kolumna haslo_hash już istnieje")

if "api_token" not in kolumny:
    cursor.execute("ALTER TABLE uzytkownicy ADD COLUMN api_token TEXT;")
    print("✅ Dodano kolumnę api_token")
else:
    print("ℹ️ Kolumna api_token już istnieje")

conn.commit()

# --- Podgląd obecnej zawartości tabeli ---
cursor.execute("SELECT id, imie, email, haslo_hash, api_token FROM uzytkownicy;")
wiersze = cursor.fetchall()
print(f"\n📋 Obecna zawartość tabeli uzytkownicy ({len(wiersze)} wiersz(y)):")
if not wiersze:
    print("   (pusto - zarejestruj się normalnie przez /register, patrz punkt 2 w komentarzu na górze pliku)")
for w in wiersze:
    print(f"   id={w[0]} imie={w[1]!r} email={w[2]!r} ma_haslo={'TAK' if w[3] else 'NIE'} token={w[4]}")

# --- Opcjonalne: ręczne ustawienie hasła/tokenu dla istniejącego konta o id=1 ---
if len(sys.argv) >= 3:
    email = sys.argv[1]
    haslo = sys.argv[2]
    haslo_hash = generate_password_hash(haslo)
    token = secrets.token_urlsafe(24)

    cursor.execute("SELECT id FROM uzytkownicy WHERE id = 1;")
    if cursor.fetchone():
        cursor.execute(
            "UPDATE uzytkownicy SET email = ?, haslo_hash = ?, api_token = ? WHERE id = 1;",
            (email, haslo_hash, token)
        )
        conn.commit()
        print(f"\n✅ Ustawiono e-mail/hasło/token dla konta id=1. Zaloguj się jako {email}.")
        print(f"🔑 Twój api_token: {token}")
    else:
        print("\n⚠️ Nie ma wiersza o id=1 w tabeli - najpierw zarejestruj się normalnie przez /register.")

conn.close()
print("\n🎉 Migracja zakończona.")

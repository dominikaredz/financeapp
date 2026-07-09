import sqlite3

# Definiujemy nazwę pliku bazy danych (jest w tym samym folderze, więc wystarczy sama nazwa)
path_to_db = r'E:\Programowanie\FinanceApp\finance_db.sqlite'

try:
    # 1. Tworzymy połączenie z bazą danych
    connection = sqlite3.connect(path_to_db)
    cursor = connection.cursor()

    # 2. Wysyłamy zapytanie SQL, żeby wyciągnąć kategorie
    cursor.execute("SELECT id, nazwa_kategorii FROM kategorie;")
    kategorie = cursor.fetchall()

    # 3. Wypisujemy wynik w konsoli Pythona
    print("\n🚀 SUKCES! PYTHON POŁĄCZYŁ SIĘ Z BAZĄ DATAGRIPA. \n")
    print("Oto Twoje kategorie pobrane z pliku:")
    print("-" * 40)
    for kat in kategorie:
        print(f"ID: {kat[0]} | Kategoria: {kat[1]}")
    print("-" * 40)

    # 4. Zamykamy połączenie
    connection.close()

except sqlite3.Error as e:
    print(f"❌ Coś poszło nie tak przy połączeniu z bazą: {e}")
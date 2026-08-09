import os
import sqlite3
import traceback
import urllib.parse
import secrets
import threading
import json
from functools import wraps
from datetime import datetime, date
import calendar
from flask import Flask, request, jsonify, render_template, redirect, session, url_for
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

folder_projektu = r'/home/domiredz00/FinanceApp'
load_dotenv(dotenv_path=r'/home/domiredz00/.env')
path_to_db = os.path.join(folder_projektu, 'finance_db.sqlite')

from auto_finance_updater import przetworz_powiadomienie_push
from parser_bank_csv import importuj_plik_csv

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "losowy-bardzo-dlugi-ciag-znakow-zabezpieczajacych")


def wymagaj_logowania(f):
    """Dekorator - przekierowuje na /login, jeśli nikt nie jest zalogowany."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'uzytkownik_id' not in session:
            return redirect(url_for('zaloguj'))
        return f(*args, **kwargs)
    return wrapper


# --- 1. STRONA GŁÓWNA (ZABEZPIECZONA) ---
@app.route('/')
@wymagaj_logowania
def strona_glowna():
    uzytkownik_id = session['uzytkownik_id']
    TRANSAKCJI_NA_STRONE = 10

    # --- Parametry filtrowania z paska nad tabelą (GET, żeby dało się linkować/odświeżać) ---
    f_kategoria = request.args.get('f_kategoria', '').strip()
    f_typ = request.args.get('f_typ', '').strip()
    f_od = request.args.get('f_od', '').strip()
    f_do = request.args.get('f_do', '').strip()
    f_szukaj = request.args.get('f_szukaj', '').strip()

    # Numer strony tabeli transakcji (paginacja)
    try:
        strona = max(1, int(request.args.get('strona', 1)))
    except ValueError:
        strona = 1

    # --- Zakres dat DLA WYKRESU (niezależny od filtrów tabeli!) - domyślnie bieżący
    # miesiąc, żeby nie pokazywać od razu wszystkich transakcji z całej historii. ---
    dzis = date.today()
    domyslny_w_od = dzis.replace(day=1).isoformat()
    ostatni_dzien_miesiaca = calendar.monthrange(dzis.year, dzis.month)[1]
    domyslny_w_do = dzis.replace(day=ostatni_dzien_miesiaca).isoformat()

    # Brak PARAMETRÓW w URL w ogóle -> pierwszy raz na stronie -> użyj domyślnego miesiąca.
    # Parametr obecny, ale pusty (np. z linku "Cały okres") -> szanujemy pustkę (brak filtra).
    if request.args.get('w_od') is None and request.args.get('w_do') is None:
        w_od, w_do = domyslny_w_od, domyslny_w_do
    else:
        w_od = request.args.get('w_od', '').strip()
        w_do = request.args.get('w_do', '').strip()

    # Czytelna etykieta okresu wyświetlana na kartach ("Lipiec 2026" dla DOWOLNEGO pełnego
    # miesiąca / zakres dat / "Cały okres")
    MIESIACE_PL = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
                   "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]
    try:
        d_od = date.fromisoformat(w_od) if w_od else None
        d_do = date.fromisoformat(w_do) if w_do else None
    except ValueError:
        d_od = d_do = None

    czy_pelny_miesiac = (
        d_od and d_do and d_od.day == 1
        and d_od.year == d_do.year and d_od.month == d_do.month
        and d_do.day == calendar.monthrange(d_od.year, d_od.month)[1]
    )
    if not w_od and not w_do:
        okres_etykieta = "Cały okres"
    elif czy_pelny_miesiac:
        okres_etykieta = f"{MIESIACE_PL[d_od.month - 1]} {d_od.year}"
    else:
        okres_etykieta = f"{w_od or '...'} — {w_do or '...'}"

    try:
        conn = sqlite3.connect(path_to_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 1. Pobieramy transakcje główne użytkownika - z dynamicznymi filtrami
        warunki = ["t.uzytkownik_id = ?"]
        parametry = [uzytkownik_id]

        if f_kategoria:
            if f_kategoria == 'rozbite':
                warunki.append("t.kategoria_id IS NULL")
            else:
                warunki.append("t.kategoria_id = ?")
                parametry.append(int(f_kategoria))
        if f_typ in ('WYDATEK', 'PRZYCHOD'):
            warunki.append("t.typ = ?")
            parametry.append(f_typ)
        if f_od:
            warunki.append("t.data >= ?")
            parametry.append(f_od)
        if f_do:
            warunki.append("t.data <= ?")
            parametry.append(f_do)
        if f_szukaj:
            warunki.append("t.opis_sklepu LIKE ?")
            parametry.append(f"%{f_szukaj}%")

        # Najpierw liczymy ILE łącznie pasuje (do wyliczenia liczby stron)
        query_liczba = f"SELECT COUNT(*) FROM transakcje t WHERE {' AND '.join(warunki)};"
        cursor.execute(query_liczba, parametry)
        razem_transakcji = cursor.fetchone()[0]
        liczba_stron = max(1, -(-razem_transakcji // TRANSAKCJI_NA_STRONE))  # ceil bez importu math
        if strona > liczba_stron:
            strona = liczba_stron

        query_transakcje = f"""
            SELECT t.id, t.kwota, t.data, t.opis_sklepu as opis, t.typ, t.zrodlo,
                   t.kategoria_id, k.nazwa_kategorii as kategoria_nazwa
            FROM transakcje t
            LEFT JOIN kategorie k ON t.kategoria_id = k.id
            WHERE {' AND '.join(warunki)}
            ORDER BY t.data DESC, t.id DESC
            LIMIT ? OFFSET ?;
        """
        cursor.execute(query_transakcje, parametry + [TRANSAKCJI_NA_STRONE, (strona - 1) * TRANSAKCJI_NA_STRONE])
        surowe_transakcje = cursor.fetchall()

        # Dla każdej transakcji NA TEJ STRONIE sprawdzamy, czy posiada pod-pozycje z paragonu
        transakcje = []
        for row in surowe_transakcje:
            t_dict = dict(row)
            cursor.execute("""
                SELECT p.nazwa_pozycji, p.kwota, k.nazwa_kategorii as kategoria_nazwa
                FROM pozycje_transakcji p
                JOIN kategorie k ON p.kategoria_id = k.id
                WHERE p.transakcja_id = ?;
            """, (t_dict['id'],))
            t_dict['pozycje'] = [dict(p) for p in cursor.fetchall()]
            transakcje.append(t_dict)

        # Podsumowania Cash Flow - TERAZ w tym samym zakresie dat co wykres (w_od/w_do),
        # żeby obie karty pokazywały spójny, filtrowany obraz zamiast mieszać "cały czas"
        # z "bieżący miesiąc" w jednym widoku.
        warunki_podsumowania = ["uzytkownik_id = ?"]
        parametry_podsumowania = [uzytkownik_id]
        if w_od:
            warunki_podsumowania.append("data >= ?")
            parametry_podsumowania.append(w_od)
        if w_do:
            warunki_podsumowania.append("data <= ?")
            parametry_podsumowania.append(w_do)
        warunek_podsumowania_sql = " AND ".join(warunki_podsumowania)

        cursor.execute(f"SELECT SUM(kwota) FROM transakcje WHERE typ = 'PRZYCHOD' AND {warunek_podsumowania_sql};", parametry_podsumowania)
        przychod = cursor.fetchone()[0] or 0.0

        cursor.execute(f"SELECT SUM(kwota) FROM transakcje WHERE typ = 'WYDATEK' AND {warunek_podsumowania_sql};", parametry_podsumowania)
        wydatek = cursor.fetchone()[0] or 0.0

        podsumowanie = {"przychod": przychod, "wydatek": wydatek}

        # 2. WYKRES - niezależny zakres dat (w_od/w_do), domyślnie bieżący miesiąc
        wykres_warunki_t = ["t.typ = 'WYDATEK'", "t.uzytkownik_id = ?"]
        wykres_parametry_t = [uzytkownik_id]
        wykres_warunki_p = ["t.uzytkownik_id = ?"]
        wykres_parametry_p = [uzytkownik_id]

        if w_od:
            wykres_warunki_t.append("t.data >= ?")
            wykres_parametry_t.append(w_od)
            wykres_warunki_p.append("t.data >= ?")
            wykres_parametry_p.append(w_od)
        if w_do:
            wykres_warunki_t.append("t.data <= ?")
            wykres_parametry_t.append(w_do)
            wykres_warunki_p.append("t.data <= ?")
            wykres_parametry_p.append(w_do)

        query_wykres = f"""
            SELECT kat_nazwa, SUM(kwota) as suma FROM (
                SELECT k.nazwa_kategorii as kat_nazwa, t.kwota FROM transakcje t
                JOIN kategorie k ON t.kategoria_id = k.id
                WHERE {' AND '.join(wykres_warunki_t)}
                  AND t.id NOT IN (SELECT DISTINCT transakcja_id FROM pozycje_transakcji)
                UNION ALL
                SELECT k.nazwa_kategorii as kat_nazwa, p.kwota FROM pozycje_transakcji p
                JOIN transakcje t ON p.transakcja_id = t.id
                JOIN kategorie k ON p.kategoria_id = k.id
                WHERE {' AND '.join(wykres_warunki_p)}
            ) GROUP BY kat_nazwa;
        """
        cursor.execute(query_wykres, wykres_parametry_t + wykres_parametry_p)
        dane_wykresu_rows = cursor.fetchall()

        wykres_dane = {
            "labels": [row["kat_nazwa"] for row in dane_wykresu_rows],
            "sumy": [row["suma"] for row in dane_wykresu_rows]
        }

        cursor.execute("SELECT id, nazwa_kategorii FROM kategorie ORDER BY id;")
        kategorie = cursor.fetchall()

        cursor.execute("SELECT api_token FROM uzytkownicy WHERE id = ?;", (uzytkownik_id,))
        token_row = cursor.fetchone()
        api_token = token_row["api_token"] if token_row else None

        conn.close()
    except Exception as e:
        transakcje = []
        podsumowanie = {"przychod": 0.0, "wydatek": 0.0}
        wykres_dane = {"labels": [], "sumy": []}
        kategorie = []
        api_token = None
        strona = 1
        liczba_stron = 1
        razem_transakcji = 0
        traceback.print_exc()

    webhook_url = f"{request.url_root}webhook/push?token={api_token}" if api_token else None

    return render_template(
        'index.html',
        transakcje=transakcje,
        podsumowanie=podsumowanie,
        wykres_dane=wykres_dane,
        kategorie=kategorie,
        webhook_url=webhook_url,
        imie=session.get('imie'),
        filtry={'kategoria': f_kategoria, 'typ': f_typ, 'od': f_od, 'do': f_do, 'szukaj': f_szukaj},
        wykres_filtry={'od': w_od, 'do': w_do},
        okres_etykieta=okres_etykieta,
        strona=strona,
        liczba_stron=liczba_stron,
        razem_transakcji=razem_transakcji,
    )


# --- NOWA ŚCIEŻKA: ROZBIJANIE E-PARAGONU KAUFLAND (ZE ZDJĘCIA/SCREENSHOTA) ---
@app.route('/rozbij-paragon', methods=['POST'])
def rozbij_paragon():
    uzytkownik_id = session.get('uzytkownik_id')
    is_api_request = False

    if not uzytkownik_id:
        token = request.args.get("token")
        if token:
            is_api_request = True
            conn = sqlite3.connect(path_to_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM uzytkownicy WHERE api_token = ?;", (token,))
            user = cursor.fetchone()
            conn.close()
            if user:
                uzytkownik_id = user['id']

    if not uzytkownik_id:
        if is_api_request:
            return jsonify({"status": "error", "message": "Brak autoryzacji"}), 401
        return redirect(url_for('zaloguj'))

    # --- Wyciągnięcie danych: PREFEROWANE źródło to zdjęcie/screenshot paragonu
    # (Gemini "czyta" je bezpośrednio, bez żadnego OCR); wklejony tekst zostaje jako
    # zapasowa ścieżka (np. do wywołań przez API/automatyzację). ---
    tekst_paragonu = None
    obraz_bytes = None
    obraz_mime_type = None

    plik_obrazu = request.files.get('plik_paragon')
    ROZSZERZENIA_OBRAZOW = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}

    if plik_obrazu and plik_obrazu.filename:
        rozszerzenie = os.path.splitext(plik_obrazu.filename.lower())[1]
        if rozszerzenie in ROZSZERZENIA_OBRAZOW:
            obraz_bytes = plik_obrazu.read()
            obraz_mime_type = ROZSZERZENIA_OBRAZOW[rozszerzenie]

    if not obraz_bytes:
        tekst_paragonu = request.form.get('tekst_paragonu', '').strip()

    if not obraz_bytes and not tekst_paragonu:
        if is_api_request:
            return jsonify({"status": "error", "message": "Brak zdjęcia paragonu ani tekstu"}), 400
        return redirect(url_for('strona_glowna'))

    from ai_categorized import analizuj_eparagon_kaufland
    wynik_ai = analizuj_eparagon_kaufland(tekst_paragonu=tekst_paragonu, obraz_bytes=obraz_bytes, obraz_mime_type=obraz_mime_type)

    if wynik_ai and "pozycje" in wynik_ai and wynik_ai["pozycje"]:
        try:
            conn = sqlite3.connect(path_to_db)
            cursor = conn.cursor()

            laczna_kwota = round(sum(float(p["kwota"]) for p in wynik_ai["pozycje"]), 2)

            import re
            data_paragonu = wynik_ai.get("data_paragonu")
            if not data_paragonu or not re.match(r"^\d{4}-\d{2}-\d{2}$", data_paragonu):
                data_paragonu = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT id FROM transakcje
                WHERE uzytkownik_id = ?
                  AND data = ?
                  AND abs(kwota - ?) < 0.05
                  AND (opis_sklepu LIKE '%Kaufland%' OR zrodlo = 'PARAGON');
            """, (uzytkownik_id, data_paragonu, laczna_kwota))

            istniejaca_transakcja = cursor.fetchone()

            # WAŻNE: kategoria_id = NULL zamiast twardej "1" - transakcja rozbita na
            # pozycje nie ma jednej kategorii głównej, więc kategoria per-pozycja
            # (w tabeli pozycje_transakcji) jest jedynym źródłem prawdy o kategoriach.
            if istniejaca_transakcja:
                transakcja_id = istniejaca_transakcja[0]
                cursor.execute("""
                    UPDATE transakcje
                    SET opis_sklepu = 'Kaufland (E-paragon)', zrodlo = 'PARAGON', kategoria_id = NULL
                    WHERE id = ?;
                """, (transakcja_id,))
                cursor.execute("DELETE FROM pozycje_transakcji WHERE transakcja_id = ?;", (transakcja_id,))
            else:
                cursor.execute("""
                    INSERT INTO transakcje (uzytkownik_id, kategoria_id, kwota, data, opis_sklepu, typ, zrodlo)
                    VALUES (?, NULL, ?, ?, 'Kaufland (E-paragon)', 'WYDATEK', 'PARAGON');
                """, (uzytkownik_id, laczna_kwota, data_paragonu))
                transakcja_id = cursor.lastrowid

            for p in wynik_ai["pozycje"]:
                cursor.execute("""
                    INSERT INTO pozycje_transakcji (transakcja_id, nazwa_pozycji, kwota, kategoria_id)
                    VALUES (?, ?, ?, ?);
                """, (transakcja_id, p["nazwa"], float(p["kwota"]), int(p["kategoria_id"])))

            conn.commit()
            conn.close()

            if is_api_request:
                return jsonify({
                    "status": "success",
                    "message": "Paragon przetworzony pomyślnie!",
                    "data": data_paragonu,
                    "kwota": laczna_kwota,
                    "czy_scalono": bool(istniejaca_transakcja)
                }), 200
        except Exception as e:
            traceback.print_exc()
            if is_api_request:
                return jsonify({"status": "error", "message": str(e)}), 500

    if is_api_request:
        return jsonify({"status": "error", "message": "Nie udało się sparsować paragonu przez AI"}), 500
    return redirect(url_for('strona_glowna'))


@app.route('/login', methods=['GET', 'POST'])
def zaloguj():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        haslo = request.form.get('haslo', '')
        conn = sqlite3.connect(path_to_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM uzytkownicy WHERE email = ?;", (email,))
        user = cursor.fetchone()
        conn.close()
        if user and user['haslo_hash'] and check_password_hash(user['haslo_hash'], haslo):
            session['uzytkownik_id'] = user['id']
            session['imie'] = user['imie']
            return redirect(url_for('strona_glowna'))
        return render_template('login.html', blad="Nieprawidłowy e-mail lub hasło.")
    return render_template('login.html', blad=None)


@app.route('/register', methods=['GET', 'POST'])
def zarejestruj():
    if request.method == 'POST':
        imie = request.form.get('imie', '').strip()
        email = request.form.get('email', '').strip().lower()
        haslo = request.form.get('haslo', '')
        if not imie or not email or not haslo:
            return render_template('register.html', blad="Wypełnij wszystkie pola.")
        if len(haslo) < 8:
            return render_template('register.html', blad="Hasło musi mieć minimum 8 znaków.")
        conn = sqlite3.connect(path_to_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM uzytkownicy WHERE email = ?;", (email,))
        if cursor.fetchone():
            conn.close()
            return render_template('register.html', blad="Ten adres e-mail jest już zarejestrowany.")
        haslo_hash = generate_password_hash(haslo)
        api_token = secrets.token_urlsafe(24)
        cursor.execute("INSERT INTO uzytkownicy (imie, email, haslo_hash, api_token) VALUES (?, ?, ?, ?);", (imie, email, haslo_hash, api_token))
        conn.commit()
        nowy_id = cursor.lastrowid
        conn.close()
        session['uzytkownik_id'] = nowy_id
        session['imie'] = imie
        return redirect(url_for('strona_glowna'))
    return render_template('register.html', blad=None)


@app.route('/logout')
def wyloguj():
    session.clear()
    return redirect(url_for('zaloguj'))


@app.route('/upload-csv', methods=['POST'])
@wymagaj_logowania
def upload_csv():
    uzytkownik_id = session['uzytkownik_id']
    plik = request.files.get('plik_csv')
    if not plik or plik.filename == '' or not plik.filename.lower().endswith('.csv'):
        return redirect(url_for('strona_glowna'))

    tmp_path = os.path.join(folder_projektu, f"tmp_import_{uzytkownik_id}.csv")
    plik.save(tmp_path)

    def uruchom_import_w_tle():
        # Działa w osobnym wątku - request HTTP już dawno wrócił do przeglądarki,
        # więc pauzy między zapytaniami AI (potrzebne, żeby nie zapchać Gemini)
        # nie trzymają już nikogo w zawieszeniu i nie trafiają w limit czasu serwera.
        try:
            importuj_plik_csv(tmp_path, uzytkownik_id)
        except Exception:
            traceback.print_exc()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    watek = threading.Thread(target=uruchom_import_w_tle, daemon=True)
    watek.start()

    # Wracamy NATYCHMIAST - panel sam odpyta /import-status i pokaże postęp.
    return redirect(url_for('strona_glowna'))


@app.route('/import-status')
def import_status():
    uzytkownik_id = session.get('uzytkownik_id')
    if not uzytkownik_id:
        return jsonify({"stan": "brak"}), 401

    sciezka_statusu = os.path.join(folder_projektu, f"import_status_{uzytkownik_id}.json")
    if not os.path.exists(sciezka_statusu):
        return jsonify({"stan": "brak"})

    try:
        with open(sciezka_statusu, 'r', encoding='utf-8') as f:
            dane = json.load(f)

        # WAŻNE: jeśli import jest już ZAKOŃCZONY (gotowe/blad), kasujemy plik statusu
        # PO odczytaniu. Inaczej każda kolejna wizyta na stronie (nawet dni później)
        # widziałaby ten sam stary status "gotowe" i próbowałaby znowu odświeżyć stronę -
        # to właśnie powodowało niekończącą się pętlę odświeżeń.
        if dane.get("stan") in ("gotowe", "blad"):
            try:
                os.remove(sciezka_statusu)
            except OSError:
                pass

        return jsonify(dane)
    except Exception:
        return jsonify({"stan": "brak"})


@app.route('/edytuj-transakcja/<int:transakcja_id>', methods=['POST'])
def edytuj_transakcja(transakcja_id):
    uzytkownik_id = session['uzytkownik_id']
    nowy_opis = request.form.get('opis', '').strip()
    nowa_kategoria = request.form.get('kategoria_id')
    if not nowy_opis or not nowa_kategoria:
        return redirect(url_for('strona_glowna'))
    conn = sqlite3.connect(path_to_db)
    cursor = conn.cursor()
    cursor.execute("UPDATE transakcje SET opis_sklepu = ?, kategoria_id = ? WHERE id = ? AND uzytkownik_id = ?;", (nowy_opis, int(nowa_kategoria), transakcja_id, uzytkownik_id))
    conn.commit()
    conn.close()
    return redirect(url_for('strona_glowna'))


@app.route('/instrukcja-macrodroid')
@wymagaj_logowania
def instrukcja_macrodroid():
    return render_template('macrodroid_instrukcja.html')


@app.route('/webhook/push', methods=['GET', 'POST'])
def odbierz_powiadomienie():
    token = request.args.get("token")
    if not token:
        return jsonify({"status": "error", "message": "Brak tokenu autoryzacji"}), 401
    conn = sqlite3.connect(path_to_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM uzytkownicy WHERE api_token = ?;", (token,))
    user = cursor.fetchone()
    conn.close()
    if not user:
        return jsonify({"status": "error", "message": "Niepoprawny token API"}), 403
    uzytkownik_id = user['id']
    app_name = request.args.get("app_name")
    text = request.args.get("text")
    if not text:
        data = request.form if request.form else (request.get_json(silent=True) or {})
        app_name = data.get("app_name")
        text = data.get("text")
    if not text:
        return jsonify({"status": "ignored", "message": "Pusty tekst"}), 200
    text_czysty = urllib.parse.unquote(text)
    app_name_czysty = urllib.parse.unquote(app_name) if app_name else "NieznanaAplikacja"
    sformatowana_linia = f"{app_name_czysty}: {text_czysty}"
    try:
        przetworz_powiadomienie_push(sformatowana_linia, uzytkownik_id)
        return jsonify({"status": "success", "message": "Przetworzono pomyślnie"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
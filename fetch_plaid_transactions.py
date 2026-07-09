import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

# 1. Ładujemy klucze z pliku .env
folder_projektu = r'E:\Programowanie\FinanceApp'
sciezka_env = os.path.join(folder_projektu, '.env')
load_dotenv(dotenv_path=sciezka_env)

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")

# 2. Konfiguracja klienta Plaid
configuration = plaid.Configuration(
    host=plaid.Environment.Sandbox,
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_SECRET,
        'plaidClientId': PLAID_CLIENT_ID,
        'plaidSecret': PLAID_SECRET,
    }
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


def pobierz_transakcje_z_api():
    # Oficjalny, darmowy token testowy Plaid dla środowiska Sandbox
    ACCESS_TOKEN = "access-sandbox-2e41401b-d94b-4e2f-93b8-94b3693205c3"

    # Definiujemy zakres dat (np. ostatnie 30 dni)
    start_date = (datetime.now() - timedelta(days=30)).date()
    end_date = datetime.now().date()

    try:
        print(f"📡 Pobieram transakcje z Plaid od {start_date} do {end_date}...")

        # Tworzymy prośbę o pobranie transakcji
        request = TransactionsGetRequest(
            access_token=ACCESS_TOKEN,
            start_date=start_date,
            end_date=end_date,
            options=TransactionsGetRequestOptions(count=10)  # Pobierzmy na próbę 10 transakcji
        )

        response = client.transactions_get(request)
        transakcje = response['transactions']

        print(f"\n✅ Sukces! Pobrano {len(transakcje)} testowych transakcji z banku:\n")

        for t in transakcje:
            kwota = t['amount']
            data = t['date']
            opis = t['name']

            # W Plaidzie wydatki mają wartość DODATNIĄ, a przychody UJEMNĄ (odwrotnie niż zazwyczaj)
            typ = "WYDATEK" if kwota > 0 else "PRZYCHOD"
            kwota_abs = abs(kwota)

            print(f"📅 {data} | 🛒 {opis:<30} | 💰 {kwota_abs:>8} PLN | 🏷️ {typ}")

    except plaid.ApiException as e:
        print(f"❌ Błąd API Plaid: {e.body}")
    except Exception as e:
        print(f"❌ Wystąpił błąd: {e}")


if __name__ == "__main__":
    pobierz_transakcje_z_api()
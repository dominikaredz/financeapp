import os
import time
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
from plaid.model.link_token_create_request_auth import LinkTokenCreateRequestAuth

# 1. Wskazujemy dokładną ścieżkę do pliku .env
folder_projektu = r'E:\Programowanie\FinanceApp'
sciezka_env = os.path.join(folder_projektu, '.env')
load_dotenv(dotenv_path=sciezka_env)

# Pobieramy to, co masz obecnie w .env
CLIENT_ID_RAW = os.getenv("PLAID_CLIENT_ID")
SECRET_RAW = os.getenv("PLAID_SECRET")

if not CLIENT_ID_RAW or not SECRET_RAW:
    print("❌ Błąd: Dalej nie widzę kluczy w pliku .env!")
    exit()

# 2. ⚠️ KLUCZOWY MOMENT: Wymuszamy małe litery w nazwach kluczy dla konfiguracji Plaid
configuration = plaid.Configuration(
    host=plaid.Environment.Sandbox,
    api_key={
        'clientId': CLIENT_ID_RAW,  # <-- Zwróć uwagę na dokładną wielkość liter: clientId
        'secret': SECRET_RAW,      # <-- Zwróć uwagę na dokładną wielkość liter: secret
    }
)

# Dodatkowo wstrzykujemy je globalnie, bo biblioteka potrafi być kapryśna:
configuration.api_key['plaidClientId'] = CLIENT_ID_RAW
configuration.api_key['plaidSecret'] = SECRET_RAW

api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

print("✅ Klucze zostały poprawnie wczytane i sformatowane dla API Plaid!")
def generuj_link_token():
    try:
        user_id = str(int(time.time()))

        # Tworzymy prośbę dostosowaną do europejskiego Sandboxa
        request = LinkTokenCreateRequest(
            client_name="Moja Aplikacja Finansowa",
            country_codes=[CountryCode('PL')],  # Kraj: Polska
            language='pl',  # Język: polski
            user=LinkTokenCreateRequestUser(client_user_id=user_id),
            # Dla Europy/Polski bezpieczniej zacząć bez sztywnego wymuszania 'transactions' w tym miejscu,
            # lub pozwolić Plaidowi na automatyczne dopasowanie interfejsu:
            products=[Products('transactions')]
        )

        response = client.link_token_create(request)
        link_token = response['link_token']

        print("\n🚀 SUKCES! SYSTEM PLAID DZIAŁA POPRAWNIE.")
        print("Wygenerowano unikalny Link Token sesji:")
        print(f"👉 {link_token} 👈")

    except plaid.ApiException as e:
        # Rozbudowany print, który wyciągnie z serwera DOKŁADNY powód błędu 400
        print(f"❌ Błąd API Plaid (Status {e.status}): {e.body}")
    except Exception as e:
        print(f"❌ Wystąpił niespodziewany błąd: {e}")


if __name__ == "__main__":
    generuj_link_token()
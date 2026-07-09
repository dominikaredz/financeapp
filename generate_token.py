import os
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.products import Products

load_dotenv(dotenv_path=r'E:\Programowanie\FinanceApp\.env')

# Konfiguracja klienta
configuration = plaid.Configuration(
    host=plaid.Environment.Sandbox,
    api_key={'clientId': os.getenv("PLAID_CLIENT_ID"), 'secret': os.getenv("PLAID_SECRET")}
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


def stworz_prawdziwy_testowy_token():
    try:
        # 1. Symulujemy, że użytkownik wybrał testowy bank "ins_109511" (np. First Platypus Bank)
        print("🔄 Tworzę testowy Public Token...")
        pt_request = SandboxPublicTokenCreateRequest(
            institution_id="ins_109511",
            initial_products=[Products('transactions')]
        )
        pt_response = client.sandbox_public_token_create(pt_request)
        public_token = pt_response['public_token']

        # 2. Wymieniamy Public Token na stały Access Token
        print("🔄 Wymieniam na stały Access Token...")
        exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
        exchange_response = client.item_public_token_exchange(exchange_request)

        actual_access_token = exchange_response['access_token']

        print("\n🎯 MAMY GO! Oto Twój prawdziwy, unikalny token testowy:")
        print(f"👉 {actual_access_token} 👈")
        print("\nSkopiuj go i podmień w pliku fetch_plaid_transactions.py!")

    except Exception as e:
        print(f"❌ Błąd generowania tokenu: {e}")


if __name__ == "__main__":
    stworz_prawdziwy_testowy_token()
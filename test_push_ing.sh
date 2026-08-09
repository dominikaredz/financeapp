#!/bin/bash
# Testowe powiadomienia push symulujące appkę banku ING (celowo INNY bank niż
# Millennium/mBank, żeby sprawdzić czy nowa uniwersalna logika faktycznie działa
# dla dowolnej appki, bez żadnego hardkodowania nazw banków w kodzie).

URL="https://domiredz00.pythonanywhere.com/webhook/push"
TOKEN="RyTYbiCwdkOd4cGTBn3X7dPjoD_8O8I5"

echo "1) Zwykła płatność kartą (appka banku ZNA nazwę sklepu) -> powinno zapisać się od razu jako 'Biedronka'"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=ING Bank Mobile" \
  --data-urlencode "text=Platnosc karta: -45,99 PLN w BIEDRONKA 1234 WARSZAWA, data: 15.07.2026 12:34"
echo -e "\n"
sleep 3

echo "2) Platnosc BLIK -> test rozpoznania sklepu Zabka"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=ING Bank Mobile" \
  --data-urlencode "text=Transakcja BLIK: -22,50 PLN, ZABKA Z5678 KRAKOW, 15.07.2026 13:10"
echo -e "\n"
sleep 3

echo "3) Przelew przychodzacy z imieniem i nazwiskiem -> AI powinno zredagowac do samego imienia"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=ING Bank Mobile" \
  --data-urlencode "text=Przelew przychodzacy: 350,00 PLN od Tomasz Wisniewski, 15.07.2026 14:00"
echo -e "\n"
sleep 3

echo "4) SPAM MARKETINGOWY bez kwoty w formacie XX,XX -> powinno zostac ODRZUCONE, zero zapisu do bazy"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=ING Bank Mobile" \
  --data-urlencode "text=ING Bank: Sprawdz nowa oferte kredytu gotowkowego juz dzis!"
echo -e "\n"
sleep 3

echo "5a) Platnosc BEZ nazwy sklepu (appka banku jej nie zna) -> powinno zapisac sie jako generyczna 'Platnosc'"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=ING Bank Mobile" \
  --data-urlencode "text=Platnosc karta debetowa: Kwota: -19,99 PLN, Karta: Visa ****1234, Data: 15.07.2026 15:00"
echo -e "\n"
sleep 3

echo "5b) TA SAMA kwota/data z 'portfela' (inna appka) ZE znana nazwa sklepu -> powinno SCALIC sie z 5a, nadpisujac 'Platnosc' -> 'Rossmann'"
curl -s -X POST -G "$URL" \
  --data-urlencode "token=$TOKEN" \
  --data-urlencode "app_name=Portfel Testowy" \
  --data-urlencode "text=Zaplacono 19,99 zl w Rossmann"
echo -e "\n"

echo "Gotowe. Sprawdz panel - powinnas zobaczyc: Biedronka, Zabka, Tomasz, oraz JEDEN wpis Rossmann (nie dwa) na 19,99 zl. Spam z pkt. 4 nie powinien sie pojawic wcale."

# Lennupiletid

Otsib odavaid edasi-tagasi lende lähtelennujaamadest (vaikimisi Tallinn, Riia, Helsinki)
ühte või mitmesse sihtkohta ning kirjutab tulemused CSV-sse koos lingiga.

Andmeallikaid on kaks:

- **Momondo** (vaikimisi). Päris otsing momondo.ee-s, värsked hinnad paljudelt lennufirmadelt ja
  müüjatelt. Skript käivitab Chrome'i (või Edge'i) ja loeb tulemused samast sisemisest API-st, mida
  Momondo leht ise kasutab. Headless brauseri suunab Momondo robotilehele, seega töötab päris aken,
  mis on vaikimisi minimeeritud tegumiribale (`--show-browser` näitab seda). Üks otsing võtab umbes
  pool minutit.
- **Travelpayouts** (`--source travelpayouts`). [Aviasales Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163)
  vahemälu: kiire ja ilma brauserita, kuid sisaldab ainult hindu, mida keegi on hiljuti otsinud
  (leidmise kuupäev on veerus `found_date`). Populaarsetel suundadel (Kanaarid, Barcelona) piisab,
  harvematel (nt Maroko) on tulemus sageli tühi. Vahemälus on iga kuupäevapaari kohta ainult odavaim
  pilet, seepärast küsib skript `--max-duration` või `--max-stops` korral otselende eraldi juurde.
  Vajab tasuta API tokenit.

## Momondo otsing

Üks Momondo otsing katab kuni 10 lähte- ja 10 sihtlennujaama. Otsing käib kahes osas:

1. **Paindlik otsing** (±3 päeva nii väljumisel kui naasmisel) katab korraga kuni 49 kuupäevapaari.
   Skript jagab perioodi selliseteks plokkideks (nt 18.12–03.01 ja 3–7 ööd on 3 otsingut).
2. **Täpne otsing** kontrollib 10 soodsamat kuupäevapaari üle (`--refine`). Paindlik otsing uurib iga
   paari pinnapealsemalt ja täpne leiab sama paari kohta sageli 10–20% odavama pileti.

Filtrid (kuupäevad, ööd, `--max-duration`, `--max-stops`, `--no-self-transfer`) lähevad Momondole kaasa,
nii et iga otsingu 500 odavaima tulemuse hulka ei jää sobimatuid lende. Iga lennujaamade ja
kuupäevapaari kohta jääb CSV-sse odavaim pilet. Maroko näide (3 + 10 otsingut) võtab umbes 6 minutit.

Odavaimad pakkumised on sageli **self-transfer** piletid (veerg `self_transfer`): eraldi piletid, mille
vahel tuleb ise pagas uuesti registreerida ja turvakontroll läbida. Kui esimene lend hilineb, on järgmise
lennu kaotamine sinu riisiko. Võti `--no-self-transfer` jätab need välja.

`booking_link` avab momondo.ee-s just selle lennu (lehe ülaosas „Jagatud lend“), kui see on veel müügis.
`momondo_link` ja `google_flights_link` on sama marsruudi ja kuupäevade üldotsingud.

Kui Momondo peab otsingut robotiks, lõpetab skript ja salvestab seni leitud lennud; proovi mõne aja
pärast uuesti. Brauseri profiil (küpsised, nõusolekud) säilib kaustas `.momondo-profile`. Momondo
sisemine API pole avalik ja võib muutuda, siis vajab `momondo.py` kohendamist.

## Seadistus

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Momondo jaoks peab arvutis olema Chrome või Edge (muidu `python -m playwright install chromium`).
Travelpayoutsi jaoks: `copy .env.example .env` ja lisa oma `TRAVELPAYOUTS_TOKEN`.

## Kasutamine

```powershell
python lennud.py --to maroko --start 2026-12-18 --end 2027-01-03 `
    --min-nights 3 --max-nights 7 --max-duration 15 -o maroko.csv
```

| Argument | Tähendus |
|---|---|
| `--to` | IATA koodid ja/või regioonid `regions.json`-ist (nt `kanaarid`, `kreeka`, `tai`) |
| `--origins` | Lähtelennujaamad, vaikimisi `TLL,RIX,HEL` |
| `--start` / `--end` | Varaseim väljumine / hiliseim tagasijõudmine |
| `--min-nights` / `--max-nights` | Reisi pikkus öödes |
| `--max-duration` | Max reisiaeg tundides **ühes suunas** koos ümberistumiste ja ootamisega (veerg `total_*_h`) |
| `--max-stops` | Max ümberistumisi ühes suunas (`0` = otselend) |
| `--max-price` | Hinnalagi |
| `--no-self-transfer` | Ainult ühe piletiga ümberistumised (ainult Momondo) |
| `--refine` | Mitu soodsamat kuupäevapaari täpse otsinguga üle kontrollida, vaikimisi 10 (`0` = kiirem, aga ebatäpsem) |
| `--source` | `momondo` (vaikimisi) või `travelpayouts` |
| `--show-browser` | Näita Momondo otsingu Chrome'i akent (vaikimisi minimeeritud) |
| `--currency` | Valuuta, ainult Travelpayouts (momondo.ee hinnad on eurodes) |
| `--limit` | Ainult N odavaimat |
| `--delimiter` | CSV eraldaja, vaikimisi `;` (Eesti lokaadiga Excel) |

Uusi regioone saab lisada `regions.json` faili.

## CSV veerud

`price, currency, origin, destination, depart_date, depart_time, return_date, return_time, nights,
duration_out_h, duration_back_h, total_out_h, total_back_h, stops_out, stops_back, route_out, route_back,
self_transfer, airline, seller, found_date, booking_link, momondo_link, google_flights_link`

| Veerg | Tähendus |
|---|---|
| `duration_*_h` | Ainult lennuaeg õhus, ooteaeg ümberistumistel ei ole sees |
| `total_*_h` | Tegelik reisiaeg väljumisest saabumiseni koos ümberistumistega |
| `route_*` | Pileti lennujaamad, nt `HEL-STN-LTN-TFS`. Kui vahepealseid lennujaamu on rohkem kui ümberistumisi (`stops_*`), tuleb vahepeal lennujaama vahetada (siin Stanstedist Lutonisse) |
| `self_transfer` | `yes` = eraldi piletid, ümberistumine omal riisikol (Travelpayoutsi puhul alati tühi) |
| `seller` | Müüja (agentuur või lennufirma), kelle hinna otsing leidis |
| `found_date` | Millal hind leiti. Momondo puhul otsingu päev; Travelpayoutsi puhul mida vanem, seda tõenäolisemalt on pilet muutunud või otsas |

## Testid

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

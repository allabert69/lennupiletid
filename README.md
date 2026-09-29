# Lennupiletid

Otsib odavaid edasi-tagasi lende lähtelennujaamadest (vaikimisi Tallinn, Riia, Helsinki)
ühte või mitmesse sihtkohta ning kirjutab tulemused CSV-sse koos broneerimislingiga.

Andmeallikas on [Travelpayouts / Aviasales Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163)
(tasuta). Hinnad pärinevad vahemälust (kasutajate otsingud viimase ~48h jooksul), seega kontrolli
hinda enne ostu. Iga rea juures on ka Momondo ja Google Flights link sama kuupäevapaari otsinguga.

## Seadistus

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # ja lisa oma TRAVELPAYOUTS_TOKEN
```

## Kasutamine

```powershell
python lennud.py --to kanaarid,BCN,LIS --start 2026-11-01 --end 2026-12-15 `
    --min-nights 5 --max-nights 10 --max-duration 8 -o tulemused.csv
```

| Argument | Tähendus |
|---|---|
| `--to` | IATA koodid ja/või regioonid `regions.json`-ist (nt `kanaarid`, `kreeka`, `tai`) |
| `--origins` | Lähtelennujaamad, vaikimisi `TLL,RIX,HEL` |
| `--start` / `--end` | Varaseim väljumine / hiliseim tagasijõudmine |
| `--min-nights` / `--max-nights` | Reisi pikkus öödes |
| `--max-duration` | Max lennuaeg tundides **ühes suunas** (koos ümberistumistega) |
| `--max-stops` | Max ümberistumisi ühes suunas (`0` = otselend) |
| `--max-price` | Hinnalagi |
| `--limit` | Ainult N odavaimat |
| `--delimiter` | CSV eraldaja, vaikimisi `;` (Eesti lokaadiga Excel) |

Uusi regioone saab lisada `regions.json` faili.

## CSV veerud

`price, currency, origin, destination, depart_date, depart_time, return_date, return_time, nights,
duration_out_h, duration_back_h, stops_out, stops_back, airline, booking_link, momondo_link, google_flights_link`

## Testid

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

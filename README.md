# Lennupiletid

Otsib odavaid edasi-tagasi lende lähtelennujaamadest (vaikimisi Tallinn, Riia, Helsinki)
ühte või mitmesse sihtkohta ning kirjutab tulemused CSV-sse koos broneerimislingiga.

Andmeallikas on [Travelpayouts / Aviasales Data API](https://support.travelpayouts.com/hc/en-us/articles/203956163)
(tasuta). Hinnad pärinevad vahemälust (Aviasalesi kasutajate hiljutised otsingud, leidmise kuupäev
on veerus `found_date`), seega kontrolli hinda enne ostu. Vahemälus on iga kuupäevapaari kohta ainult
odavaim pilet, tavaliselt pikk ümberistumistega kombinatsioon. Seepärast küsib skript `--max-duration`
või `--max-stops` korral otselende eraldi juurde (päringuid on siis kaks korda rohkem).

`booking_link` viib just selle pileti juurde Aviasalesis: leht teeb uue otsingu ja tõstab selle pileti
esile, kui see on veel müügis. Kui piletit enam pole, näed tavalisi otsingutulemusi. Momondo ja
Google Flightsi lingid on üldotsingud sama marsruudi ja kuupäevadega. Konkreetse pileti leiad sealt
veergude `route_*`, `depart_time`/`return_time` ja `airline` järgi.

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
| `--max-duration` | Max reisiaeg tundides **ühes suunas** koos ümberistumiste ja ootamisega (veerg `total_*_h`) |
| `--max-stops` | Max ümberistumisi ühes suunas (`0` = otselend) |
| `--max-price` | Hinnalagi |
| `--limit` | Ainult N odavaimat |
| `--delimiter` | CSV eraldaja, vaikimisi `;` (Eesti lokaadiga Excel) |

Uusi regioone saab lisada `regions.json` faili.

## CSV veerud

`price, currency, origin, destination, depart_date, depart_time, return_date, return_time, nights,
duration_out_h, duration_back_h, total_out_h, total_back_h, stops_out, stops_back, route_out, route_back,
airline, seller, found_date, booking_link, momondo_link, google_flights_link`

| Veerg | Tähendus |
|---|---|
| `duration_*_h` | Ainult lennuaeg õhus (API väli), ooteaeg ümberistumistel ei ole sees |
| `total_*_h` | Tegelik reisiaeg väljumisest saabumiseni koos ümberistumistega |
| `route_*` | Pileti lennujaamad, nt `HEL-STN-LTN-TFS`. Kui vahepealseid lennujaamu on rohkem kui ümberistumisi (`stops_*`), tuleb vahepeal lennujaama vahetada (siin Stanstedist Lutonisse) |
| `seller` | Müüja (agentuur), kelle hinna Aviasales leidis |
| `found_date` | Millal hind leiti. Mida vanem, seda tõenäolisemalt on pilet muutunud või otsas |

## Testid

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

"""Odavate edasi-tagasi lennupiletite otsija.

Näide:
    python lennud.py --to kanaarid,BCN --start 2026-11-01 --end 2026-12-15 \
        --min-nights 5 --max-nights 10 --max-duration 8 -o tulemused.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from travelpayouts import TravelpayoutsClient, TravelpayoutsError

HERE = Path(__file__).resolve().parent
DEFAULT_ORIGINS = ["TLL", "RIX", "HEL"]

# Aviasalesi lingi t= parameeter kirjeldab konkreetset piletit: lennufirma (2 märki), siis iga suuna kohta
# väljumine ja saabumine (kohalik aeg Unix-sekunditena), kestus minutites ja lennujaamad; lõpus _<räsi>_<hind>.
TICKET_LEG = re.compile(r"(\d{10})(\d{10})(\d{6})((?:[A-Z]{3})+)")

CSV_COLUMNS = [
    "price",
    "currency",
    "origin",
    "destination",
    "depart_date",
    "depart_time",
    "return_date",
    "return_time",
    "nights",
    "duration_out_h",
    "duration_back_h",
    "total_out_h",
    "total_back_h",
    "stops_out",
    "stops_back",
    "route_out",
    "route_back",
    "airline",
    "seller",
    "found_date",
    "booking_link",
    "momondo_link",
    "google_flights_link",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def load_regions() -> dict[str, list[str]]:
    with open(HERE / "regions.json", encoding="utf-8") as f:
        return {k.lower(): v for k, v in json.load(f).items()}


def expand_destinations(raw: str, regions: dict[str, list[str]]) -> list[str]:
    """'kanaarid,BCN' -> ['LPA', 'TFS', ..., 'BCN'] (järjekord säilib, duplikaadid välja)."""
    codes: list[str] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        if token.lower() in regions:
            codes.extend(regions[token.lower()])
        elif len(token) == 3 and token.isalpha():
            codes.append(token.upper())
        else:
            known = ", ".join(sorted(regions))
            raise SystemExit(f"Tundmatu sihtkoht/regioon '{token}'. Teadaolevad regioonid: {known}")
    return list(dict.fromkeys(codes))


def months_between(start: date, end: date) -> list[str]:
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours(minutes: int | None) -> float | str:
    return round(minutes / 60, 1) if minutes is not None else ""


def ticket_legs(link: str) -> list[tuple[list[str], int]]:
    """Pileti lennujaamad ja kogukestus (min, koos ümberistumistega) suundade kaupa, nt
    [(["HEL", "STN", "LTN", "TFS"], 1380), (["TFS", "HEL"], 375)]; [] kui lingis pileti koodi pole."""
    code = parse_qs(urlparse(link).query).get("t", [""])[0].split("_", 1)[0][2:]
    legs = TICKET_LEG.findall(code)
    if not legs or "".join("".join(leg) for leg in legs) != code:
        return []
    return [([a[i:i + 3] for i in range(0, len(a), 3)], int(minutes)) for _, _, minutes, a in legs]


def ticket_found_date(link: str) -> date | None:
    """Millal hind leiti (lingi search_date=DDMMYYYY)."""
    raw = parse_qs(urlparse(link).query).get("search_date", [""])[0]
    try:
        return datetime.strptime(raw, "%d%m%Y").date()
    except ValueError:
        return None


def momondo_link(origin: str, dest: str, out: date, back: date) -> str:
    return f"https://www.momondo.com/flight-search/{origin}-{dest}/{out}/{back}?sort=price_a"


def google_flights_link(origin: str, dest: str, out: date, back: date) -> str:
    q = f"Flights from {origin} to {dest} on {out} through {back}"
    return f"https://www.google.com/travel/flights?q={quote(q)}"


def to_row(item: dict, currency: str) -> dict | None:
    if not item.get("return_at"):
        return None
    dep = parse_dt(item["departure_at"])
    ret = parse_dt(item["return_at"])
    origin = item.get("origin_airport") or item["origin"]
    dest = item.get("destination_airport") or item["destination"]
    link = item.get("booking_link", "")
    legs = ticket_legs(link)
    if len(legs) != 2:
        legs = [([], None), ([], None)]
    (route_out, total_out), (route_back, total_back) = legs
    return {
        "price": item["price"],
        "currency": currency.upper(),
        "origin": origin,
        "destination": dest,
        "depart_date": dep.date(),
        "depart_time": dep.strftime("%H:%M"),
        "return_date": ret.date(),
        "return_time": ret.strftime("%H:%M"),
        "nights": (ret.date() - dep.date()).days,
        "duration_out_h": hours(item.get("duration_to")),
        "duration_back_h": hours(item.get("duration_back")),
        "total_out_h": hours(total_out),
        "total_back_h": hours(total_back),
        "stops_out": item.get("transfers", ""),
        "stops_back": item.get("return_transfers", ""),
        "route_out": "-".join(route_out),
        "route_back": "-".join(route_back),
        "airline": item.get("airline", ""),
        "seller": item.get("gate", ""),
        "found_date": ticket_found_date(link) or "",
        "booking_link": link,
        "momondo_link": momondo_link(origin, dest, dep.date(), ret.date()),
        "google_flights_link": google_flights_link(origin, dest, dep.date(), ret.date()),
    }


def passes_filters(row: dict, args: argparse.Namespace) -> bool:
    if not (args.start <= row["depart_date"] and row["return_date"] <= args.end):
        return False
    if not (args.min_nights <= row["nights"] <= args.max_nights):
        return False
    if args.max_duration is not None:
        limit = args.max_duration
        # Kui kestus puudub, ei saa filtrit kontrollida -> jätame välja.
        for key in ("total_out_h", "total_back_h"):
            if row[key] == "" or row[key] > limit:
                return False
    if args.max_stops is not None:
        for key in ("stops_out", "stops_back"):
            if row[key] != "" and row[key] > args.max_stops:
                return False
    if args.max_price is not None and row["price"] > args.max_price:
        return False
    return True


def cheapest_unique(rows: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["origin"], r["destination"], r["depart_date"], r["return_date"])
        if key not in best or r["price"] < best[key]["price"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: (r["price"], r["depart_date"]))


def write_csv(rows: list[dict], path: str, delimiter: str) -> None:
    # utf-8-sig, et Excel avaks täpitähed õigesti
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def search(args: argparse.Namespace, client: TravelpayoutsClient) -> list[dict]:
    regions = load_regions()
    destinations = expand_destinations(args.to, regions)
    origins = [o.strip().upper() for o in args.origins.split(",") if o.strip()]
    months = months_between(args.start, args.end)
    # Vahemälus on kuupäevapaari kohta vaid odavaim pilet, tavaliselt pikk ümberistumistega kombinatsioon.
    # Kestuse või ümberistumiste filtri korral küsime otselende eraldi, muidu jääksid need selle taha peitu.
    direct_modes = [False, True] if args.max_duration is not None or args.max_stops is not None else [False]
    queries = list(itertools.product(origins, destinations, months, direct_modes))

    rows: list[dict] = []
    for n, (origin, dest, month, direct) in enumerate(queries, 1):
        print(f"[{n}/{len(queries)}] {origin} -> {dest} {month}{' otse' if direct else ''}", file=sys.stderr)
        try:
            items = client.round_trips(origin, dest, month, direct=direct)
        except TravelpayoutsError:
            raise
        except Exception as exc:  # võrguviga ühe päringu puhul ei peata kogu otsingut
            print(f"  hoiatus: {exc}", file=sys.stderr)
            continue
        for item in items:
            row = to_row(item, args.currency)
            if row and passes_filters(row, args):
                rows.append(row)
    return cheapest_unique(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Odavate edasi-tagasi lendude otsing (Travelpayouts/Aviasales).")
    p.add_argument("--to", required=True,
                   help="Sihtkohad komadega: IATA koodid ja/või regioonid regions.json-ist, nt 'kanaarid,BCN,LIS'")
    p.add_argument("--origins", default=",".join(DEFAULT_ORIGINS),
                   help="Lähtelennujaamad (vaikimisi TLL,RIX,HEL)")
    p.add_argument("--start", required=True, type=date.fromisoformat, help="Varaseim väljumiskuupäev YYYY-MM-DD")
    p.add_argument("--end", required=True, type=date.fromisoformat, help="Hiliseim tagasijõudmise kuupäev YYYY-MM-DD")
    p.add_argument("--min-nights", type=int, default=1)
    p.add_argument("--max-nights", type=int, default=30)
    p.add_argument("--max-duration", type=float, default=None,
                   help="Max reisiaeg tundides ühes suunas koos ümberistumiste ja ootamisega")
    p.add_argument("--max-stops", type=int, default=None, help="Max ümberistumisi ühes suunas (0 = otselend)")
    p.add_argument("--max-price", type=float, default=None)
    p.add_argument("--currency", default="eur")
    p.add_argument("--limit", type=int, default=None, help="Kirjuta CSV-sse ainult N odavaimat")
    p.add_argument("-o", "--output", default="lennud.csv")
    p.add_argument("--delimiter", default=";", help="CSV eraldaja (vaikimisi ';' – Eesti Excel)")
    p.add_argument("--token", default=None, help="Travelpayouts API token (või TRAVELPAYOUTS_TOKEN)")
    args = p.parse_args(argv)
    if args.end < args.start:
        p.error("--end peab olema hiljem kui --start")
    if args.min_nights > args.max_nights:
        p.error("--min-nights ei tohi olla suurem kui --max-nights")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv(HERE / ".env")
    args = parse_args(argv)
    token = args.token or os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        print("Puudub API token: pane TRAVELPAYOUTS_TOKEN .env faili või kasuta --token.", file=sys.stderr)
        return 2

    client = TravelpayoutsClient(token, currency=args.currency)
    try:
        rows = search(args, client)
    except TravelpayoutsError as exc:
        print(f"Viga: {exc}", file=sys.stderr)
        return 1

    if args.limit:
        rows = rows[: args.limit]
    write_csv(rows, args.output, args.delimiter)
    print(f"Leitud {len(rows)} varianti -> {args.output}", file=sys.stderr)
    for r in rows[:5]:
        print(f"  {r['price']} {r['currency']}  {r['origin']}->{r['destination']}  "
              f"{r['depart_date']} – {r['return_date']} ({r['nights']} ööd)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

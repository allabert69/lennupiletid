"""Odavate edasi-tagasi lennupiletite otsija.

Näide:
    python lennud.py --to kanaarid,BCN --start 2026-11-01 --end 2026-12-15 \
        --min-nights 5 --max-nights 10 --max-duration 8 -o tulemused.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from travelpayouts import TravelpayoutsClient, TravelpayoutsError

HERE = Path(__file__).resolve().parent
DEFAULT_ORIGINS = ["TLL", "RIX", "HEL"]

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
    "stops_out",
    "stops_back",
    "airline",
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
    dur_out = item.get("duration_to")
    dur_back = item.get("duration_back")
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
        "duration_out_h": round(dur_out / 60, 1) if dur_out is not None else "",
        "duration_back_h": round(dur_back / 60, 1) if dur_back is not None else "",
        "stops_out": item.get("transfers", ""),
        "stops_back": item.get("return_transfers", ""),
        "airline": item.get("airline", ""),
        "booking_link": item.get("booking_link", ""),
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
        for key in ("duration_out_h", "duration_back_h"):
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

    rows: list[dict] = []
    total = len(origins) * len(destinations) * len(months)
    n = 0
    for origin in origins:
        for dest in destinations:
            for month in months:
                n += 1
                print(f"[{n}/{total}] {origin} -> {dest} {month}", file=sys.stderr)
                try:
                    items = client.round_trips(origin, dest, month)
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
                   help="Max lennuaeg tundides ühes suunas (koos ümberistumistega)")
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

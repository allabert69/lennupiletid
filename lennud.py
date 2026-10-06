"""Odavate edasi-tagasi lennupiletite otsija (vaikimisi Momondo, valikuliselt Travelpayouts).

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

import momondo
from travelpayouts import TravelpayoutsClient, TravelpayoutsError

HERE = Path(__file__).resolve().parent
DEFAULT_ORIGINS = [
    "TLL", 
    "RIX", 
    "HEL"
    ]

# Otsing, mis käivitub, kui lennud.py käivitada ilma argumentideta (nt VS Code'i Run-nupuga).
# Samad võtmed mis käsureal (muud võtmed: python lennud.py --help); käsurea argumentidega seda ei kasutata.
target = "maroko"
DEFAULT_SEARCH = f"""
    --to {target} 
    --start 2027-04-09 
    --end 2027-04-18
    --min-nights 3 
    --max-nights 8 
    --max-duration 15 
    --no-self-transfer
    -o {target}.csv
""".split()

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
    "self_transfer",
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
    return momondo.search_url([origin], [dest], out, back)


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
        "self_transfer": "",  # Travelpayouts seda ei ütle
        "airline": item.get("airline", ""),
        "seller": item.get("gate", ""),
        "found_date": ticket_found_date(link) or "",
        "booking_link": link,
        "momondo_link": momondo_link(origin, dest, dep.date(), ret.date()),
        "google_flights_link": google_flights_link(origin, dest, dep.date(), ret.date()),
    }


def route_of(segments: list[dict]) -> str:
    """'HEL-BGY-MXP-RAK': lennujaama vahetus (BGY -> MXP) jääb näha kahe järjestikuse koodina."""
    airports: list[str] = []
    for s in segments:
        if not airports or airports[-1] != s["origin"]:
            airports.append(s["origin"])
        airports.append(s["destination"])
    return "-".join(airports)


def momondo_row(trip: dict) -> dict:
    out, back = trip["legs"]
    dep = datetime.fromisoformat(out["departure"])
    ret = datetime.fromisoformat(back["departure"])
    origin = out["segments"][0]["origin"]
    dest = out["segments"][-1]["destination"]
    # Tagasilend võib alata/lõppeda sama linna teises lennujaamas; siis peab link otsima mõlemat.
    link_origins = list(dict.fromkeys([origin, back["segments"][-1]["destination"]]))
    link_dests = list(dict.fromkeys([dest, back["segments"][0]["origin"]]))
    airlines = dict.fromkeys(s["airline"] for leg in trip["legs"] for s in leg["segments"])
    return {
        "price": trip["price"],
        "currency": trip["currency"],
        "origin": origin,
        "destination": dest,
        "depart_date": dep.date(),
        "depart_time": dep.strftime("%H:%M"),
        "return_date": ret.date(),
        "return_time": ret.strftime("%H:%M"),
        "nights": (ret.date() - dep.date()).days,
        "duration_out_h": hours(sum(s["duration"] for s in out["segments"])),
        "duration_back_h": hours(sum(s["duration"] for s in back["segments"])),
        "total_out_h": hours(out["duration"]),
        "total_back_h": hours(back["duration"]),
        "stops_out": len(out["segments"]) - 1,
        "stops_back": len(back["segments"]) - 1,
        "route_out": route_of(out["segments"]),
        "route_back": route_of(back["segments"]),
        "self_transfer": "yes" if trip["self_transfer"] else "",
        "airline": ", ".join(airlines),
        "seller": trip["seller"],
        "found_date": date.today(),
        "booking_link": momondo.search_url(link_origins, link_dests, dep.date(), ret.date(),
                                           result_id=trip["result_id"]),
        "momondo_link": momondo.search_url(link_origins, link_dests, dep.date(), ret.date()),
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
    if args.no_self_transfer and row["self_transfer"]:
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


def parse_origins(raw: str) -> list[str]:
    return [o.strip().upper() for o in raw.split(",") if o.strip()]


def best_date_pairs(rows: list[dict], n: int) -> list[tuple[date, date]]:
    """n kuupäevapaari, mille odavaim lend on kõige soodsam."""
    best: dict[tuple[date, date], float] = {}
    for r in rows:
        key = (r["depart_date"], r["return_date"])
        best[key] = min(best.get(key, r["price"]), r["price"])
    return sorted(best, key=lambda k: (best[k], k))[:n]


def momondo_trips(client: momondo.MomondoClient, args: argparse.Namespace, origins: list[str],
                  destinations: list[str], depart: date, return_: date, fs: str, flex: bool) -> list[dict]:
    """Ühe Momondo otsingu filtritele vastavad read; blokeerimise korral MomondoError."""
    try:
        trips = client.round_trips(origins, destinations, depart, return_, fs, flex=flex)
    except momondo.MomondoError:
        raise
    except Exception as exc:  # üks ebaõnnestunud otsing ei peata ülejäänuid
        print(f"  hoiatus: {exc}", file=sys.stderr)
        return []
    rows = [row for row in map(momondo_row, trips) if passes_filters(row, args)]
    print(f"  {len(trips)} lendu, neist sobivad {len(rows)}", file=sys.stderr)
    return rows


def search_momondo(args: argparse.Namespace, client: momondo.MomondoClient) -> list[dict]:
    destinations = expand_destinations(args.to, load_regions())
    origins = parse_origins(args.origins)
    groups = list(itertools.product(momondo.chunks(origins), momondo.chunks(destinations)))
    blocks = momondo.flex_blocks(max(args.start, date.today()), args.end, args.min_nights, args.max_nights)
    common = {
        "max_leg_minutes": round(args.max_duration * 60) if args.max_duration is not None else None,
        "max_stops": args.max_stops,
        "no_self_transfer": args.no_self_transfer,
    }
    print(f"Momondo: {len(blocks) * len(groups)} paindlikku otsingut (±3 päeva), seejärel kuni {args.refine} "
          f"soodsamat kuupäevapaari täpse otsinguga. Otsing võtab umbes pool minutit; Chrome töötab "
          f"{'nähtavas aknas' if args.show_browser else 'minimeeritult tegumiribal'}, ära seda sule.",
          file=sys.stderr)

    rows: list[dict] = []
    try:
        for n, (block, (orig, dest)) in enumerate(itertools.product(blocks, groups), 1):
            print(f"[{n}/{len(blocks) * len(groups)}] {','.join(orig)} -> {','.join(dest)}  "
                  f"väljumine {block.depart_dates[0]:%d.%m}–{block.depart_dates[-1]:%d.%m}, "
                  f"naasmine {block.return_dates[0]:%d.%m}–{block.return_dates[-1]:%d.%m}", file=sys.stderr)
            fs = momondo.filters(**common, block=block, min_nights=args.min_nights, max_nights=args.max_nights)
            rows += momondo_trips(client, args, orig, dest, block.depart, block.return_, fs, flex=True)
        # Paindlik otsing uurib iga kuupäevapaari pinnapealselt; soodsamad paarid otsime täpse kuupäevaga uuesti.
        exact = list(itertools.product(best_date_pairs(rows, args.refine), groups))
        for n, ((depart, return_), (orig, dest)) in enumerate(exact, 1):
            print(f"[täpne {n}/{len(exact)}] {','.join(orig)} -> {','.join(dest)}  "
                  f"{depart:%d.%m}–{return_:%d.%m}", file=sys.stderr)
            rows += momondo_trips(client, args, orig, dest, depart, return_, momondo.filters(**common), flex=False)
    except momondo.MomondoError as exc:  # Momondo blokeeris: lõpetame, aga seni leitu salvestatakse
        print(f"Viga: {exc}", file=sys.stderr)
    return cheapest_unique(rows)


def search_travelpayouts(args: argparse.Namespace, client: TravelpayoutsClient) -> list[dict]:
    regions = load_regions()
    destinations = expand_destinations(args.to, regions)
    origins = parse_origins(args.origins)
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
    p = argparse.ArgumentParser(description="Odavate edasi-tagasi lendude otsing (Momondo või Travelpayouts).")
    p.add_argument("--source", choices=["momondo", "travelpayouts"], default="momondo",
                   help="momondo: päris otsing Chrome'iga (vaikimisi); travelpayouts: kiire, kuid hõre vahemälu")
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
    p.add_argument("--show-browser", action="store_true",
                   help="Näita Momondo otsingu Chrome'i akent (vaikimisi minimeeritud)")
    p.add_argument("--no-self-transfer", action="store_true",
                   help="Jäta välja eraldi piletitega ümberistumised (ümberistumine omal riisikol; ainult Momondo)")
    p.add_argument("--refine", type=int, default=10,
                   help="Mitu soodsamat kuupäevapaari täpse otsinguga üle kontrollida (Momondo; 0 = ei kontrolli)")
    p.add_argument("--currency", default="eur", help="Valuuta (ainult Travelpayouts; momondo.ee hinnad on eurodes)")
    p.add_argument("--limit", type=int, default=None, help="Kirjuta CSV-sse ainult N odavaimat")
    p.add_argument("-o", "--output", default="lennud.csv")
    p.add_argument("--delimiter", default=";", help="CSV eraldaja (vaikimisi ';' – Eesti Excel)")
    p.add_argument("--token", default=None, help="Travelpayouts API token (või TRAVELPAYOUTS_TOKEN)")
    args = p.parse_args(argv)
    if args.end < args.start:
        p.error("--end peab olema hiljem kui --start")
    if args.min_nights > args.max_nights:
        p.error("--min-nights ei tohi olla suurem kui --max-nights")
    if (args.end - args.start).days < args.min_nights:
        p.error("--start ja --end vahele ei mahu --min-nights ööd")
    return args


def main(argv: list[str] | None = None) -> int:
    load_dotenv(HERE / ".env")
    args = parse_args(argv)
    if args.source == "momondo":
        try:
            with momondo.MomondoClient(HERE / ".momondo-profile", show_window=args.show_browser) as client:
                rows = search_momondo(args, client)
        except momondo.MomondoError as exc:
            print(f"Viga: {exc}", file=sys.stderr)
            return 1
    else:
        token = args.token or os.environ.get("TRAVELPAYOUTS_TOKEN")
        if not token:
            print("Puudub API token: pane TRAVELPAYOUTS_TOKEN .env faili või kasuta --token.", file=sys.stderr)
            return 2
        client = TravelpayoutsClient(token, currency=args.currency)
        try:
            rows = search_travelpayouts(args, client)
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
    sys.exit(main(sys.argv[1:] or DEFAULT_SEARCH))

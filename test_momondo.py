from datetime import date, timedelta

import pytest

import lennud
import momondo


def days(first, last):
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def raw_segment(airline, origin, dest, departure, arrival, minutes):
    return {"airline": airline, "origin": origin, "destination": dest, "departure": f"{departure}:00",
            "arrival": f"{arrival}:00", "duration": minutes}


def raw_leg(departure, arrival, minutes, *segment_ids):
    return {"departure": f"{departure}:00", "arrival": f"{arrival}:00", "duration": minutes,
            "segments": [{"id": s} for s in segment_ids]}


def payload():
    """Momondo poll-vastuse lühendatud kuju: tulemused viitavad lõikudele (legs) ja need segmentidele."""
    segments = {  # kohalikud kellaajad: HEL, TLL UTC+2; BGY, MXP, CDG UTC+1; RAK, CMN UTC+0
        "s1": raw_segment("FR", "HEL", "BGY", "2026-12-20T22:45", "2026-12-21T00:55", 190),
        "s2": raw_segment("U2", "MXP", "RAK", "2026-12-21T12:35", "2026-12-21T14:05", 150),  # lennujaama vahetus
        "s3": raw_segment("FR", "RAK", "HEL", "2026-12-27T15:00", "2026-12-27T23:40", 400),
        "s4": raw_segment("BT", "TLL", "CDG", "2026-12-21T07:00", "2026-12-21T09:20", 200),
        "s5": raw_segment("AT", "CDG", "CMN", "2026-12-21T12:50", "2026-12-21T15:00", 190),
        "s6": raw_segment("AT", "CMN", "CDG", "2026-12-26T09:00", "2026-12-26T13:00", 180),
        "s7": raw_segment("BT", "CDG", "TLL", "2026-12-26T16:10", "2026-12-26T20:20", 190),
    }
    legs = {
        "out1": raw_leg("2026-12-20T22:45", "2026-12-21T14:05", 1040, "s1", "s2"),
        "back1": raw_leg("2026-12-27T15:00", "2026-12-27T23:40", 400, "s3"),
        "out2": raw_leg("2026-12-21T07:00", "2026-12-21T15:00", 600, "s4", "s5"),
        "back2": raw_leg("2026-12-26T09:00", "2026-12-26T20:20", 560, "s6", "s7"),
    }

    def result(rid, prices, leg_ids, warnings=(), provider="KIWIVILCC"):
        return {
            "type": "core", "resultId": rid, "warnings": list(warnings),
            "legs": [{"id": leg, "segments": []} for leg in leg_ids],
            "bookingOptions": [{"providerCode": provider, "displayPrice": {"price": p, "currency": "EUR"}}
                               for p in prices],
        }

    return {
        "results": [
            result("aaa", [283, 211], ["out1", "back1"], warnings=["SELF_TRANSFER"]),
            {"type": "inlineAd"},
            result("bbb", [437], ["out2", "back2"], provider="AIRBALTIC"),
            {**result("ccc", [100], ["out2", "back2"]), "bookingOptions": []},
            result("ddd", [90], ["out2", "missing"]),
        ],
        "legs": legs,
        "segments": segments,
        "providers": {"KIWIVILCC": {"displayName": "Kiwi.com"}},
        "airlines": {"FR": {"name": "Ryanair"}, "U2": {"name": "easyJet"}},
    }


def test_parse_results_skips_ads_and_incomplete():
    trips = momondo.parse_results(payload())
    assert [(t["result_id"], t["price"], t["seller"], t["self_transfer"]) for t in trips] == [
        ("aaa", 211, "Kiwi.com", True),  # odavaim müüja valik; nimi providers-ist
        ("bbb", 437, "AIRBALTIC", False),  # nimeta müüja -> kood
    ]


def test_momondo_row():
    trip = momondo.parse_results(payload())[0]
    row = lennud.momondo_row(trip)
    assert (row["origin"], row["destination"], row["route_out"], row["route_back"]) == (
        "HEL", "RAK", "HEL-BGY-MXP-RAK", "RAK-HEL")
    assert (row["depart_date"], row["depart_time"], row["return_date"], row["nights"]) == (
        date(2026, 12, 20), "22:45", date(2026, 12, 27), 7)
    assert (row["duration_out_h"], row["total_out_h"], row["stops_out"], row["stops_back"]) == (5.7, 17.3, 1, 0)
    assert (row["airline"], row["self_transfer"], row["currency"]) == ("Ryanair, easyJet", "yes", "EUR")
    assert row["booking_link"] == (
        "https://www.momondo.ee/flight-search/HEL-RAK/2026-12-20/2026-12-27/faaa?sort=price_a")
    assert row["momondo_link"] == "https://www.momondo.ee/flight-search/HEL-RAK/2026-12-20/2026-12-27?sort=price_a"


def test_duration_from_local_times_and_time_zones():
    def minutes(origin, dest, departure, arrival, momondo_minutes=1):
        return momondo.duration({"departure": departure, "arrival": arrival, "duration": momondo_minutes},
                                origin, dest)

    assert minutes("HEL", "RAK", "2027-01-20T09:30:00", "2027-01-20T13:00:00") == 330  # Maroko UTC+0, HEL UTC+2
    # Suveaja algus 28.03.2027 (kell 2 -> 3): sama ajavööndi lend on tund lühem, kui kellaajad näitavad.
    assert minutes("MAD", "AGP", "2027-03-28T00:30:00", "2027-03-28T03:30:00") == 120
    assert minutes("HEL", "XXX", "2027-01-20T09:30:00", "2027-01-20T13:00:00", 270) == 270  # tundmatu lennujaam
    assert minutes("HEL", "RAK", "2027-01-20T09:30:00", "2027-01-20T07:00:00", 270) == 270  # vigased kellaajad


def test_morocco_durations_follow_time_zones():
    # Päris Finnairi lend (jaanuar 2027). Momondo arvestab Marokos veel UTC+1, kuigi seal on alates
    # 20.09.2026 UTC+0: tema järgi kestab lend Agadiri 4 h 50 min ja tagasi 6 h 40 min.
    p = {
        "results": [{"type": "core", "resultId": "x", "legs": [{"id": "out"}, {"id": "back"}],
                     "bookingOptions": [{"providerCode": "FINNAIR",
                                         "displayPrice": {"price": 484, "currency": "EUR"}}]}],
        "legs": {"out": raw_leg("2027-01-20T11:35", "2027-01-20T15:25", 290, "s1"),
                 "back": raw_leg("2027-01-27T16:15", "2027-01-27T23:55", 400, "s2")},
        "segments": {"s1": raw_segment("AY", "HEL", "AGA", "2027-01-20T11:35", "2027-01-20T15:25", 290),
                     "s2": raw_segment("AY", "AGA", "HEL", "2027-01-27T16:15", "2027-01-27T23:55", 400)},
    }
    row = lennud.momondo_row(momondo.parse_results(p)[0])
    assert (row["total_out_h"], row["total_back_h"]) == (5.8, 5.7)  # 5 h 50 min ja 5 h 40 min
    assert (row["duration_out_h"], row["duration_back_h"]) == (5.8, 5.7)


@pytest.mark.parametrize("start, end, min_nights, max_nights", [
    (date(2026, 12, 18), date(2027, 1, 3), 3, 7),
    (date(2026, 11, 1), date(2026, 12, 15), 5, 10),
    (date(2026, 11, 1), date(2026, 11, 30), 1, 30),
    (date(2026, 12, 20), date(2026, 12, 27), 7, 7),
])
def test_flex_blocks_cover_every_valid_trip(start, end, min_nights, max_nights):
    blocks = momondo.flex_blocks(start, end, min_nights, max_nights)
    for b in blocks:  # iga plokk mahub ühte ±3 päeva otsingusse
        assert all(abs((d - b.depart).days) <= 3 for d in b.depart_dates)
        assert all(abs((r - b.return_).days) <= 3 for r in b.return_dates)
    covered = {(d, r) for b in blocks for d in b.depart_dates for r in b.return_dates}
    valid = {(d, r) for d in days(start, end) for r in days(start, end) if min_nights <= (r - d).days <= max_nights}
    assert valid <= covered


def test_flex_blocks_morocco_needs_three_searches():
    blocks = momondo.flex_blocks(date(2026, 12, 18), date(2027, 1, 3), 3, 7)
    assert [(b.depart, b.return_) for b in blocks] == [
        (date(2026, 12, 21), date(2026, 12, 24)),
        (date(2026, 12, 21), date(2026, 12, 31)),
        (date(2026, 12, 28), date(2026, 12, 31)),
    ]


def test_filters():
    block = momondo.FlexBlock(date(2026, 12, 21), date(2026, 12, 24), days(date(2026, 12, 18), date(2026, 12, 19)),
                              days(date(2026, 12, 21), date(2026, 12, 22)))
    assert momondo.filters(900, 1, True, block=block, min_nights=3, max_nights=7) == (
        "baditin=baditin;flexdepart=20261218,20261219;flexreturn=20261221,20261222;triplength=3-4;"
        "legdur=-960;stops=0,1;virtualinterline=-virtualinterline")  # reisiajale tund varu
    assert momondo.filters(max_stops=2) == "baditin=baditin"  # 2+ ümberistumist kontrollime ise


def test_chunks_respect_airport_limit():
    codes = [f"A{i:02d}" for i in range(21)]
    assert [len(c) for c in momondo.chunks(codes[:12])] == [6, 6]
    assert [len(c) for c in momondo.chunks(codes)] == [7, 7, 7]
    assert momondo.chunks(codes[:3]) == [codes[:3]]


def test_search_url():
    assert momondo.search_url(["TLL", "RIX"], ["RAK"], date(2026, 12, 21), date(2026, 12, 24), flex=True,
                              fs="legdur=-900;stops=0") == (
        "https://www.momondo.ee/flight-search/TLL,RIX-RAK/2026-12-21-flexible-3days/2026-12-24-flexible-3days"
        "?sort=price_a&fs=legdur%3D-900%3Bstops%3D0")


def trip(origin, dest, dep, ret, price, total_out=600, self_transfer=False, rid="x"):
    def leg(a, b, when, total):
        return {"departure": f"{when}T10:00:00", "duration": total,
                "segments": [{"airline": "BT", "origin": a, "destination": b, "duration": total}]}
    return {"result_id": rid, "price": price, "currency": "EUR", "seller": "airBaltic",
            "self_transfer": self_transfer, "legs": [leg(origin, dest, dep, total_out), leg(dest, origin, ret, 300)]}


class FakeMomondo:
    """Paindlik otsing annab lennud ±3 päeva piires, täpne ainult antud kuupäevadel (ja lisaks exact_only)."""

    def __init__(self, trips, exact_only=()):
        self.trips = trips
        self.exact_only = list(exact_only)
        self.calls = []

    def round_trips(self, origins, destinations, depart, return_, fs="", flex=False):
        self.calls.append((origins, destinations, depart, return_, flex, fs))
        span = 3 if flex else 0
        return [t for t in (self.trips if flex else self.trips + self.exact_only)
                if abs((date.fromisoformat(t["legs"][0]["departure"][:10]) - depart).days) <= span
                and abs((date.fromisoformat(t["legs"][1]["departure"][:10]) - return_).days) <= span]


START = date.today() + timedelta(days=60)


def d(n):
    return START + timedelta(days=n)


def run_momondo(trips, *extra, exact_only=()):
    client = FakeMomondo(trips, exact_only)
    args = lennud.parse_args(["--to", "RAK,CMN", "--origins", "TLL,HEL", "--start", str(d(0)), "--end", str(d(16)),
                              "--min-nights", "3", "--max-nights", "7", *extra])
    return lennud.search_momondo(args, client), client


def test_search_momondo_filters_and_dedups():
    trips = [
        trip("TLL", "RAK", d(2), d(7), 300),
        trip("TLL", "RAK", d(2), d(7), 250),  # sama kuupäevapaar odavamalt -> jääb see
        trip("HEL", "RAK", d(2), d(7), 200, self_transfer=True),  # --no-self-transfer
        trip("HEL", "CMN", d(3), d(8), 150, total_out=1000),  # reisiaeg üle 15 h
        trip("TLL", "CMN", d(10), d(12), 100),  # 2 ööd, liiga lühike
    ]
    rows, client = run_momondo(trips, "--max-duration", "15", "--no-self-transfer")
    assert [(r["origin"], r["destination"], r["price"]) for r in rows] == [("TLL", "RAK", 250)]
    flex = [c for c in client.calls if c[4]]
    assert len(flex) == 3  # 3 kuupäevaplokki x 1 lähte- x 1 sihtrühm
    assert all(c[0] == ["TLL", "HEL"] and c[1] == ["RAK", "CMN"] for c in client.calls)
    assert all("legdur=-960" in c[5] and "virtualinterline=-virtualinterline" in c[5] for c in client.calls)


def test_exact_search_refines_cheapest_date_pairs():
    trips = [
        trip("TLL", "RAK", d(1), d(5), 300),
        trip("TLL", "RAK", d(2), d(6), 200),
        trip("TLL", "RAK", d(3), d(7), 250),
    ]
    cheaper = [trip("HEL", "RAK", d(3), d(7), 180)]  # leidub ainult täpse otsinguga
    rows, client = run_momondo(trips, "--refine", "2", exact_only=cheaper)
    exact = [(c[2], c[3], c[5]) for c in client.calls if not c[4]]
    assert [(dep, ret) for dep, ret, _ in exact] == [(d(2), d(6)), (d(3), d(7))]  # 2 odavamat paari
    assert all("flexdepart" not in fs for _, _, fs in exact)
    assert [(r["origin"], r["price"]) for r in rows] == [("HEL", 180), ("TLL", 200), ("TLL", 250), ("TLL", 300)]

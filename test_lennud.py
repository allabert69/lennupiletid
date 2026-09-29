from datetime import date

import lennud


def item(origin, dest, dep, ret, price, dur_to=240, dur_back=250, transfers=0):
    return {
        "origin": origin, "destination": dest,
        "origin_airport": origin, "destination_airport": dest,
        "departure_at": dep, "return_at": ret, "price": price,
        "duration_to": dur_to, "duration_back": dur_back,
        "transfers": transfers, "return_transfers": transfers,
        "airline": "BT", "booking_link": "https://www.aviasales.com/search/x",
    }


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def round_trips(self, origin, dest, month):
        self.calls.append((origin, dest, month))
        return [i for i in self.data if i["origin"] == origin and i["destination"] == dest
                and i["departure_at"].startswith(month)]


def run(argv, data):
    args = lennud.parse_args(argv)
    client = FakeClient(data)
    return lennud.search(args, client), client


def test_expand_region_and_codes():
    regions = {"kanaarid": ["LPA", "TFS"]}
    assert lennud.expand_destinations("Kanaarid,bcn,LPA", regions) == ["LPA", "TFS", "BCN"]


def test_months_between_crosses_year():
    assert lennud.months_between(date(2026, 11, 20), date(2027, 1, 5)) == ["2026-11", "2026-12", "2027-01"]


def test_filters_nights_dates_duration_and_sorting():
    data = [
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150),  # ok, 7 ööd
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-07T20:00:00+01:00", 50),   # 2 ööd - liiga lühike
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 90, dur_to=900),  # liiga pikk lend
        item("RIX", "BCN", "2026-11-25T07:00:00+02:00", "2026-12-03T20:00:00+01:00", 120),  # tagasi pärast --end
        item("HEL", "BCN", "2026-11-10T07:00:00+02:00", "2026-11-16T20:00:00+01:00", 110),  # ok, 6 ööd
    ]
    rows, client = run(["--to", "BCN", "--start", "2026-11-01", "--end", "2026-11-30",
                        "--min-nights", "5", "--max-nights", "8", "--max-duration", "8"], data)
    assert [(r["origin"], r["price"]) for r in rows] == [("HEL", 110), ("TLL", 150)]
    assert rows[0]["nights"] == 6
    assert "momondo.com/flight-search/HEL-BCN/2026-11-10/2026-11-16" in rows[0]["momondo_link"]
    assert len(client.calls) == 3  # 3 lähtekohta x 1 sihtkoht x 1 kuu


def test_dedup_keeps_cheapest():
    data = [
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150),
        item("TLL", "BCN", "2026-11-05T09:00:00+02:00", "2026-11-12T22:00:00+01:00", 130),
    ]
    rows, _ = run(["--to", "BCN", "--origins", "TLL", "--start", "2026-11-01", "--end", "2026-11-30"], data)
    assert [r["price"] for r in rows] == [130]


def test_csv_written(tmp_path):
    data = [item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150)]
    rows, _ = run(["--to", "BCN", "--origins", "TLL", "--start", "2026-11-01", "--end", "2026-11-30"], data)
    out = tmp_path / "x.csv"
    lennud.write_csv(rows, str(out), ";")
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("price;currency;origin")
    assert "https://www.aviasales.com/search/x" in text

from datetime import date

import lennud


def item(origin, dest, dep, ret, price, dur_to=240, dur_back=250, transfers=0, total_to=None, total_back=None):
    # Aviasalesi pileti kood; filtrite jaoks on olulised ainult kogukestused, ajatemplid on suvalised.
    ts = "1793223900" * 2
    t = f"BT{ts}{total_to or dur_to:06d}{origin}{dest}{ts}{total_back or dur_back:06d}{dest}{origin}_0_0"
    return {
        "origin": origin, "destination": dest,
        "origin_airport": origin, "destination_airport": dest,
        "departure_at": dep, "return_at": ret, "price": price,
        "duration_to": dur_to, "duration_back": dur_back,
        "transfers": transfers, "return_transfers": transfers,
        "airline": "BT", "booking_link": f"https://www.aviasales.com/search/x?t={t}",
    }


class FakeClient:
    def __init__(self, data, direct_data=()):
        self.data = data
        self.direct_data = direct_data
        self.calls = []

    def round_trips(self, origin, dest, month, direct=False):
        self.calls.append((origin, dest, month, direct))
        return [i for i in (self.direct_data if direct else self.data) if i["origin"] == origin
                and i["destination"] == dest and i["departure_at"].startswith(month)]


def run(argv, data, direct_data=()):
    args = lennud.parse_args(argv)
    client = FakeClient(data, direct_data)
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
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 90,
             dur_to=300, total_to=900, transfers=1),  # lennuaeg 5 h, aga reisiaeg koos ootamisega 15 h
        item("RIX", "BCN", "2026-11-25T07:00:00+02:00", "2026-12-03T20:00:00+01:00", 120),  # tagasi pärast --end
        item("HEL", "BCN", "2026-11-10T07:00:00+02:00", "2026-11-16T20:00:00+01:00", 110),  # ok, 6 ööd
    ]
    rows, client = run(["--to", "BCN", "--start", "2026-11-01", "--end", "2026-11-30",
                        "--min-nights", "5", "--max-nights", "8", "--max-duration", "8"], data)
    assert [(r["origin"], r["price"]) for r in rows] == [("HEL", 110), ("TLL", 150)]
    assert rows[0]["nights"] == 6
    assert "momondo.com/flight-search/HEL-BCN/2026-11-10/2026-11-16" in rows[0]["momondo_link"]
    assert len(client.calls) == 6  # 3 lähtekohta x 1 sihtkoht x 1 kuu x (tava + otse)


def test_direct_flights_fetched_separately_when_filtering():
    # Tavapäring annab kuupäevapaari kohta vaid odavaima pileti: siin 23 h ümberistumistega.
    long = item("HEL", "TFS", "2026-10-24T08:00:00+03:00", "2026-10-30T16:00:00+00:00", 203,
                dur_to=456, total_to=1380, transfers=1)
    direct = item("HEL", "TFS", "2026-10-24T07:00:00+03:00", "2026-10-30T16:00:00+00:00", 566, dur_to=400)
    argv = ["--to", "TFS", "--origins", "HEL", "--start", "2026-10-23", "--end", "2026-10-31"]
    rows, client = run(argv + ["--max-duration", "12"], [long], [direct])
    assert [r["price"] for r in rows] == [566]
    rows, client = run(argv, [long], [direct])
    assert [r["price"] for r in rows] == [203]
    assert len(client.calls) == 1  # filtrita otselende eraldi ei küsita


def test_dedup_keeps_cheapest():
    data = [
        item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150),
        item("TLL", "BCN", "2026-11-05T09:00:00+02:00", "2026-11-12T22:00:00+01:00", 130),
    ]
    rows, _ = run(["--to", "BCN", "--origins", "TLL", "--start", "2026-11-01", "--end", "2026-11-30"], data)
    assert [r["price"] for r in rows] == [130]


TICKET_LINK = ("https://www.aviasales.com/search/HEL2810TFS01111?t=D817932239001793299500001380HELSTNLTNTFS"
               "17935488001793578500000375TFSHEL_3760dd430da367ca014fd6798227f5a5_19373"
               "&search_date=29092026&expected_price_currency=eur&expected_price=203")


def test_ticket_details_from_booking_link():
    it = item("HEL", "TFS", "2026-10-28T21:45:00+02:00", "2026-11-01T16:00:00+00:00", 203,
              dur_to=456, dur_back=375, transfers=1)
    it.update(booking_link=TICKET_LINK, gate="Kiwi.com")
    row = lennud.to_row(it, "eur")
    assert row["route_out"] == "HEL-STN-LTN-TFS"  # 1 ümberistumine + lennujaama vahetus STN -> LTN
    assert row["route_back"] == "TFS-HEL"
    assert (row["duration_out_h"], row["total_out_h"]) == (7.6, 23.0)  # lennuaeg vs kogu reisiaeg
    assert row["seller"] == "Kiwi.com"
    assert row["found_date"] == date(2026, 9, 29)


def test_link_without_ticket_code():
    it = item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150)
    it["booking_link"] = "https://www.aviasales.com/search/x"
    row = lennud.to_row(it, "eur")
    assert (row["route_out"], row["total_out_h"], row["found_date"]) == ("", "", "")
    assert lennud.ticket_legs("https://www.aviasales.com/search/x?t=garbage_1_2") == []


def test_csv_written(tmp_path):
    data = [item("TLL", "BCN", "2026-11-05T07:00:00+02:00", "2026-11-12T20:00:00+01:00", 150)]
    rows, _ = run(["--to", "BCN", "--origins", "TLL", "--start", "2026-11-01", "--end", "2026-11-30"], data)
    out = tmp_path / "x.csv"
    lennud.write_csv(rows, str(out), ";")
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("price;currency;origin")
    assert "https://www.aviasales.com/search/x" in text

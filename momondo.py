"""Momondo (momondo.ee) otsing Playwrightiga.

Momondol pole avalikku API-t. Skript avab otsingu päris Chrome'i aknas (peidetud brauseri suunab Momondo
kohe robotilehele) ja loeb tulemused samast sisemisest API-st, mida leht ise kasutab. Kui Momondo
oma lehte muudab, võib see moodul vajada kohendamist.

Ühe otsinguga saab katta kuni 10 lähte- ja 10 sihtlennujaama. Paindlik otsing (±3 päeva nii väljumisel
kui naasmisel) katab korraga kuni 49 kuupäevapaari, kuid uurib igaüht pinnapealsemalt: täpse kuupäevaga
otsing leiab sama paari kohta sageli 10-20% odavama pileti. Filtrid (kuupäevad, ööd, reisiaeg jne)
rakenduvad Momondo serveris, nii et odavaimate tulemuste hulka ei jää sobimatuid lende.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import airportsdata

DOMAIN = "www.momondo.ee"
FLEX_DAYS = 3  # URL-i "-flexible-3days": ±3 päeva
MAX_AIRPORTS = 10  # rohkemate lennujaamadega suunab Momondo otsinguvormile
RESULTS_PER_SEARCH = 500  # odavaimat tulemust otsingu kohta (pärast filtreid)
SEARCH_TIMEOUT = 90  # s; tavaliselt lõpeb otsing 15-35 sekundiga
CHECK_TIMEOUT = 600  # s turvakontrolli ("Ma ei ole robot") käsitsi lahendamiseks
PAUSE = 10  # s otsingute vahel, et Momondot liialt ei koormaks
LEGDUR_SLACK = 60  # min varu Momondo reisiaja filtrile, sest Momondo enda kestused võivad olla tund valed

# Lennujaama ajavöönd (IATA -> nt "Africa/Casablanca"), et kestused ise kohalikest kellaaegadest arvutada.
AIRPORT_TZ = {code: info["tz"] for code, info in airportsdata.load("IATA").items()}

POLL_PATH = "/i/api/search/dynamic/flights/poll"
POLL_JS = """async ({body, headers}) => {
  const r = await fetch('%s', {method: 'POST', headers, body: JSON.stringify(body), credentials: 'include'});
  return {status: r.status, text: await r.text()};
}""" % POLL_PATH


class MomondoError(RuntimeError):
    pass


@dataclass(frozen=True)
class FlexBlock:
    """Üks paindlik otsing: URL-i kuupäevad (±3 päeva keskpunktid) ja neist otsitavad kuupäevad."""
    depart: date
    return_: date
    depart_dates: tuple[date, ...]
    return_dates: tuple[date, ...]


def _days(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def flex_blocks(first_depart: date, last_return: date, min_nights: int, max_nights: int) -> list[FlexBlock]:
    """Paindlikud otsingud, mis katavad kõik väljumise ja naasmise paarid, kus ööde arv on lubatud."""
    span = 2 * FLEX_DAYS + 1
    last_depart = last_return - timedelta(days=min_nights)
    blocks = []
    d0 = first_depart
    while d0 <= last_depart:
        departs = _days(d0, min(d0 + timedelta(days=span - 1), last_depart))
        r_last = min(departs[-1] + timedelta(days=max_nights), last_return)
        r0 = departs[0] + timedelta(days=min_nights)
        while r0 <= r_last:
            returns = _days(r0, min(r0 + timedelta(days=span - 1), r_last))
            blocks.append(FlexBlock(d0 + timedelta(days=FLEX_DAYS), r0 + timedelta(days=FLEX_DAYS), departs, returns))
            r0 += timedelta(days=span)
        d0 += timedelta(days=span)
    return blocks


def chunks(codes: list[str], size: int = MAX_AIRPORTS) -> list[list[str]]:
    """Lennujaamad ühtlase suurusega rühmadesse, igas kuni size koodi (12 -> 6 + 6)."""
    if not codes:
        return []
    groups = -(-len(codes) // size)
    per = -(-len(codes) // groups)
    return [codes[i:i + per] for i in range(0, len(codes), per)]


def filters(max_leg_minutes: int | None = None, max_stops: int | None = None, no_self_transfer: bool = False,
            block: FlexBlock | None = None, min_nights: int = 0, max_nights: int = 0) -> str:
    """Momondo filtrid (URL-i fs= parameeter); block korral ka paindliku otsingu kuupäevad ja ööd."""
    # Momondo peidab vaikimisi "pikemad lennud"; toome need tagasi, et kehtiksid ainult meie filtrid.
    parts = ["baditin=baditin"]
    if block:
        longest = (block.return_dates[-1] - block.depart_dates[0]).days
        parts += [
            "flexdepart=" + ",".join(f"{d:%Y%m%d}" for d in block.depart_dates),
            "flexreturn=" + ",".join(f"{d:%Y%m%d}" for d in block.return_dates),
            f"triplength={min_nights}-{min(max_nights, longest)}",
        ]
    if max_leg_minutes is not None:
        # Momondo filtreerib oma (vahel tund valede) kestuste järgi; täpse piiri kontrollib lennud.py ise.
        parts.append(f"legdur=-{max_leg_minutes + LEGDUR_SLACK}")
    if max_stops is not None and max_stops < 2:  # Momondo valikud on 0, 1 ja 2+
        parts.append("stops=" + ",".join(str(n) for n in range(max_stops + 1)))
    if no_self_transfer:
        parts.append("virtualinterline=-virtualinterline")
    return ";".join(parts)


def search_url(origins: list[str], destinations: list[str], depart: date, return_: date, flex: bool = False,
               fs: str = "", result_id: str | None = None, domain: str = DOMAIN) -> str:
    """Momondo otsingu link; result_id korral avaneb leht konkreetse lennuga."""
    suffix = f"-flexible-{FLEX_DAYS}days" if flex else ""
    path = f"/flight-search/{','.join(origins)}-{','.join(destinations)}/{depart}{suffix}/{return_}{suffix}"
    if result_id:
        path += f"/f{result_id}"
    query = "sort=price_a" + (f"&fs={quote(fs, safe='')}" if fs else "")
    return f"https://{domain}{path}?{query}"


def duration(item: dict, origin: str, destination: str) -> int:
    """Lennu või lõigu kestus minutites kohalikest kellaaegadest ja lennujaamade ajavöönditest.

    Momondo enda kestused on valed, kui tema ajavööndi andmed on vananenud: Maroko läks 20.09.2026 üle GMT-le,
    Momondo arvestab endiselt UTC+1 ja näitab Marokosse tund lühemat, tagasi tund pikemat lendu. Momondo kestus
    jääb alles ainult siis, kui lennujaama ajavöönd pole teada või kellaajad on vigased.
    """
    try:
        start = datetime.fromisoformat(item["departure"]).replace(tzinfo=ZoneInfo(AIRPORT_TZ[origin]))
        end = datetime.fromisoformat(item["arrival"]).replace(tzinfo=ZoneInfo(AIRPORT_TZ[destination]))
    except KeyError:  # ka ZoneInfoNotFoundError
        return item["duration"]
    # timestamp(), sest sama ajavööndi aegade lahutamine jätaks vahepealse suveaja vahetuse arvestamata
    minutes = round((end.timestamp() - start.timestamp()) / 60)
    return minutes if minutes > 0 else item["duration"]


def parse_results(payload: dict) -> list[dict]:
    """Momondo vastuse tulemused lihtsamal kujul; reklaamid ja poolikud tulemused jäävad välja."""
    legs = payload.get("legs") or {}
    segments = payload.get("segments") or {}
    providers = payload.get("providers") or {}
    airlines = payload.get("airlines") or {}
    trips = []
    for result in payload.get("results") or []:
        if result.get("type") != "core" or not result.get("bookingOptions"):
            continue
        try:
            option = min(result["bookingOptions"], key=lambda o: o["displayPrice"]["price"])
            trip_legs = []
            for ref in result["legs"]:
                leg = legs[ref["id"]]
                segs = [segments[s["id"]] for s in leg["segments"]]
                trip_legs.append({
                    "departure": leg["departure"],
                    "duration": duration(leg, segs[0]["origin"], segs[-1]["destination"]),
                    "segments": [{
                        "airline": (airlines.get(s["airline"]) or {}).get("name", s["airline"]),
                        "origin": s["origin"],
                        "destination": s["destination"],
                        "duration": duration(s, s["origin"], s["destination"]),
                    } for s in segs],
                })
            provider = option.get("providerCode", "")
            trips.append({
                "result_id": result["resultId"],
                "price": option["displayPrice"]["price"],
                "currency": option["displayPrice"]["currency"],
                "seller": (providers.get(provider) or {}).get("displayName", provider),
                # Eraldi piletid: ümberistumisel tuleb ise pagas uuesti registreerida ja hilinemine on enda riisiko.
                "self_transfer": "SELF_TRANSFER" in (result.get("warnings") or [])
                or any(s.get("hasSelfTransfer") for ref in result["legs"] for s in ref.get("segments", [])),
                "legs": trip_legs,
            })
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return trips


def _is_search(body: dict, origins: list[str], destinations: list[str], depart: date, return_: date,
               flex: bool) -> bool:
    try:
        out, back = body["userSearchParams"]["legs"]
        return (out["origin"]["airports"] == list(origins) and out["destination"]["airports"] == list(destinations)
                and out["date"] == depart.isoformat() and back["date"] == return_.isoformat()
                and (out.get("flex") == "exact") != flex)
    except (KeyError, TypeError, ValueError):
        return False


class MomondoClient:
    """Chrome'i aken Momondo otsinguteks: with MomondoClient(profiil) as client: client.round_trips(...)."""

    def __init__(self, profile_dir: Path, show_window: bool = False, domain: str = DOMAIN,
                 per_search: int = RESULTS_PER_SEARCH, timeout: float = SEARCH_TIMEOUT, pause: float = PAUSE):
        self.profile_dir = profile_dir
        self.show_window = show_window
        self.domain = domain
        self.per_search = per_search
        self.timeout = timeout
        self.pause = pause
        self._responses: list = []
        self._searches = 0

    def __enter__(self) -> MomondoClient:
        try:
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError:
            raise MomondoError("Playwright puudub: pip install -r requirements.txt") from None
        self._playwright = sync_playwright().start()
        # Brauseri profiil säilib (küpsised, nõusolekud), nii et Momondo tunneb järgmisel korral ära.
        for channel in ("chrome", "msedge", None):
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self.profile_dir / (channel or "chromium")), channel=channel, headless=False)
                break
            except PlaywrightError as exc:
                error = exc
        else:
            self._playwright.stop()
            raise MomondoError(f"Chrome'i ega Edge'i ei õnnestunud käivitada: {error}")
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        if not self.show_window:
            # Headless brauseri blokeerib Momondo, seega päris aken lihtsalt minimeeritakse tegumiribale.
            self._set_window_state("minimized")
        self.page.on("response", lambda r: self._responses.append(r) if r.url.endswith(POLL_PATH) else None)
        return self

    def _set_window_state(self, state: str) -> None:
        try:
            cdp = self._context.new_cdp_session(self.page)
            window = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": state}})
        except Exception:  # akna olek on ainult mugavus, otsing töötab ka ilma
            pass

    def _security_check(self) -> None:
        """Momondo küsib "Ma ei ole robot" kontrolli: näitame akent ja ootame, kuni kasutaja selle lahendab."""
        print("  Momondo küsib turvakontrolli: lahenda see Chrome'i aknas (\"Ma ei ole robot\", siis Continue). "
              "Otsing jätkub seejärel ise.", file=sys.stderr)
        self._set_window_state("normal")
        self.page.bring_to_front()
        deadline = time.monotonic() + CHECK_TIMEOUT
        while "/security/check" in self.page.url:
            if time.monotonic() > deadline:
                raise MomondoError("Momondo turvakontroll jäi lahendamata.")
            self.page.wait_for_timeout(1000)
        if not self.show_window:
            self._set_window_state("minimized")

    def __exit__(self, *exc_info) -> None:
        try:
            self._context.close()
        finally:
            self._playwright.stop()

    def round_trips(self, origins: list[str], destinations: list[str], depart: date, return_: date, fs: str = "",
                    flex: bool = False) -> list[dict]:
        """Edasi-tagasi lennud kõigi lähte- ja sihtlennujaamade vahel, odavamad eespool; flex: ±3 päeva."""
        if self.page.is_closed():
            raise MomondoError("Brauseriaken suleti.")
        if self._searches:
            self.page.wait_for_timeout(self.pause * 1000)
        self._searches += 1
        self._responses.clear()
        url = search_url(origins, destinations, depart, return_, flex=flex, fs=fs, domain=self.domain)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        body, headers, complete = self._wait_for_search(origins, destinations, depart, return_, flex)
        if not complete:
            print("  hoiatus: Momondo otsing ei jõudnud lõpuni, tulemused võivad olla puudulikud", file=sys.stderr)
        return parse_results(self._poll(body, headers, fs))

    def _wait_for_search(self, origins: list[str], destinations: list[str], depart: date, return_: date,
                         flex: bool) -> tuple[dict, dict, bool]:
        """Ootab, kuni leht on otsingu lõpetanud; tagastab lehe viimase päringu keha ja päised."""
        deadline = time.monotonic() + self.timeout
        found = None
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(1000)
            if "/help/bots" in self.page.url:
                raise MomondoError("Momondo pidas otsingut robotiks (help/bots.html). Proovi mõne aja pärast uuesti.")
            if "/security/check" in self.page.url:
                self._security_check()  # pärast kontrolli suunab Momondo tagasi otsingusse
                deadline = time.monotonic() + self.timeout
                continue
            # Eelmise lehe hilinenud vastused ei kuulu siia otsingusse.
            for response in reversed(self._responses):
                try:
                    body = json.loads(response.request.post_data or "")
                except ValueError:
                    continue
                if not _is_search(body, origins, destinations, depart, return_, flex):
                    continue
                found = (body, response.request.headers)
                try:
                    if response.json().get("status") == "complete":
                        return body, response.request.headers, True
                except Exception:
                    pass
                break
        if found is None:
            # Tundmatu vahekontroll: ka järgmised otsingud kukuksid läbi, seega lõpetame kohe.
            if "/flight-search/" not in self.page.url:
                raise MomondoError(f"Momondo näitab otsingu asemel lehte {self.page.url}")
            raise RuntimeError(f"Momondo ei alustanud otsingut {self.timeout:.0f} s jooksul")
        return found[0], found[1], False

    def _poll(self, body: dict, headers: dict, fs: str) -> dict:
        """Küsib lõpetatud otsingust filtritega kuni per_search odavaimat tulemust."""
        body = json.loads(json.dumps(body))
        body["filterParams"] = {"fs": fs} if fs else {}
        body.setdefault("searchMetaData", {}).update(pageNumber=1, pageSize=self.per_search)
        keep = {k: v for k, v in headers.items() if k.lower() in ("x-csrf", "x-requested-with", "content-type")}
        response = self.page.evaluate(POLL_JS, {"body": body, "headers": keep})
        if response["status"] in (401, 403, 429):
            raise MomondoError(f"Momondo keeldus päringust (HTTP {response['status']}). "
                               "Proovi mõne aja pärast uuesti.")
        if response["status"] != 200:
            raise RuntimeError(f"Momondo vastas HTTP {response['status']}")
        return json.loads(response["text"])

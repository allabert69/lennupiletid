"""Travelpayouts (Aviasales) Data API klient.

Dokumentatsioon: https://support.travelpayouts.com/hc/en-us/articles/203956163
Andmed on vahemällu salvestatud hinnad (kasutajate otsingud viimase ~48h jooksul),
seega tasub enne ostu hinda broneerimislingi kaudu kontrollida.
"""

from __future__ import annotations

import time

import requests

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BOOKING_BASE = "https://www.aviasales.com"
PAGE_LIMIT = 1000
MAX_PAGES = 3


class TravelpayoutsError(RuntimeError):
    pass


class TravelpayoutsClient:
    def __init__(self, token: str, currency: str = "eur", pause: float = 0.2):
        self.token = token
        self.currency = currency
        self.pause = pause
        self.session = requests.Session()
        self.session.headers["X-Access-Token"] = token

    def round_trips(self, origin: str, destination: str, month: str) -> list[dict]:
        """Edasi-tagasi lennud, mille väljumine on antud kuus (YYYY-MM)."""
        results: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                "origin": origin,
                "destination": destination,
                "departure_at": month,
                "one_way": "false",
                "sorting": "price",
                "currency": self.currency,
                "limit": PAGE_LIMIT,
                "page": page,
            }
            resp = self.session.get(API_URL, params=params, timeout=30)
            if resp.status_code == 401:
                raise TravelpayoutsError("Vigane Travelpayouts token (401).")
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success", False):
                raise TravelpayoutsError(f"API viga: {payload.get('error')}")
            data = payload.get("data") or []
            results.extend(data)
            time.sleep(self.pause)
            if len(data) < PAGE_LIMIT:
                break
        for item in results:
            if item.get("link"):
                item["booking_link"] = BOOKING_BASE + item["link"]
        return results

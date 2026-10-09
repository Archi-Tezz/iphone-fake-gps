"""Build the bundled airport database from OurAirports.

OurAirports publishes its data into the public domain and regenerates it daily,
which makes it the right source for something that has to stay current: a
rebuild picks up new airports, renames and closures without touching this file.

Only large airports with scheduled service are kept. That is a few hundred
entries instead of eighty thousand, and it is exactly the set a flight leg could
plausibly start or end at -- nobody flies internationally out of a grass strip.

Run: .venv\\Scripts\\python.exe tools/make_airports.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
COUNTRIES_URL = "https://davidmegginson.github.io/ourairports-data/countries.csv"

OUTPUT = Path(__file__).resolve().parent.parent / "iosloc" / "data" / "airports.json"

#: Only these carry the scheduled international traffic the plane profile models.
KEEP_TYPES = {"large_airport"}
USER_AGENT = "ios-loc/1.x (airport database build)"
TIMEOUT = 120


def fetch_csv(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        text = response.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def main() -> None:
    # Airport names carry non-ASCII characters the Windows console cannot encode.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("fetching countries ...", end=" ", flush=True)
    countries = {row["code"]: row["name"] for row in fetch_csv(COUNTRIES_URL)}
    print(f"{len(countries)}")

    print("fetching airports ...", end=" ", flush=True)
    rows = fetch_csv(AIRPORTS_URL)
    print(f"{len(rows)} total")

    airports = []
    for row in rows:
        if row.get("type") not in KEEP_TYPES:
            continue
        if row.get("scheduled_service") != "yes":
            continue
        iata = (row.get("iata_code") or "").strip()
        if len(iata) != 3:
            # Without an IATA code it is not something a traveller would name.
            continue
        try:
            lat = float(row["latitude_deg"])
            lon = float(row["longitude_deg"])
        except (KeyError, TypeError, ValueError):
            continue

        airports.append({
            "iata": iata,
            "icao": (row.get("ident") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "city": (row.get("municipality") or "").strip(),
            "country": countries.get(row.get("iso_country", ""), row.get("iso_country", "")),
            "cc": row.get("iso_country", ""),
            # Six decimals is ~0.1 m; more would only pad the file.
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        })

    airports.sort(key=lambda a: (a["country"], a["city"], a["iata"]))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(airports, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    size_kb = OUTPUT.stat().st_size // 1024
    countries_covered = len({a["cc"] for a in airports})
    print(f"\n{len(airports)} airports in {countries_covered} countries -> {OUTPUT} ({size_kb} KB)")
    for sample in ("SVO", "JFK", "DXB", "HND", "IST"):
        hit = next((a for a in airports if a["iata"] == sample), None)
        print(f"  {sample}: {hit['name']}, {hit['city']}" if hit else f"  {sample}: not found")


if __name__ == "__main__":
    main()

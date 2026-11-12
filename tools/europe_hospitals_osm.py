"""
europe_hospitals_osm.py
-----------------------
Queries the OpenStreetMap Overpass API for every hospital in Europe, country by country.
Output: .tmp/europe_hospitals_osm.csv

Free — no API key required.
"""

import csv
import json
import os
import time
import requests

# ── Config ────────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT_FILE = ".tmp/europe_hospitals_osm.csv"
PROGRESS_FILE = ".tmp/europe_hospitals_osm_progress.json"
DELAY_BETWEEN_COUNTRIES = 3  # seconds — be polite to the free API

# 44 European countries (ISO 3166-1 alpha-2)
EUROPEAN_COUNTRIES = [
    ("AD", "Andorra"),
    ("AL", "Albania"),
    ("AT", "Austria"),
    ("BA", "Bosnia and Herzegovina"),
    ("BE", "Belgium"),
    ("BG", "Bulgaria"),
    ("BY", "Belarus"),
    ("CH", "Switzerland"),
    ("CY", "Cyprus"),
    ("CZ", "Czech Republic"),
    ("DE", "Germany"),
    ("DK", "Denmark"),
    ("EE", "Estonia"),
    ("ES", "Spain"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GB", "United Kingdom"),
    ("GR", "Greece"),
    ("HR", "Croatia"),
    ("HU", "Hungary"),
    ("IE", "Ireland"),
    ("IS", "Iceland"),
    ("IT", "Italy"),
    ("LI", "Liechtenstein"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("LV", "Latvia"),
    ("MD", "Moldova"),
    ("ME", "Montenegro"),
    ("MK", "North Macedonia"),
    ("MT", "Malta"),
    ("NL", "Netherlands"),
    ("NO", "Norway"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("RO", "Romania"),
    ("RS", "Serbia"),
    ("SE", "Sweden"),
    ("SI", "Slovenia"),
    ("SK", "Slovakia"),
    ("SM", "San Marino"),
    ("TR", "Turkey"),
    ("UA", "Ukraine"),
    ("XK", "Kosovo"),
]

FIELDNAMES = [
    "osm_id", "osm_type", "name", "name_local",
    "street", "housenumber", "city", "postcode", "country_code", "country_name",
    "phone", "website", "email",
    "lat", "lon",
    "operator", "beds", "emergency",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def build_query(iso_code):
    return f"""
[out:json][timeout:180];
area["ISO3166-1"="{iso_code}"]["admin_level"="2"]->.searchArea;
(
  node["amenity"="hospital"](area.searchArea);
  way["amenity"="hospital"](area.searchArea);
  relation["amenity"="hospital"](area.searchArea);
);
out center tags;
"""

def extract_tags(element, iso_code, country_name):
    tags = element.get("tags", {})

    # Get coordinates
    if element["type"] == "node":
        lat = element.get("lat", "")
        lon = element.get("lon", "")
    else:
        center = element.get("center", {})
        lat = center.get("lat", "")
        lon = center.get("lon", "")

    # Build address — OSM uses addr: prefix tags
    street = tags.get("addr:street", "")
    housenumber = tags.get("addr:housenumber", "")
    city = tags.get("addr:city", "") or tags.get("addr:town", "") or tags.get("addr:village", "")
    postcode = tags.get("addr:postcode", "")

    # Name — prefer English, fall back to local
    name = tags.get("name:en", "") or tags.get("name", "")
    name_local = tags.get("name", "") if tags.get("name:en") else ""

    return {
        "osm_id": element.get("id", ""),
        "osm_type": element.get("type", ""),
        "name": name,
        "name_local": name_local,
        "street": f"{housenumber} {street}".strip() if housenumber else street,
        "housenumber": housenumber,
        "city": city,
        "postcode": postcode,
        "country_code": iso_code,
        "country_name": country_name,
        "phone": tags.get("phone", "") or tags.get("contact:phone", ""),
        "website": tags.get("website", "") or tags.get("contact:website", "") or tags.get("url", ""),
        "email": tags.get("email", "") or tags.get("contact:email", ""),
        "lat": lat,
        "lon": lon,
        "operator": tags.get("operator", ""),
        "beds": tags.get("beds", ""),
        "emergency": tags.get("emergency", ""),
    }

def query_country(iso_code, country_name):
    query = build_query(iso_code)
    try:
        response = requests.post(OVERPASS_URL, data={"data": query}, timeout=200)
        response.raise_for_status()
        data = response.json()
        elements = data.get("elements", [])
        rows = [extract_tags(el, iso_code, country_name) for el in elements]
        print(f"  {country_name} ({iso_code}): {len(rows)} hospitals")
        return rows
    except requests.exceptions.Timeout:
        print(f"  TIMEOUT: {country_name} — will retry once")
        time.sleep(10)
        try:
            response = requests.post(OVERPASS_URL, data={"data": query}, timeout=200)
            data = response.json()
            rows = [extract_tags(el, iso_code, country_name) for el in data.get("elements", [])]
            print(f"  {country_name} (retry): {len(rows)} hospitals")
            return rows
        except Exception as e:
            print(f"  FAILED after retry: {country_name} — {e}")
            return []
    except Exception as e:
        print(f"  ERROR: {country_name} ({iso_code}) — {e}")
        return []

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(".tmp", exist_ok=True)

    # Load progress (which countries already done)
    completed = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            completed = set(json.load(f))
        print(f"Resuming — {len(completed)} countries already done")

    # Open CSV (append mode if resuming)
    file_exists = os.path.exists(OUTPUT_FILE)
    mode = "a" if completed else "w"

    with open(OUTPUT_FILE, mode, newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
        if not file_exists or not completed:
            writer.writeheader()

        total_written = 0
        for iso_code, country_name in EUROPEAN_COUNTRIES:
            if iso_code in completed:
                print(f"  Skipping {country_name} ({iso_code}) — already done")
                continue

            print(f"Querying {country_name} ({iso_code})...")
            rows = query_country(iso_code, country_name)

            for row in rows:
                writer.writerow(row)
            csvfile.flush()

            total_written += len(rows)
            completed.add(iso_code)

            # Save progress after each country
            with open(PROGRESS_FILE, "w") as f:
                json.dump(list(completed), f)

            time.sleep(DELAY_BETWEEN_COUNTRIES)

    print(f"\nDone. {total_written} hospitals written to {OUTPUT_FILE}")
    print(f"Progress file: {PROGRESS_FILE}")

    # Clean up progress file on full completion
    if len(completed) == len(EUROPEAN_COUNTRIES):
        os.remove(PROGRESS_FILE)
        print("All countries complete — progress file removed")

if __name__ == "__main__":
    main()

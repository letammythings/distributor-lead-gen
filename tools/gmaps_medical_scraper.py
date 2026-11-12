"""
gmaps_medical_scraper.py

Scrapes Google Maps via SerpAPI (engine=google_maps) for medical supply businesses
in top US cities + UK, AU, CA, NZ, IE cities.

Why: Google Maps returns local businesses that don't appear in regular SERP results.
     Many small independent medical supply shops only show up on Maps.

Queries: "medical supply" + city → extract website domain, name, address, phone

Usage:
  python3 gmaps_medical_scraper.py             # all cities
  python3 gmaps_medical_scraper.py --us-only   # US cities only
  python3 gmaps_medical_scraper.py --intl-only # international only

Output: .tmp/gmaps_medical_domains.csv
"""

import glob
import os
import sys
import requests
import time
import re
import pandas as pd
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# Use Key 3 (has remaining credits), fall back to Key 1/2 if they've reset
SERP_API_KEYS = [
    k for k in [
        os.getenv("SERP_API_KEY_3"),
        os.getenv("SERP_API_KEY_1"),
        os.getenv("SERP_API_KEY_2"),
    ] if k
]

OUTPUT_FILE = "../.tmp/gmaps_medical_domains.csv"

# ── CITY LISTS ────────────────────────────────────────────────────────────────

US_CITIES = [
    # Top 15 by DME market density (prioritized for limited credits)
    "Houston TX", "Los Angeles CA", "Chicago IL", "New York NY", "Phoenix AZ",
    "Dallas TX", "Philadelphia PA", "San Antonio TX", "Atlanta GA", "Miami FL",
    "Tampa FL", "Charlotte NC", "Columbus OH", "Indianapolis IN", "Nashville TN",
    # Extended (run with --us-extended if credits allow)
    # "Seattle WA", "Denver CO", "Memphis TN", "Baltimore MD", "Raleigh NC",
    # "Minneapolis MN", "Louisville KY", "Milwaukee WI", "Sacramento CA", "Tucson AZ",
]

INTL_CITIES = [
    # UK
    "London UK", "Birmingham UK", "Manchester UK", "Leeds UK", "Glasgow UK",
    "Sheffield UK", "Liverpool UK", "Bristol UK", "Leicester UK", "Edinburgh UK",
    # Australia
    "Sydney Australia", "Melbourne Australia", "Brisbane Australia",
    "Perth Australia", "Adelaide Australia", "Gold Coast Australia",
    # Canada
    "Toronto Canada", "Vancouver Canada", "Calgary Canada",
    "Ottawa Canada", "Edmonton Canada",
    # New Zealand
    "Auckland New Zealand", "Wellington New Zealand",
    # Ireland
    "Dublin Ireland", "Cork Ireland",
    # South Africa
    "Johannesburg South Africa", "Cape Town South Africa",
]

# ── JUNK FILTER ───────────────────────────────────────────────────────────────

CHAIN_DOMAINS = {
    "cvs.com", "walgreens.com", "riteaid.com", "walmart.com", "target.com",
    "costco.com", "samsclub.com", "amazon.com", "ebay.com", "yelp.com",
    "google.com", "facebook.com", "instagram.com", "linkedin.com",
    "mckesson.com", "medline.com", "cardinalhealth.com", "henryschein.com",
    "grainger.com", "staples.com", "bestbuy.com", "homedepot.com",
    "yellowpages.com", "healthgrades.com", "zocdoc.com", "webmd.com",
    "bbb.org", "angieslist.com", "angi.com", "manta.com", "whitepages.com",
    "mapquest.com", "tripadvisor.com", "nextdoor.com",
}

JUNK_KEYWORDS = [
    "amazon", "ebay", "walmart", "yelp", "facebook", "twitter",
    "instagram", "linkedin", "youtube", "google", "pinterest",
    "tripadvisor", "yellowpages", "healthgrades", "zocdoc", "webmd",
    "wordpress", "blogspot", "wix.com", "squarespace",
]

MEDICAL_KEYWORDS = [
    "medical", "health", "ortho", "surgical", "pharmacy", "pharma", "dme",
    "rehab", "therapy", "physio", "supply", "care", "clinic", "home",
    "wound", "mobility", "prosthetics", "orthotics", "podiatry",
]


def is_junk(domain):
    if not domain:
        return True
    d = domain.lower()
    if d in CHAIN_DOMAINS:
        return True
    for kw in JUNK_KEYWORDS:
        if kw in d:
            return True
    return False


def looks_medical(name, domain):
    """Returns True if the business name or domain looks healthcare-related."""
    text = (name + " " + domain).lower()
    return any(kw in text for kw in MEDICAL_KEYWORDS)


def parse_domain(url):
    if not url:
        return ""
    try:
        if not url.startswith("http"):
            url = "http://" + url
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "").split(":")[0].split("/")[0]
        if domain and "." in domain:
            return domain.lower()
    except:
        pass
    return ""


def gmaps_search(query, api_key):
    """Search Google Maps via SerpAPI and return place results."""
    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_maps",
                "q": query,
                "api_key": api_key,
                "type": "search",
            },
            timeout=20
        )
        if r.status_code == 200:
            return r.json().get("local_results", [])
        else:
            print(f"  [!] SerpAPI {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"  [!] Error: {e}")
    return []


def load_existing_domains():
    """Load all previously found domains to avoid duplicates."""
    existing = set()
    # Every earlier lead file in .tmp/, so the same business is never added twice.
    files = sorted(glob.glob("../.tmp/*_contacts.csv")) + sorted(glob.glob("../.tmp/*_domains*.csv"))
    files.append(OUTPUT_FILE)
    for fname in files:
        try:
            df = pd.read_csv(fname)
            if "domain" in df.columns:
                existing.update(df["domain"].dropna().str.lower().tolist())
        except FileNotFoundError:
            pass
    return existing


SEARCH_QUERIES = [
    "medical supply store",
    "independent pharmacy",
    "surgical supply",
    "orthopedic supply",
    "home health supply",
    "pharmacy DME",
    "compounding pharmacy",
]


def main():
    args = sys.argv[1:]
    us_only   = "--us-only"   in args
    intl_only = "--intl-only" in args

    # Build city+query combinations
    if us_only:
        cities = [(c, "US") for c in US_CITIES]
    elif intl_only:
        cities = [(c, "INTL") for c in INTL_CITIES]
    else:
        cities = [(c, "US") for c in US_CITIES] + [(c, "INTL") for c in INTL_CITIES]

    # Run multiple query types per city (medical supply + pharmacy)
    query_type = args[args.index("--query") + 1] if "--query" in args else "medical supply"
    print(f"Query type: {query_type}")

    print("=" * 60)
    print("Google Maps Medical Supply Scraper")
    print(f"Cities: {len(cities)} | Keys: {len(SERP_API_KEYS)}")
    print("=" * 60)

    existing = load_existing_domains()
    print(f"[Dedup] Skipping {len(existing)} existing domains\n")

    # Resume support
    already_done_cities = set()
    rows = []
    try:
        df_existing = pd.read_csv(OUTPUT_FILE)
        rows = df_existing.to_dict("records")
        already_done_cities = set(df_existing["city"].dropna().tolist())
        print(f"[Resume] {len(already_done_cities)} cities already scraped\n")
    except FileNotFoundError:
        pass

    key_idx = 0
    new_count = 0

    for city, region in cities:
        if city in already_done_cities:
            print(f"[skip] {city}")
            continue

        query = f"{query_type} {city}"
        api_key = SERP_API_KEYS[key_idx % len(SERP_API_KEYS)]
        key_idx += 1

        print(f"\n[Maps] {query}")
        results = gmaps_search(query, api_key)

        city_count = 0
        for place in results:
            name   = place.get("title", "")
            url    = place.get("website", "")
            phone  = place.get("phone", "")
            addr   = place.get("address", "")
            rating = place.get("rating", "")
            reviews= place.get("reviews", "")

            domain = parse_domain(url)

            if is_junk(domain):
                continue
            if not looks_medical(name, domain):
                continue
            if domain and domain in existing:
                continue

            row = {
                "domain":   domain,
                "company_name": name,
                "city":     city,
                "region":   region,
                "phone":    phone,
                "address":  addr,
                "rating":   rating,
                "reviews":  reviews,
                "source":   "gmaps",
            }
            rows.append(row)
            if domain:
                existing.add(domain)
            city_count += 1
            new_count += 1
            print(f"  + {name} | {domain}")

        if not results:
            print(f"  (no results)")

        # Mark city done even if no results (to allow resume)
        if not any(r.get("city") == city for r in rows):
            rows.append({"city": city, "region": region, "domain": "", "company_name": "", "source": "gmaps_empty"})

        pd.DataFrame(rows).to_csv(OUTPUT_FILE, index=False)
        print(f"  [{city_count} new] saved.")
        time.sleep(1.0)

    df_out = pd.DataFrame(rows)
    df_with_domain = df_out[df_out["domain"].notna() & (df_out["domain"] != "")]
    print(f"\n{'='*60}")
    print(f"DONE. {len(df_with_domain)} businesses with domains from {len(cities)} cities")
    print(f"-> {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()

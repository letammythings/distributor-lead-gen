"""
cms_dmepos_scraper.py

Downloads US DME (Durable Medical Equipment) supplier organizations from the
free CMS NPI Registry API. Filters to relevant taxonomy codes, deduplicates
against existing leads, then does domain lookup via SerpAPI.

Why: Every Medicare-enrolled DME supplier is in this database. It's free,
     authoritative, and covers thousands of small independent DME businesses
     that would never show up in competitor brand scrapes.

Taxonomy codes targeted:
  332B00000X  - Durable Medical Equipment & Medical Supplies
  335E00000X  - Prosthetics & Orthotics Supplier (cast covers relevant)
  332BC3200X  - Customized Equipment

Priority states: TX, FL, CA, NY, PA, OH, GA, NC, IL, MI, AZ, CO, WA, VA,
                 TN, MO, MD, NJ, SC, OR, MN, IN, AL, KY, WI, CT, OK, NV

Output:
  .tmp/cms_dmepos_companies.csv   — filtered company list with addresses
  .tmp/cms_dmepos_domains.csv     — after domain lookup via SerpAPI

Usage:
  python3 cms_dmepos_scraper.py               # all priority states
  python3 cms_dmepos_scraper.py --state TX    # single state
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

SERP_API_KEY = (
    os.getenv("SERP_API_KEY_3") or
    os.getenv("SERP_API_KEY_1") or
    os.getenv("SERP_API_KEY_2")
)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")   # 100 free searches/day
GOOGLE_CX      = os.getenv("GOOGLE_CX")

NPI_API = "https://npiregistry.cms.hhs.gov/api/"
OUT_COMPANIES = "../.tmp/cms_dmepos_companies.csv"
OUT_DOMAINS   = "../.tmp/cms_dmepos_domains.csv"

PRIORITY_STATES = [
    "TX", "FL", "CA", "NY", "PA", "OH", "GA", "NC", "IL", "MI",
    "AZ", "CO", "WA", "VA", "TN", "MO", "MD", "NJ", "SC", "OR",
    "MN", "IN", "AL", "KY", "WI", "CT", "OK", "NV", "LA", "AR",
]

TAXONOMY_CODES = [
    "Durable Medical Equipment",  # 332B00000X + all subspecialties
    "Prosthetics & Orthotics",    # 335E00000X (cast covers very relevant)
]

# Large chains to skip
CHAIN_SKIP = [
    "walgreen", "cvs", "rite aid", "walmart", "target", "costco",
    "medline", "mckesson", "cardinal health", "henry schein", "amerisource",
    "amazon", "byram", "apria", "lincare", "rotech", "coram", "caremark",
    "adt medical", "philips", "invacare", "sunrise medical",
    "aerocare", "coram healthcare", "national home", "gentiva", "amedisys",
    "hca", "tenet", "ascension", "adventist", "dignity health", "commonspirit",
]


def is_chain(name):
    n = name.lower()
    return any(c in n for c in CHAIN_SKIP)


def fetch_npi_page(taxonomy_code, state, skip=0, limit=200):
    """Fetch one page of NPI results for a given taxonomy + state."""
    params = {
        "version":          "2.1",
        "enumeration_type": "NPI-2",   # NPI-2 = organizations only
        "taxonomy_description": taxonomy_code,
        "state":            state,
        "limit":            limit,
        "skip":             skip,
    }
    try:
        r = requests.get(NPI_API, params=params, timeout=20)
        if r.status_code == 200:
            data = r.json()
            return data.get("results", []), data.get("result_count", 0)
    except Exception as e:
        print(f"    [!] NPI API error: {e}")
    return [], 0


def parse_npi_record(record, taxonomy_code, state):
    """Extract useful fields from an NPI API result."""
    basic = record.get("basic", {})
    name  = basic.get("organization_name", "").strip()
    if not name:
        return None
    if is_chain(name):
        return None

    # Get primary address
    addresses = record.get("addresses", [])
    addr = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), None)
    if not addr:
        addr = addresses[0] if addresses else {}

    return {
        "npi":          record.get("number", ""),
        "company_name": name,
        "address":      addr.get("address_1", ""),
        "city":         addr.get("city", ""),
        "state":        addr.get("state", state),
        "zip":          addr.get("postal_code", "")[:5],
        "phone":        addr.get("telephone_number", ""),
        "taxonomy":     taxonomy_code,
        "search_query": f'"{name}" {addr.get("city", "")} {state} medical supply',
    }


def collect_companies(states, max_per_state_per_taxonomy=400):
    """Pull company records from NPI API for all state+taxonomy combos."""

    # Resume support
    already_done = set()
    rows = []
    try:
        df_ex = pd.read_csv(OUT_COMPANIES)
        if not df_ex.empty and "npi" in df_ex.columns:
            rows = df_ex.to_dict("records")
            already_done = set(df_ex["npi"].dropna().astype(str).tolist())
            print(f"[Resume] {len(already_done)} companies already fetched\n")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    for state in states:
        for taxonomy in TAXONOMY_CODES:
            skip = 0
            state_count = 0
            while state_count < max_per_state_per_taxonomy:
                results, total = fetch_npi_page(taxonomy, state, skip=skip)
                if not results:
                    break
                for rec in results:
                    npi = rec.get("number", "")
                    if npi in already_done:
                        continue
                    parsed = parse_npi_record(rec, taxonomy, state)
                    if parsed:
                        rows.append(parsed)
                        already_done.add(npi)
                        state_count += 1
                skip += 200
                time.sleep(0.3)
                if skip >= min(total, max_per_state_per_taxonomy):
                    break

            print(f"  {state} / {taxonomy}: {state_count} new companies")

        pd.DataFrame(rows).to_csv(OUT_COMPANIES, index=False)

    return pd.DataFrame(rows)


STOP_WORDS = {
    "inc", "llc", "ltd", "corp", "co", "the", "and", "of", "for",
    "medical", "healthcare", "health", "care", "products", "solutions",
    "services", "group", "company", "technologies", "technology",
    "international", "global", "systems", "industries", "usa", "supply",
}

NPI_JUNK = {
    "providerwire.com", "npiprofile.com", "npidb.org", "npino.com",
    "healthgrades.com", "zocdoc.com", "vitals.com", "doximity.com",
    "doctors.com", "wellness.com", "usnews.com", "castleconnolly.com",
    "ama-assn.org", "betterdoctor.com", "sharecare.com", "ratemds.com",
}


def guess_domain_candidates(company_name):
    """Generate likely domain candidates from company name."""
    name = re.sub(r"[^\w\s]", "", company_name.lower())
    words = [w for w in name.split() if w not in STOP_WORDS and len(w) > 1]
    if not words:
        return []
    candidates = []
    joined = "".join(words)
    if len(joined) <= 20:
        candidates.append(f"{joined}.com")
    if len(words) >= 2:
        two = "".join(words[:2])
        if two != joined:
            candidates.append(f"{two}.com")
    candidates.append("-".join(words[:3]) + ".com")
    return candidates[:3]


# Strong signals — must appear on the site to be accepted.
# These are specific enough that a non-medical business is unlikely to use them.
MEDICAL_CONTENT_KEYWORDS = [
    "medical supply", "medical supplies", "medical equipment",
    "durable medical equipment", "dme supplier", "dme provider",
    "surgical supply", "surgical supplies",
    "orthopedic supply", "orthopaedic supply",
    "home health supply", "home medical",
    "pharmacy", "pharmacist", "prescription", "compounding pharmacy",
    "wound care supply", "ostomy supply", "prosthetics", "orthotics",
    "wheelchair", "mobility equipment", "hospital supply",
    "cast cover", "cast protector", "waterproof cast",
]


def probe_and_validate(domain, company_name, timeout=3):
    """Fetch domain homepage, check it loads AND contains medical keywords.
    Returns confirmed domain string or '' if validation fails."""
    all_junk = JUNK_DOMAINS | NPI_JUNK
    for scheme in ["https://", "http://"]:
        try:
            r = requests.get(scheme + domain, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                             allow_redirects=True)
            if r.status_code >= 400:
                continue
            final = urlparse(r.url).netloc.replace("www.", "").split(":")[0].lower()
            if not final or "." not in final or final in all_junk:
                continue
            # Must contain at least one medical keyword
            text = r.text.lower()
            if not any(kw in text for kw in MEDICAL_CONTENT_KEYWORDS):
                return ""
            # At least one meaningful word from company name must appear in page text or domain
            name_words = [w for w in re.sub(r"[^\w\s]", "", company_name.lower()).split()
                          if len(w) > 3 and w not in STOP_WORDS]
            if name_words and not any(w in (final + " " + text[:8000]) for w in name_words):
                return ""
            return final
        except:
            continue
    return ""


def duckduckgo_lookup(company_name, city=""):
    """DuckDuckGo Instant Answer API — checks AbstractURL + Results."""
    query = f"{company_name} {city} medical supply DME".strip()
    try:
        r = requests.get("https://api.duckduckgo.com/",
                         params={"q": query, "format": "json",
                                 "no_html": 1, "no_redirect": 1},
                         timeout=10)
        if r.status_code != 200:
            return ""
        data = r.json()
        candidates = []
        # AbstractURL is usually the company's own website
        if data.get("AbstractURL"):
            candidates.append(data["AbstractURL"])
        for res in data.get("Results", []):
            if res.get("FirstURL"):
                candidates.append(res["FirstURL"])
        for topic in data.get("RelatedTopics", []):
            if topic.get("FirstURL"):
                candidates.append(topic["FirstURL"])
        all_junk = JUNK_DOMAINS | NPI_JUNK
        for url in candidates:
            domain = parse_domain(url)
            if domain and is_good_domain(domain, company_name) and domain not in all_junk:
                return domain
    except:
        pass
    return ""


def find_domain(company_name, city=""):
    """
    Multi-strategy domain lookup with content validation:
    1. DuckDuckGo Instant Answer (AbstractURL + Results)
    2. Pattern guessing
    Both strategies run each candidate through probe_and_validate()
    which confirms the site has genuine medical/DME content.
    """
    # Strategy 1: DuckDuckGo
    domain = duckduckgo_lookup(company_name, city)
    if domain:
        confirmed = probe_and_validate(domain, company_name)
        if confirmed:
            return confirmed

    # Strategy 2: Pattern guessing + content validation
    for candidate in guess_domain_candidates(company_name):
        confirmed = probe_and_validate(candidate, company_name)
        if confirmed:
            return confirmed

    return ""


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


JUNK_DOMAINS = {
    "linkedin.com", "facebook.com", "yelp.com", "bloomberg.com",
    "crunchbase.com", "zoominfo.com", "dnb.com", "hoovers.com",
    "yellowpages.com", "healthgrades.com", "vitals.com", "zocdoc.com",
    "webmd.com", "doximity.com", "npidb.org", "npino.com",
    "bbb.org", "manta.com", "bizapedia.com", "opencorporates.com",
    "corporationwiki.com", "bizstanding.com", "wikipedia.org",
}


def is_good_domain(domain, company_name):
    if not domain or domain in JUNK_DOMAINS:
        return False
    if re.match(r'^[a-f0-9]{20,}', domain):
        return False
    for tld in [".gov", ".edu"]:
        if domain.endswith(tld):
            return False
    return True


WORKERS = 5  # concurrent domain lookups


def find_domains(df_companies):
    """Domain lookup for all companies — parallel with 5 workers."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Resume support
    already_done = {}
    rows = []
    try:
        df_ex = pd.read_csv(OUT_DOMAINS)
        rows = df_ex.to_dict("records")
        for _, row in df_ex.iterrows():
            already_done[str(row.get("npi", ""))] = row.get("domain", "")
        print(f"[Resume] {len(already_done)} domains already looked up\n")
    except FileNotFoundError:
        pass

    # Load all existing leads to avoid duplicates
    existing_domains = set()
    for fname in sorted(glob.glob("../.tmp/*_contacts.csv")) + sorted(glob.glob("../.tmp/*_domains*.csv")):
        try:
            df = pd.read_csv(fname)
            if "domain" in df.columns:
                existing_domains.update(df["domain"].dropna().str.lower().tolist())
        except FileNotFoundError:
            pass

    todo = df_companies[~df_companies["npi"].astype(str).isin(already_done.keys())]
    print(f"Domain lookup: {len(todo)} companies to process ({WORKERS} workers)\n")

    lock = threading.Lock()
    completed = [0]
    total = len(todo)
    todo_records = list(todo.iterrows())

    def process_one(args):
        _, row = args
        npi   = str(row.get("npi", ""))
        name  = row.get("company_name", "")
        city  = row.get("city", "")
        state = row.get("state", "")

        domain = find_domain(name, city)

        with lock:
            if domain and domain in existing_domains:
                domain = ""
            elif domain:
                existing_domains.add(domain)

            result = {**row.to_dict(), "domain": domain}
            rows.append(result)
            already_done[npi] = domain
            completed[0] += 1

            if completed[0] % 10 == 0 or domain:
                pd.DataFrame(rows).to_csv(OUT_DOMAINS, index=False)
                print(f"  [{completed[0]}/{total}] {name[:40]} -> {domain or 'not found'}")

        return npi, domain

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(process_one, rec) for rec in todo_records]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"  [!] Worker error: {e}")

    # Final save
    pd.DataFrame(rows).to_csv(OUT_DOMAINS, index=False)
    return pd.DataFrame(rows)


def main():
    args = sys.argv[1:]
    state_filter = None
    if "--state" in args:
        idx = args.index("--state")
        if idx + 1 < len(args):
            state_filter = args[idx + 1].upper()

    states = [state_filter] if state_filter else PRIORITY_STATES

    print("=" * 60)
    print("CMS DMEPOS Supplier Scraper (NPI Registry)")
    print(f"States: {', '.join(states)}")
    print(f"Taxonomy codes: {len(TAXONOMY_CODES)}")
    print("=" * 60)

    print("\n[Phase 1] Fetching company records from NPI Registry (free)...")
    df_companies = collect_companies(states)
    if df_companies.empty:
        print("\n[!] No companies fetched. Check NPI API connectivity.")
        return
    df_companies = df_companies.drop_duplicates(subset=["npi"])
    print(f"\n{len(df_companies)} unique DME companies found")
    print(f"-> {OUT_COMPANIES}")

    print("\n[Phase 2] Finding website domains via Google Custom Search...")
    df_domains = find_domains(df_companies)

    df_with_domain = df_domains[df_domains["domain"].notna() & (df_domains["domain"] != "")]
    print(f"\n{'='*60}")
    print(f"DONE. {len(df_with_domain)}/{len(df_domains)} companies have domains")
    print(f"-> {OUT_DOMAINS}")
    print("\nNext: Run batch_enricher.py --input cms_dmepos_domains.csv")
    print("=" * 60)


if __name__ == "__main__":
    main()

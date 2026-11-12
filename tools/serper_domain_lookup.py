#!/usr/bin/env python3
"""
Serper Domain Lookup
Finds website domains for healthcare facilities using Serper.dev Google Search API.
Skips already-completed rows for resume support.

Input:  tools/all_facilities_cleaned.csv  (must have "search_query" column)
Output: .tmp/domains_output.csv
"""

import os
import time
import requests
import pandas as pd
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# ── Settings ───────────────────────────────────────────────────────────────────
INPUT_FILE  = os.path.join(os.path.dirname(__file__), '..', 'all_facilities_cleaned.csv')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), '..', '.tmp', 'domains_output.csv')

SERPER_URL  = 'https://google.serper.dev/search'
SKIP_DOMAINS = ['healthgrades', 'zocdoc', 'webmd', 'vitals', 'yelp', 'facebook', 'linkedin',
                'npidb', 'npino', 'doctors.com', 'doximity', 'betterdoctor', 'wellness.com',
                'practo', 'sharecare', 'usnews', 'castleconnolly', 'ama-assn']
DELAY       = 1.0  # seconds between requests

# ── Load API key ───────────────────────────────────────────────────────────────
API_KEY = os.getenv('SERPER_API_KEY')
if not API_KEY:
    print("ERROR: SERPER_API_KEY not found in .env — stopping.")
    exit(1)

# ── Load input ─────────────────────────────────────────────────────────────────
print(f"Loading {INPUT_FILE}...")
try:
    df = pd.read_csv(INPUT_FILE, dtype=str)
except FileNotFoundError:
    print(f"ERROR: {INPUT_FILE} not found — stopping.")
    exit(1)

if 'search_query' not in df.columns:
    print(f"ERROR: 'search_query' column not found. Columns present: {list(df.columns)}")
    exit(1)

# ── Resume support ─────────────────────────────────────────────────────────────
if os.path.exists(OUTPUT_FILE):
    done = pd.read_csv(OUTPUT_FILE, dtype=str)
    completed = set(done['search_query'].dropna().tolist())
    print(f"Resuming — {len(completed):,} rows already completed, skipping.")
else:
    done = pd.DataFrame()
    completed = set()

# ── Helpers ────────────────────────────────────────────────────────────────────
def extract_domain(url):
    try:
        parsed = urlparse(url if url.startswith('http') else 'https://' + url)
        host = parsed.netloc.lower().replace('www.', '')
        return host.split('/')[0]
    except Exception:
        return ''

def is_valid(domain):
    if not domain:
        return False
    return not any(skip in domain for skip in SKIP_DOMAINS)

def search_domain(query):
    try:
        r = requests.post(
            SERPER_URL,
            headers={'X-API-KEY': API_KEY, 'Content-Type': 'application/json'},
            json={'q': query},
            timeout=10
        )
        r.raise_for_status()
        results = r.json().get('organic', [])
        for result in results:
            link = result.get('link', '')
            domain = extract_domain(link)
            if is_valid(domain):
                return domain
        return ''
    except Exception as e:
        print(f"  [ERROR] {query}: {e}")
        return ''

# ── Main loop ──────────────────────────────────────────────────────────────────
to_process = df[~df['search_query'].isin(completed)].copy()
total = len(to_process)
print(f"{total:,} rows to process...\n")

results = []
for i, (_, row) in enumerate(to_process.iterrows(), 1):
    query = str(row['search_query']).strip()
    domain = search_domain(query)
    row_out = row.to_dict()
    row_out['domain'] = domain
    results.append(row_out)

    if i % 10 == 0 or i == total:
        found = sum(1 for r in results if r.get('domain'))
        print(f"  [{i}/{total}] Found domains: {found} ({found/i*100:.0f}%)")

        # Save incrementally
        batch = pd.DataFrame(results)
        if done.empty:
            combined = batch
        else:
            combined = pd.concat([done, batch], ignore_index=True)
        combined.to_csv(OUTPUT_FILE, index=False)

    time.sleep(DELAY)

print(f"\nDone. Results saved to {OUTPUT_FILE}")
found_total = sum(1 for r in results if r.get('domain'))
print(f"  {found_total:,} / {total:,} domains found ({found_total/total*100:.1f}%)")

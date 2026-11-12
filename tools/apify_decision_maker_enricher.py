#!/usr/bin/env python3
"""
Apify Decision Maker Enricher
------------------------------
Calls snipercoder/decision-maker-email-finder for each domain,
fills in person-level fields in each Smartlead-ready CSV listed in BRAND_FILES.

Cost cap: $0.95 hard stop (buffer before $1.00)
Resume: skips domains already in .tmp/apify_enrichment_results.csv
"""

import csv
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.getenv('APIFY_TOKEN')
ACTOR_ID    = 'snipercoder~decision-maker-email-finder'
BASE_URL    = f'https://api.apify.com/v2/acts/{ACTOR_ID}/runs'
COST_CAP    = 0.95  # hard stop before $1.00

ROOT = os.path.join(os.path.dirname(__file__), '..')
TMP  = os.path.join(ROOT, '.tmp')
OUT  = os.path.expanduser('~/Downloads')

CACHE_FILE = os.path.join(TMP, 'apify_enrichment_results.csv')

# One entry per lead list (for example, one per competitor brand the leads already stock).
BRAND_FILES = [
    {
        'domains_file': os.path.join(TMP, 'competitor_a_domains_clean.csv'),
        'output_file':  os.path.join(OUT, 'competitor_a_smartlead_final.csv'),
        'label': 'Competitor A',
    },
    {
        'domains_file': os.path.join(TMP, 'competitor_b_domains_clean.csv'),
        'output_file':  os.path.join(OUT, 'competitor_b_smartlead_final.csv'),
        'label': 'Competitor B',
    },
]

SMARTLEAD_COLS = [
    'first_name', 'last_name', 'email', 'company_name', 'domain',
    'job_title', 'department', 'email_type', 'email_status', 'confidence_score',
    'linkedin_url', 'direct_phone', 'company_phone', 'address', 'city',
    'state', 'country', 'industry', 'company_size', 'company_linkedin'
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_cache():
    """Return set of already-processed domains."""
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE, newline='', encoding='utf-8') as f:
        return {r['domain']: r for r in csv.DictReader(f)}

def save_to_cache(results):
    """Append list of result dicts to cache file."""
    if not results:
        return
    write_header = not os.path.exists(CACHE_FILE)
    fields = ['domain', 'first_name', 'last_name', 'email', 'job_title',
              'linkedin_url', 'direct_phone', 'result_index']
    with open(CACHE_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if write_header:
            w.writeheader()
        w.writerows(results)

def run_actor(domain):
    """Start actor run and return run_id."""
    r = requests.post(
        f'{BASE_URL}?token={APIFY_TOKEN}',
        json={
            'domain': domain,
            'category': 'CEO, Founder, Owner',
            'leadsFromDomain': 2,
        },
        timeout=30
    )
    r.raise_for_status()
    return r.json()['data']['id']

def wait_for_run(run_id, poll_interval=5, max_wait=400):
    """Poll until run finishes. Returns run data dict."""
    url = f'https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}'
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()['data']
        status = data['status']
        if status in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
            return data
    return None

def get_results(run_id):
    """Fetch dataset items from a completed run."""
    url = f'https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}'
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

def extract_cost(run_data):
    """Pull USD cost from run stats."""
    stats = run_data.get('stats', {})
    return float(stats.get('usageTotalUsd', 0) or 0)

def map_item(item, domain, idx):
    """Map actor output fields to our cache schema.
    Actor uses numbered keys: 02_First_name, 03_Last_name, 04_Email, etc.
    """
    return {
        'domain':       domain,
        'first_name':   item.get('02_First_name') or item.get('firstName') or item.get('first_name') or '',
        'last_name':    item.get('03_Last_name')  or item.get('lastName')  or item.get('last_name')  or '',
        'email':        item.get('04_Email')       or item.get('email') or '',
        'job_title':    item.get('07_Title')       or item.get('title') or item.get('jobTitle') or '',
        'linkedin_url': item.get('06_Linkedin_url') or item.get('linkedinUrl') or item.get('linkedin') or '',
        'direct_phone': item.get('05_Phone_number') or item.get('phone') or '',
        'result_index': idx,
    }

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not APIFY_TOKEN:
        print('ERROR: APIFY_TOKEN not set in .env')
        return

    # Load all domains with brand mapping
    all_domains = []
    for brand in BRAND_FILES:
        with open(brand['domains_file'], newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                all_domains.append({
                    'domain': row['domain'].strip().lower(),
                    'brand':  brand['label'],
                })

    print(f'Total domains: {len(all_domains)}')

    # Load cache — skip already done
    cache = load_cache()
    todo  = [d for d in all_domains if d['domain'] not in cache]
    print(f'Already processed: {len(cache)} | Remaining: {len(todo)}')
    print(f'Cost cap: ${COST_CAP:.2f}\n')

    running_cost = 0.0
    processed    = 0
    new_results  = []

    for i, entry in enumerate(todo, 1):
        domain = entry['domain']

        # Cost check before each run
        if running_cost >= COST_CAP:
            print(f'\n⚠️  Cost cap reached (${running_cost:.4f}). Stopping.')
            break

        print(f'[{i}/{len(todo)}] {domain}', end=' ... ', flush=True)

        try:
            run_id   = run_actor(domain)
            run_data = wait_for_run(run_id)

            if not run_data or run_data['status'] != 'SUCCEEDED':
                status = run_data['status'] if run_data else 'TIMEOUT'
                print(f'SKIPPED ({status})')
                # Still count toward cache so we don't retry endlessly
                save_to_cache([{'domain': domain, 'first_name': '', 'last_name': '',
                                'email': '', 'job_title': '', 'linkedin_url': '',
                                'direct_phone': '', 'result_index': 0}])
                cache[domain] = {}
                continue

            cost = extract_cost(run_data)
            running_cost += cost

            items = get_results(run_id)
            mapped = [map_item(item, domain, idx) for idx, item in enumerate(items)]

            if not mapped:
                mapped = [{'domain': domain, 'first_name': '', 'last_name': '',
                           'email': '', 'job_title': '', 'linkedin_url': '',
                           'direct_phone': '', 'result_index': 0}]

            save_to_cache(mapped)
            for m in mapped:
                cache[domain] = m  # store first result in memory cache

            emails_found = sum(1 for m in mapped if m.get('email'))
            print(f'{emails_found} email(s) | run cost ${cost:.4f} | total ${running_cost:.4f}')
            new_results.extend(mapped)
            processed += 1

            # Safety: re-check cap after adding cost
            if running_cost >= COST_CAP:
                print(f'\n⚠️  Cost cap reached (${running_cost:.4f}). Stopping.')
                break

        except Exception as e:
            print(f'ERROR: {e}')
            time.sleep(2)

    print(f'\n── Enrichment complete ──')
    print(f'Domains processed this run: {processed}')
    print(f'Total spent: ${running_cost:.4f}')

    # ── Merge results into Smartlead CSVs ─────────────────────────────────────
    print('\nMerging into Smartlead CSVs...')
    full_cache = load_cache()  # reload full cache for merge

    for brand in BRAND_FILES:
        # Load existing CSV
        existing = []
        with open(brand['output_file'], newline='', encoding='utf-8') as f:
            existing = list(csv.DictReader(f))

        updated = []
        for row in existing:
            domain = row['domain']
            enriched = full_cache.get(domain)

            if enriched and enriched.get('email'):
                row['first_name']   = enriched.get('first_name', '') or row['first_name']
                row['last_name']    = enriched.get('last_name', '')  or row['last_name']
                row['email']        = enriched.get('email', '')      or row['email']
                row['job_title']    = enriched.get('job_title', '')  or row['job_title']
                row['linkedin_url'] = enriched.get('linkedin_url', '') or row['linkedin_url']
                row['direct_phone'] = enriched.get('direct_phone', '') or row['direct_phone']

            updated.append(row)

        # Write back
        with open(brand['output_file'], 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=SMARTLEAD_COLS)
            w.writeheader()
            w.writerows(updated)

        emails = sum(1 for r in updated if r.get('email'))
        print(f'  {brand["label"]}: {len(updated)} rows | {emails} with email')

    print('\nDone. Files saved to ~/Downloads.')

if __name__ == '__main__':
    main()

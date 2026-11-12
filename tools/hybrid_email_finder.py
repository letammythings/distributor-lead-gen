#!/usr/bin/env python3
"""
Firecrawl Email Pattern Finder
-------------------------------
- Crawls top domains (ranked by NP count) using Firecrawl
- Stops early per domain as soon as valid emails are found
- Detects email pattern → generates emails for all NPs
- Budget-safe: processes top domains by NP coverage first

Input:  data/np_with_domains_cleaned.csv
Output: .tmp/final_np_emails.csv
Cache:  .tmp/domain_patterns_cache.csv
"""

import os
import re
import time
import requests
import pandas as pd
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

# ── Settings ───────────────────────────────────────────────────────────────────
INPUT_FILE     = 'data/np_with_domains_cleaned.csv'
OUTPUT_FILE    = os.path.join(os.path.dirname(__file__), '..', '.tmp', 'final_np_emails.csv')
PATTERNS_CACHE = os.path.join(os.path.dirname(__file__), '..', '.tmp', 'domain_patterns_cache.csv')

FIRECRAWL_MAX  = 100        # max domains to crawl (credit budget)
MAX_EMAILS     = 3          # max emails to keep per domain
PAGE_TIMEOUT   = 8          # seconds per page request
DELAY          = 1.0        # seconds between requests

FIRECRAWL_KEY  = os.getenv('FIRECRAWL_API_KEY')

# Priority pages — stop as soon as emails are found
PRIORITY_PAGES = ['/contact', '/contact-us', '/about', '/team', '/staff', '/providers', '/directory']

# Skip these domains entirely
BAD_DOMAIN_KEYWORDS = ['.gov', 'healthgrades', 'zocdoc', 'webmd', 'yelp', 'facebook',
                       'linkedin', 'npidb', 'npino', 'doximity', 'medicare', 'medicaid']

# Generic emails to discard
GENERIC_RE  = re.compile(r'^(info|support|contact|admin|billing|noreply|no-reply|donotreply|webmaster|privacy|help|office|appointments|reception|hr|careers)@', re.I)
EMAIL_RE    = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Name-like email patterns (preferred)
NAME_LIKE   = re.compile(r'^[a-z]+\.[a-z]+$|^[a-z][a-z]{3,}$', re.I)

# ── Load input ─────────────────────────────────────────────────────────────────
print("Loading NP data...")
df = pd.read_csv(INPUT_FILE, dtype=str)
df['domain_clean'] = df['domain_clean'].fillna('').str.strip().str.lower()

# Rank domains by number of NPs — maximize coverage per credit spent
domain_counts = df[df['domain_clean'].ne('')]['domain_clean'].value_counts()
all_domains   = domain_counts.index.tolist()

# Filter out bad domains
good_domains = [d for d in all_domains if not any(bad in d for bad in BAD_DOMAIN_KEYWORDS)]

print(f"  Total NPs: {len(df):,}")
print(f"  Unique domains (clean): {len(good_domains):,}")
print(f"  Top {FIRECRAWL_MAX} domains cover: {domain_counts[good_domains[:FIRECRAWL_MAX]].sum():,} NPs")

# ── Resume: load cached patterns ───────────────────────────────────────────────
if os.path.exists(PATTERNS_CACHE):
    cache_df  = pd.read_csv(PATTERNS_CACHE, dtype=str)
    patterns  = dict(zip(cache_df['domain'], cache_df['pattern']))
    print(f"  Loaded {len(patterns):,} cached patterns — will skip completed domains.")
else:
    patterns = {}

def save_patterns():
    pd.DataFrame([{'domain': d, 'pattern': p} for d, p in patterns.items()]) \
      .to_csv(PATTERNS_CACHE, index=False)

# ── Helpers ────────────────────────────────────────────────────────────────────
def is_valid_email(email, domain):
    """Keep only on-domain, non-generic, real-looking emails."""
    local = email.split('@')[0].lower()
    host  = email.split('@')[1].lower() if '@' in email else ''
    if domain not in host:
        return False
    if GENERIC_RE.match(local):
        return False
    return True

def prefer_name_like(emails):
    """Sort so name-like emails (first.last, jsmith) come first."""
    def score(e):
        local = e.split('@')[0]
        return 0 if NAME_LIKE.match(local) else 1
    return sorted(emails, key=score)

def detect_pattern(emails):
    """Infer format from real email locals."""
    scores = Counter()
    for email in emails:
        local = email.split('@')[0].lower()
        if re.match(r'^[a-z]+\.[a-z]+$', local):
            scores['first.last'] += 1
        elif re.match(r'^[a-z][a-z]{3,15}$', local) and '.' not in local:
            # Could be firstlast or firstinitiallast — check length heuristic
            if len(local) <= 8:
                scores['firstinitiallast'] += 1
            else:
                scores['firstlast'] += 1
        elif re.match(r'^[a-z]{2,8}$', local):
            scores['first'] += 1
    return scores.most_common(1)[0][0] if scores else None

def generate_email(first, last, domain, pattern):
    f = str(first).lower().strip()
    l = str(last).lower().strip()
    if not f or not l or not domain or not pattern or f == 'nan' or l == 'nan':
        return ''
    if pattern == 'first.last':
        return f'{f}.{l}@{domain}'
    elif pattern == 'firstinitiallast':
        return f'{f[0]}{l}@{domain}'
    elif pattern == 'first':
        return f'{f}@{domain}'
    elif pattern == 'firstlast':
        return f'{f}{l}@{domain}'
    return ''

def firecrawl_scrape(url):
    """Scrape a single page via Firecrawl. Returns markdown text or None."""
    try:
        r = requests.post(
            'https://api.firecrawl.dev/v1/scrape',
            headers={'Authorization': f'Bearer {FIRECRAWL_KEY}', 'Content-Type': 'application/json'},
            json={'url': url, 'formats': ['markdown']},
            timeout=PAGE_TIMEOUT
        )
        if r.status_code == 200:
            return r.json().get('data', {}).get('markdown', '')
        return None
    except Exception:
        return None

# ── Main crawl loop ────────────────────────────────────────────────────────────
if not FIRECRAWL_KEY:
    print("\nERROR: FIRECRAWL_API_KEY not set in .env — stopping.")
    exit(1)

to_crawl = [d for d in good_domains[:FIRECRAWL_MAX] if d not in patterns]
print(f"\n── Firecrawl: {len(to_crawl)} domains to process ({FIRECRAWL_MAX - len(to_crawl)} already cached) ──\n")

for i, domain in enumerate(to_crawl, 1):
    np_count = domain_counts.get(domain, 0)
    print(f"[{i}/{len(to_crawl)}] {domain} ({np_count} NPs)")

    found_emails = []

    for path in PRIORITY_PAGES:
        url = f'https://{domain}{path}'
        content = firecrawl_scrape(url)

        if content:
            raw_emails = EMAIL_RE.findall(content)
            valid = [e.lower() for e in raw_emails if is_valid_email(e.lower(), domain)]
            valid = prefer_name_like(valid)[:MAX_EMAILS]

            if valid:
                found_emails = valid
                print(f"  ✓ Found {len(valid)} email(s) on {path} — stopping early")
                break
            else:
                print(f"  - {path}: no valid emails")
        else:
            print(f"  - {path}: failed/timeout")

        time.sleep(0.5)

    if found_emails:
        pattern = detect_pattern(found_emails)
        if pattern:
            patterns[domain] = pattern
            print(f"  → Pattern: {pattern}")
        else:
            print(f"  → Could not detect pattern from: {found_emails}")
    else:
        print(f"  → No emails found on any page")

    save_patterns()
    time.sleep(DELAY)

# ── Generate emails for all NPs ────────────────────────────────────────────────
print(f"\n── Generating emails for {len(df):,} NPs ──")

df['pattern']         = df['domain_clean'].map(patterns)
df['generated_email'] = df.apply(
    lambda r: generate_email(r.get('first_name',''), r.get('last_name',''),
                             r.get('domain_clean',''), r.get('pattern','')), axis=1
)

# ── Output ─────────────────────────────────────────────────────────────────────
out_cols = ['first_name', 'last_name', 'credential', 'facility_name',
            'city', 'state', 'domain_clean', 'pattern', 'generated_email']
out_cols = [c for c in out_cols if c in df.columns]
out = df[out_cols].rename(columns={'domain_clean': 'domain'})

with_email = out[out['generated_email'].ne('')].drop_duplicates('generated_email')
with_email.to_csv(OUTPUT_FILE, index=False)

print(f"\n✓ Done.")
print(f"  Patterns detected: {len(patterns):,} domains")
print(f"  NPs with generated email: {len(with_email):,}")
print(f"  Saved → .tmp/final_np_emails.csv")

"""
nppes_np_scorer.py
------------------
Scores and tiers 512K NPs from NPPES data based on YourBrand commercial ICP.

Scoring dimensions:
  1. Clinical Fit   — how often they see product-relevant patient scenarios
  2. Commercial Fit — how likely the practice is to stock/adopt vs bureaucratic friction

Output files:
  .tmp/np_tier1_wholesale.csv     — Revenue accounts (score >= 60)
  .tmp/np_tier2_portal.csv        — Referral/portal accounts (score 30-59)
  .tmp/np_tier3_awareness.csv     — Awareness/event audience (score < 30)
  .tmp/np_all_scored.csv          — Full list with scores and tiers

Usage: python3 tools/nppes_np_scorer.py
"""

import csv
import os
import re

INPUT_FILE  = ".tmp/nppes_nurse_practitioners.csv"
OUT_TIER1   = ".tmp/np_tier1_wholesale.csv"
OUT_TIER2   = ".tmp/np_tier2_portal.csv"
OUT_TIER3   = ".tmp/np_tier3_awareness.csv"
OUT_ALL     = ".tmp/np_all_scored.csv"

# ── US states only ─────────────────────────────────────────────────────────────
US_STATES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC','PR','GU','VI','MP','AS'
}

# ── Scoring rules ───────────────────────────────────────────────────────────────
# Each rule: (regex_pattern, field, score, description)
# field: 'practice' or 'taxonomy'

# NOTE: NPPES Type 1 (individual NPI) records do not include practice names.
# All scoring is taxonomy-based only. Practice-name scoring requires a separate
# enrichment step (e.g. Google Maps → practice website → company name lookup).

CLINICAL_RULES = [
    # Tier 1 taxonomy signals — high clinical fit for YourBrand use cases
    (r'acute care',          'taxonomy', 40, 'Acute Care NP — sees injuries/casts/wounds'),
    (r'adult health',        'taxonomy', 20, 'Adult Health NP — outpatient adult care'),

    # Tier 2 taxonomy signals — broad but relevant
    (r'\bfamily\b',          'taxonomy', 15, 'Family NP — sees cast/fracture follow-ups'),
    (r'pediatric',           'taxonomy', 15, 'Pediatric NP — high fracture rate in kids'),
    (r'primary care',        'taxonomy', 12, 'Primary Care NP'),
    (r'gerontology',         'taxonomy', 10, 'Gerontology NP — elderly falls/fractures'),

    # Lower fit
    (r'neonatal',            'taxonomy',  0, 'Neonatal — low YourBrand fit'),
    (r"women.?s health|ob.?gyn|obstetric", 'taxonomy', 5, "Women's Health NP"),
]

def score_np(practice: str, taxonomy: str) -> tuple[int, list]:
    """Returns (total_score, matched_rules)"""
    score = 0
    matched = []
    p = practice.lower()
    t = taxonomy.lower()

    for pattern, field, pts, label in CLINICAL_RULES:
        text = p if field == 'practice' else t
        if re.search(pattern, text):
            score += pts
            matched.append(label)

    return max(score, 0), matched  # floor at 0

def assign_tier(score: int) -> str:
    if score >= 40:
        return 'Tier1'
    elif score >= 10:
        return 'Tier2'
    else:
        return 'Tier3'

def main():
    os.makedirs('.tmp', exist_ok=True)

    counts = {'Tier1': 0, 'Tier2': 0, 'Tier3': 0, 'skipped_intl': 0}

    # Open all output files
    with open(INPUT_FILE, newline='', encoding='utf-8') as fin, \
         open(OUT_ALL,   'w', newline='', encoding='utf-8') as fall, \
         open(OUT_TIER1, 'w', newline='', encoding='utf-8') as f1, \
         open(OUT_TIER2, 'w', newline='', encoding='utf-8') as f2, \
         open(OUT_TIER3, 'w', newline='', encoding='utf-8') as f3:

        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames + ['score', 'tier', 'signals']

        writers = {
            'all':   csv.DictWriter(fall, fieldnames=fieldnames),
            'Tier1': csv.DictWriter(f1,   fieldnames=fieldnames),
            'Tier2': csv.DictWriter(f2,   fieldnames=fieldnames),
            'Tier3': csv.DictWriter(f3,   fieldnames=fieldnames),
        }
        for w in writers.values():
            w.writeheader()

        for i, row in enumerate(reader):
            # US-only filter
            if row.get('state', '').strip().upper() not in US_STATES:
                counts['skipped_intl'] += 1
                continue

            practice = row.get('practice_name', '') or ''
            taxonomy = row.get('taxonomy_description', '') or ''

            score, signals = score_np(practice, taxonomy)
            tier = assign_tier(score)
            counts[tier] += 1

            row['score']   = score
            row['tier']    = tier
            row['signals'] = ' | '.join(signals) if signals else ''

            writers['all'].writerow(row)
            writers[tier].writerow(row)

            if (i + 1) % 100000 == 0:
                print(f"[Score] Processed {i+1:,} records...")

    total = sum(v for k, v in counts.items() if k != 'skipped_intl')
    print(f"\n[Done] Scored {total:,} US NPs")
    print(f"  Tier 1 (Wholesale/Revenue):  {counts['Tier1']:,}")
    print(f"  Tier 2 (Portal/Referral):    {counts['Tier2']:,}")
    print(f"  Tier 3 (Awareness/Event):    {counts['Tier3']:,}")
    print(f"  Skipped (non-US):            {counts['skipped_intl']:,}")
    print(f"\nOutputs:")
    print(f"  {OUT_TIER1}")
    print(f"  {OUT_TIER2}")
    print(f"  {OUT_TIER3}")
    print(f"  {OUT_ALL}")

if __name__ == '__main__':
    main()

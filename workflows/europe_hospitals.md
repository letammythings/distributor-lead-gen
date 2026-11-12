# Workflow: Europe Hospital Scraper

## Objective
Build a complete database of hospitals across all of Europe with physical mailing addresses and emails for YourBrand direct mail and future outreach.

## Scope
- 44 European countries
- Hospital-type facilities only (not nursing homes, dental, GP surgeries)
- All data sources are free — no API keys required

## Pipeline (run scripts in this order)

### Step 1 — OSM Scraper
```bash
python tools/europe_hospitals_osm.py
```
- Queries OpenStreetMap Overpass API for all `amenity=hospital` across 44 countries
- Runs country-by-country with 3s delay between requests
- Saves progress after each country — safe to interrupt and resume
- Expected runtime: 15-25 minutes
- Output: `.tmp/europe_hospitals_osm.csv`
- Expected volume: 12,000-20,000 hospitals

### Step 2 — Supplement Downloads
```bash
python tools/europe_hospitals_supplements.py
```
- Downloads NHS ODS hospital trusts register (UK) — official government CSV
- Downloads FINESS health facility register (France) — official government CSV
- Both are direct downloads, no API key needed
- Expected runtime: 2-5 minutes
- Output: `.tmp/europe_hospitals_nhs.csv`, `.tmp/europe_hospitals_finess.csv`

### Step 3 — Merge & Deduplicate
```bash
python tools/europe_hospitals_merger.py
```
- Merges OSM + NHS + FINESS into one clean master list
- Deduplicates by normalized (name, city, country) — accent-insensitive
- Supplements merge in data missing from OSM (e.g. phone numbers)
- Sorts by country then name
- Output: `.tmp/europe_hospitals_master.csv`

### Step 4 — Email Crawler (free, no API)
```bash
python tools/europe_hospitals_email_crawler.py
```
- For every hospital with a website: fetches homepage + contact page
- Extracts emails using regex, filters junk (noreply, webmaster, etc.)
- Picks best email (prefers info@, contact@ over generic)
- Saves progress incrementally — safe to interrupt and resume
- Expected runtime: several hours for full dataset (respects rate limits)
- Expected email hit rate: 30-50% of hospitals with websites
- Output: `.tmp/europe_hospitals_final.csv`

**STOP HERE — next step (Hunter.io) uses paid credits.**
Run Hunter.io enrichment only if email coverage from crawling is insufficient.

## Final Output Columns
| Column | Description |
|---|---|
| name | Hospital name (English where available) |
| street | Street address |
| city | City |
| postcode | Postal code |
| country_code | ISO 3166-1 alpha-2 (FR, DE, GB, etc.) |
| country_name | Full country name |
| phone | Phone number |
| website | Hospital website URL |
| email | Best email found |
| lat | Latitude (OSM rows only) |
| lon | Longitude (OSM rows only) |
| source | OSM / NHS ODS / FINESS |

## Known Constraints
- Overpass API is free but rate-limited — do not remove the 3s delay between countries
- FINESS download URL may change as data.gouv.fr publishes updates — script auto-discovers the current URL via API
- NHS ODS zip structure may change if NHS Digital updates the file format — check etr.zip column order if NHS data looks wrong
- Email crawler respects robots and uses 0.5-1.5s delays — do not reduce this
- Some hospital websites block scrapers — these will have blank emails, which is expected

## Data Quality Notes
- OSM coverage: ~90% Western Europe, ~75% Eastern Europe
- Address completeness: ~85% of hospitals will have a usable street address
- OSM hospital data for Germany is exceptionally complete
- UK: OSM + NHS ODS combined gives near-complete coverage
- France: OSM + FINESS combined gives near-complete coverage

## Status
- [ ] Step 1: OSM scrape
- [ ] Step 2: Supplement downloads
- [ ] Step 3: Merge
- [ ] Step 4: Email crawl

# Distributor Lead Gen Pipeline

A Python pipeline for finding and qualifying B2B distributors and retailers for a product
entering retail and medical distribution. It finds businesses that already sell similar
products, so outreach starts warm instead of cold.

Built for a real prospecting project. Client names and lead data are removed. The brand is
written as `YourBrand` in the code.

## What it does

1. **Find businesses** from free and paid sources
   - `cms_dmepos_scraper.py`: US medical equipment suppliers from the public CMS dataset
   - `nppes_np_parser.py`: the free NPI registry from CMS.gov
   - `gmaps_medical_scraper.py`: Google Maps results through SerpAPI
   - `europe_hospitals_osm.py`: every hospital in a European country from OpenStreetMap
2. **Score and filter** them
   - `nppes_np_scorer.py`: scores providers into tiers by clinical and commercial fit
   - `lead_filters.py`: keeps real buyers, rejects marketplaces, big chains and directories
3. **Find websites and contacts**
   - `serper_domain_lookup.py`: finds each business's website
   - `free_contact_finder.py`, `batch_enricher.py`: crawl sites for emails, phones and socials
   - `hybrid_email_finder.py`: guesses email patterns with Firecrawl
   - `apify_decision_maker_enricher.py`: finds decision makers by role
   - `europe_hospitals_email_crawler.py`: hospital contact pages
4. **Draft outreach** in a local web app
   - `lead_ui.py`: browse leads, then Claude writes a cold email, LinkedIn DM and follow-up for
     each one, using the brand they already stock as the hook

`workflows/europe_hospitals.md` is a step-by-step run of the Europe pipeline.

## Tools and tech

Python, Flask, Claude API, SerpAPI, Serper, Firecrawl, Apify, OpenStreetMap Overpass, CMS public data.

## Setup

```bash
git clone https://github.com/letammythings/distributor-lead-gen.git
cd distributor-lead-gen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # add only the keys for the tools you use
```

## Run the outreach app

Put lead files named `*_contacts.csv` in `.tmp/`, then:

```bash
python tools/lead_ui.py
```

Open http://127.0.0.1:5002. Drafting outreach needs `ANTHROPIC_API_KEY`.

## Notes

- Input and output file paths are set at the top of each script. Change them to your own files.
- Lead data is not included in this repo.

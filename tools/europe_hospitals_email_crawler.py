"""
europe_hospitals_email_crawler.py
-----------------------------------
Crawls each hospital website from the master CSV and extracts email addresses.

Strategy per hospital:
  1. Fetch homepage
  2. Sniff for a contact/about page link and fetch that too
  3. Extract all emails from both pages using regex
  4. Filter junk emails (noreply, webmaster, privacy, etc.)
  5. Save the best email back to the CSV

Free — uses requests + BeautifulSoup, no API key needed.
Progress is saved after every hospital so the script can be safely interrupted.

Input:  .tmp/europe_hospitals_master.csv
Output: .tmp/europe_hospitals_final.csv
"""

import csv
import os
import re
import time
import random
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

INPUT_FILE = ".tmp/europe_hospitals_master.csv"
OUTPUT_FILE = ".tmp/europe_hospitals_final.csv"

REQUEST_TIMEOUT = 10       # seconds per page fetch
DELAY_MIN = 0.5            # seconds between requests (be polite)
DELAY_MAX = 1.5
MAX_PAGES_PER_HOSPITAL = 2  # homepage + 1 contact page

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
}

# Contact page path keywords (multilingual — covers most European languages)
CONTACT_PATH_KEYWORDS = [
    "contact", "kontakt", "kontakta", "kontakty", "kontakte",
    "about", "uber-uns", "a-propos", "qui-sommes", "sobre-nos",
    "over-ons", "chi-siamo", "o-nas", "om-oss",
    "impressum",  # German legal/contact page
]

# Emails to filter out (junk / generic system emails)
JUNK_EMAIL_PATTERNS = [
    r"noreply", r"no-reply", r"donotreply", r"do-not-reply",
    r"webmaster", r"postmaster", r"hostmaster",
    r"admin@", r"abuse@",
    r"privacy@", r"dpo@", r"gdpr@",
    r"example\.", r"test@", r"\.png$", r"\.jpg$", r"\.gif$",
    r"sentry\.", r"wix\.", r"wordpress\.",
]

JUNK_PATTERN = re.compile("|".join(JUNK_EMAIL_PATTERNS), re.IGNORECASE)
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch(url, timeout=REQUEST_TIMEOUT):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None

def extract_emails(html):
    if not html:
        return []
    # Also decode obfuscated mailto: links
    emails = set(EMAIL_REGEX.findall(html))
    # Filter junk
    emails = {e for e in emails if not JUNK_PATTERN.search(e)}
    return list(emails)

def find_contact_url(base_url, html):
    """Find a contact or about page link on the homepage."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        for keyword in CONTACT_PATH_KEYWORDS:
            if keyword in href or keyword in text:
                full_url = urljoin(base_url, a["href"])
                # Stay on the same domain
                if urlparse(full_url).netloc == urlparse(base_url).netloc:
                    return full_url
    return None

def normalize_url(url):
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url

def pick_best_email(emails):
    """Prefer info@, contact@, hospital name addresses over generic ones."""
    if not emails:
        return ""
    priority = ["info@", "contact@", "hospital@", "hopital@", "krankenhaus@",
                "secretariat@", "secretaria@", "sekretariat@"]
    for prefix in priority:
        for e in emails:
            if e.lower().startswith(prefix):
                return e
    return sorted(emails)[0]  # fallback: alphabetical first

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"ERROR: {INPUT_FILE} not found. Run europe_hospitals_merger.py first.")
        return

    # Load master CSV
    with open(INPUT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    print(f"Loaded {len(all_rows)} hospitals from {INPUT_FILE}")

    # Load existing output to resume from where we left off
    processed_names = set()
    output_rows = []
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                output_rows.append(row)
                processed_names.add(row.get("name", "") + "|" + row.get("country_code", ""))
        print(f"Resuming — {len(output_rows)} already processed")

    # Ensure output fieldnames include email
    out_fieldnames = list(fieldnames) if fieldnames else []
    if "email" not in out_fieldnames:
        out_fieldnames.append("email")

    # Open output in append mode for resume
    mode = "a" if output_rows else "w"
    outfile = open(OUTPUT_FILE, mode, newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=out_fieldnames, extrasaction="ignore")
    if not output_rows:
        writer.writeheader()

    total = len(all_rows)
    crawled = 0
    emails_found = 0

    for i, row in enumerate(all_rows):
        resume_key = row.get("name", "") + "|" + row.get("country_code", "")
        if resume_key in processed_names:
            continue

        website = normalize_url(row.get("website", ""))
        existing_email = row.get("email", "").strip()

        if existing_email:
            # Already has email from OSM — write as-is
            writer.writerow(row)
            outfile.flush()
            emails_found += 1
            crawled += 1
            continue

        if not website:
            # No website — write as-is, nothing to crawl
            writer.writerow(row)
            outfile.flush()
            crawled += 1
            if crawled % 100 == 0:
                print(f"  Progress: {crawled}/{total} | emails found: {emails_found}")
            continue

        # Fetch homepage
        html = fetch(website)
        emails = extract_emails(html)

        # Try contact page if homepage had no emails
        if not emails and html:
            contact_url = find_contact_url(website, html)
            if contact_url:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                contact_html = fetch(contact_url)
                emails.extend(extract_emails(contact_html))

        best_email = pick_best_email(emails)
        row["email"] = best_email

        if best_email:
            emails_found += 1

        writer.writerow(row)
        outfile.flush()
        crawled += 1

        if crawled % 50 == 0:
            pct = crawled / total * 100
            print(f"  Progress: {crawled}/{total} ({pct:.1f}%) | emails found: {emails_found}")

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    outfile.close()
    print(f"\nDone.")
    print(f"  Total hospitals: {total}")
    print(f"  Emails found: {emails_found}")
    print(f"  Hit rate: {emails_found / total * 100:.1f}%")
    print(f"  Output: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

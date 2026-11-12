"""
batch_enricher.py

Runs the free web-crawl enricher on any domain list CSV.
Fetches homepage + contact page, extracts emails, phones, company name.

Usage:
  python batch_enricher.py                          # uses default all_new_leads_domains.csv
  python batch_enricher.py --input my_domains.csv   # custom input (path relative to .tmp/)

Output: .tmp/all_new_leads_contacts.csv (resume-safe, saves after every domain)
"""

import sys
import os
import requests
import re
import time
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

INPUT_FILE  = "../.tmp/all_new_leads_domains.csv"
OUTPUT_FILE = "../.tmp/all_new_leads_contacts.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

CONTACT_PAGE_HINTS = [
    "/contact", "/contact-us", "/contactus", "/about", "/about-us",
    "/aboutus", "/reach-us", "/get-in-touch", "/info", "/company"
]


def fetch(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""

def get_homepage(domain):
    for scheme in ["https://", "http://"]:
        html = fetch(scheme + domain)
        if html:
            return scheme + domain, html
    return None, ""

def extract_emails(text):
    found = re.findall(r"[a-zA-Z0-9_.%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return list({e.lower() for e in found
                 if not any(junk in e.lower() for junk in
                            ["example", "test", "your@", "email@", "name@",
                             "user@", "domain.com", "sentry", ".png", ".jpg",
                             "noreply", "no-reply", "privacy@"])})

def extract_phones(text):
    found = re.findall(
        r"(?:\+1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}", text
    )
    return list(set(p.strip() for p in found if len(re.sub(r"\D", "", p)) >= 10))[:3]

def extract_address(text):
    patterns = [
        r"\d{1,5}\s+[A-Z][a-zA-Z0-9\s,\.]{5,60}(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Way|Court|Ct|Place|Pl|Suite|Ste)[,\s]+[A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}",
        r"\d{1,5}\s+[A-Z][a-zA-Z0-9\s,\.]{5,60}(?:Street|St|Avenue|Ave|Blvd|Drive|Dr|Road|Rd)[^\n]{0,60}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return ""

def extract_company_name(soup, domain):
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        for sep in [" | ", " - ", " – ", " — "]:
            if sep in title:
                return title.split(sep)[0].strip()
        return title[:60]
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:60]
    return domain.split(".")[0].title()

def find_contact_url(base_url, soup):
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if any(hint in href or hint.strip("/") in text for hint in CONTACT_PAGE_HINTS):
            full = urljoin(base_url, a["href"])
            if (urlparse(full).netloc == urlparse(base_url).netloc or
                    not urlparse(a["href"]).netloc):
                return full
    for hint in CONTACT_PAGE_HINTS:
        url = base_url.rstrip("/") + hint
        html = fetch(url)
        if html:
            return url
    return None

def enrich_domain(domain):
    result = {
        "domain":       domain,
        "company_name": "",
        "emails":       "",
        "phones":       "",
        "address":      "",
        "contact_url":  "",
        "source_urls":  "",
    }

    base_url, homepage_html = get_homepage(domain)
    if not homepage_html:
        return result

    soup = BeautifulSoup(homepage_html, "html.parser")
    result["company_name"] = extract_company_name(soup, domain)

    all_text   = soup.get_text(separator=" ")
    all_emails = set(extract_emails(all_text))
    all_phones = set(extract_phones(all_text))
    all_urls   = [base_url]

    contact_url = find_contact_url(base_url, soup)
    if contact_url and contact_url != base_url:
        contact_html = fetch(contact_url)
        if contact_html:
            contact_soup = BeautifulSoup(contact_html, "html.parser")
            contact_text = contact_soup.get_text(separator=" ")
            all_emails.update(extract_emails(contact_text))
            all_phones.update(extract_phones(contact_text))
            address = extract_address(contact_text)
            if address:
                result["address"] = address
            all_urls.append(contact_url)
            result["contact_url"] = contact_url

    if not result["address"]:
        result["address"] = extract_address(all_text)

    result["emails"]      = " | ".join(sorted(all_emails))
    result["phones"]      = " | ".join(sorted(all_phones))
    result["source_urls"] = " | ".join(all_urls)

    return result


def main():
    args = sys.argv[1:]
    input_file = INPUT_FILE
    if "--input" in args:
        idx = args.index("--input")
        if idx + 1 < len(args):
            input_file = f"../.tmp/{args[idx+1]}"

    print("=" * 60)
    print(f"Batch Domain Enricher")
    print(f"Input:  {input_file}")
    print(f"Output: {OUTPUT_FILE}")
    print("=" * 60)

    # Resume support
    already_done = set()
    rows = []
    try:
        existing_out = pd.read_csv(OUTPUT_FILE)
        already_done = set(existing_out["domain"].dropna().str.lower().tolist())
        rows = existing_out.to_dict("records")
        print(f"[Resume] {len(already_done)} domains already enriched\n")
    except FileNotFoundError:
        pass

    try:
        df = pd.read_csv(input_file)
    except FileNotFoundError:
        print(f"[!] Input file not found: {input_file}")
        return

    domains = df["domain"].dropna().unique().tolist()
    todo = [d for d in domains if d.lower() not in already_done]
    print(f"Total:     {len(domains)} domains")
    print(f"Todo:      {len(todo)} (skipping {len(already_done)} already done)\n")

    for i, domain in enumerate(todo):
        print(f"[{i+1}/{len(todo)}] {domain}")
        row = enrich_domain(domain)
        # Carry over source_track if present
        if "source_track" in df.columns:
            match = df[df["domain"] == domain]
            if not match.empty:
                row["source_track"] = match.iloc[0]["source_track"]
        rows.append(row)
        pd.DataFrame(rows).to_csv(OUTPUT_FILE, index=False)
        time.sleep(1.2)

    df_out = pd.DataFrame(rows)
    with_email = df_out[df_out["emails"].notna() & (df_out["emails"] != "")]
    print(f"\n{'='*60}")
    print(f"DONE. {len(df_out)} enriched | {len(with_email)} with email ({len(with_email)/len(df_out)*100:.0f}%)")
    print(f"-> {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()

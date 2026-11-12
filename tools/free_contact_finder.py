#!/usr/bin/env python3
"""
free_contact_finder.py

Finds decision-maker emails for no-email domains using 3 free methods in order:
  1. DuckDuckGo search (no API, completely free, unlimited)
  2. Google Custom Search API (100 req/day free, keys in .env)
  3. Direct website scrape (regex email extraction from homepage + /contact)

For each domain, tries to find:
  - Email addresses (from search snippets or page HTML)
  - LinkedIn profile (name + title extracted from snippet)

Output: .tmp/free_contact_results.csv (resume-safe)
"""

import os
import re
import sys
import time
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("[!] ddgs not installed. Run: pip3 install ddgs --break-system-packages")
        sys.exit(1)

load_dotenv()

MASTER        = "data/master_leads.csv"
OUTPUT        = ".tmp/free_contact_results.csv"
GOOGLE_KEY    = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX     = os.getenv("GOOGLE_CX")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE
)

JUNK_EMAILS = re.compile(
    r'^(noreply|no-reply|donotreply|info|sales|contact|admin|support|'
    r'office|hello|help|team|mail|general|enquiries|enquiry|service|'
    r'accounts|billing|hr|marketing|media|press|reception|'
    r'customerservice|customer\.service|privacy|legal|abuse|webmaster|'
    r'postmaster|sentry|example|test|demo)@',
    re.IGNORECASE
)

LINKEDIN_RE = re.compile(r'linkedin\.com/in/[\w\-]+', re.IGNORECASE)

CONTACT_PATHS = ["/contact", "/contact-us", "/contactus", "/about", "/about-us",
                 "/team", "/get-in-touch", "/reach-us"]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def extract_emails(text, domain=None):
    """Pull real business emails from raw text, optionally filtered to domain."""
    found = EMAIL_RE.findall(text)
    out = []
    for e in found:
        e = e.strip().lower().rstrip(".,;:")
        if JUNK_EMAILS.match(e):
            continue
        if domain and domain.lower() not in e:
            continue
        if "example" in e or "test" in e:
            continue
        out.append(e)
    return list(dict.fromkeys(out))  # dedup preserving order


def extract_linkedin(text):
    m = LINKEDIN_RE.search(text)
    return f"https://www.{m.group(0)}" if m else ""


def extract_name_from_snippet(snippet, title_tag=""):
    """Best-effort: pull a name from a LinkedIn snippet like 'John Smith · CEO at ...'"""
    for sep in ["·", "|", "-", "–"]:
        parts = snippet.split(sep)
        if len(parts) >= 2:
            candidate = parts[0].strip()
            words = candidate.split()
            # Likely a name: 2-3 words, each capitalized
            if 2 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
                first = words[0]
                last = " ".join(words[1:])
                return first, last
    return "", ""


def fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""


# ── METHOD 1: DUCKDUCKGO ──────────────────────────────────────────────────────

def ddg_search(query, max_results=5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        print(f"    DDG error: {e}")
        return []


def search_ddg_for_domain(domain, company_name=""):
    """Try multiple DDG queries to find email or LinkedIn for this domain."""
    queries = []
    if company_name:
        queries.append(f'"{company_name}" owner OR founder OR CEO email')
        queries.append(f'site:linkedin.com/in "{company_name}"')
    queries.append(f'site:linkedin.com/in "{domain}" owner OR CEO OR founder')
    queries.append(f'"{domain}" contact email owner')

    emails = []
    linkedin = ""
    first_name, last_name, job_title = "", "", ""

    for q in queries:
        results = ddg_search(q, max_results=5)
        for r in results:
            snippet = r.get("body", "") + " " + r.get("title", "")
            href = r.get("href", "")

            # Extract emails from snippet
            found = extract_emails(snippet, domain)
            emails.extend(found)

            # Extract LinkedIn
            if not linkedin and "linkedin.com/in" in href.lower():
                linkedin = href
                fn, ln = extract_name_from_snippet(r.get("title", ""))
                if fn:
                    first_name, last_name = fn, ln
                # Try to get title from snippet
                for sep in ["·", "|"]:
                    parts = r.get("title", "").split(sep)
                    if len(parts) >= 2:
                        job_title = parts[1].strip().split(" at ")[0].strip()
                        break

        if emails or linkedin:
            break
        time.sleep(0.5)

    return {
        "emails":      list(dict.fromkeys(emails)),
        "linkedin":    linkedin,
        "firstName":   first_name,
        "lastName":    last_name,
        "jobTitle":    job_title,
        "method":      "duckduckgo",
    }


# ── METHOD 2: GOOGLE CUSTOM SEARCH ───────────────────────────────────────────

_google_calls_today = 0
GOOGLE_DAILY_LIMIT = 95  # leave 5 buffer

def search_google(query):
    global _google_calls_today
    if not GOOGLE_KEY or not GOOGLE_CX:
        return []
    if _google_calls_today >= GOOGLE_DAILY_LIMIT:
        return []
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_KEY, "cx": GOOGLE_CX, "q": query, "num": 5},
            timeout=10
        )
        _google_calls_today += 1
        if r.status_code == 200:
            return r.json().get("items", [])
        elif r.status_code == 429:
            print("    [!] Google CSE daily limit hit")
            _google_calls_today = GOOGLE_DAILY_LIMIT
    except Exception as e:
        print(f"    Google CSE error: {e}")
    return []


def search_google_for_domain(domain, company_name=""):
    queries = []
    if company_name:
        queries.append(f'site:linkedin.com/in "{company_name}" owner OR CEO OR founder')
    queries.append(f'site:linkedin.com/in "{domain}"')

    emails = []
    linkedin = ""
    first_name, last_name, job_title = "", "", ""

    for q in queries:
        items = search_google(q)
        for item in items:
            snippet = item.get("snippet", "") + " " + item.get("title", "")
            href = item.get("link", "")
            found = extract_emails(snippet, domain)
            emails.extend(found)
            if not linkedin and "linkedin.com/in" in href.lower():
                linkedin = href
                fn, ln = extract_name_from_snippet(item.get("title", ""))
                if fn:
                    first_name, last_name = fn, ln
        if emails or linkedin:
            break

    return {
        "emails":    list(dict.fromkeys(emails)),
        "linkedin":  linkedin,
        "firstName": first_name,
        "lastName":  last_name,
        "jobTitle":  job_title,
        "method":    "google_cse",
    }


# ── METHOD 3: DIRECT SCRAPE ───────────────────────────────────────────────────

def scrape_for_emails(domain):
    emails = []
    base = None
    for scheme in ["https://", "http://"]:
        html = fetch(scheme + domain)
        if html:
            base = scheme + domain
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator=" ")
            emails.extend(extract_emails(text, domain))
            # Try contact subpages
            for path in CONTACT_PATHS[:3]:
                contact_html = fetch(base.rstrip("/") + path)
                if contact_html:
                    csoup = BeautifulSoup(contact_html, "html.parser")
                    emails.extend(extract_emails(csoup.get_text(separator=" "), domain))
            break

    return {
        "emails":    list(dict.fromkeys(emails)),
        "linkedin":  "",
        "firstName": "",
        "lastName":  "",
        "jobTitle":  "",
        "method":    "scrape",
    }


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Free Contact Finder")
    print("Methods: DuckDuckGo → Google CSE → Direct Scrape")
    print("=" * 60)

    # Load no-email domains from master
    df = pd.read_csv(MASTER, dtype=str).fillna("")
    no_email = df[df["email"] == ""]
    has_domain = no_email[no_email["domain"] != ""].copy()

    # Resume support
    done = set()
    rows = []
    try:
        df_ex = pd.read_csv(OUTPUT, dtype=str).fillna("")
        if not df_ex.empty and "domain" in df_ex.columns:
            rows = df_ex.to_dict("records")
            done = set(df_ex["domain"].str.lower().tolist())
            print(f"[Resume] {len(done)} already processed\n")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    todo = has_domain[~has_domain["domain"].str.lower().isin(done)]
    print(f"Domains to process: {len(todo)}")
    print(f"Google CSE available: {'Yes' if GOOGLE_KEY else 'No'}\n")

    found_count = 0

    for i, (_, row) in enumerate(todo.iterrows()):
        domain      = row["domain"].strip()
        company     = row["companyName"].strip()
        print(f"[{i+1}/{len(todo)}] {domain} ({company or 'no name'})")

        result = None

        # Method 1: DuckDuckGo
        result = search_ddg_for_domain(domain, company)
        if result["emails"] or result["linkedin"]:
            print(f"  ✓ DDG: emails={result['emails']} linkedin={'yes' if result['linkedin'] else 'no'}")
        else:
            print(f"  · DDG: nothing found")

        # Method 2: Google CSE (if DDG found nothing)
        if not result["emails"] and not result["linkedin"] and GOOGLE_KEY and _google_calls_today < GOOGLE_DAILY_LIMIT:
            g = search_google_for_domain(domain, company)
            if g["emails"] or g["linkedin"]:
                result = g
                print(f"  ✓ Google: emails={result['emails']} linkedin={'yes' if result['linkedin'] else 'no'}")
            else:
                print(f"  · Google: nothing found")

        # Method 3: Direct scrape (if still nothing)
        if not result["emails"]:
            s = scrape_for_emails(domain)
            if s["emails"]:
                # Merge — keep linkedin/name from earlier methods
                result["emails"] = s["emails"]
                result["method"] = result["method"] + "+scrape"
                print(f"  ✓ Scrape: {result['emails']}")
            else:
                print(f"  · Scrape: nothing found")

        # Use existing name from master if we didn't find one
        fn = result.get("firstName") or row.get("firstName", "")
        ln = result.get("lastName")  or row.get("lastName", "")

        email_str = " | ".join(result["emails"]) if result["emails"] else ""
        if email_str:
            found_count += 1

        rows.append({
            "domain":       domain,
            "companyName":  company or row.get("companyName", ""),
            "emails":       email_str,
            "firstName":    fn,
            "lastName":     ln,
            "jobTitle":     result.get("jobTitle") or row.get("jobTitle", ""),
            "linkedin":     result.get("linkedin") or row.get("linkedin", ""),
            "method":       result.get("method", ""),
            "source_brand": row.get("brand", ""),
            "source_orig":  row.get("source", ""),
        })

        pd.DataFrame(rows).to_csv(OUTPUT, index=False)
        time.sleep(1.5)  # be polite to DDG

    df_out = pd.DataFrame(rows)
    with_email = df_out[df_out["emails"] != ""]
    pct = len(with_email) / len(df_out) * 100 if len(df_out) else 0

    print(f"\n{'='*60}")
    print(f"DONE. {len(df_out)} domains | {len(with_email)} with email ({pct:.0f}%)")
    print(f"Output: {OUTPUT}")
    print(f"\nNext: run merge_free_results.py to push into masterlist")
    print("=" * 60)


if __name__ == "__main__":
    main()

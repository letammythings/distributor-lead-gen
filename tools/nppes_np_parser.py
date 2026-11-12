"""
nppes_np_parser.py
------------------
Downloads the free NPPES government NPI registry from CMS.gov,
filters for all Nurse Practitioners (taxonomy codes starting with 363L),
and exports a clean CSV ready for email enrichment via Dropleads/Hunter.

Free — no API key required.
Output: .tmp/nppes_nurse_practitioners.csv

Usage:
    python tools/nppes_np_parser.py

    # If you already downloaded the ZIP manually:
    python tools/nppes_np_parser.py --zip path/to/NPPES_Data_Dissemination_March_2026_V2.zip
"""

import argparse
import csv
import io
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

# ── Config ────────────────────────────────────────────────────────────────────

NPPES_URL = "https://download.cms.gov/nppes/NPPES_Data_Dissemination_March_2026_V2.zip"
RAW_DIR = ".tmp/nppes_raw"
ZIP_PATH = os.path.join(RAW_DIR, "nppes_march_2026.zip")
OUTPUT_FILE = ".tmp/nppes_nurse_practitioners.csv"
CHUNK_SIZE = 50_000  # rows per chunk — keeps memory low on the 20GB+ unzipped file

# All Nurse Practitioner taxonomy codes start with 363L
NP_TAXONOMY_PREFIX = "363L"

# Taxonomy code columns in the NPPES file (there are up to 15 per provider)
TAXONOMY_COLS = [f"Healthcare Provider Taxonomy Code_{i}" for i in range(1, 16)]

# Columns to extract for output
EXTRACT_COLS = [
    "NPI",
    "Entity Type Code",
    "Provider Last Name (Legal Name)",
    "Provider First Name",
    "Provider Middle Name",
    "Provider Credential Text",
    "Provider Organization Name (Legal Business Name)",
    "Provider Business Practice Location Address City Name",
    "Provider Business Practice Location Address State Name",
    "Provider Business Practice Location Address Postal Code",
    "Provider Business Practice Location Address Telephone Number",
]

OUTPUT_COLS = [
    "npi",
    "entity_type",
    "last_name",
    "first_name",
    "middle_name",
    "credential",
    "practice_name",
    "city",
    "state",
    "zip",
    "phone",
    "taxonomy_code",
    "taxonomy_description",
]

# NP taxonomy code → plain English description
NP_TAXONOMY_DESCRIPTIONS = {
    "363L00000X": "Nurse Practitioner",
    "363LA2100X": "NP - Acute Care",
    "363LA2200X": "NP - Adult Health",
    "363LC0200X": "NP - Community Health",
    "363LC1500X": "NP - Critical Care Medicine",
    "363LF0000X": "NP - Family",
    "363LG0600X": "NP - Gerontology",
    "363LN0000X": "NP - Neonatal",
    "363LN0005X": "NP - Neonatal Critical Care",
    "363LP0200X": "NP - Pediatrics",
    "363LP1700X": "NP - Perinatal",
    "363LP2300X": "NP - Primary Care",
    "363LS0200X": "NP - School",
    "363LW0102X": "NP - Women's Health",
    "363LX0001X": "NP - Obstetrics & Gynecology",
    "363LX0106X": "NP - Occupational Health",
}


# ── Download ──────────────────────────────────────────────────────────────────

def download_zip():
    """Download the NPPES ZIP with a simple progress indicator."""
    os.makedirs(RAW_DIR, exist_ok=True)

    if os.path.exists(ZIP_PATH):
        size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
        print(f"[Download] ZIP already exists ({size_mb:.0f} MB) — skipping download.")
        return

    print(f"[Download] Downloading NPPES data from CMS.gov (~1 GB)...")
    print(f"[Download] URL: {NPPES_URL}")

    response = requests.get(NPPES_URL, stream=True, timeout=300)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    last_pct = -1

    with open(ZIP_PATH, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = int(downloaded / total * 100)
                if pct != last_pct and pct % 5 == 0:
                    mb = downloaded / (1024 * 1024)
                    print(f"[Download] {pct}% ({mb:.0f} MB)")
                    last_pct = pct

    print(f"[Download] Complete — saved to {ZIP_PATH}")


# ── Parse ─────────────────────────────────────────────────────────────────────

def find_npi_csv_in_zip(zf: zipfile.ZipFile) -> str:
    """Find the main npidata CSV inside the ZIP (name changes each month)."""
    for name in zf.namelist():
        if name.startswith("npidata_pfile") and name.endswith(".csv"):
            return name
    raise FileNotFoundError(
        f"Could not find npidata_pfile*.csv in ZIP. Files found: {zf.namelist()}"
    )


def get_np_taxonomy(row):
    """Return the first NP taxonomy code found in the row's 15 taxonomy columns."""
    for col in TAXONOMY_COLS:
        val = row.get(col, "")
        if isinstance(val, str) and val.startswith(NP_TAXONOMY_PREFIX):
            return val
    return None


def parse_and_filter(zip_path: str) -> int:
    """Stream-read the NPPES CSV, filter NPs, write output. Returns row count."""

    # Columns we actually need to read (extract + taxonomy)
    cols_to_read = EXTRACT_COLS + TAXONOMY_COLS

    print(f"[Parse] Opening ZIP: {zip_path}")
    total_written = 0
    header_written = False

    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_name = find_npi_csv_in_zip(zf)
        print(f"[Parse] Found NPI file: {csv_name}")

        with zf.open(csv_name) as raw_file:
            # Wrap in TextIOWrapper so pandas can read it as text
            text_file = io.TextIOWrapper(raw_file, encoding="utf-8", errors="replace")

            reader = pd.read_csv(
                text_file,
                dtype=str,
                usecols=lambda c: c in cols_to_read,
                chunksize=CHUNK_SIZE,
                low_memory=False,
            )

            chunk_num = 0
            for chunk in reader:
                chunk_num += 1

                # Keep only individual providers (Entity Type Code == "1")
                chunk = chunk[chunk.get("Entity Type Code", pd.Series()) == "1"]

                # Find NP taxonomy code for each row
                chunk["_np_code"] = chunk.apply(get_np_taxonomy, axis=1)
                chunk = chunk[chunk["_np_code"].notna()]

                if chunk.empty:
                    continue

                # Build output rows
                rows = []
                for _, row in chunk.iterrows():
                    code = row["_np_code"]
                    rows.append({
                        "npi": row.get("NPI", ""),
                        "entity_type": row.get("Entity Type Code", ""),
                        "last_name": row.get("Provider Last Name (Legal Name)", ""),
                        "first_name": row.get("Provider First Name", ""),
                        "middle_name": row.get("Provider Middle Name", ""),
                        "credential": row.get("Provider Credential Text", ""),
                        "practice_name": row.get("Provider Organization Name (Legal Business Name)", ""),
                        "city": row.get("Provider Business Practice Location Address City Name", ""),
                        "state": row.get("Provider Business Practice Location Address State Name", ""),
                        "zip": row.get("Provider Business Practice Location Address Postal Code", ""),
                        "phone": row.get("Provider Business Practice Location Address Telephone Number", ""),
                        "taxonomy_code": code,
                        "taxonomy_description": NP_TAXONOMY_DESCRIPTIONS.get(code, f"NP - {code}"),
                    })

                # Append to output CSV
                mode = "w" if not header_written else "a"
                with open(OUTPUT_FILE, mode, newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
                    if not header_written:
                        writer.writeheader()
                        header_written = True
                    writer.writerows(rows)

                total_written += len(rows)

                if chunk_num % 10 == 0:
                    print(f"[Parse] Processed {chunk_num * CHUNK_SIZE:,} records... {total_written:,} NPs found so far")

    return total_written


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Parse NPPES data for Nurse Practitioners")
    parser.add_argument(
        "--zip",
        default=None,
        help="Path to a pre-downloaded NPPES ZIP file (skips download)"
    )
    args = parser.parse_args()

    zip_path = args.zip if args.zip else ZIP_PATH

    # Step 1: Download if needed
    if not args.zip:
        download_zip()
    else:
        if not os.path.exists(zip_path):
            print(f"[Error] ZIP not found at: {zip_path}")
            sys.exit(1)
        print(f"[Parse] Using provided ZIP: {zip_path}")

    # Step 2: Parse and filter
    print(f"\n[Parse] Filtering for Nurse Practitioners (taxonomy codes starting with 363L)...")
    print(f"[Parse] Output: {OUTPUT_FILE}\n")

    count = parse_and_filter(zip_path)

    print(f"\n[Done] Wrote {count:,} Nurse Practitioners to {OUTPUT_FILE}")
    print(f"[Done] Next step: run Dropleads/Hunter enrichment to find emails for each NP")


if __name__ == "__main__":
    main()

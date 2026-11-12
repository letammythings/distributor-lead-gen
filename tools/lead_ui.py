#!/usr/bin/env python3
"""
Local Flask UI: browse merged *_contacts.csv leads from .tmp/ and draft outreach via Claude.

Run from repo root (use project venv — see README):
  .venv/bin/python tools/lead_ui.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import fnmatch
from pathlib import Path

try:
    import anthropic
    from flask import Flask, jsonify, render_template_string, request, url_for
except ImportError as e:
    raise SystemExit(
        "Missing dependency: "
        + str(e).splitlines()[0]
        + "\n\nFrom the repo root, run:\n"
        "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
        "  .venv/bin/python tools/lead_ui.py\n"
    ) from e

BASE_DIR = Path(__file__).resolve().parent.parent
TMP_DIR = BASE_DIR / ".tmp"
ENV_PATH = BASE_DIR / ".env"
NOTES_PATH = TMP_DIR / "lead_notes.json"

DISPLAY_COLS = [
    "company_name",
    "business_type",
    "brand",
    "tier",
    "emails",
    "phones",
    "decision_maker_name",
    "decision_maker_title",
    "domain",
]

# Workflow schema — 18 core contact columns (shown as "What we know" cards)
SCHEMA_18_FIELDS = [
    "domain",
    "brand",
    "tier",
    "why_relevant",
    "company_name",
    "company_name_source",
    "business_type",
    "decision_maker_name",
    "decision_maker_title",
    "emails",
    "phones",
    "address",
    "linkedin_company",
    "facebook",
    "instagram",
    "twitter",
    "contact_url",
    "source_urls",
]


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val


def safe_tmp_csv_name(name: str) -> bool:
    if not name or "/" in name or "\\" in name or ".." in name:
        return False
    return fnmatch.fnmatch(name, "*_contacts.csv")


def iter_contact_files() -> list[Path]:
    if not TMP_DIR.is_dir():
        return []
    return sorted(TMP_DIR.glob("*_contacts.csv"))


def load_merged_leads() -> list[dict]:
    rows: list[dict] = []
    for path in iter_contact_files():
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                    r["_source_file"] = path.name
                    rows.append(r)
        except OSError:
            continue
    return rows


def notes_key(domain: str) -> str:
    return (domain or "").lower().strip()


def load_notes() -> dict[str, str]:
    if not NOTES_PATH.is_file():
        return {}
    try:
        with open(NOTES_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        return {notes_key(str(k)): str(v) for k, v in raw.items()}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_notes_store(store: dict[str, str]) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def get_note_for_domain(domain: str) -> str:
    return load_notes().get(notes_key(domain), "")


def row_has_email(row: dict) -> bool:
    e = (row.get("emails") or "").strip()
    return bool(e and "@" in e)


def row_has_phone(row: dict) -> bool:
    p = (row.get("phones") or "").strip()
    return bool(re.search(r"\d", p))


def row_done(row: dict) -> bool:
    v = str(row.get("done", "")).strip().lower()
    return v in ("true", "1", "yes", "y")


def tier_bucket(tier_val) -> int | None:
    try:
        return int(float(str(tier_val).strip()))
    except (TypeError, ValueError):
        return None


def compute_dataset_stats(leads: list[dict]) -> dict:
    tier_counts = {1: 0, 2: 0, 3: 0}
    for row in leads:
        tb = tier_bucket(row.get("tier"))
        if tb in tier_counts:
            tier_counts[tb] += 1
    return {
        "total": len(leads),
        "with_email": sum(1 for r in leads if row_has_email(r)),
        "with_phone": sum(1 for r in leads if row_has_phone(r)),
        "tier_1": tier_counts[1],
        "tier_2": tier_counts[2],
        "tier_3": tier_counts[3],
        "actioned": sum(1 for r in leads if row_done(r)),
    }


def find_lead(domain: str, src: str | None, company_name: str | None) -> dict | None:
    if not domain:
        return None
    d = domain.lower().strip()
    for row in load_merged_leads():
        if (row.get("domain") or "").lower().strip() != d:
            continue
        if src and row.get("_source_file") != src:
            continue
        if company_name and (row.get("company_name") or "").strip() != company_name.strip():
            continue
        return row
    return None


def tier_class(tier_val) -> str:
    try:
        t = int(float(str(tier_val).strip()))
    except (TypeError, ValueError):
        return "tier-unknown"
    if t == 1:
        return "tier-1"
    if t == 2:
        return "tier-2"
    return "tier-3"


def mark_lead_done(source_file: str, domain: str, company_name: str | None) -> tuple[bool, str]:
    if not safe_tmp_csv_name(source_file):
        return False, "invalid source file"
    path = TMP_DIR / source_file
    if not path.is_file():
        return False, "file not found"
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        data = list(reader)
    if "done" not in fieldnames:
        fieldnames.append("done")
    domain_l = domain.lower().strip()
    hit = False
    for row in data:
        if (row.get("domain") or "").lower().strip() != domain_l:
            continue
        if company_name and (row.get("company_name") or "").strip() != company_name.strip():
            continue
        row["done"] = "true"
        hit = True
        break
    if not hit:
        return False, "lead not found in CSV"
    for row in data:
        row.setdefault("done", row.get("done", ""))
    for fn in fieldnames:
        for row in data:
            row.setdefault(fn, "")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
    return True, ""


def claude_generate_outreach(lead: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (check .env at repo root)")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    notes_text = get_note_for_domain(lead.get("domain") or "")
    payload = {k: lead.get(k, "") for k in SCHEMA_18_FIELDS}
    payload["done"] = lead.get("done", "")
    payload["your_notes_on_this_lead"] = notes_text

    social_block = {
        "linkedin_company": lead.get("linkedin_company") or "",
        "facebook": lead.get("facebook") or "",
        "instagram": lead.get("instagram") or "",
        "twitter": lead.get("twitter") or "",
    }

    client = anthropic.Anthropic(api_key=api_key)
    system = (
        "You write concise B2B wholesale outreach for YourBrand (describe your product line here). "
        "Output MUST be a single JSON object only, no markdown fences, with keys: "
        "cold_email, linkedin_dm, follow_up_email, personalization_score (integer 0-100). "
        "Tone: professional RevOps — direct, respectful, specific.\n\n"
        "Personalisation priorities:\n"
        "- Lead with the competitor/medical brand they already stock (`brand`) — this is the strongest hook.\n"
        "- Reflect their `business_type` (e.g. DME supplier vs pharmacy vs independent retailer) in word choice.\n"
        "- Use `tier` as signal for priority/fit when relevant.\n"
        "- Use any non-empty social URLs in social_links naturally if helpful (don't invent profiles).\n\n"
        "Positioning rule (critical): Frame YourBrand as a COMPLEMENTARY product line that sits alongside "
        "what they already carry — expanding their assortment — NOT as a replacement or direct competitor "
        "to brands they stock.\n\n"
        "If `your_notes_on_this_lead` is non-empty, incorporate those angles faithfully."
    )
    user = (
        "Lead fields (JSON):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nSocial links (subset):\n"
        + json.dumps(social_block, ensure_ascii=False, indent=2)
        + "\n\nReturn JSON only with cold_email, linkedin_dm, follow_up_email, personalization_score."
    )
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = ""
    for block in msg.content:
        if block.type == "text":
            text += block.text
    text = text.strip()
    # Strip optional ```json fences
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)
    parsed = json.loads(text)
    return {
        "cold_email": str(parsed.get("cold_email", "")).strip(),
        "linkedin_dm": str(parsed.get("linkedin_dm", "")).strip(),
        "follow_up_email": str(parsed.get("follow_up_email", "")).strip(),
        "personalization_score": int(parsed.get("personalization_score", 0)),
    }


load_env_file(ENV_PATH)
app = Flask(__name__)


BASE_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Syne:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0a0f;
  --bg-elevated: #12121a;
  --border: #1f1f2e;
  --text: #e8e8ef;
  --muted: #8b8b9e;
  --accent: #7c6dfa;
  --accent-dim: rgba(124, 109, 250, 0.15);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3, .font-heading {
  font-family: Syne, system-ui, sans-serif;
  font-weight: 600;
  letter-spacing: -0.02em;
}
a { color: var(--accent); text-decoration: none; transition: opacity 0.15s; }
a:hover { opacity: 0.85; }
.shell {
  max-width: 1400px;
  margin: 0 auto;
  padding: 28px 24px 48px;
  animation: fadeIn 0.35s ease-out;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}
.btn {
  font-family: Syne, sans-serif;
  font-size: 13px;
  font-weight: 600;
  padding: 10px 18px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.1s;
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--accent-dim);
}
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-primary {
  background: linear-gradient(135deg, #6d5aed 0%, var(--accent) 100%);
  border-color: transparent;
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  filter: brightness(1.06);
}
.btn-ghost { background: transparent; }
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  margin-bottom: 16px;
}
.filters label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
}
select, input[type="search"] {
  font-family: 'DM Mono', monospace;
  font-size: 13px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text);
  min-width: 160px;
}
input[type="search"] { min-width: 220px; }
.table-wrap {
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
}
table {
  width: 100%;
  border-collapse: collapse;
}
th {
  font-family: Syne, sans-serif;
  text-align: left;
  padding: 12px 14px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--bg-elevated);
  z-index: 1;
}
td {
  padding: 11px 14px;
  border-bottom: 1px solid rgba(31, 31, 46, 0.6);
  vertical-align: middle;
}
tbody tr {
  transition: background 0.12s ease;
}
tbody tr:hover { background: rgba(124, 109, 250, 0.06); }
tr.tier-1 td:first-child { box-shadow: inset 3px 0 0 #3dcf8e; }
tr.tier-2 td:first-child { box-shadow: inset 3px 0 0 #e8c547; }
tr.tier-3 td:first-child,
tr.tier-unknown td:first-child { box-shadow: inset 3px 0 0 #6b6b78; }
.badge-count {
  font-family: Syne, sans-serif;
  font-size: 14px;
  color: var(--muted);
}
.badge-count strong { color: var(--accent); font-weight: 700; }
.row-actions .btn { padding: 8px 14px; font-size: 12px; }
.research-layout {
  display: grid;
  grid-template-columns: minmax(300px, 440px) 1fr;
  gap: 24px;
  align-items: start;
}
@media (max-width: 960px) {
  .research-layout { grid-template-columns: 1fr; }
}
.panel {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  padding: 20px;
}
.panel h2 {
  margin: 0 0 14px;
  font-size: 18px;
}
.kv {
  display: grid;
  gap: 10px;
}
.kv div {
  border-bottom: 1px solid rgba(31, 31, 46, 0.7);
  padding-bottom: 10px;
}
.kv dt {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
  margin-bottom: 4px;
}
.kv dd { margin: 0; word-break: break-word; white-space: pre-wrap; }
.target-line {
  font-size: 14px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--accent-dim);
  border: 1px solid rgba(124, 109, 250, 0.35);
}
.tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.tab {
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-family: Syne, sans-serif;
  font-size: 12px;
  font-weight: 600;
  transition: all 0.15s;
}
.tab.active {
  border-color: var(--accent);
  color: var(--text);
  background: var(--accent-dim);
}
.tab-panel {
  display: none;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  background: var(--bg);
  min-height: 180px;
}
.tab-panel.active { display: block; }
.copy-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 10px;
}
.outreach-body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
}
.score-pill {
  display: inline-block;
  margin-top: 12px;
  padding: 6px 12px;
  border-radius: 999px;
  background: var(--accent-dim);
  border: 1px solid rgba(124, 109, 250, 0.4);
  font-family: Syne, sans-serif;
  font-weight: 600;
}
.err {
  color: #ff8b8b;
  margin-top: 10px;
}
.actions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}
.empty-state {
  padding: 48px 24px;
  text-align: center;
  color: var(--muted);
  border: 1px dashed var(--border);
  border-radius: 12px;
}
.stats-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
}
.stat-chip {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--bg-elevated);
  flex: 1 1 100px;
  min-width: 108px;
}
.stat-chip .stat-value {
  font-family: Syne, sans-serif;
  font-size: 21px;
  font-weight: 700;
  color: var(--accent);
  line-height: 1.2;
}
.stat-chip .stat-label {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
  margin-top: 6px;
}
.top-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.know-sidebar h2 { margin-top: 0; }
.know-meta {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.know-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: 10px;
}
.know-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 11px;
  background: var(--bg);
  min-height: 72px;
  display: flex;
  flex-direction: column;
}
.know-card .kf-label {
  font-size: 8px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 6px;
  font-family: Syne, sans-serif;
}
.know-card .kf-val {
  font-size: 12px;
  word-break: break-word;
  white-space: pre-wrap;
  flex: 1;
  line-height: 1.35;
}
.extra-fields {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed var(--border);
}
.extra-fields summary {
  font-family: Syne, sans-serif;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  cursor: pointer;
}
.extra-kv {
  margin-top: 12px;
  display: grid;
  gap: 8px;
  font-size: 11px;
}
.extra-kv div { word-break: break-word; }
.notes-panel {
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}
.notes-panel h3 {
  font-family: Syne, sans-serif;
  font-size: 14px;
  margin: 0 0 10px;
}
.notes-panel textarea {
  width: 100%;
  min-height: 110px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  resize: vertical;
}
.notes-panel .note-hint {
  font-size: 10px;
  color: var(--muted);
  margin-top: 8px;
}
</style>
"""


INDEX_HTML = (
    BASE_STYLE
    + """
<div class="shell">
  <div class="topbar">
    <div>
      <h1 style="margin:0 0 6px;font-size:26px;">Lead browser</h1>
      <p class="badge-count"><strong id="visible-count">{{ total }}</strong> leads visible · <strong>{{ total }}</strong> loaded</p>
    </div>
    <div class="top-actions">
      {% if rows %}
      <button type="button" class="btn" id="btn-export-csv">Export filtered CSV</button>
      {% endif %}
      <a class="btn btn-ghost" href="{{ url_for('index') }}">Refresh</a>
    </div>
  </div>
  {% if not rows %}
  <div class="empty-state">
    No <code>*_contacts.csv</code> files found under <code>.tmp/</code>. Run your scrapers/enrichers first.
  </div>
  {% else %}
  <div class="stats-bar">
    <div class="stat-chip"><div class="stat-value">{{ stats.total }}</div><div class="stat-label">Total leads</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.with_email }}</div><div class="stat-label">With email</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.with_phone }}</div><div class="stat-label">With phone</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.tier_1 }}</div><div class="stat-label">Tier 1</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.tier_2 }}</div><div class="stat-label">Tier 2</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.tier_3 }}</div><div class="stat-label">Tier 3</div></div>
    <div class="stat-chip"><div class="stat-value">{{ stats.actioned }}</div><div class="stat-label">Done (actioned)</div></div>
  </div>
  <script type="application/json" id="csv-cols">{{ DISPLAY_COLS | tojson }}</script>
  <div class="filters">
    <label>Brand
      <select id="f-brand"><option value="">All brands</option>{% for b in brands %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select>
    </label>
    <label>Business type
      <select id="f-type"><option value="">All types</option>{% for t in biz_types %}<option value="{{ t }}">{{ t }}</option>{% endfor %}</select>
    </label>
    <label>Tier
      <select id="f-tier"><option value="">All tiers</option>{% for t in tiers %}<option value="{{ t }}">{{ t }}</option>{% endfor %}</select>
    </label>
    <label>Search
      <input type="search" id="f-q" placeholder="company or domain…" autocomplete="off">
    </label>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        {% for c in DISPLAY_COLS %}<th>{{ c }}</th>{% endfor %}
        <th></th>
      </tr></thead>
      <tbody id="lead-body">
        {% for row in rows %}
        <tr class="{{ row._tier_class }}"
            data-brand="{{ row.brand | e }}"
            data-type="{{ row.business_type | e }}"
            data-tier="{{ row.tier | e }}"
            data-company="{{ row.company_name | e }}"
            data-domain="{{ row.domain | e }}">
          {% for c in DISPLAY_COLS %}<td>{{ row[c] }}</td>{% endfor %}
          <td class="row-actions">
            <a class="btn btn-primary"
               href="{{ url_for('research', domain=row.domain, src=row._source_file, company=row.company_name) }}">Research & Write Outreach</a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
<script>
(function(){
  const body = document.getElementById('lead-body');
  const cnt = document.getElementById('visible-count');
  if (!body) return;
  const b = document.getElementById('f-brand');
  const t = document.getElementById('f-type');
  const tier = document.getElementById('f-tier');
  const q = document.getElementById('f-q');
  function norm(s){ return (s||'').toLowerCase(); }
  function apply(){
    const fb = norm(b.value);
    const ft = norm(t.value);
    const fti = norm(tier.value);
    const fq = norm(q.value.trim());
    let n = 0;
    for (const tr of body.querySelectorAll('tr')){
      let ok = true;
      if (fb && norm(tr.dataset.brand) !== fb) ok = false;
      if (ft && norm(tr.dataset.type) !== ft) ok = false;
      if (fti && norm(tr.dataset.tier) !== fti) ok = false;
      if (fq){
        const hay = norm(tr.dataset.company + ' ' + tr.dataset.domain);
        if (!hay.includes(fq)) ok = false;
      }
      tr.style.display = ok ? '' : 'none';
      if (ok) n++;
    }
    if (cnt) cnt.textContent = n;
  }
  [b,t,tier,q].forEach(el => el && el.addEventListener('input', apply));
  [b,t,tier,q].forEach(el => el && el.addEventListener('change', apply));
})();

(function(){
  const btn = document.getElementById('btn-export-csv');
  const body = document.getElementById('lead-body');
  if (!btn || !body) return;
  const colsEl = document.getElementById('csv-cols');
  const cols = colsEl ? JSON.parse(colsEl.textContent) : [];
  function escCell(v){
    const s = String(v ?? '');
    if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g,'""') + '"';
    return s;
  }
  btn.addEventListener('click', () => {
    const lines = [cols.map(escCell).join(',')];
    for (const tr of body.querySelectorAll('tr')){
      if (tr.style.display === 'none') continue;
      const cells = tr.querySelectorAll('td');
      const vals = [];
      for (let i = 0; i < cols.length; i++) vals.push((cells[i] && cells[i].innerText) || '');
      lines.push(vals.map(escCell).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'filtered_leads_' + new Date().toISOString().slice(0,19).replace(/[:-]/g,'') + '.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
</script>
"""
)


RESEARCH_HTML = (
    BASE_STYLE
    + """
<div class="shell">
  <div class="topbar">
    <div>
      <h1 style="margin:0;font-size:24px;">Research & outreach</h1>
      <p style="margin:8px 0 0;color:var(--muted);">{{ target }}</p>
    </div>
    <a class="btn btn-ghost" href="{{ url_for('index') }}">← Back to leads</a>
  </div>
  {% if not lead %}
  <div class="empty-state">Lead not found. Go back and pick a row again.</div>
  {% else %}
  <div class="research-layout">
    <div class="panel know-sidebar">
      <h2>What we know</h2>
      <div class="know-meta">
        <div><strong>Source CSV:</strong> {{ source_file }}</div>
        {% if done_flag %}<div style="margin-top:8px;color:#3dcf8e;font-family:Syne,sans-serif;font-weight:600;font-size:12px;">Marked done</div>{% endif %}
      </div>
      <div class="know-grid">
        {% for c in know_cards %}
        <div class="know-card">
          <div class="kf-label">{{ c.label }}</div>
          <div class="kf-val">{{ c.value }}</div>
        </div>
        {% endfor %}
      </div>
      {% if extra_pairs %}
      <details class="extra-fields">
        <summary>Other CSV columns ({{ extra_pairs|length }})</summary>
        <div class="extra-kv">
          {% for label, val in extra_pairs %}
          <div><strong>{{ label }}:</strong> {{ val }}</div>
          {% endfor %}
        </div>
      </details>
      {% endif %}
      <div class="notes-panel">
        <h3>Notes</h3>
        <textarea id="lead-notes" name="notes" placeholder="Angles, talk tracks, objections…">{{ note_text }}</textarea>
        <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;">
          <button type="button" class="btn btn-primary" id="btn-save-notes">Save notes</button>
        </div>
        <p class="note-hint">Stored in <code>.tmp/lead_notes.json</code>, keyed by domain.</p>
        <p id="note-feedback" style="display:none;margin-top:10px;font-size:12px;color:#3dcf8e;"></p>
      </div>
    </div>
    <div class="panel">
      <div class="target-line"><strong>Target:</strong> {{ target }}</div>
      <div class="actions-row">
        <button type="button" class="btn btn-primary" id="btn-gen">Generate Outreach</button>
        <button type="button" class="btn" disabled title="Coming soon">Push to Smartlead</button>
        <button type="button" class="btn" id="btn-done">Mark as Done</button>
      </div>
      <p class="err" id="gen-err" style="display:none;"></p>
      <div id="out-area" style="display:none;margin-top:18px;">
        <span class="score-pill" id="score-pill"></span>
        <div class="tabs" style="margin-top:16px;">
          <button type="button" class="tab active" data-tab="cold">Cold email</button>
          <button type="button" class="tab" data-tab="li">LinkedIn DM</button>
          <button type="button" class="tab" data-tab="fu">Follow-up</button>
        </div>
        <div class="tab-panel active" id="panel-cold">
          <div class="copy-row"><button type="button" class="btn btn-ghost" data-copy="cold">Copy</button></div>
          <div class="outreach-body" id="txt-cold"></div>
        </div>
        <div class="tab-panel" id="panel-li">
          <div class="copy-row"><button type="button" class="btn btn-ghost" data-copy="li">Copy</button></div>
          <div class="outreach-body" id="txt-li"></div>
        </div>
        <div class="tab-panel" id="panel-fu">
          <div class="copy-row"><button type="button" class="btn btn-ghost" data-copy="fu">Copy</button></div>
          <div class="outreach-body" id="txt-fu"></div>
        </div>
      </div>
    </div>
  </div>
  <script type="application/json" id="lead-payload">{{ lead | tojson }}</script>
  <script>
  const payload = JSON.parse(document.getElementById('lead-payload').textContent);
  const meta = {
    domain: payload.domain || '',
    src: payload._source_file || '',
    company_name: payload.company_name || ''
  };
  let texts = { cold: '', li: '', fu: '' };

  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
    });
  });

  async function copyWhich(which){
    const map = { cold: texts.cold, li: texts.li, fu: texts.fu };
    try {
      await navigator.clipboard.writeText(map[which] || '');
    } catch(e) {}
  }
  document.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', () => copyWhich(btn.getAttribute('data-copy')));
  });

  document.getElementById('btn-gen').addEventListener('click', async () => {
    const errEl = document.getElementById('gen-err');
    errEl.style.display = 'none';
    try {
      const r = await fetch('{{ url_for("api_generate") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(meta)
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Request failed');
      texts.cold = data.cold_email || '';
      texts.li = data.linkedin_dm || '';
      texts.fu = data.follow_up_email || '';
      document.getElementById('txt-cold').textContent = texts.cold;
      document.getElementById('txt-li').textContent = texts.li;
      document.getElementById('txt-fu').textContent = texts.fu;
      document.getElementById('score-pill').textContent =
        'Personalisation score: ' + (data.personalization_score ?? '—');
      document.getElementById('out-area').style.display = 'block';
    } catch(e) {
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
    }
  });

  document.getElementById('btn-done').addEventListener('click', async () => {
    const errEl = document.getElementById('gen-err');
    errEl.style.display = 'none';
    try {
      const r = await fetch('{{ url_for("api_mark_done") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(meta)
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || 'Could not mark done');
      errEl.textContent = 'Marked done in CSV.';
      errEl.style.color = '#3dcf8e';
      errEl.style.display = 'block';
    } catch(e) {
      errEl.style.color = '';
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
    }
  });

  document.getElementById('btn-save-notes').addEventListener('click', async () => {
    const fb = document.getElementById('note-feedback');
    const txt = document.getElementById('lead-notes');
    fb.style.display = 'none';
    fb.style.color = '#3dcf8e';
    try {
      const r = await fetch('{{ url_for("api_save_notes") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ domain: meta.domain, note: txt.value })
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || 'Save failed');
      fb.textContent = 'Notes saved.';
      fb.style.color = '#3dcf8e';
      fb.style.display = 'block';
    } catch(e) {
      fb.style.color = '#ff8b8b';
      fb.textContent = e.message || String(e);
      fb.style.display = 'block';
    }
  });
  </script>
  {% endif %}
</div>
"""
)


@app.route("/")
def index():
    rows_raw = load_merged_leads()
    brands = sorted({(r.get("brand") or "").strip() for r in rows_raw if (r.get("brand") or "").strip()})
    biz_types = sorted(
        {(r.get("business_type") or "").strip() for r in rows_raw if (r.get("business_type") or "").strip()}
    )
    tiers = sorted({str((r.get("tier") or "").strip()) for r in rows_raw if (r.get("tier") or "").strip()})

    rows = []
    for r in rows_raw:
        display = {}
        for c in DISPLAY_COLS:
            display[c] = r.get(c, "") or ""
        display["_source_file"] = r.get("_source_file", "")
        display["_tier_class"] = tier_class(r.get("tier"))
        rows.append(display)

    return render_template_string(
        INDEX_HTML,
        rows=rows,
        total=len(rows),
        stats=compute_dataset_stats(rows_raw),
        brands=brands,
        biz_types=biz_types,
        tiers=tiers,
        DISPLAY_COLS=DISPLAY_COLS,
    )


@app.route("/research")
def research():
    domain = request.args.get("domain") or ""
    src = request.args.get("src") or None
    company = request.args.get("company") or None
    lead = find_lead(domain, src, company)

    target = ""
    know_cards: list[dict] = []
    extra_pairs: list[tuple[str, str]] = []
    note_text = ""
    source_file = "—"
    done_flag = False
    if lead:
        dm = lead.get("decision_maker_name") or ""
        title = lead.get("decision_maker_title") or ""
        co = lead.get("company_name") or ""
        dom = lead.get("domain") or ""
        name_title = ", ".join([p for p in [dm, title] if p])
        if name_title and co:
            target = f"{name_title} @ {co}"
            if dom:
                target += f" ({dom})"
        elif co:
            target = co + (f" ({dom})" if dom else "")
        elif dom:
            target = dom
        else:
            target = ""

        source_file = lead.get("_source_file") or "—"
        done_flag = row_done(lead)
        know_cards = [
            {"label": field.replace("_", " "), "value": lead.get(field) or "—"} for field in SCHEMA_18_FIELDS
        ]
        schema_keys = set(SCHEMA_18_FIELDS)
        for key in sorted(lead.keys()):
            if key.startswith("_") or key in schema_keys or key == "done":
                continue
            extra_pairs.append((key.replace("_", " "), lead.get(key, "") or "—"))

        note_text = get_note_for_domain(lead.get("domain") or "")

    return render_template_string(
        RESEARCH_HTML,
        lead=lead,
        target=target,
        know_cards=know_cards,
        extra_pairs=extra_pairs,
        note_text=note_text,
        source_file=source_file,
        done_flag=done_flag,
    )


@app.route("/api/generate-outreach", methods=["POST"])
def api_generate():
    data = request.get_json(force=True, silent=True) or {}
    domain = data.get("domain") or ""
    src = data.get("src") or None
    company_name = data.get("company_name") or None
    lead = find_lead(domain, src, company_name)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    try:
        out = claude_generate_outreach(lead)
        return jsonify(out)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Claude returned invalid JSON: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/mark-done", methods=["POST"])
def api_mark_done():
    data = request.get_json(force=True, silent=True) or {}
    domain = data.get("domain") or ""
    src = data.get("src") or ""
    company_name = data.get("company_name") or None
    if not domain or not src:
        return jsonify({"ok": False, "error": "domain and src required"}), 400
    ok, msg = mark_lead_done(src, domain, company_name)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True})


@app.route("/api/lead-notes", methods=["POST"])
def api_save_notes():
    data = request.get_json(force=True, silent=True) or {}
    domain = data.get("domain") or ""
    note = data.get("note")
    if note is None:
        note = ""
    if not domain.strip():
        return jsonify({"ok": False, "error": "domain required"}), 400
    store = load_notes()
    key = notes_key(domain)
    if not str(note).strip():
        store.pop(key, None)
    else:
        store[key] = str(note)
    try:
        save_notes_store(store)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


def main():
    # Bind on localhost only — local RevOps UI.
    app.run(host="127.0.0.1", port=5002, debug=False)


if __name__ == "__main__":
    main()

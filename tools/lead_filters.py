import re

# Example market: waterproof cast covers sold through medical retail.
# Change CATEGORY_TERMS and COMPETITOR_BRANDS for your own product.

# Competitor brands a good lead already stocks (lowercase).
COMPETITOR_BRANDS = ("competitor-a", "competitor-b")

COMMERCIAL_TERMS = {
    "add to cart", "buy now", "in stock", "shop", "store", "product", "products",
    "retailer", "retail", "dealer", "distributor", "stockist", "wholesale",
    "supplier", "supplies", "for sale", "buy online", "checkout", "shop now",
}

MEDICAL_TERMS = {
    "medical", "medical supply", "medical supplies", "pharmacy", "pharma", "rx",
    "dme", "durable medical equipment", "home health", "home medical",
    "orthopedic", "orthopaedic", "rehab", "rehabilitation", "surgical",
    "wound care", "clinic", "prosthetic", "orthotics",
}

CATEGORY_TERMS = {
    "cast cover", "cast protector", "waterproof cast", "waterproof cast cover",
    "waterproof cast protector", "shower cast cover", "bandage protector",
    "waterproof bandage", "wound protector", "post surgery waterproof cover",
    "post-op", "post operative", "dressing protector", "splint cover",
    "limb protector", "cast bag", "cast sleeve",
}

BUYER_TYPE_TERMS = {
    "pharmacy", "medical supply", "medical supplies", "dme", "durable medical equipment",
    "home health", "home medical", "orthopedic supply", "orthopaedic supply",
    "rehab supply", "surgical supply", "distributor", "dealer", "stockist",
    "wholesale", "retailer",
}

HARD_REJECT_PATTERNS = {
    "amazon", "ebay", "walmart", "target", "alibaba", "aliexpress", "desertcart",
    "ubuy", "mercari", "etsy", "reddit", "youtube", "facebook", "instagram",
    "linkedin", "pinterest", "tiktok", "wikipedia", "webmd", "healthline",
    "forbes", "bloomberg", "reuters", "npr", "cbc", "wsj", "nytimes", "guardian",
    "pharmacytimes", "medpagetoday", "woundsinternational", "woundcare-today",
    "journal", "news", "blog", "wordpress", "blogspot", "medium", "forum",
    "directory", "yellowpages", "yelp", "manta", "zoominfo", "trustpilot",
    "sitejabber", "tripadvisor", "indeed", "glassdoor", "ziprecruiter",
    "pubmed", "sciencedirect", "researchgate", "academia", "scholar",
    ".gov", ".edu", ".mil", "nhs.uk", "formulary", "hospital", "charity",
    "ngo", "foundation", "reliefweb", "doctorswithoutborders", "projecthope",
    "actionnetwork", "cdn", "cloudinary", "cdn-website", "square.site",
    "dev.", "staging.", "beta", "blob.core",
}


def _normalize(*parts):
    text = " ".join((p or "") for p in parts).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _has_any(text, terms):
    return any(term in text for term in terms)


def classify_search_result(domain, query="", title="", snippet=""):
    text = _normalize(domain, query, title, snippet)
    domain_only = (domain or "").lower().strip()

    if not domain_only or "." not in domain_only:
        return "REJECT", ["invalid domain"]

    reject_reasons = [term for term in HARD_REJECT_PATTERNS if term in text or term in domain_only]
    if reject_reasons:
        return "REJECT", [f"hard reject: {reject_reasons[0]}"]

    has_commercial = _has_any(text, COMMERCIAL_TERMS)
    has_medical = _has_any(text, MEDICAL_TERMS)
    has_category = _has_any(text, CATEGORY_TERMS)
    has_buyer_type = _has_any(text, BUYER_TYPE_TERMS)

    reasons = []
    if has_commercial:
        reasons.append("commercial")
    if has_medical:
        reasons.append("medical")
    if has_category:
        reasons.append("category")
    if has_buyer_type:
        reasons.append("buyer_type")

    if has_commercial and has_medical and has_category and has_buyer_type:
        return "ACCEPT", reasons

    # Allow obvious retailer/distributor cases when the query is highly specific.
    competitor_specific = any(
        brand in text for brand in COMPETITOR_BRANDS
    )
    if competitor_specific and has_commercial and has_medical and has_buyer_type:
        return "ACCEPT", reasons

    return "REJECT", reasons or ["insufficient brand fit"]

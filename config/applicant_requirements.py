"""
Applicant requirements — defaults and structured lists.
Score thresholds and recency are loaded from data/applicant_requirements.md when present.
"""

from config.md_loader import (
    get_requirements_for_scorer,
    load_applicant_requirements,
    load_requirements_config,
    score_thresholds_from_md,
)

# ── From applicant_requirements.md frontmatter (or defaults) ─────────────────
_cfg = load_requirements_config()

SCORE_THRESHOLDS = score_thresholds_from_md()
MAX_YEARS_HARD_SKIP = int(_cfg["max_years_hard_skip"])
MIN_REQUIREMENTS_MATCH_PCT = int(_cfg["min_requirements_match_pct"])
MIN_SALARY_AED_MONTHLY = int(_cfg.get("min_salary_aed_monthly", 12000))
LINKEDIN_HOURS_FRESH = int(_cfg["linkedin_hours_fresh"])
ATS_DAYS_FRESH = int(_cfg["ats_days_fresh"])

# Full markdown for scorer: source + enhanced + custom scoring sections
APPLICANT_REQUIREMENTS_TEXT = get_requirements_for_scorer()


def reload_applicant_requirements_text() -> str:
    """Reload after requirements or enhanced layer save."""
    global APPLICANT_REQUIREMENTS_TEXT
    APPLICANT_REQUIREMENTS_TEXT = get_requirements_for_scorer()
    return APPLICANT_REQUIREMENTS_TEXT

# ── Geography ──────────────────────────────────────────────────────────────────
PREFERRED_LOCATIONS = [
    "Abu Dhabi", "Dubai", "DIFC", "ADGM", "UAE", "United Arab Emirates",
    "Sharjah", "GCC", "Qatar", "Doha", "Saudi", "Riyadh", "Bahrain", "Kuwait",
]

BLOCKED_EMPLOYERS = [
    "Abu Dhabi Investment Authority", "ADIA",
    "Abu Dhabi Investment Council", "ADIC",
]

TARGET_ROLE_FAMILIES = {
    "quant_trading": [
        "quantitative researcher", "quantitative analyst", "quantitative trader",
        "junior trader", "graduate trader", "systematic trading analyst",
        "algorithmic trading analyst", "derivatives analyst", "portfolio analytics analyst",
        "market risk analyst", "trading analyst", "execution trader",
    ],
    "investments": [
        "investment analyst", "graduate investment analyst", "investment associate",
        "portfolio analyst", "private equity analyst", "venture capital analyst",
        "corporate development analyst", "M&A analyst", "fund analyst",
        "alternatives analyst", "infrastructure investments analyst",
        "family office analyst", "asset management analyst",
    ],
    "ai_data": [
        "data scientist", "machine learning engineer", "AI engineer",
        "applied scientist", "research engineer", "AI research analyst",
    ],
    "space_defense": [
        "space systems analyst", "geospatial data scientist", "robotics engineer",
        "defense strategy analyst", "aerospace analyst", "mission analyst",
    ],
    "energy_commodities": [
        "energy trading analyst", "commodities analyst", "LNG analyst",
        "carbon trading analyst", "energy transition analyst", "clean energy analyst",
    ],
    "fintech": [
        "fintech analyst", "trading technology analyst", "payments analyst",
    ],
    "climate": [
        "carbon markets analyst", "sustainability analyst", "ESG data analyst",
        "climate analyst", "green finance analyst",
    ],
    "strategy": [
        "strategy analyst", "corporate development analyst", "chief of staff",
        "founder associate", "innovation analyst", "economic development analyst",
    ],
    "product_cyber": [
        "technical product manager", "product analyst", "cybersecurity consultant",
    ],
}

TIER_1_TARGET_COMPANIES = [
    "Mubadala", "Mubadala Capital", "ADQ", "Emirates Investment Authority", "EIA",
    "Lunate", "Chimera", "Invest AD", "ICD",
    "G42", "Core42", "M42", "AI71", "Space42", "Presight", "TII", "MBZUAI",
    "EDGE Group", "Bayanat", "Yahsat", "Thuraya", "MBRSC", "UAE Space Agency",
    "ADNOC", "ADNOC Trading", "Masdar",
    "Brevan Howard", "Millennium", "Point72", "Squarepoint", "BAM", "Balyasny",
    "Schonfeld", "ExodusPoint", "Verition", "Qube Research & Technologies",
    "McKinsey", "Oliver Wyman", "KKR", "Partners Group",
    "PIF", "QIA", "Hub71", "Abu Dhabi Catalyst Partners",
]

PRIORITY_KEYWORDS = [
    "quant", "quantitative", "systematic", "trading", "hedge fund", "prop trading",
    "investment analyst", "private equity", "venture capital", "asset management",
    "sovereign", "family office", "data scientist", "machine learning", "AI engineer",
    "space", "satellite", "geospatial", "robotics", "defense",
    "energy trading", "commodities", "carbon", "fintech", "DIFC", "ADGM",
]

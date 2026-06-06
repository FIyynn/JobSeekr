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


# Full candidate profile for LLM-based outreach personalization (connection notes,
# InMail drafts, referral requests). Synthesized from APPLICANT_REQUIREMENTS_TEXT,
# TARGET_ROLE_FAMILIES, PREFERRED_LOCATIONS, and TIER_1_TARGET_COMPANIES.
PROFILE_FULL = """\
# Candidate Profile — Rashed Alneyadi

## Identity
Rashed Ahmed Alneyadi is an Emirati (UAE National) early-career quantitative researcher
and technical founder based in Abu Dhabi. He is fluent in Arabic and English and
positions strongly for UAE/GCC sovereign-backed, government-linked, and national
development roles where an Emirati profile is an advantage.

## Education
- BA Mathematics, Computer Science minor — New York University, New York (NYU New York), Dec 2024
- NYU Abu Dhabi (NYUAD): Quantum Computation Research Assistant (research only; degree from NYU New York)
- MIT Media Lab / MBRSC-linked space robotics exposure

## Target Roles (priority order)
1. **Investment analyst** — sovereign wealth, private equity, venture capital, asset management,
   portfolio analysis, corporate development, M&A, family offices
2. **Quant** — quantitative researcher/analyst/trader, systematic trading, market risk,
   derivatives, portfolio analytics, hedge funds, prop trading
3. **AI engineer** — machine learning engineer, applied scientist, research engineer,
   data scientist, AI product analyst
4. **Strategy** — strategy analyst, government/economic development, chief of staff,
   founder associate, innovation analyst, corporate strategy

Secondary interests: space/defense/geospatial, energy/commodities, fintech,
climate/carbon markets, technical product, cybersecurity.

Appropriate levels: analyst, associate, graduate, junior, researcher, trainee,
rotational/graduate development programs, Emiratization programs.

## Geography
Priority: Abu Dhabi, Dubai, DIFC, ADGM, UAE, Sharjah, GCC (Qatar/Doha, Saudi/Riyadh,
Bahrain, Kuwait). Willing to relocate within UAE and GCC. International only if elite
and highly relevant.

## Key Strengths
- Mathematics + quantitative modeling + statistics
- Python, MATLAB, Excel, data analysis, automation, backtesting research prototypes
- Investment research: ADIA (private equity research), ADIC (active investment analytics)
- Space robotics optimization, geospatial/defense-adjacent research
- Quantum computation research workflows
- Founder/operator: software, cybersecurity, climate-tech, product building, GTM
- Bilingual (Arabic/English); high-agency builder — not a generic business graduate

## Positioning for Outreach
Lead with: Emirati NYU mathematics graduate + ADIA/ADIC experience + quant/AI/software
research background. Emphasize analytical depth, technical execution, and turning research
into practical systems. Do not claim live professional trading; describe DIBA/backtesting
as research prototype experience. No master's degree — do not over-penalize unless mandatory.

## Compensation Trajectory
Target 35,000+ AED/month by Year 2; ideal path 35k–45k AED/month within 2–4 years.
Minimum acceptable ~27,000 AED/month. Elite graduate/analyst programs worth pursuing even
if salary is unlisted.

## Tier-1 Target Companies
Mubadala, Mubadala Capital, ADQ, EIA, Lunate, Chimera, Invest AD, ICD,
G42, Core42, M42, AI71, Space42, Presight, TII, MBZUAI,
EDGE Group, Bayanat, Yahsat, Thuraya, MBRSC, UAE Space Agency,
ADNOC, ADNOC Trading, Masdar,
Brevan Howard, Millennium, Point72, Squarepoint, BAM/Balyasny, Schonfeld,
ExodusPoint, Verition, Qube Research & Technologies,
McKinsey, Oliver Wyman, KKR, Partners Group, PIF, QIA, Hub71,
Abu Dhabi Catalyst Partners.

ADIA and ADIC are active targets (not blocked).

## Outreach Voice
- First person ("I" / "my"); sign off as Rashed
- Concise, specific, low-pressure; ask for advice or the right team/contact
- Weave in 1–2 credential highlights relevant to the recipient's function
- Connection notes ≤300 characters when required
"""

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

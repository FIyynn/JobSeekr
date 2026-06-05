"""
JobHunter Configuration
Profile: data/applicant_profile.md
Requirements: data/applicant_requirements.md (+ config/applicant_requirements.py lists)
"""

from pathlib import Path
from config.md_loader import get_candidate_profile_for_prompt, load_applicant_profile

# ── Paths ─────────────────────────────────────────────────────────────────────
# Default resume; runtime uses profile_settings.json via get_resume_path() when present
RESUME_PATH = r"C:/Users/Lordy/Downloads/4.5cx_Found.pdf"

# ── Ollama ────────────────────────────────────────────────────────────────────
# Model is read from data/profile_settings.json ("ollama_model" key) so users
# can swap models without touching code.  Falls back to qwen3:8b if not set.
def _read_ollama_model() -> str:
    try:
        import json
        from pathlib import Path as _P
        _s = _P(__file__).parent.parent / "data" / "profile_settings.json"
        if _s.exists():
            _d = json.loads(_s.read_text(encoding="utf-8"))
            _m = (_d.get("ollama_model") or "").strip()
            if _m:
                return _m
    except Exception:
        pass
    return "qwen3:8b"

OLLAMA_MODEL = _read_ollama_model()
OLLAMA_VISION_MODEL = "llava"
OLLAMA_BASE_URL = "http://localhost:11434"


def get_ollama_model() -> str:
    """Current model from profile_settings.json (re-read each call)."""
    return _read_ollama_model()


def _read_ollama_model_fast() -> str:
    """Fast model for short/factual fields. Falls back to OLLAMA_MODEL."""
    try:
        import json
        from pathlib import Path as _P
        _s = _P(__file__).parent.parent / "data" / "profile_settings.json"
        if _s.exists():
            _d = json.loads(_s.read_text(encoding="utf-8"))
            _m = (_d.get("ollama_model_fast") or "").strip()
            if _m:
                return _m
    except Exception:
        pass
    return OLLAMA_MODEL


OLLAMA_MODEL_FAST = _read_ollama_model_fast()

# ── Notion ────────────────────────────────────────────────────────────────────
NOTION_TOKEN = ""
NOTION_DATABASE_ID = ""

# ── Scheduler ─────────────────────────────────────────────────────────────────
RUN_EVERY_HOURS = 4
MAX_JOBS_PER_RUN = 60

# ── Requirements (thresholds, targets) ─────────────────────────────────────────
from config.applicant_requirements import (
    SCORE_THRESHOLDS,
    MAX_YEARS_HARD_SKIP,
    TIER_1_TARGET_COMPANIES,
    LINKEDIN_HOURS_FRESH,
)

MAX_YEARS_REQUIRED = MAX_YEARS_HARD_SKIP  # hard skip in discovery/prefilter at 7+ years

SEARCH_SITES = ["linkedin"]
SEARCH_HOURS_FRESH = LINKEDIN_HOURS_FRESH

SEARCH_QUERIES = [
    # Quant / trading
    {"term": "quantitative researcher", "location": "Abu Dhabi"},
    {"term": "quantitative analyst", "location": "Dubai"},
    {"term": "quantitative trader", "location": "DIFC"},
    {"term": "graduate trader", "location": "UAE"},
    {"term": "systematic trading analyst", "location": "UAE"},
    {"term": "algorithmic trading analyst", "location": "Dubai"},
    {"term": "portfolio analytics analyst", "location": "UAE"},
    {"term": "derivatives analyst", "location": "DIFC"},
    {"term": "market risk analyst", "location": "UAE"},
    # Investments / PE / VC
    {"term": "investment analyst", "location": "Abu Dhabi"},
    {"term": "graduate investment analyst", "location": "UAE"},
    {"term": "investment associate", "location": "Abu Dhabi"},
    {"term": "private equity analyst", "location": "UAE"},
    {"term": "venture capital analyst", "location": "UAE"},
    {"term": "corporate development analyst", "location": "UAE"},
    {"term": "infrastructure investments analyst", "location": "UAE"},
    {"term": "family office analyst", "location": "Dubai"},
    {"term": "asset management analyst", "location": "UAE"},
    # AI / data
    {"term": "data scientist", "location": "Abu Dhabi"},
    {"term": "machine learning engineer", "location": "UAE"},
    {"term": "AI engineer", "location": "UAE"},
    {"term": "research engineer", "location": "Abu Dhabi"},
    {"term": "applied scientist", "location": "UAE"},
    # Space / defense
    {"term": "space systems analyst", "location": "UAE"},
    {"term": "geospatial data scientist", "location": "UAE"},
    {"term": "robotics engineer", "location": "Abu Dhabi"},
    {"term": "defense strategy analyst", "location": "UAE"},
    # Energy / commodities / climate
    {"term": "energy trading analyst", "location": "UAE"},
    {"term": "commodities analyst", "location": "UAE"},
    {"term": "carbon trading analyst", "location": "UAE"},
    {"term": "carbon markets analyst", "location": "UAE"},
    {"term": "sustainability analyst", "location": "Abu Dhabi"},
    # Strategy / founder-operator
    {"term": "strategy analyst", "location": "Abu Dhabi"},
    {"term": "chief of staff", "location": "UAE"},
    {"term": "founder associate", "location": "UAE"},
    # Fintech / tech
    {"term": "fintech analyst", "location": "DIFC"},
    {"term": "trading technology analyst", "location": "UAE"},
    {"term": "product analyst", "location": "Dubai"},
]

# ── Hard Blocklist ─────────────────────────────────────────────────────────────
BLOCKED_COMPANIES = [
    "Abu Dhabi Investment Authority", "ADIA",
    "Abu Dhabi Investment Council", "ADIC",
    "DataAnnotation", "Scale AI", "Appen", "Outlier AI", "Outlier",
    "Remotasks", "Invisible AI", "Surge AI", "Labelbox", "Toloka",
    "Defined.ai", "iComply", "Lionbridge", "TELUS International",
    "TaskUs", "Clickworker", "Microworkers",
]

BLOCKED_KEYWORDS = [
    "commission only", "unpaid intern", "talent community", "talent pool",
    "evergreen", "submit your cv", "nursing", "clinical", "customer service",
    "call centre", "call center", "senior director", "vice president",
    "managing director", "chief executive", "c-suite",
    "data labeling", "data annotation", "content moderation",
    "AI trainer", "AI training", "crowdsource", "microtask",
    "ai agents only", "exclusively open to ai agents",
    "applications from individual candidates will not be considered",
    "developer submission required",
]

BLOCKED_JOB_TITLES = [
    "nurse", "doctor", "physician", "receptionist",
    "hr manager", "recruiter", "sales representative",
    "account executive", "retail",
]

SCORE_WEIGHTS = {
    "compensation_potential": 40,
    "progression_speed": 20,
    "brand_signal": 15,
    "profile_fit": 15,
    "strategic_optionality": 10,
}

TIER_1_COMPANIES = TIER_1_TARGET_COMPANIES

TIER_2_COMPANIES = [
    "Hub71", "Abu Dhabi Catalyst Partners", "Thuraya", "Huawei UAE",
    "Dymon Asia", "TCI Fund Management", "KBW", "Verition",
]

# ── Candidate profile (data/applicant_profile.md) ─────────────────────────────
_CANDIDATE_PROFILE_FALLBACK = """
Rashed Ahmed Alneyadi | Emirati (UAE National) | Arabic & English fluent | Abu Dhabi
BA Mathematics, CS Minor — NYU New York, Dec 2024 | Quant researcher + technical founder
"""

def reload_candidate_profile() -> str:
    """Reload merged profile (source + enhanced layer) into CANDIDATE_PROFILE."""
    global CANDIDATE_PROFILE
    merged = get_candidate_profile_for_prompt()
    CANDIDATE_PROFILE = merged if merged else _CANDIDATE_PROFILE_FALLBACK.strip()
    return CANDIDATE_PROFILE


_profile = get_candidate_profile_for_prompt() or load_applicant_profile()
CANDIDATE_PROFILE = _profile if _profile else _CANDIDATE_PROFILE_FALLBACK.strip()

# ── Application Q&A ───────────────────────────────────────────────────────────
APPLICATION_QA = {
    "first_name": "Rashed",
    "last_name": "Alneyadi",
    "full_name": "Rashed Ahmed Alneyadi",
    "email": "",
    "phone": "+971505612301",
    "linkedin": "https://linkedin.com/in/rashed-alneyadi",
    "location": "Abu Dhabi, UAE",
    "nationality": "Emirati (UAE National)",
    "languages": "Arabic (fluent), English (fluent)",
    "visa_sponsorship": "No (UAE National — no visa required)",
    "years_experience": "1–2 years",
    "education_level": "Bachelor's Degree",
    "degree_field": "Mathematics with Computer Science Minor",
    "university": "New York University, New York (NYU New York)",
    "graduation_year": "2024",
    "salary_expectation": "Competitive / open to discussion",
    "start_date": "Immediately",
    "willing_to_relocate": "Yes, within UAE and GCC",
    "gender": "Male",
    "requires_sponsorship": "No",
    "authorized_to_work": "Yes — Emirati (UAE National)",
    "excel": "Advanced — financial models, analysis, reporting",
    "resume_path": RESUME_PATH,
}

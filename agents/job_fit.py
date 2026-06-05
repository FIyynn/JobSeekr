"""
Shared job-fit rules: hard pre-filters + experience parsing.
Used by discovery, scorer, apply_from_notion, and form_filler.
"""

import re
from typing import Optional

# Roles at G42 marketed to AI agents, not human applicants
_G42_AI_AGENT_TITLES = (
    "legal intelligence agent",
    "marketing intelligence agent",
    "compliance intelligence agent",
    "intelligence agent",
)

AI_AGENT_ONLY_PHRASES = (
    "ai agents only",
    "for ai agents only",
    "exclusively open to ai agents",
    "open to ai agents",
    "applications from individual candidates will not be considered",
    "not open to human",
    "not open to human applicants",
    "not open to individual candidates",
    "developer submission required",
    "submissions must be made by a developer or engineer representing",
    "representing a specific ai agent",
    "bot-only",
    "for bots only",
)

SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|manager|director|"
    r"vice president|\bvp\b|head of|chief|executive)\b",
    re.I,
)

ENTRY_TITLE_HINTS = re.compile(
    r"\b(analyst|associate|intern|graduate|entry|junior|trainee|"
    r"coordinator|assistant|researcher)\b",
    re.I,
)

YEARS_RE = re.compile(
    r"(\d+)\+?\s*(?:–|-|to)\s*(\d+)?\s*years?|(\d+)\+\s*years?|"
    r"minimum\s+of\s+(\d+)\s+years?",
    re.I,
)


def parse_min_years_required(text: str) -> Optional[int]:
    """Return minimum years of experience mentioned, or None."""
    if not text:
        return None
    mins = []
    for match in YEARS_RE.finditer(text):
        groups = match.groups()
        nums = [int(g) for g in groups if g is not None]
        if nums:
            mins.append(min(nums))
    return min(mins) if mins else None


def is_senior_title(title: str) -> bool:
    """True if title implies senior+ level (not entry/junior analyst)."""
    t = (title or "").lower()
    if ENTRY_TITLE_HINTS.search(t) and not SENIOR_TITLE_RE.search(t):
        return False
    if re.search(r"\bsenior\b", t) and "analyst" in t:
        return True
    if SENIOR_TITLE_RE.search(t):
        # Allow "Associate" without senior prefix
        if re.match(r"^(investment |private equity |venture )?associate\b", t):
            return False
        return True
    return False


def is_ai_agent_only_job(job: dict) -> tuple[bool, str]:
    """Detect postings for AI agents / bots, not human hires."""
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    desc = (job.get("description") or "").lower()
    combined = f"{title}\n{desc}"

    for phrase in AI_AGENT_ONLY_PHRASES:
        if phrase in combined:
            return True, f"Role is for AI agents/automation, not human applicants ({phrase})"

    has_agent_context = any(
        phrase in combined
        for phrase in (
            "ai agent",
            "ai agents",
            "autonomous agent",
            "autonomous agents",
            "software agent",
            "software agents",
        )
    )
    excludes_humans = any(
        phrase in combined
        for phrase in (
            "not human",
            "not open to human",
            "not open to individual",
            "individual candidates will not be considered",
            "developer submission",
            "developer or engineer representing",
            "representing a specific",
            "bot-only",
            "bots only",
        )
    )
    if has_agent_context and excludes_humans:
        return True, "Role is for AI agents/automation, not human applicants"

    if "g42" in company:
        for t in _G42_AI_AGENT_TITLES:
            if t in title:
                return True, (
                    f"G42 '{job.get('title')}' is an AI-agent submission role, not a human job"
                )

    if "intelligence agent" in title and any(
        p in combined for p in ("ai agent", "developer submission", "not be considered")
    ):
        return True, "Intelligence Agent role requires AI agent submission, not humans"

    # Many GCC employers use "* Intelligence Agent" for AI-bot hiring, not humans
    if re.search(r"\b\w+\s+intelligence\s+agent\b", title) or title.strip() == "intelligence agent":
        return True, (
            f"'{job.get('title')}' matches Intelligence Agent pattern "
            "(typically AI-agent roles, not human applicants)"
        )

    return False, ""


# ── Geography ───────────────────────────────────────────────────────────────
# GCC / target-region tokens. If any appears in the location, the role is in-region.
_GCC_LOCATION_TOKENS = (
    "united arab emirates", "uae", "abu dhabi", "dubai", "sharjah", "ajman",
    "fujairah", "ras al", "umm al", "al ain", "difc", "adgm",
    "qatar", "doha", "saudi", "riyadh", "jeddah", "dammam", "ksa",
    "bahrain", "manama", "kuwait", "oman", "muscat", "gcc",
    "middle east", "mena", "gulf", "remote",
)

# Clear non-GCC signals: foreign countries / major cities. Used only as a positive
# "this is elsewhere" detector — when none of these match and no GCC token is present
# we keep the job (unknown location → let the scorer decide).
_NON_GCC_TOKENS = (
    "united states", "usa", " u.s.", "united kingdom", "england", "scotland",
    "london", "new york", "san francisco", "boston", "chicago", "los angeles",
    "washington", "seattle", "austin", "toronto", "canada", "australia", "sydney",
    "singapore", "hong kong", "shanghai", "beijing", "tokyo", "japan", "india",
    "bangalore", "bengaluru", "mumbai", "hyderabad", "pune", "delhi", "pakistan",
    "germany", "berlin", "munich", "france", "paris", "netherlands", "amsterdam",
    "spain", "madrid", "ireland", "dublin", "poland", "warsaw", "egypt", "cairo",
    "nigeria", "kenya", "south africa", "brazil", "mexico", "philippines",
    "malaysia", "indonesia", "vietnam", "thailand", "turkey", "istanbul",
)

# US state-abbreviation suffix like "New York, NY" or "Little Falls, NJ"
_US_STATE_SUFFIX = re.compile(r",\s*[A-Z]{2}\b")


def is_outside_target_geo(job: dict) -> tuple[bool, str]:
    """Return (outside, reason). Conservative: only drops jobs with a positive
    non-GCC location signal, and never drops elite/target-company roles
    (those are kept for manual review per 'international only if elite')."""
    location = (job.get("location") or "").strip()
    if not location:
        return False, ""  # unknown location — let the scorer decide
    low = location.lower()

    if any(tok in low for tok in _GCC_LOCATION_TOKENS):
        return False, ""

    non_gcc = any(tok in low for tok in _NON_GCC_TOKENS) or bool(_US_STATE_SUFFIX.search(location))
    if not non_gcc:
        return False, ""  # not clearly elsewhere — keep

    # Elite/target-company override: keep international elite roles for manual review.
    try:
        from config.applicant_requirements import TIER_1_TARGET_COMPANIES
        company = (job.get("company") or "").lower()
        if any(c.lower() in company for c in TIER_1_TARGET_COMPANIES):
            return False, ""
    except Exception:
        pass

    return True, f"Outside UAE/GCC (location: {location})"


def prefilter_job(
    job: dict,
    blocked_companies: list = None,
    blocked_keywords: list = None,
    blocked_titles: list = None,
    max_years: int = 3,
) -> tuple[bool, str]:
    """
    Hard pre-filter before LLM scoring or apply.
    Returns (should_skip, reason).
    """
    title   = (job.get("title") or "").strip()
    company = (job.get("company") or "").strip()
    desc    = (job.get("description") or "").strip()
    location = (job.get("location") or "").strip()

    # 1. Blocked company
    if blocked_companies:
        co_lower = company.lower()
        for bc in blocked_companies:
            if bc.lower() in co_lower:
                return True, f"Blocked company: {company}"

    # 2. Blocked title keyword
    if blocked_titles:
        t_lower = title.lower()
        for bt in blocked_titles:
            if bt.lower() in t_lower:
                return True, f"Blocked title keyword: {bt}"

    # 3. Blocked description keyword
    if blocked_keywords:
        d_lower = desc.lower()
        for bk in blocked_keywords:
            if bk.lower() in d_lower:
                return True, f"Blocked keyword in description: {bk}"

    # 4. AI-agent-only role
    skip, reason = is_ai_agent_only_job(job)
    if skip:
        return True, reason

    # 5. Outside GCC
    skip, reason = is_outside_target_geo(job)
    if skip:
        return True, reason

    return False, ""

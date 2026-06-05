"""
Detect whether a job is outside the applicant's target industries (from requirements).
"""

import re
from typing import Tuple

# Roles / sectors we do NOT target (heuristic)
OFF_TARGET_TITLE_PATTERNS = [
    r"\bnurse\b", r"\bnursing\b", r"\bteacher\b", r"\bteaching\b", r"\bchef\b",
    r"\bwaiter\b", r"\bwaitress\b", r"\bbarista\b", r"\bdriver\b", r"\bdelivery\b",
    r"\bcashier\b", r"\bretail associate\b", r"\bsales associate\b", r"\breal estate agent\b",
    r"\binsurance agent\b", r"\brecruiter\b", r"\btalent acquisition\b",
    r"\bhr generalist\b", r"\bhuman resources officer\b", r"\bcustomer service\b",
    r"\bcall center\b", r"\bhousekeeping\b", r"\bsecurity guard\b",
    r"\bdental\b", r"\bpharmacist\b", r"\bphysician\b", r"\bmedical doctor\b",
    r"\blawyer\b", r"\blegal counsel\b", r"\bparalegal\b",
    r"\bgraphic designer\b", r"\bsocial media manager\b", r"\bcontent creator\b",
    r"\bhotel\b", r"\bhospitality\b", r"\bflight attendant\b",
]

OFF_TARGET_DESC_KEYWORDS = [
    "commission-only", "door-to-door", "cold calling quotas",
    "must have driver's license for deliveries", "retail floor",
    "hospitality experience required", "nursing license", "teaching license",
    "registered nurse", "licensed practical nurse",
]


def _all_target_keywords() -> list[str]:
    try:
        from config.applicant_requirements import (
            TARGET_ROLE_FAMILIES, PRIORITY_KEYWORDS, TIER_1_TARGET_COMPANIES,
        )
        kws = list(PRIORITY_KEYWORDS)
        for family in TARGET_ROLE_FAMILIES.values():
            kws.extend(family)
        for c in TIER_1_TARGET_COMPANIES:
            kws.append(c.lower())
        return list(set(k.lower() for k in kws if k))
    except Exception:
        return [
            "quant", "quantitative", "investment", "analyst", "data scientist",
            "machine learning", "private equity", "venture capital", "trading",
            "fintech", "energy", "commodities", "space", "robotics", "strategy",
        ]


def _text_blob(job: dict) -> str:
    parts = [
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("description", "")[:3000],
        job.get("positioning_angle", ""),
    ]
    return " ".join(parts).lower()


def is_outside_target_industry(job: dict) -> Tuple[bool, str]:
    """
    Returns (True, reason) if job appears outside target role families / industries.
    """
    blob = _text_blob(job)
    title = (job.get("title") or "").lower()

    for pat in OFF_TARGET_TITLE_PATTERNS:
        if re.search(pat, title, re.I):
            return True, f"Title suggests off-target sector: {job.get('title', '')}"

    for kw in OFF_TARGET_DESC_KEYWORDS:
        if kw in blob:
            return True, f"Description suggests off-target role ({kw})"

    target_kws = _all_target_keywords()
    hits = [k for k in target_kws if k in blob]
    if hits:
        return False, ""

    # Tier-1 company name in company field counts as in-target
    company = (job.get("company") or "").lower()
    for c in target_kws:
        if len(c) > 4 and c in company:
            return False, ""

    angle = (job.get("positioning_angle") or "").lower()
    valid_angles = {
        "quant", "investments", "ai", "space", "energy", "fintech",
        "climate", "strategy", "cyber", "pe", "finance", "trading",
    }
    if any(a in angle for a in valid_angles):
        return False, ""

    return True, (
        "Role does not match target industries (quant, investments, AI, space, "
        "energy, fintech, climate, strategy) — review manually."
    )

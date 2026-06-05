"""
Parse salary from job postings and compare to applicant minimum (AED/month).
"""

import re
from typing import Optional

# Approximate FX for parsing USD/EUR listings into AED
_USD_TO_AED = 3.67
_EUR_TO_AED = 4.0


def _to_monthly_aed(amount: float, period: str, currency: str) -> float:
    cur = (currency or "aed").lower()
    if cur in ("usd", "us$", "$"):
        amount *= _USD_TO_AED
    elif cur in ("eur", "€"):
        amount *= _EUR_TO_AED
    period = (period or "month").lower()
    if "year" in period or "annual" in period or "pa" in period or "per annum" in period:
        amount /= 12
    elif "week" in period:
        amount *= 4.33
    elif "day" in period or "daily" in period:
        amount *= 22
    return amount


def parse_salary_from_text(text: str) -> dict:
    """
    Extract salary hints from job description.
    Returns dict with min_monthly_aed, max_monthly_aed, raw_snippets, confidence.
    """
    text = text or ""
    low = text.lower()
    snippets = []
    mins, maxs = [], []

    # AED / DHS explicit
    for m in re.finditer(
        r"(?:aed|dhs|dirham)s?\s*([\d,]+(?:\.\d+)?)\s*(?:k)?\s*(?:-|to|–)?\s*(?:aed|dhs)?\s*([\d,]+(?:\.\d+)?)?\s*(k)?"
        r"(?:\s*(?:/|per)\s*(month|mo|year|annum|annual))?",
        low,
        re.I,
    ):
        snippets.append(m.group(0))
        v1 = float(m.group(1).replace(",", ""))
        if m.group(3) == "k" or v1 < 500:
            v1 *= 1000
        period = m.group(4) or "month"
        mins.append(_to_monthly_aed(v1, period, "aed"))
        if m.group(2):
            v2 = float(m.group(2).replace(",", ""))
            if m.group(3) == "k" or v2 < 500:
                v2 *= 1000
            maxs.append(_to_monthly_aed(v2, period, "aed"))

    # USD
    for m in re.finditer(
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(k)?\s*(?:-|to)?\s*\$?\s*([\d,]+(?:\.\d+)?)?\s*(k)?"
        r"(?:\s*(?:/|per)\s*(month|mo|year|annum))?",
        text,
        re.I,
    ):
        snippets.append(m.group(0))
        v1 = float(m.group(1).replace(",", ""))
        if m.group(2) == "k" or v1 < 500:
            v1 *= 1000
        period = m.group(5) or "month"
        mins.append(_to_monthly_aed(v1, period, "usd"))
        if m.group(3):
            v2 = float(m.group(3).replace(",", ""))
            if m.group(4) == "k" or v2 < 500:
                v2 *= 1000
            maxs.append(_to_monthly_aed(v2, period, "usd"))

    # "15000-18000 AED per month" or "15,000 - 20,000 per month"
    for m in re.finditer(
        r"([\d,]{3,})\s*(?:-|to|–)\s*([\d,]{3,})\s*(?:aed|dhs|usd|\$)?\s*(?:per\s+)?(month|mo|annum|year|annual)?",
        low,
    ):
        snippets.append(m.group(0))
        v1 = float(m.group(1).replace(",", ""))
        v2 = float(m.group(2).replace(",", ""))
        period = m.group(3) or "month"
        cur = "aed" if any(x in m.group(0) for x in ("aed", "dhs")) else (
            "usd" if "$" in m.group(0) or "usd" in m.group(0) else
            ("aed" if any(x in low for x in ("uae", "dubai", "abu dhabi")) else "usd")
        )
        mins.append(_to_monthly_aed(min(v1, v2), period, cur))
        maxs.append(_to_monthly_aed(max(v1, v2), period, cur))

    # USD/EUR amount per month without range
    for m in re.finditer(
        r"(usd|eur|\$|€)\s*([\d,]+(?:\.\d+)?)\s*(k)?\s*(?:/|per)\s*(month|mo|year|annum)",
        low,
    ):
        snippets.append(m.group(0))
        cur = "usd" if m.group(1) in ("usd", "$") else "eur"
        v = float(m.group(2).replace(",", ""))
        if m.group(3) == "k" or v < 500:
            v *= 1000
        mins.append(_to_monthly_aed(v, m.group(4), cur))

    # Single "18k monthly"
    for m in re.finditer(r"([\d]+)\s*k\s*(?:/|per)?\s*(month|mo|year|annum)?", low):
        v = float(m.group(1)) * 1000
        period = m.group(2) or "month"
        mins.append(_to_monthly_aed(v, period, "aed"))

    min_aed = min(mins) if mins else None
    max_aed = max(maxs) if maxs else (max(mins) if mins else None)

    return {
        "min_monthly_aed": int(min_aed) if min_aed else None,
        "max_monthly_aed": int(max_aed) if max_aed else None,
        "salary_snippet": "; ".join(snippets[:3])[:500],
        "salary_parsed": bool(mins),
    }


def check_salary_floor(job: dict, min_monthly_aed: int) -> tuple[bool, str]:
    """
    Returns (below_minimum, reason).
    If salary not mentioned, returns (False, "") — do not auto-skip unknown.
    If max parsed salary is below floor, flag.
    """
    if not min_monthly_aed or min_monthly_aed <= 0:
        return False, ""

    # Re-parse only if salary not already extracted (avoid updating bool into job dict)
    parsed = {}  # always initialise so Python never raises UnboundLocalError
    if job.get("min_monthly_aed") is None and job.get("max_monthly_aed") is None:
        parsed = parse_salary_from_text(
            (job.get("description") or "") + " " + (job.get("job_profile_json") or "")
        )
        job.update(parsed)

    min_offered = job.get("min_monthly_aed")
    max_offered = job.get("max_monthly_aed")
    if not min_offered and not max_offered:
        return False, ""

    # Use max of range if present (employer often lists band)
    effective = max_offered or min_offered
    if effective and effective < min_monthly_aed:
        return True, (
            f"Posted compensation ~{effective:,} AED/month below your minimum "
            f"{min_monthly_aed:,} AED/month"
        )
    return False, ""


def get_min_salary_from_config() -> int:
    try:
        from config.md_loader import load_requirements_config
        c = load_requirements_config()
        return int(c.get("min_salary_aed_monthly") or 0)
    except Exception:
        return 12000

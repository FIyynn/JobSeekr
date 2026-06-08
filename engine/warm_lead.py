"""
Warm lead scoring — alumni, shared employers, mutual connections (0–100).
"""

from __future__ import annotations

import re
from typing import Optional


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def compute_warm_lead_score(contact: dict, profile: str = "") -> float:
    """
    Score 0–100 for referral / outreach priority.
    Uses contact metadata and optional profile text overlap.
    """
    score = 25.0
    prof = _normalize(profile)

    if contact.get("is_connection") or contact.get("Outreach status") == "Accepted":
        score += 35.0

    if contact.get("shared_group"):
        score += 15.0

    if contact.get("open_profile") or contact.get("free_message_available"):
        score += 10.0

    if contact.get("alumni_match"):
        score += 20.0

    if contact.get("shared_employer"):
        score += 15.0

    if contact.get("mutual_connections"):
        try:
            n = int(contact["mutual_connections"])
            score += min(20.0, n * 4.0)
        except (TypeError, ValueError):
            pass

    title = _normalize(contact.get("title") or "")
    if any(t in title for t in ("recruiter", "talent", "hiring manager", "head of")):
        score += 8.0

    if prof:
        name = _normalize(contact.get("name") or "")
        company = _normalize(contact.get("company") or "")
        if name and name in prof:
            score += 5.0
        if company and company in prof:
            score += 5.0

    if contact.get("warm_lead_score") is not None:
        try:
            return min(100.0, max(0.0, float(contact["warm_lead_score"])))
        except (TypeError, ValueError):
            pass

    return min(100.0, max(0.0, round(score, 1)))


def enrich_contact_warmth(contact: dict, profile: str = "") -> dict:
    """Attach warm_lead_score to a contact dict."""
    contact = dict(contact)
    score = compute_warm_lead_score(contact, profile)
    contact["warm_lead_score"] = min(100.0, max(0.0, float(score)))
    return contact


def best_contact_for_job(contacts: list[dict], profile: str = "") -> Optional[dict]:
    """Return highest warm-lead contact for IPS / referral-first."""
    if not contacts:
        return None
    scored = [enrich_contact_warmth(c, profile) for c in contacts]
    return max(scored, key=lambda c: c.get("warm_lead_score") or 0)

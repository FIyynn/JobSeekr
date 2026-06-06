"""
Power Trio resolver — hiring manager, recruiter, peer per opportunity.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from engine.warm_lead import enrich_contact_warmth

logger = logging.getLogger("stakeholder_mapper")

POWER_TRIO = (
    ("hiring_manager", "Hiring Manager"),
    ("recruiter", "Recruiter / Talent"),
    ("peer", "Peer / Teammate"),
)

_HM_TITLES = (
    "hiring manager", "head of", "director", "vp", "vice president",
    "managing director", "team lead", "principal", "engineering manager",
)
_RECRUITER_TITLES = (
    "recruiter", "talent acquisition", "talent partner", "hr business partner",
    "people partner", "early careers", "graduate recruitment",
)
_PEER_TITLES = (
    "analyst", "associate", "engineer", "scientist", "specialist", "consultant",
)


def _classify_role(title: str) -> str:
    t = (title or "").lower()
    if any(x in t for x in _RECRUITER_TITLES):
        return "recruiter"
    if any(x in t for x in _HM_TITLES):
        return "hiring_manager"
    if any(x in t for x in _PEER_TITLES):
        return "peer"
    return "peer"


def resolve_power_trio(
    opportunity: dict,
    *,
    profile: str = "",
    max_per_role: int = 1,
) -> list[dict]:
    """
    Resolve HM / recruiter / peer contacts for an opportunity.
    Persists to opportunity_store when store is available.
    """
    company = (opportunity.get("company") or "").strip()
    if not company:
        return []

    contacts: list[dict] = []
    opp_id = opportunity.get("opportunity_id") or opportunity.get("id") or ""

    try:
        from agents.linkedin_outreach import find_people_for_company
        rows, status = find_people_for_company(company, max_results=12)
        logger.debug("People search for %s: %s (%d rows)", company, status, len(rows))
        for row in rows:
            title = row.get("title") or row.get("Title") or ""
            role = _classify_role(title)
            contact = enrich_contact_warmth({
                "id": str(uuid.uuid4()),
                "opportunity_id": opp_id,
                "role": role,
                "name": row.get("name") or row.get("Name") or "",
                "title": title,
                "linkedin_url": row.get("linkedin_url") or row.get("LinkedIn URL") or "",
                "company": company,
            }, profile)
            contacts.append(contact)
    except Exception as exc:
        logger.debug("find_people_for_company failed for %s: %s", company, exc)

    if opportunity.get("person") or opportunity.get("post_url"):
        sig_contact = enrich_contact_warmth({
            "id": str(uuid.uuid4()),
            "opportunity_id": opp_id,
            "role": _classify_role(opportunity.get("title") or ""),
            "name": opportunity.get("person") or "",
            "title": opportunity.get("title") or "",
            "linkedin_url": opportunity.get("post_url") or "",
            "company": company,
        }, profile)
        contacts.insert(0, sig_contact)

    by_role: dict[str, list] = {r: [] for r, _ in POWER_TRIO}
    for c in contacts:
        role = c.get("role") or "peer"
        if role in by_role and len(by_role[role]) < max_per_role:
            by_role[role].append(c)

    trio = []
    for role, _label in POWER_TRIO:
        trio.extend(by_role.get(role, []))

    if opp_id and trio:
        try:
            from storage.opportunity_store import get_opportunity_store
            store = get_opportunity_store()
            for c in trio:
                store.upsert_contact(c)
        except Exception as exc:
            logger.debug("Contact persist failed: %s", exc)

    return trio


def resolve_and_enrich_job(job: dict, profile: str = "") -> dict:
    """Resolve Power Trio, pick best contact, re-run engine enrich with warmth."""
    trio = resolve_power_trio(job, profile=profile)
    best = trio[0] if trio else None
    if best:
        job["warm_lead_score"] = best.get("warm_lead_score")
        job["connection_strength"] = best.get("warm_lead_score")
    from agents.unified_engine import enrich_job_with_engine
    return enrich_job_with_engine(job, contact=best)

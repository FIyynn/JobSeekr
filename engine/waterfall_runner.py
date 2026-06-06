"""
Outreach waterfall state machine — levels 1→4 with human gates.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from agents.unified_engine import OUTREACH_LEVELS, plan_outreach_waterfall

logger = logging.getLogger("waterfall_runner")

ATTEMPT_STATUSES = (
    "pending",
    "draft_ready",
    "sent",
    "responded",
    "exhausted",
    "skipped",
)


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_initial_attempt(
    job: dict,
    contact: Optional[dict] = None,
    *,
    draft_message: str = "",
) -> dict:
    """Create or refresh the first waterfall attempt for an opportunity."""
    wf = plan_outreach_waterfall(job, contact)
    if wf.get("outreach_level") == 3 and contact and not contact.get("verified_email"):
        try:
            from engine.email_discovery import discover_work_email
            discovered = discover_work_email(
                contact.get("name") or "",
                job.get("company") or "",
            )
            if discovered.get("email"):
                contact = {**contact, **discovered}
                wf = plan_outreach_waterfall(job, contact)
        except Exception:
            pass
    opp_id = job.get("opportunity_id") or job.get("id") or str(uuid.uuid4())
    attempt = {
        "id": str(uuid.uuid4()),
        "opportunity_id": opp_id,
        "contact_id": (contact or {}).get("id") or "",
        "level": wf["outreach_level"],
        "channel": wf["outreach_channel"],
        "status": "draft_ready" if draft_message else "pending",
        "draft_message": draft_message,
        "human_gate_required": True,
        "notes": f"IPS={wf.get('ips', '')} stop_after={wf.get('outreach_stop_after')}",
    }
    try:
        from storage.opportunity_store import get_opportunity_store
        get_opportunity_store().upsert_outreach_attempt(attempt)
    except Exception as exc:
        logger.debug("Outreach attempt persist failed: %s", exc)
    return attempt


def get_next_action(opportunity_id: str) -> Optional[dict]:
    """Return the active outreach attempt or None."""
    try:
        from storage.opportunity_store import get_opportunity_store
        store = get_opportunity_store()
        attempt = store.get_active_outreach_attempt(opportunity_id)
        if attempt:
            return attempt
        opp = store.get_opportunity(opportunity_id)
        if not opp:
            return None
        job_like = {
            "company": opp.get("company"),
            "title": opp.get("title"),
            "sps": opp.get("sps"),
            "ips": opp.get("ips"),
            "opportunity_id": opportunity_id,
        }
        contacts = store.list_contacts(opportunity_id)
        contact = contacts[0] if contacts else None
        draft = ""
        try:
            from engine.social_listening import build_outreach_hook
            draft = build_outreach_hook(job_like, contact)
        except Exception:
            pass
        return plan_initial_attempt(job_like, contact, draft_message=draft)
    except Exception as exc:
        logger.debug("get_next_action failed: %s", exc)
        return None


def mark_attempt_sent(attempt_id: str, *, notes: str = "") -> bool:
    """Human marked message/connection as sent."""
    try:
        from storage.opportunity_store import get_opportunity_store
        store = get_opportunity_store()
        store.upsert_outreach_attempt({
            "id": attempt_id,
            "status": "sent",
            "attempted_at": _now_iso(),
            "notes": notes,
            "human_gate_required": True,
        })
        return True
    except Exception:
        return False


def mark_attempt_responded(attempt_id: str) -> bool:
    """Recipient responded — stop waterfall (credit recovery path for InMail)."""
    try:
        from storage.opportunity_store import get_opportunity_store
        store = get_opportunity_store()
        store.upsert_outreach_attempt({
            "id": attempt_id,
            "status": "responded",
            "response_at": _now_iso(),
        })
        return True
    except Exception:
        return False


def advance_waterfall(
    opportunity_id: str,
    *,
    contact: Optional[dict] = None,
    job: Optional[dict] = None,
) -> Optional[dict]:
    """
    Advance to next level when current level exhausted (no response / no path).
    Never auto-sends — returns draft for human review.
    """
    try:
        from storage.opportunity_store import get_opportunity_store
        store = get_opportunity_store()
        attempts = store.list_outreach_attempts(opportunity_id)
        current_level = max((a.get("level") or 1) for a in attempts) if attempts else 0

        if current_level >= 4:
            store.upsert_outreach_attempt({
                "id": str(uuid.uuid4()),
                "opportunity_id": opportunity_id,
                "level": 4,
                "channel": OUTREACH_LEVELS[4],
                "status": "exhausted",
                "human_gate_required": True,
                "notes": "Waterfall level 4 exhausted",
            })
            return None

        job_like = job or store.get_opportunity(opportunity_id) or {}
        job_like["opportunity_id"] = opportunity_id
        wf = plan_outreach_waterfall(job_like, contact)
        next_level = min(4, max(current_level + 1, wf["outreach_level"]))

        draft = ""
        try:
            from engine.social_listening import build_outreach_hook
            draft = build_outreach_hook(job_like, contact)
        except Exception:
            pass

        attempt = {
            "id": str(uuid.uuid4()),
            "opportunity_id": opportunity_id,
            "contact_id": (contact or {}).get("id") or "",
            "level": next_level,
            "channel": OUTREACH_LEVELS.get(next_level, OUTREACH_LEVELS[2]),
            "status": "draft_ready",
            "draft_message": draft,
            "human_gate_required": True,
        }
        store.upsert_outreach_attempt(attempt)
        return attempt
    except Exception as exc:
        logger.warning("advance_waterfall failed: %s", exc)
        return None


def queue_outreach_for_job(job: dict, profile: str = "") -> Optional[dict]:
    """Resolve stakeholders + plan first waterfall step for a job."""
    from engine.stakeholder_mapper import resolve_power_trio
    from engine.warm_lead import best_contact_for_job

    trio = resolve_power_trio(job, profile=profile)
    contact = best_contact_for_job(trio, profile)
    if contact and job.get("recommended_action") == "referral_first":
        job["referral_status"] = "requested"
    return plan_initial_attempt(job, contact)

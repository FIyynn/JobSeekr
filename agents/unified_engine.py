"""
Unified Job-Market & Outreach Engine

Two synchronized tracks:
  Track A — Visible Market (public listings → portal-aware apply)
  Track B — Hidden Market (signals → stakeholder mapping → outreach)

Computes SPS (Success Probability Score) and IPS (InMail Priority Score),
gates Easy Apply vs networking-only, blocks bespoke portals, and plans the
outreach waterfall. External actions always require human approval.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("unified_engine")

# ── Bespoke / networking-first employers (no unattended apply) ───────────────

BESPOKE_EMPLOYERS = {
    "mckinsey", "goldman sachs", "goldman", "bain", "bcg", "boston consulting",
    "blackrock", "jane street", "citadel", "two sigma", "de shaw", "d.e. shaw",
    "bridgewater", "point72", "millennium", "brevan howard", "winton",
    "abu dhabi investment authority", "adia", "abu dhabi investment council", "adic",
    "mubadala", "adq", "lunate", "qia", "qatar investment authority",
    "pif", "public investment fund", "gic", "temasek",
}

BESPOKE_URL_FRAGMENTS = (
    "mckinsey.com", "goldmansachs.com", "bain.com", "bcg.com",
    "blackrock.com", "janestreet.com", "citadel.com",
)

POWER_TRIO_ROLES = (
    ("hiring_manager", "Hiring Manager — likely role owner"),
    ("recruiter", "Recruiter / Talent — process owner"),
    ("peer", "Peer — potential future teammate"),
)

OUTREACH_LEVELS = {
    1: "free_in_platform_message",
    2: "connection_request",
    3: "email_discovery",
    4: "paid_inmail",
}


def _engine_config() -> dict:
    from config.engine_config import ENGINE_CONFIG
    return ENGINE_CONFIG


def detect_ats_platform(job: dict) -> str:
    """Detect ATS from apply URL / job URL."""
    try:
        from agents.form_filler import _detect_platform
        url = (
            job.get("job_url_direct")
            or job.get("job_url")
            or job.get("apply_url")
            or ""
        )
        return _detect_platform(url, page=None) or "ai_driven"
    except Exception:
        return "ai_driven"


def is_bespoke_portal(job: dict) -> bool:
    """McKinsey, Goldman, sovereign funds, etc. — networking only, no auto-apply."""
    company = (job.get("company") or "").lower()
    if any(b in company for b in BESPOKE_EMPLOYERS):
        return True
    urls = " ".join([
        job.get("job_url") or "",
        job.get("job_url_direct") or "",
        job.get("apply_url") or "",
    ]).lower()
    return any(frag in urls for frag in BESPOKE_URL_FRAGMENTS)


def is_linkedin_easy_apply_job(job: dict) -> bool:
    try:
        from agents.apply_method import is_linkedin_easy_apply
        return is_linkedin_easy_apply(job)
    except Exception:
        method = (job.get("apply_method") or "").lower()
        url = (job.get("job_url") or "").lower()
        return "easy apply" in method and "linkedin.com" in url


def parse_job_age_hours(job: dict) -> Optional[float]:
    """Estimate hours since posting from date_posted or description snippets."""
    raw = (job.get("date_posted") or "").strip()
    if raw:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
                return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
            except ValueError:
                continue
    text = " ".join([
        job.get("description") or "",
        job.get("title") or "",
    ]).lower()
    m = re.search(r"(\d+)\s*(minute|hour|day|week)s?\s+ago", text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {"minute": 1 / 60, "hour": 1, "day": 24, "week": 168}.get(unit, 24)
    return n * mult


def parse_applicant_count(job: dict) -> Optional[int]:
    """Parse LinkedIn-style applicant counts from job metadata or description."""
    for key in ("applicant_count", "num_applicants", "applicants"):
        val = job.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    text = " ".join([
        job.get("description") or "",
        job.get("apply_notes") or "",
        job.get("notes") or "",
    ]).lower()
    for pat in (
        r"(\d+)\s+applicants?",
        r"over\s+(\d+)\s+applicants?",
        r"(\d+)\s+people\s+clicked\s+apply",
        r"(\d+)\+?\s+applications?",
    ):
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    if "actively reviewing applicants" in text:
        return 200
    return None


def _tier1_company_boost(company: str) -> float:
    try:
        from config.applicant_requirements import TIER_1_TARGET_COMPANIES
        c = (company or "").lower()
        for t in TIER_1_TARGET_COMPANIES:
            if t.lower() in c or c in t.lower():
                return 90.0
    except Exception:
        pass
    return 50.0


def _breakdown_value(job: dict, key: str, default: float = 50.0) -> float:
    bd = job.get("score_breakdown") or {}
    if isinstance(bd, str):
        try:
            bd = json.loads(bd)
        except Exception:
            bd = {}
    val = bd.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def compute_role_fit(job: dict) -> float:
    """0–100 role fit from scorer output."""
    score = float(job.get("score") or 0)
    profile_fit = _breakdown_value(job, "profile_fit", score * 0.15)
    # profile_fit is 0–15 in breakdown; scale to 0–100
    scaled = min(100.0, profile_fit * (100 / 15))
    if job.get("matches_stated_targets") is False:
        scaled *= 0.85
    return round(min(100.0, max(0.0, (scaled * 0.6 + score * 0.4))), 1)


def compute_timing_signal(job: dict) -> float:
    """Fresh postings score higher."""
    cfg = _engine_config()
    max_h = float(cfg.get("easy_apply_max_hours") or 24)
    age = parse_job_age_hours(job)
    if age is None:
        return 55.0
    if age <= max_h:
        return 95.0
    if age <= max_h * 2:
        return 70.0
    if age <= max_h * 7:
        return 45.0
    return 25.0


def compute_hiring_urgency(job: dict) -> float:
    """Low applicant count = higher urgency to apply early."""
    cfg = _engine_config()
    max_applicants = int(cfg.get("easy_apply_max_applicants") or 50)
    count = parse_applicant_count(job)
    if count is None:
        return 60.0
    if count < max_applicants * 0.5:
        return 95.0
    if count < max_applicants:
        return 80.0
    if count < max_applicants * 2:
        return 45.0
    return 20.0


def compute_ats_match(job: dict) -> float:
    """Portal fit + Easy Apply availability."""
    platform = detect_ats_platform(job)
    if platform == "linkedin" and is_linkedin_easy_apply_job(job):
        return 90.0
    known = {
        "greenhouse", "lever", "ashby", "workday", "workable",
        "teamtailor", "icims", "smartrecruiters",
    }
    if platform in known:
        return 75.0
    if platform == "ai_driven":
        return 40.0
    return 55.0


def compute_connection_strength(job: dict, contact: Optional[dict] = None) -> float:
    """Warm lead score if present, else baseline."""
    if contact and contact.get("warm_lead_score") is not None:
        return float(contact["warm_lead_score"])
    wl = job.get("warm_lead_score")
    if wl is not None:
        return float(wl)
    return float(job.get("connection_strength") or 25.0)


def compute_outreach_quality(job: dict) -> float:
    """Draft message quality proxy — higher when we have hooks/angles."""
    q = 50.0
    if (job.get("fit_reason") or "").strip():
        q += 15.0
    if (job.get("positioning_angle") or "").strip():
        q += 10.0
    if job.get("message_angle") or job.get("outreach_hook"):
        q += 15.0
    return min(100.0, q)


def compute_sps(job: dict, contact: Optional[dict] = None) -> dict[str, Any]:
    """
    Success Probability Score (0–100).

    SPS = 0.20×RoleFit + 0.20×Connection + 0.15×ATS + 0.15×Timing
        + 0.10×Urgency + 0.10×CompanyPriority + 0.10×OutreachQuality
    """
    components = {
        "role_fit": compute_role_fit(job),
        "connection_strength": compute_connection_strength(job, contact),
        "ats_match": compute_ats_match(job),
        "timing_signal": compute_timing_signal(job),
        "hiring_urgency": compute_hiring_urgency(job),
        "company_priority": _tier1_company_boost(job.get("company") or ""),
        "outreach_quality": compute_outreach_quality(job),
    }
    weights = {
        "role_fit": 0.20,
        "connection_strength": 0.20,
        "ats_match": 0.15,
        "timing_signal": 0.15,
        "hiring_urgency": 0.10,
        "company_priority": 0.10,
        "outreach_quality": 0.10,
    }
    total = round(sum(components[k] * weights[k] for k in weights), 1)
    cfg = _engine_config()
    if total >= cfg.get("sps_immediate_action", 85):
        band = "immediate_action"
    elif total >= cfg.get("sps_apply_network", 70):
        band = "apply_and_network"
    elif total >= cfg.get("sps_network_only", 50):
        band = "network_only"
    else:
        band = "monitor"
    return {"sps": total, "sps_band": band, "sps_components": components}


def compute_ips(job: dict, contact: Optional[dict] = None) -> dict[str, Any]:
    """
    InMail Priority Score (0–100). Use paid InMail only if IPS ≥ threshold.
    """
    role_fit = compute_role_fit(job)
    contact_power = 50.0
    if contact:
        title = (contact.get("Person title") or contact.get("title") or "").lower()
        if any(t in title for t in ("recruiter", "talent", "hiring manager", "head of", "director", "partner")):
            contact_power = 85.0
        elif any(t in title for t in ("analyst", "associate", "engineer", "manager")):
            contact_power = 65.0
    warmth = compute_connection_strength(job, contact)
    components = {
        "role_fit": role_fit,
        "contact_power": contact_power,
        "timing_signal": compute_timing_signal(job),
        "company_priority": _tier1_company_boost(job.get("company") or ""),
        "warmth": warmth,
    }
    weights = {
        "role_fit": 0.30,
        "contact_power": 0.25,
        "timing_signal": 0.20,
        "company_priority": 0.15,
        "warmth": 0.10,
    }
    total = round(sum(components[k] * weights[k] for k in weights), 1)
    cfg = _engine_config()
    inmail_ok = total >= cfg.get("ips_inmail_threshold", 75)
    return {
        "ips": total,
        "ips_inmail_eligible": inmail_ok,
        "ips_components": components,
    }


def easy_apply_eligible(job: dict) -> tuple[bool, str]:
    """
    Module 1: Apply ONLY if age < 24h AND applicants < 50 (Easy Apply jobs).
    """
    if not is_linkedin_easy_apply_job(job):
        return True, "not_easy_apply"
    cfg = _engine_config()
    max_hours = float(cfg.get("easy_apply_max_hours") or 24)
    max_applicants = int(cfg.get("easy_apply_max_applicants") or 50)
    age = parse_job_age_hours(job)
    applicants = parse_applicant_count(job)
    if age is not None and age >= max_hours:
        return False, f"job age {age:.0f}h ≥ {max_hours:.0f}h — networking only"
    if applicants is not None and applicants >= max_applicants:
        return False, f"{applicants} applicants ≥ {max_applicants} — networking only"
    if age is None and applicants is None:
        return True, "easy_apply_fresh_unknown"
    return True, "easy_apply_fresh"


def determine_apply_mode(job: dict) -> tuple[str, str, str]:
    """
    Returns (apply_mode, engine_action, reason).

    apply_mode: apply | networking_only | referral_first | blocked_bespoke
    engine_action: human-readable next step
    """
    if is_bespoke_portal(job):
        return (
            "networking_only",
            "notify_networking",
            "Bespoke portal — no automated application; networking-first",
        )

    cfg = _engine_config()
    warm = float(job.get("warm_lead_score") or job.get("connection_strength") or 0)
    sps_data = job.get("sps") or compute_sps(job)["sps"]
    if (
        warm >= cfg.get("warm_lead_referral_threshold", 70)
        and sps_data >= cfg.get("sps_immediate_action", 85)
    ):
        return (
            "referral_first",
            "pause_for_referral",
            f"Warm lead score {warm:.0f} + SPS {sps_data:.0f} — referral before apply",
        )

    if is_linkedin_easy_apply_job(job):
        ok, reason = easy_apply_eligible(job)
        if not ok:
            return "networking_only", "stakeholder_outreach", reason

    sps_band = job.get("sps_band") or compute_sps(job)["sps_band"]
    if sps_band == "network_only":
        return (
            "networking_only",
            "stakeholder_outreach",
            f"SPS band {sps_band} — network only",
        )
    if sps_band == "monitor":
        return (
            "networking_only",
            "monitor",
            f"SPS band {sps_band} — monitor or ignore",
        )

    return "apply", "apply_or_network", "Eligible for application track"


def plan_outreach_waterfall(
    job: dict,
    contact: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Recommend outreach level (1–4). Engine drafts only — user sends.
    """
    contact = contact or {}
    cfg = _engine_config()
    ips_data = compute_ips(job, contact)
    ips = ips_data["ips"]

    if contact.get("open_profile") or contact.get("free_message_available"):
        level, channel = 1, OUTREACH_LEVELS[1]
        stop = True
    elif contact.get("shared_group"):
        level, channel = 1, OUTREACH_LEVELS[1]
        stop = True
    elif contact.get("is_connection") or contact.get("Outreach status") == "Accepted":
        level, channel = 1, OUTREACH_LEVELS[1]
        stop = True
    elif contact.get("verified_email"):
        level, channel = 3, OUTREACH_LEVELS[3]
        stop = True
    elif ips >= cfg.get("ips_inmail_threshold", 75):
        level, channel = 4, OUTREACH_LEVELS[4]
        stop = False
    else:
        level, channel = 2, OUTREACH_LEVELS[2]
        stop = False

    return {
        "outreach_level": level,
        "outreach_channel": channel,
        "outreach_stop_after": stop,
        "ips": ips,
        "ips_inmail_eligible": ips_data["ips_inmail_eligible"],
        "power_trio": [
            {"role": r, "description": d} for r, d in POWER_TRIO_ROLES
        ],
        "human_gate_required": True,
    }


def apply_engine_decision_gate(job: dict) -> dict:
    """
    Adjust decision for auto-apply queue based on engine rules.
    Does not downgrade confirmed applied rows.
    """
    if job.get("applied"):
        return job
    if job.get("apply_mode"):
        apply_mode = job["apply_mode"]
        engine_action = job.get("engine_action") or ""
        reason = job.get("engine_reason") or ""
    else:
        apply_mode, engine_action, reason = determine_apply_mode(job)
        job["apply_mode"] = apply_mode
        job["engine_action"] = engine_action
        job["engine_reason"] = reason

    if apply_mode in ("networking_only", "referral_first", "blocked_bespoke"):
        if job.get("decision") == "auto_apply":
            job["decision"] = "manual_review"
            prefix = f"[Engine: {apply_mode}] "
            job["fit_reason"] = prefix + (job.get("fit_reason") or reason)
            if apply_mode == "referral_first":
                job["skip_reason"] = ""
            else:
                job["apply_notes"] = (job.get("apply_notes") or "") or reason
    return job


REFERRAL_STATUSES = ("none", "requested", "referred", "declined", "timeout")


def enrich_job_with_engine(job: dict, contact: Optional[dict] = None) -> dict:
    """Main hook: attach SPS, IPS, apply mode, outreach plan to a job dict."""
    sps_data = compute_sps(job, contact)
    ips_data = compute_ips(job, contact)
    waterfall = plan_outreach_waterfall(job, contact)
    apply_mode, engine_action, reason = determine_apply_mode(job)
    track = (
        "hidden"
        if job.get("source") in ("employee_post", "web_indexed", "signal", "manual_import")
        else job.get("track") or "visible"
    )

    job.update({
        "sps": sps_data["sps"],
        "sps_band": sps_data["sps_band"],
        "ips": ips_data["ips"],
        "ips_inmail_eligible": ips_data["ips_inmail_eligible"],
        "apply_mode": apply_mode,
        "engine_action": engine_action,
        "engine_reason": reason,
        "ats_platform": detect_ats_platform(job),
        "bespoke_portal": is_bespoke_portal(job),
        "outreach_level": waterfall["outreach_level"],
        "outreach_channel": waterfall["outreach_channel"],
        "job_age_hours": parse_job_age_hours(job),
        "applicant_count": parse_applicant_count(job),
        "track": track,
        "referral_status": job.get("referral_status") or "none",
        "engine_json": json.dumps({
            "sps": sps_data,
            "ips": ips_data,
            "waterfall": waterfall,
            "track": track,
        }, ensure_ascii=False),
    })

    job = apply_engine_decision_gate(job)
    try:
        from engine.recommend_action import apply_recommendation_to_job
        return apply_recommendation_to_job(job, contact)
    except Exception as exc:
        logger.debug("recommend_action skipped: %s", exc)
        return job


def recommend_action(job: dict, contact: Optional[dict] = None) -> dict:
    """Public decision API — delegates to engine.recommend_action."""
    from engine.recommend_action import recommend_action as _rec
    return _rec(job, contact)


def job_eligible_for_auto_apply(job: dict) -> bool:
    """Gate for orchestrator / apply_jobs queue."""
    if job.get("decision") != "auto_apply":
        return False
    if job.get("applied"):
        return False
    action = (job.get("recommended_action") or "").lower()
    if action and action != "apply_now":
        return False
    try:
        from engine.recommend_action import referral_blocks_apply
        if referral_blocks_apply(job):
            return False
    except Exception:
        pass
    mode = job.get("apply_mode") or determine_apply_mode(job)[0]
    if mode in ("networking_only", "referral_first"):
        return False
    if is_bespoke_portal(job):
        return False
    if is_linkedin_easy_apply_job(job):
        ok, _ = easy_apply_eligible(job)
        if not ok:
            return False
    return True


def filter_apply_queue(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split queue into apply-eligible vs networking-deferred."""
    apply_list: list[dict] = []
    deferred: list[dict] = []
    for job in jobs:
        if job_eligible_for_auto_apply(job):
            apply_list.append(job)
        else:
            deferred.append(job)
    return apply_list, deferred

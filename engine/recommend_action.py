"""
Single decision brain: one recommended action per opportunity.

Preferred order:
  Hidden → Warm lead → Referral → Network → Apply (last resort)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from engine.opportunity import RecommendedAction, ReferralStatus, infer_track

logger = logging.getLogger("recommend_action")

REFERRAL_UNBLOCK = frozenset({
    ReferralStatus.REFERRED.value,
    ReferralStatus.DECLINED.value,
    ReferralStatus.TIMEOUT.value,
})


def recommend_action(
    job: dict,
    contact: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Return recommended_action and supporting metadata.

    Actions: referral_first | network_only | apply_now | monitor | ignore
    """
    from agents.unified_engine import (
        compute_sps,
        determine_apply_mode,
        easy_apply_eligible,
        is_bespoke_portal,
        is_linkedin_easy_apply_job,
    )

    decision = (job.get("decision") or "skip").lower()
    if decision == "skip":
        return _result(
            RecommendedAction.IGNORE.value,
            "Scored as skip",
            job,
        )
    if decision == "closed":
        return _result(
            RecommendedAction.IGNORE.value,
            "Job closed",
            job,
        )
    if job.get("applied"):
        return _result(
            RecommendedAction.IGNORE.value,
            "Already applied",
            job,
        )

    apply_mode = job.get("apply_mode") or ""
    engine_action = job.get("engine_action") or ""
    reason = job.get("engine_reason") or ""
    if not apply_mode:
        apply_mode, engine_action, reason = determine_apply_mode(job)

    sps_data = job.get("sps_band") and {"sps_band": job["sps_band"]} or compute_sps(job, contact)
    sps_band = job.get("sps_band") or sps_data.get("sps_band") or "monitor"
    track = infer_track(job)

    if apply_mode == "referral_first":
        return _result(
            RecommendedAction.REFERRAL_FIRST.value,
            reason,
            job,
            apply_mode=apply_mode,
            engine_action=engine_action,
            sps_band=sps_band,
            track=track,
        )

    if is_bespoke_portal(job) or apply_mode == "networking_only":
        if sps_band == "monitor":
            return _result(
                RecommendedAction.MONITOR.value,
                reason,
                job,
                apply_mode="networking_only",
                engine_action=engine_action,
                sps_band=sps_band,
                track=track,
            )
        return _result(
            RecommendedAction.NETWORK_ONLY.value,
            reason,
            job,
            apply_mode=apply_mode or "networking_only",
            engine_action=engine_action,
            sps_band=sps_band,
            track=track,
        )

    if decision != "auto_apply":
        if decision == "manual_review" and sps_band in ("immediate_action", "apply_and_network"):
            return _result(
                RecommendedAction.NETWORK_ONLY.value,
                f"Manual review band — network first ({reason})",
                job,
                apply_mode=apply_mode,
                engine_action="stakeholder_outreach",
                sps_band=sps_band,
                track=track,
            )
        return _result(
            RecommendedAction.MONITOR.value,
            f"Decision={decision}",
            job,
            apply_mode=apply_mode,
            engine_action=engine_action,
            sps_band=sps_band,
            track=track,
        )

    if track == "hidden" and sps_band in ("immediate_action", "apply_and_network"):
        return _result(
            RecommendedAction.NETWORK_ONLY.value,
            "Hidden opportunity — network before public apply",
            job,
            apply_mode="networking_only",
            engine_action="stakeholder_outreach",
            sps_band=sps_band,
            track=track,
        )

    referral = (job.get("referral_status") or ReferralStatus.NONE.value).lower()
    if referral == ReferralStatus.REQUESTED.value:
        return _result(
            RecommendedAction.REFERRAL_FIRST.value,
            "Referral requested — apply paused",
            job,
            apply_mode="referral_first",
            engine_action="pause_for_referral",
            sps_band=sps_band,
            track=track,
        )

    if is_linkedin_easy_apply_job(job):
        ok, ea_reason = easy_apply_eligible(job)
        if not ok:
            return _result(
                RecommendedAction.NETWORK_ONLY.value,
                ea_reason,
                job,
                apply_mode="networking_only",
                engine_action="stakeholder_outreach",
                sps_band=sps_band,
                track=track,
            )

    return _result(
        RecommendedAction.APPLY_NOW.value,
        reason or "Eligible for application",
        job,
        apply_mode=apply_mode or "apply",
        engine_action=engine_action or "apply_or_network",
        sps_band=sps_band,
        track=track,
    )


def referral_blocks_apply(job: dict) -> bool:
    """True when referral-first gate should block unattended apply."""
    action = job.get("recommended_action") or ""
    if action != RecommendedAction.REFERRAL_FIRST.value:
        return False
    status = (job.get("referral_status") or ReferralStatus.NONE.value).lower()
    return status not in REFERRAL_UNBLOCK


def apply_recommendation_to_job(
    job: dict,
    contact: Optional[dict] = None,
) -> dict:
    """Attach recommended_action and related fields to a job dict."""
    rec = recommend_action(job, contact)
    job.update({
        "recommended_action": rec["recommended_action"],
        "referral_status": job.get("referral_status") or "none",
        "track": rec.get("track") or infer_track(job),
        "engine_action": rec.get("engine_action") or job.get("engine_action", ""),
        "engine_reason": rec.get("reason", ""),
    })
    if rec["recommended_action"] in (
        RecommendedAction.NETWORK_ONLY.value,
        RecommendedAction.REFERRAL_FIRST.value,
        RecommendedAction.MONITOR.value,
    ):
        if job.get("decision") == "auto_apply" and not job.get("applied"):
            job["decision"] = "manual_review"
            prefix = f"[Engine: {rec['recommended_action']}] "
            job["fit_reason"] = prefix + (job.get("fit_reason") or rec.get("reason", ""))
    logger.debug(
        "Recommend %s @ %s → %s (%s)",
        job.get("title"),
        job.get("company"),
        rec["recommended_action"],
        rec.get("reason", "")[:60],
    )
    return job


def _result(
    action: str,
    reason: str,
    job: dict,
    *,
    apply_mode: str = "",
    engine_action: str = "",
    sps_band: str = "",
    track: str = "",
) -> dict[str, Any]:
    return {
        "recommended_action": action,
        "reason": reason,
        "apply_mode": apply_mode,
        "engine_action": engine_action,
        "sps_band": sps_band,
        "track": track or infer_track(job),
    }

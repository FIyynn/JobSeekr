"""
Unified Opportunity domain model — visible listings and hidden signals share one lifecycle.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Track(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"


class RecommendedAction(str, Enum):
    REFERRAL_FIRST = "referral_first"
    NETWORK_ONLY = "network_only"
    APPLY_NOW = "apply_now"
    MONITOR = "monitor"
    IGNORE = "ignore"


class ReferralStatus(str, Enum):
    NONE = "none"
    REQUESTED = "requested"
    REFERRED = "referred"
    DECLINED = "declined"
    TIMEOUT = "timeout"


HIDDEN_SOURCES = frozenset({"employee_post", "web_indexed", "signal", "manual_import"})


def infer_track(job_or_signal: dict) -> str:
    source = (job_or_signal.get("source") or "").lower()
    if source in HIDDEN_SOURCES or job_or_signal.get("signal_strength"):
        return Track.HIDDEN.value
    if job_or_signal.get("track"):
        return str(job_or_signal["track"])
    return Track.VISIBLE.value


@dataclass
class Opportunity:
    id: str = ""
    track: str = Track.VISIBLE.value
    source_ref: str = ""
    source_type: str = "job"
    company: str = ""
    title: str = ""
    location: str = ""
    job_url: str = ""
    job_url_direct: str = ""
    description: str = ""
    score: int = 0
    decision: str = "skip"
    sps: Optional[int] = None
    ips: Optional[int] = None
    recommended_action: str = RecommendedAction.MONITOR.value
    apply_mode: str = ""
    referral_status: str = ReferralStatus.NONE.value
    outreach_level: int = 0
    outreach_channel: str = ""
    job_id: Optional[int] = None
    signal_id: str = ""
    engine_json: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Opportunity":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


def job_to_opportunity(job: dict) -> Opportunity:
    """Build an Opportunity from a scored job dict."""
    opp_id = job.get("opportunity_id") or str(uuid.uuid4())
    engine = {}
    raw = job.get("engine_json") or ""
    if raw:
        try:
            engine = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            engine = {}
    track = engine.get("track") or infer_track(job)
    return Opportunity(
        id=opp_id,
        track=track,
        source_ref=str(job.get("job_url") or job.get("id") or ""),
        source_type="job",
        company=job.get("company") or "",
        title=job.get("title") or "",
        location=job.get("location") or "",
        job_url=job.get("job_url") or "",
        job_url_direct=job.get("job_url_direct") or "",
        description=(job.get("description") or "")[:8000],
        score=int(job.get("score") or 0),
        decision=job.get("decision") or "skip",
        sps=int(job["sps"]) if job.get("sps") is not None else None,
        ips=int(job["ips"]) if job.get("ips") is not None else None,
        recommended_action=job.get("recommended_action") or RecommendedAction.MONITOR.value,
        apply_mode=job.get("apply_mode") or "",
        referral_status=job.get("referral_status") or ReferralStatus.NONE.value,
        outreach_level=int(job.get("outreach_level") or 0),
        outreach_channel=job.get("outreach_channel") or "",
        job_id=job.get("id") or job.get("job_id"),
        engine_json=job.get("engine_json") or "",
    )


def signal_to_opportunity(signal: dict) -> Opportunity:
    """Normalize a hidden-market signal into an Opportunity."""
    opp_id = signal.get("opportunity_id") or signal.get("id") or str(uuid.uuid4())
    title = (
        signal.get("role_mentioned")
        or signal.get("title")
        or signal.get("hiring_language", "")[:80]
        or "Hidden opportunity"
    )
    return Opportunity(
        id=opp_id,
        track=Track.HIDDEN.value,
        source_ref=signal.get("id") or "",
        source_type="signal",
        company=signal.get("company") or "",
        title=title,
        location=signal.get("location_mentioned") or "",
        job_url=signal.get("post_url") or "",
        description=(signal.get("raw_snippet") or signal.get("hiring_language") or "")[:8000],
        score=int(signal.get("relevance_score") or 0),
        decision="manual_review",
        signal_id=signal.get("id") or "",
        engine_json=json.dumps({
            "track": Track.HIDDEN.value,
            "signal_strength": signal.get("signal_strength"),
            "cta": signal.get("cta"),
            "message_to_send": signal.get("message_to_send"),
        }, ensure_ascii=False),
    )


def opportunity_to_job_like(opp: Opportunity) -> dict:
    """Dict suitable for scorer / unified_engine hooks."""
    d = opp.to_dict()
    d["source"] = "signal" if opp.source_type == "signal" else "linkedin"
    d["id"] = opp.job_id
    d["job_id"] = opp.job_id
    return d

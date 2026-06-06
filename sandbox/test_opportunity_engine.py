"""
Offline tests for Opportunity model, recommend_action, and referral gate.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.opportunity import (
    RecommendedAction,
    job_to_opportunity,
    signal_to_opportunity,
    Track,
)
from engine.recommend_action import (
    apply_recommendation_to_job,
    recommend_action,
    referral_blocks_apply,
)
from storage.opportunity_store import OpportunityStore


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return ok


def test_recommend_skip():
    job = {"decision": "skip", "score": 40, "company": "X", "title": "Y"}
    rec = recommend_action(job)
    return check("skip to ignore", rec["recommended_action"], RecommendedAction.IGNORE.value)


def test_recommend_apply_now():
    job = {
        "decision": "auto_apply",
        "score": 80,
        "company": "Startup",
        "title": "Analyst",
        "source": "linkedin",
        "apply_method": "Apply",
        "sps": 75,
        "sps_band": "apply_and_network",
    }
    rec = recommend_action(job)
    return check(
        "auto_apply fresh to apply_now",
        rec["recommended_action"],
        RecommendedAction.APPLY_NOW.value,
    )


def test_referral_first_warm():
    job = {
        "decision": "auto_apply",
        "score": 90,
        "company": "Fund",
        "title": "PM",
        "warm_lead_score": 85,
        "sps": 90,
        "sps_band": "immediate_action",
        "apply_method": "Easy Apply",
        "job_url": "https://linkedin.com/jobs/view/1",
    }
    from agents.unified_engine import enrich_job_with_engine
    enrich_job_with_engine(job)
    rec = recommend_action(job)
    ok = check(
        "warm + high SPS to referral_first",
        rec["recommended_action"],
        RecommendedAction.REFERRAL_FIRST.value,
    )
    apply_recommendation_to_job(job)
    blocked_job = {**job, "recommended_action": RecommendedAction.REFERRAL_FIRST.value, "referral_status": "requested"}
    ok &= check(
        "referral blocks apply when requested",
        referral_blocks_apply(blocked_job),
        True,
    )
    ok &= check(
        "referral unblocks after referred",
        referral_blocks_apply({**job, "referral_status": "referred"}),
        False,
    )
    return ok


def test_stale_easy_apply_network():
    job = {
        "decision": "auto_apply",
        "score": 78,
        "company": "Co",
        "title": "Role",
        "apply_method": "Easy Apply",
        "job_url": "https://linkedin.com/jobs/view/2",
        "description": "Posted 3 days ago · 120 applicants",
        "date_posted": "2020-01-01",
    }
    from agents.unified_engine import enrich_job_with_engine
    enrich_job_with_engine(job)
    rec = recommend_action(job)
    return check(
        "stale Easy Apply to network_only",
        rec["recommended_action"],
        RecommendedAction.NETWORK_ONLY.value,
    )


def test_opportunity_store_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        store = OpportunityStore(db)
        job = {
            "id": 1,
            "company": "Acme",
            "title": "Engineer",
            "score": 70,
            "decision": "auto_apply",
            "recommended_action": "apply_now",
            "sps": 72,
            "track": "visible",
        }
        oid = store.upsert_from_job(job)
        row = store.get_opportunity(oid)
        ok = row is not None and row["company"] == "Acme"
        print(f"[{'PASS' if ok else 'FAIL'}] opportunity store roundtrip")
        return ok


def test_signal_to_opportunity():
    sig = {
        "id": "sig-1",
        "company": "GrowthCo",
        "role_mentioned": "Data Scientist",
        "signal_strength": "HIGH",
        "relevance_score": 80,
        "source": "manual_import",
    }
    opp = signal_to_opportunity(sig)
    ok = opp.track == Track.HIDDEN.value and opp.company == "GrowthCo"
    print(f"[{'PASS' if ok else 'FAIL'}] signal_to_opportunity track=hidden")
    return ok


def main() -> int:
    ok = True
    ok &= test_recommend_skip()
    ok &= test_recommend_apply_now()
    ok &= test_referral_first_warm()
    ok &= test_stale_easy_apply_network()
    ok &= test_opportunity_store_roundtrip()
    ok &= test_signal_to_opportunity()
    print("\n" + ("ALL PASSED" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

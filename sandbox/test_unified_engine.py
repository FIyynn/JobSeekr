"""Offline tests for unified job-market engine (SPS, IPS, apply gates)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.unified_engine import (
    easy_apply_eligible,
    enrich_job_with_engine,
    is_bespoke_portal,
    job_eligible_for_auto_apply,
    plan_outreach_waterfall,
)


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return ok


def main() -> int:
    ok = True

    bespoke = {
        "company": "McKinsey & Company",
        "job_url": "https://www.mckinsey.com/careers",
        "score": 88,
        "decision": "auto_apply",
    }
    ok &= check("McKinsey is bespoke", is_bespoke_portal(bespoke), True)
    enriched = enrich_job_with_engine(dict(bespoke))
    ok &= check("bespoke downgrades auto_apply", enriched["decision"], "manual_review")
    ok &= check("bespoke apply_mode", enriched["apply_mode"], "networking_only")

    fresh_ea = {
        "company": "Acme",
        "title": "Analyst",
        "job_url": "https://www.linkedin.com/jobs/view/123",
        "apply_method": "Easy Apply",
        "description": "Posted 2 hours ago · 12 applicants",
        "score": 80,
        "decision": "auto_apply",
    }
    ok &= check("fresh easy apply eligible", easy_apply_eligible(fresh_ea)[0], True)

    stale_ea = {
        **fresh_ea,
        "description": "Posted 3 days ago · 120 applicants",
    }
    ok &= check("stale easy apply blocked", easy_apply_eligible(stale_ea)[0], False)
    stale_enriched = enrich_job_with_engine({**stale_ea})
    ok &= check(
        "stale easy apply not in auto queue",
        job_eligible_for_auto_apply(stale_enriched),
        False,
    )

    wf = plan_outreach_waterfall(
        {"score": 85, "company": "Lunate"},
        {"open_profile": True},
    )
    ok &= check("open profile uses level 1", wf["outreach_level"], 1)
    ok &= check("waterfall requires human gate", wf["human_gate_required"], True)

    inmail = plan_outreach_waterfall(
        {"score": 90, "company": "Target Co"},
        {"Person title": "Recruiter"},
    )
    ok &= check("default starts at connection request", inmail["outreach_level"], 2)

    high_ips = plan_outreach_waterfall(
        {"score": 95, "company": "Elite Fund", "warm_lead_score": 80},
        {"Person title": "Managing Director"},
    )
    ok &= check(
        "high IPS may reach inmail level",
        high_ips["outreach_level"] in (2, 4),
        True,
    )

    print(f"\n{'All passed' if ok else 'Some failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

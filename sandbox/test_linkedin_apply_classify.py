"""Offline tests for LinkedIn apply URL unwrap and probe field mapping."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.linkedin_apply_probe import probe_result_to_fields, unwrap_linkedin_safety_url


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return ok


def main() -> int:
    ok = True
    safety = (
        "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fats.nichehrglobal.com"
        "%2Fcareers%2F5e75aa44-e9c3-44e7-88fe-48a5b9380004"
    )
    ok &= check(
        "unwrap safety/go URL",
        unwrap_linkedin_safety_url(safety).startswith("https://ats.nichehrglobal.com"),
        True,
    )
    fields = probe_result_to_fields({
        "type": "company_website",
        "label": "Apply on company website",
        "direct_url": "https://ats.example.com/job/1",
    })
    ok &= check("external -> Apply", fields["apply_method"], "Apply")
    ok &= check("external direct URL", fields["job_url_direct"], "https://ats.example.com/job/1")
    fields_ea = probe_result_to_fields({
        "type": "easy_apply",
        "label": "LinkedIn Apply to CRO at Confidential",
    })
    ok &= check("easy apply -> Easy Apply", fields_ea["apply_method"], "Easy Apply")
    ok &= check("easy apply clears direct", fields_ea["job_url_direct"], "")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Apply LIVE to three hand-picked pending jobs (Easy Apply / Greenhouse / Workday-or-custom).

Usage:
  python sandbox/test_apply_three.py
  python sandbox/test_apply_three.py --dry-run
  python sandbox/test_apply_three.py --ids 50,43,119
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

from config.env_settings import bootstrap_settings

bootstrap_settings()

from apply_jobs import _pick_resume, run_apply_batch  # noqa: E402
from agents.form_filler import apply_jobs_batch  # noqa: E402
from agents.job_logger import get_store, update_after_apply  # noqa: E402
from config.config import (  # noqa: E402
    APPLICATION_QA,
    CANDIDATE_PROFILE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_VISION_MODEL,
)

# Default test matrix: (id, platform label)
DEFAULT_TESTS = [
    (56, "linkedin_easy_apply"),  # athGADLANG — Financial Modeler
    (131, "external_apply_workday"),  # Parsons — Workday + resume autofill
]

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()


def load_jobs_by_ids(ids: list[int]) -> list[dict]:
    store = get_store()
    jobs = []
    for jid in ids:
        job = store.get_job(jid)
        if not job:
            print(f"  [MISSING] job id {jid}")
            continue
        job["job_id"] = jid
        job["_resume_path"] = _pick_resume(job.get("positioning_angle", "investments"))
        # Reset prior attempt flags for a clean test
        job["applied"] = False
        job["apply_notes"] = ""
        jobs.append(job)
    return jobs


def main():
    p = argparse.ArgumentParser(description="Test apply on 3 platform types")
    p.add_argument("--dry-run", action="store_true", help="Fill forms but do not submit")
    p.add_argument("--ids", type=str, default="", help="Comma-separated job IDs")
    p.add_argument("--validate-fit", action="store_true", help="Re-score before apply (slower)")
    args = p.parse_args()

    if args.ids.strip():
        spec = []
        for part in args.ids.split(","):
            part = part.strip()
            if part:
                spec.append((int(part), "custom"))
        tests = spec
    else:
        tests = DEFAULT_TESTS

    ids = [t[0] for t in tests]
    jobs = load_jobs_by_ids(ids)
    if not jobs:
        print("No jobs loaded.")
        return 1

    print("=" * 60)
    print(f"  APPLY TEST | {'DRY RUN' if args.dry_run else 'LIVE'} | {len(jobs)} job(s)")
    print("=" * 60)
    for (jid, label), job in zip(tests, jobs):
        print(f"  [{label}] id={jid}: {job.get('title')} @ {job.get('company')}")
        print(f"    {job.get('job_url', '')[:85]}")

    qa = {**APPLICATION_QA}
    qa["email"] = os.getenv("APPLICANT_EMAIL", qa.get("email", ""))
    qa["phone"] = os.getenv("APPLICANT_PHONE", qa.get("phone", ""))

    results = apply_jobs_batch(
        jobs=jobs,
        qa=qa,
        candidate_profile=CANDIDATE_PROFILE,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        dry_run=args.dry_run,
        headless=False,
        linkedin_email=LINKEDIN_EMAIL,
        linkedin_password=LINKEDIN_PASSWORD,
        vision_model=OLLAMA_VISION_MODEL or "llava",
        validate_fit=args.validate_fit,
    )

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    confirmed = 0
    for (jid, label), job in zip(tests, results):
        notes = job.get("apply_notes") or ""
        applied = bool(job.get("applied"))
        confirmed_submit = applied and "Submitted via" in notes
        if confirmed_submit:
            confirmed += 1
        status = "CONFIRMED" if confirmed_submit else ("DRY-RUN" if args.dry_run and applied else "NOT SUBMITTED")
        print(f"\n  [{label}] id={jid} — {status}")
        print(f"    applied={applied} decision={job.get('decision')}")
        print(f"    notes: {notes}")
        if not args.dry_run:
            update_after_apply(job)

    print(f"\n  Confirmed submissions: {confirmed}/{len(results)}")
    return 0 if confirmed == len(results) and not args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Dry-run LinkedIn Easy Apply on one or more job IDs (multi-step wizard exercise).

Usage:
  python sandbox/test_easy_apply_matrix.py --ids 56,120
  python sandbox/test_easy_apply_matrix.py --ids 56 --live-submit
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

from apply_jobs import _pick_resume  # noqa: E402
from agents.form_filler import apply_jobs_batch  # noqa: E402
from agents.job_logger import get_store, update_after_apply  # noqa: E402
from config.config import (  # noqa: E402
    APPLICATION_QA,
    CANDIDATE_PROFILE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_VISION_MODEL,
)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()


def load_jobs(ids: list[int]) -> list[dict]:
    store = get_store()
    jobs = []
    for jid in ids:
        job = store.get_job(jid)
        if not job:
            print(f"  [MISSING] job id {jid}")
            continue
        job["job_id"] = jid
        job["decision"] = "auto_apply"
        job["applied"] = False
        job["apply_notes"] = ""
        job["_resume_path"] = _pick_resume(job.get("positioning_angle", "investments"))
        jobs.append(job)
    return jobs


def main():
    p = argparse.ArgumentParser(description="LinkedIn Easy Apply matrix (by job ID)")
    p.add_argument("--ids", type=str, required=True, help="Comma-separated job IDs")
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="Fill wizard but do not submit (default)")
    p.add_argument("--live-submit", action="store_true", help="Submit for real")
    p.add_argument("--validate-fit", action="store_true", help="Re-score before apply")
    args = p.parse_args()

    ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
    jobs = load_jobs(ids)
    if not jobs:
        print("No jobs loaded.")
        return 1

    dry_run = not args.live_submit
    print(f"Easy Apply matrix: {len(jobs)} job(s), dry_run={dry_run}")

    qa = {**APPLICATION_QA}
    qa["email"] = os.getenv("APPLICANT_EMAIL", qa.get("email", ""))
    qa["phone"] = os.getenv("APPLICANT_PHONE", qa.get("phone", ""))

    results = apply_jobs_batch(
        jobs=jobs,
        qa=qa,
        candidate_profile=CANDIDATE_PROFILE,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        dry_run=dry_run,
        headless=False,
        linkedin_email=LINKEDIN_EMAIL,
        linkedin_password=LINKEDIN_PASSWORD,
        vision_model=OLLAMA_VISION_MODEL or "llava",
        validate_fit=args.validate_fit,
    )

    for job in results:
        jid = job.get("job_id", "?")
        print(
            f"\n--- Job {jid}: {job.get('title', '')} @ {job.get('company', '')} ---\n"
            f"  applied={job.get('applied')}\n"
            f"  decision={job.get('decision')}\n"
            f"  notes={job.get('apply_notes', '')[:240]}"
        )
        if not dry_run:
            update_after_apply(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

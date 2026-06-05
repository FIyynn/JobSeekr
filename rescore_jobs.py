"""
Re-score jobs in local storage (or Notion if STORAGE_BACKEND=notion).

  python rescore_jobs.py
  python rescore_jobs.py --auto-only --gcc-only
"""

import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("rescore")

from config.env_settings import bootstrap_settings
bootstrap_settings()

from config.config import CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS
from agents.scorer import score_job
from agents.job_fit import prefilter_job
from agents.job_logger import STORAGE_BACKEND, get_store
from storage.job_store import filter_gcc_jobs


def rescore_all(
    auto_only: bool = False,
    gcc_only: bool = False,
    limit: int = 0,
    progress_callback=None,
    pending_score_only: bool = False,
):
    if STORAGE_BACKEND == "notion":
        from rescore_notion import main as notion_main
        import sys as _sys
        _sys.argv = ["rescore_notion.py"]
        if auto_only:
            _sys.argv.append("--auto-only")
        if gcc_only:
            _sys.argv.append("--gcc-only")
        if limit:
            _sys.argv.extend(["--limit", str(limit)])
        notion_main()
        return

    store = get_store()
    jobs = [
        j for j in store.list_jobs(limit=2000)
        if not j.get("applied") and j.get("decision") != "applied"
    ]
    if pending_score_only:
        jobs = [j for j in jobs if j.get("decision") == "discovered"]
    elif auto_only:
        jobs = [j for j in jobs if j.get("decision") == "auto_apply"]
    if gcc_only:
        jobs = filter_gcc_jobs(jobs)
    if limit > 0:
        jobs = jobs[:limit]

    print(f"Re-scoring {len(jobs)} job(s) in local DB...\n")
    counts = {"auto_apply": 0, "manual_review": 0, "skip": 0}

    for i, job in enumerate(jobs, 1):
        title, company = job.get("title"), job.get("company")
        print(f"[{i}/{len(jobs)}] {title} @ {company}")

        if not job.get("description"):
            job["description"] = (
                f"Title: {title}\nCompany: {company}\n"
                f"Location: {job.get('location', '')}\n"
                f"(No full description stored.)"
            )

        blocked, reason = prefilter_job(job)
        if blocked:
            job.update(score=0, decision="skip", skip_reason=reason, fit_reason="")
            print(f"  -> SKIP (prefilter): {reason}")
        else:
            score_job(job, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS)
            print(f"  -> {job.get('score')}/100 {job.get('decision', '').upper()}")

        counts[job.get("decision", "skip")] = counts.get(job.get("decision", "skip"), 0) + 1
        store.upsert_job(job, skip_if_exists=False)
        if progress_callback:
            progress_callback()

    print("\n" + "=" * 50)
    print(f"  Auto Apply:     {counts.get('auto_apply', 0)}")
    print(f"  Manual Review:  {counts.get('manual_review', 0)}")
    print(f"  Skipped:        {counts.get('skip', 0)}")
    print("=" * 50)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--auto-only", action="store_true")
    p.add_argument("--pending-score-only", action="store_true")
    p.add_argument("--gcc-only", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    rescore_all(
        auto_only=args.auto_only,
        gcc_only=args.gcc_only,
        limit=args.limit,
        pending_score_only=args.pending_score_only,
    )


if __name__ == "__main__":
    main()

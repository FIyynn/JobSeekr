"""
Apply to pending jobs from local storage (default) or Notion.

  python apply_jobs.py --gcc-only           # dry run
  python apply_jobs.py --gcc-only --live    # submit
"""

import sys, os, argparse, logging, csv, tempfile
from pathlib import Path

# Force UTF-8 so Unicode log chars (arrows, dashes) don't crash the Windows cp1252 console
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("apply_jobs")

from config.env_settings import bootstrap_settings
bootstrap_settings()

from config.config import (
    CANDIDATE_PROFILE, APPLICATION_QA, RESUME_PATH,
    OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_VISION_MODEL,
)
from agents.form_filler import apply_jobs_batch, reconcile_confirmation_pending_jobs
from agents.job_fit import prefilter_job
from agents.job_logger import (
    fetch_confirmation_pending,
    fetch_pending_apply,
    merge_email_verify_retry_jobs,
    update_after_apply,
    STORAGE_BACKEND,
    get_store,
)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()

BASE = Path(__file__).parent
EASY_APPLY_CSV = BASE / "data" / "easy_apply_jobs.csv"
EASY_APPLY_TEMP_LIST = Path(tempfile.gettempdir()) / "jobhuntrr_easy_apply_urls.csv"
RESUME_BY_ANGLE = {
    "quant": str(BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "investments": str(BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "ai": str(BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "cyber": str(BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
}
DEFAULT_RESUME = str(BASE / "Rashed_Alneyadi_Resume.pdf")


def _pick_resume(angle: str) -> str:
    """Always use profile_settings resume unless user approved a tailored variant."""
    from config.apply_agent_rules import get_resume_path
    path = get_resume_path()
    if path and Path(path).exists():
        return path
    key = (angle or "").lower().split("/")[0].strip()
    path = RESUME_BY_ANGLE.get(key, DEFAULT_RESUME)
    return path if Path(path).exists() else DEFAULT_RESUME


from agents.apply_method import is_linkedin_easy_apply as _is_linkedin_easy_apply
from agents.apply_method import resolve_apply_method


def _normalize_job_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def _easy_apply_csv_paths() -> list[Path]:
    """CSV sources checked in order: persistent data file, then temp scan file."""
    paths = [EASY_APPLY_CSV, EASY_APPLY_TEMP_LIST]
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _read_easy_apply_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        logger.warning("Could not read Easy Apply CSV %s: %s", path, exc)
        return []


def _csv_row_should_apply(row: dict) -> bool:
    action = (row.get("action") or "").strip().lower()
    if action == "skip":
        return False
    if action == "apply":
        return True
    method = (
        row.get("method_resolved")
        or row.get("apply_method")
        or row.get("method")
        or row.get("method_display")
        or ""
    ).strip()
    if method != "Easy Apply":
        return False
    decision = (row.get("decision") or "").strip().lower()
    applied = (row.get("applied") or "").strip().lower()
    if applied in ("yes", "true", "1"):
        return False
    return decision in ("auto_apply", "")


def _job_from_csv_row(row: dict) -> dict:
    return {
        "job_url": (row.get("job_url") or "").strip(),
        "title": (row.get("title") or "").strip(),
        "company": (row.get("company") or "").strip(),
        "location": (row.get("location") or "").strip(),
        "apply_method": (row.get("apply_method") or row.get("method_resolved") or "Easy Apply").strip(),
        "job_url_direct": (row.get("job_url_direct") or "").strip(),
        "source": (row.get("source") or "linkedin").strip() or "linkedin",
        "decision": (row.get("decision") or "auto_apply").strip() or "auto_apply",
        "score": int(row.get("score") or 0),
        "positioning_angle": (row.get("positioning_angle") or "investments").strip(),
    }


def _resolve_job_for_csv_row(
    row: dict,
    *,
    pending_by_url: dict[str, dict] | None = None,
) -> dict | None:
    """
    Resolve a CSV row to a job from the search DB only.

    Never invent placeholder rows — avoids opening invalid /jobs/view/ URLs.
    """
    url = (row.get("job_url") or "").strip()
    if not url or "linkedin.com/jobs/view/" not in url.lower():
        return None
    store = get_store()
    key = _normalize_job_url(url)
    job = store.get_job_by_url(url)
    if not job and pending_by_url:
        job = pending_by_url.get(key)
    if not job:
        return None
    if not (job.get("title") or "").strip() or not (job.get("company") or "").strip():
        return None
    if not _is_linkedin_easy_apply(job):
        return None
    return job


def _resolve_jobs_from_easy_apply_csvs(
    paths: list[Path] | None = None,
    *,
    gcc_only: bool = False,
) -> list[dict]:
    """Scan CSV row-by-row; apply only rows marked Easy Apply from the search DB."""
    pending = fetch_pending_apply(gcc_only=gcc_only)
    pending_by_url = {
        _normalize_job_url(job.get("job_url", "")): job
        for job in pending
        if job.get("job_url")
    }
    selected: list[dict] = []
    seen_urls: set[str] = set()
    for path in paths or _easy_apply_csv_paths():
        rows = _read_easy_apply_csv(path)
        if not rows:
            continue
        logger.info("Easy Apply CSV: %d row(s) in %s", len(rows), path)
        for idx, row in enumerate(rows, 1):
            action = (row.get("action") or "").strip().lower()
            if action == "skip":
                logger.info(
                    "  [CSV row %d] skip: %s @ %s",
                    idx,
                    row.get("title", ""),
                    row.get("company", ""),
                )
                continue
            if not _csv_row_should_apply(row):
                continue
            job = _resolve_job_for_csv_row(row, pending_by_url=pending_by_url)
            if not job:
                logger.info(
                    "  [CSV row %d] skip (not in search DB or not Easy Apply): %s",
                    idx,
                    row.get("job_url", ""),
                )
                continue
            key = _normalize_job_url(job.get("job_url", ""))
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            logger.info(
                "  [CSV row %d] Easy Apply -> apply: %s @ %s",
                idx,
                job.get("title", ""),
                job.get("company", ""),
            )
            selected.append(job)
    return selected


def _run_search_and_score_for_easy_apply(
    *,
    gcc_only: bool = False,
    limit: int = 0,
    run_focus: str = "",
    headless: bool = False,
) -> None:
    """Discover and score jobs when the pending queue has no Easy Apply targets."""
    from orchestrator import run_pipeline

    print("Easy Apply: running job search + score...")
    run_pipeline(
        dry_run=True,
        apply_enabled=False,
        headless=headless,
        discover_limit=limit if limit > 0 else 50,
        gcc_only=gcc_only,
        run_focus=run_focus,
        include_previously_seen=False,
    )


def _refresh_easy_apply_csv(
    *,
    gcc_only: bool = False,
    limit: int = 0,
    run_focus: str = "",
    headless: bool = False,
) -> set[str]:
    """Search + score, verify LinkedIn methods, write data/easy_apply_jobs.csv."""
    _run_search_and_score_for_easy_apply(
        gcc_only=gcc_only,
        limit=limit,
        run_focus=run_focus,
        headless=headless,
    )
    pending = fetch_pending_apply(gcc_only=gcc_only)
    _verify_linkedin_easy_apply_methods(pending, headless=headless)
    pending = fetch_pending_apply(gcc_only=gcc_only)
    allowlist = _write_easy_apply_temp_list(pending)
    print(
        f"Easy Apply CSV refreshed at {EASY_APPLY_CSV}: "
        f"{len(pending)} row(s), {len(allowlist)} Easy Apply"
    )
    return allowlist


def _prepare_easy_apply_jobs(
    *,
    gcc_only: bool = False,
    limit: int = 0,
    run_focus: str = "",
    headless: bool = False,
    auto_search_if_empty: bool = True,
) -> list[dict]:
    """
    Easy Apply runs scan data/easy_apply_jobs.csv row-by-row.

    Search populates/refreshes the CSV. Apply reads action=apply rows only,
    resolving each URL against jobs already stored from search (no placeholders).
    """
    jobs = _resolve_jobs_from_easy_apply_csvs(gcc_only=gcc_only)
    if jobs:
        print(f"Easy Apply: {len(jobs)} job(s) queued from CSV scan")
        return jobs[:limit] if limit > 0 else jobs

    pending = fetch_pending_apply(gcc_only=gcc_only)
    if pending:
        print("Easy Apply: refreshing CSV from existing search queue...")
        _write_easy_apply_temp_list(pending)
        jobs = _resolve_jobs_from_easy_apply_csvs(gcc_only=gcc_only)
        if jobs:
            print(f"Easy Apply: {len(jobs)} job(s) queued from CSV after queue refresh")
            return jobs[:limit] if limit > 0 else jobs

    if auto_search_if_empty:
        print("Easy Apply: CSV empty or stale — running search to refresh CSV...")
        _refresh_easy_apply_csv(
            gcc_only=gcc_only,
            limit=limit,
            run_focus=run_focus,
            headless=headless,
        )
        jobs = _resolve_jobs_from_easy_apply_csvs(gcc_only=gcc_only)
        if jobs:
            print(f"Easy Apply: {len(jobs)} job(s) queued from CSV after search")
            return jobs[:limit] if limit > 0 else jobs

    print(
        "Easy Apply: CSV has no applyable Easy Apply rows. "
        "Run Search + score first, then Re-check LinkedIn apply methods."
    )
    return []


def _verify_linkedin_easy_apply_methods(
    jobs: list[dict],
    *,
    headless: bool = False,
    limit: int = 25,
) -> None:
    """Open LinkedIn job pages to confirm which pending rows are true Easy Apply."""
    candidates = [
        job for job in jobs
        if "linkedin.com/jobs" in (job.get("job_url") or "").lower()
        and resolve_apply_method(job) != "Easy Apply"
    ][: max(1, limit)]
    if not candidates:
        return
    try:
        from agents.linkedin_apply_probe import verify_linkedin_jobs_apply_methods
        print(f"Easy Apply: verifying apply method on {len(candidates)} LinkedIn job(s)...")
        verify_linkedin_jobs_apply_methods(
            candidates,
            limit=len(candidates),
            headless=headless,
            linkedin_email=LINKEDIN_EMAIL,
            linkedin_password=LINKEDIN_PASSWORD,
        )
    except Exception as exc:
        logger.warning("LinkedIn apply-method verification skipped: %s", exc)


def _write_easy_apply_temp_list(jobs: list[dict], path: Path | None = None) -> set[str]:
    """
    Write the Easy Apply scan CSV after search.

    Easy Apply apply runs read this file row-by-row (action=apply vs skip).
    """
    rows = []
    allowlist: set[str] = set()
    for idx, job in enumerate(jobs, 1):
        is_easy = _is_linkedin_easy_apply(job)
        url = _normalize_job_url(job.get("job_url", ""))
        if is_easy and url:
            allowlist.add(url)
        rows.append({
            "row": idx,
            "job_url": job.get("job_url", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "apply_method": job.get("apply_method", ""),
            "method_resolved": resolve_apply_method(job),
            "job_url_direct": job.get("job_url_direct", ""),
            "action": "apply" if is_easy else "skip",
        })

    out_path = Path(path or EASY_APPLY_CSV)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "row", "job_url", "title", "company", "apply_method",
                "method_resolved", "job_url_direct", "action",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    if out_path != EASY_APPLY_TEMP_LIST:
        try:
            EASY_APPLY_TEMP_LIST.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not mirror Easy Apply CSV to temp path: %s", exc)
    return allowlist


def export_easy_apply_scan_csv(path: str | Path, gcc_only: bool = False) -> tuple[Path, int, int]:
    """Export the current pending queue row scan CSV for Easy Apply-only review."""
    jobs = fetch_pending_apply(gcc_only=gcc_only)
    allowlist = _write_easy_apply_temp_list(jobs, Path(path))
    return Path(path), len(jobs), len(allowlist)


def _filter_to_easy_apply_temp_list(jobs: list[dict], allowlist: set[str]) -> list[dict]:
    return [
        job for job in jobs
        if _normalize_job_url(job.get("job_url", "")) in allowlist
        and _is_linkedin_easy_apply(job)
    ]


def _scan_jobs_for_easy_apply(jobs: list[dict], allowlist: set[str]) -> list[dict]:
    """Scan rows in order and keep only method == Easy Apply rows."""
    selected: list[dict] = []
    for idx, job in enumerate(jobs, 1):
        method = resolve_apply_method(job)
        title = job.get("title", "")
        company = job.get("company", "")
        url = job.get("job_url", "")
        if _normalize_job_url(url) in allowlist and _is_linkedin_easy_apply(job):
            print(f"  [ROW {idx}] Easy Apply -> apply: {title} @ {company}: {url}")
            selected.append(job)
        else:
            print(f"  [ROW {idx}] {method or '(unknown)'} -> skip: {title} @ {company}: {url}")
    return selected


def run_apply_batch(dry_run: bool = True, gcc_only: bool = False, limit: int = 0,
                    validate_fit: bool = True, headless: bool = False,
                    easy_apply_only: bool = False, run_focus: str = "",
                    auto_search_if_empty: bool = True):
    if STORAGE_BACKEND == "notion":
        from apply_from_notion import main as notion_main
        import sys as _sys
        _sys.argv = ["apply_from_notion.py"]
        if gcc_only:
            _sys.argv.append("--gcc-only")
        if not dry_run:
            _sys.argv.append("--live")
        if limit:
            _sys.argv.extend(["--limit", str(limit)])
        if not validate_fit:
            _sys.argv.append("--no-validate-fit")
        notion_main()
        return

    APPLICATION_QA["email"] = os.getenv("APPLICANT_EMAIL", APPLICATION_QA.get("email", ""))
    APPLICATION_QA["phone"] = os.getenv("APPLICANT_PHONE", APPLICATION_QA.get("phone", ""))

    easy_apply_allowlist: set[str] = set()
    if easy_apply_only:
        jobs = _prepare_easy_apply_jobs(
            gcc_only=gcc_only,
            limit=limit,
            run_focus=run_focus,
            headless=headless,
            auto_search_if_empty=auto_search_if_empty,
        )
        easy_apply_allowlist = {
            _normalize_job_url(job.get("job_url", ""))
            for job in jobs
            if job.get("job_url")
        }
    else:
        jobs = fetch_pending_apply(gcc_only=gcc_only)
        if not dry_run:
            jobs = merge_email_verify_retry_jobs(jobs)

    if easy_apply_only and not dry_run:
        jobs = merge_email_verify_retry_jobs(jobs)

    uncertain = fetch_confirmation_pending()
    if easy_apply_only:
        uncertain_before = len(uncertain)
        uncertain = _filter_to_easy_apply_temp_list(uncertain, easy_apply_allowlist)
        skipped = uncertain_before - len(uncertain)
        if skipped:
            print(f"Easy Apply only: skipped {skipped} non-Easy-Apply reconciliation job(s)")
    if uncertain:
        print(f"Reconciling uncertain submissions: {len(uncertain)} job(s)")
        reconciled = reconcile_confirmation_pending_jobs(
            uncertain,
            headless=headless,
            linkedin_email=LINKEDIN_EMAIL,
            linkedin_password=LINKEDIN_PASSWORD,
        )
        for job in reconciled:
            update_after_apply(job)
    print(f"Pending Auto Apply: {len(jobs)} job(s)")

    kept = []
    for job in jobs:
        blocked, reason = prefilter_job(job)
        if blocked:
            print(f"  [SKIP] {job['title']} @ {job['company']}: {reason}")
            jid = job.get("id") or job.get("job_id")
            if jid:
                get_store().update_job(
                    jid, decision="skip", skip_reason=reason, notes=f"Prefilter: {reason}"
                )
        else:
            kept.append(job)
    jobs = kept
    if limit > 0:
        jobs = jobs[:limit]
    if not jobs:
        print("Nothing to apply.")
        return

    from agents.unified_engine import enrich_job_with_engine, job_eligible_for_auto_apply
    engine_kept = []
    for job in jobs:
        enrich_job_with_engine(job)
        if job_eligible_for_auto_apply(job):
            engine_kept.append(job)
        else:
            mode = job.get("apply_mode") or "networking_only"
            reason = job.get("engine_reason") or mode
            print(f"  [ENGINE {mode}] {job.get('title')} @ {job.get('company')}: {reason}")
            jid = job.get("id") or job.get("job_id")
            if jid:
                get_store().update_job(
                    jid,
                    decision=job.get("decision") or "manual_review",
                    notes=f"Engine: {reason}",
                )
    jobs = engine_kept
    if not jobs:
        print("Nothing eligible after unified engine gate (networking-only / referral-first).")
        return

    if easy_apply_only:
        for job in jobs:
            job["_easy_apply_only_run"] = True

    li_email, li_password = LINKEDIN_EMAIL, LINKEDIN_PASSWORD
    if not li_email or not li_password:
        logger.warning("LinkedIn credentials missing - relying on saved session or skipping LinkedIn jobs")

    for job in jobs:
        job["_resume_path"] = _pick_resume(job.get("positioning_angle", "investments"))
        job["job_id"] = job.get("id") or job.get("job_id")

    qa = {**APPLICATION_QA}
    results = apply_jobs_batch(
        jobs=jobs, qa=qa, candidate_profile=CANDIDATE_PROFILE,
        model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
        dry_run=dry_run, headless=headless,
        linkedin_email=li_email, linkedin_password=li_password,
        vision_model=OLLAMA_VISION_MODEL or "", validate_fit=validate_fit,
    )

    for job in results:
        update_after_apply(job)
        status = "APPLIED" if job.get("applied") else ("DRY-RUN" if dry_run else "FAILED")
        print(f"  [{status}] {job.get('title')} @ {job.get('company')}: {job.get('apply_notes', '')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--gcc-only", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-validate-fit", action="store_true")
    p.add_argument("--easy-apply-only", action="store_true")
    args = p.parse_args()
    print("=" * 60)
    print(f"  Apply | Storage: {STORAGE_BACKEND} | {'LIVE' if args.live else 'DRY RUN'}")
    print("=" * 60)
    run_apply_batch(
        dry_run=not args.live,
        gcc_only=args.gcc_only,
        limit=args.limit,
        validate_fit=not args.no_validate_fit,
        easy_apply_only=args.easy_apply_only,
    )


if __name__ == "__main__":
    main()

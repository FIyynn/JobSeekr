"""
JobHunter Orchestrator
Runs the full pipeline: Discover → Score → Log to Notion → Apply

Schedule: every N hours via APScheduler (Windows-compatible)
Run: python orchestrator.py
"""

import os
import sys

# Force UTF-8 on Windows console so Unicode chars (arrows, ticks, etc.) don't crash cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

# Load account settings from data/profile_settings.json (no .env)
from config.env_settings import bootstrap_settings
bootstrap_settings()

# ── Logging setup ──────────────────────────────────────────────────────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("orchestrator")

# ── Config ─────────────────────────────────────────────────────────────────────
from config.config import (
    SEARCH_QUERIES, SEARCH_SITES, SEARCH_HOURS_FRESH,
    BLOCKED_COMPANIES, BLOCKED_KEYWORDS, BLOCKED_JOB_TITLES, MAX_YEARS_REQUIRED,
    MAX_JOBS_PER_RUN, OLLAMA_MODEL, OLLAMA_VISION_MODEL, OLLAMA_BASE_URL,
    CANDIDATE_PROFILE, SCORE_THRESHOLDS, APPLICATION_QA,
    RUN_EVERY_HOURS,
)

from agents.discovery    import discover_jobs
from agents.scorer       import score_jobs_batch
from agents.job_logger import (
    fetch_confirmation_pending,
    fetch_pending_apply,
    get_store,
    log_jobs_batch,
    merge_email_verify_retry_jobs,
    persist_search_results,
    update_after_apply,
    STORAGE_BACKEND,
)
from agents.form_filler import (
    apply_jobs_batch,
    reconcile_confirmation_pending_jobs,
)

# Load secrets from env
NOTION_TOKEN       = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
APPLICANT_EMAIL    = os.getenv("APPLICANT_EMAIL", "")
APPLICANT_PHONE    = os.getenv("APPLICANT_PHONE", "")
LINKEDIN_EMAIL     = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD  = os.getenv("LINKEDIN_PASSWORD", "")


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value not in ("0", "false", "no", "off")


def _env_int(key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _matches_search_plan(job: dict, queries: list[dict]) -> bool:
    """Keep broad ATS feeds from flooding the scorer with unrelated roles."""
    text = f"{job.get('title', '')}\n{job.get('description', '')}".lower()
    stop_words = {
        "and", "or", "the", "for", "with", "jobs", "job", "role", "roles",
        "uae", "dubai", "abu", "dhabi", "gcc", "graduate", "junior", "senior",
    }
    for query in queries or []:
        term = (query.get("term") or "").strip().lower()
        if not term:
            continue
        if term in text:
            return True
        tokens = [
            t for t in re.findall(r"[a-z0-9+#]+", term)
            if len(t) > 2 and t not in stop_words
        ]
        if tokens and sum(1 for token in tokens if token in text) >= min(2, len(tokens)):
            return True
    return False


def _prefer_target_geo(job: dict) -> int:
    location = (job.get("location") or "").lower()
    target_tokens = (
        "united arab emirates", "uae", "abu dhabi", "dubai", "sharjah",
        "qatar", "saudi", "riyadh", "bahrain", "kuwait", "oman",
        "middle east", "mena", "gcc", "remote",
    )
    return 0 if any(token in location for token in target_tokens) else 1


def _filter_secondary_jobs(
    candidates: list[dict],
    *,
    search_queries: list[dict],
    seen_urls: set,
    cutoff_date: datetime,
    limit: int,
) -> list[dict]:
    """Apply shared job filters to native ATS/career-crawler additions."""
    from agents.job_fit import prefilter_job

    accepted: list[dict] = []
    for job in sorted(candidates or [], key=_prefer_target_geo):
        if limit and len(accepted) >= limit:
            break
        url = job.get("job_url") or ""
        if not url or url in seen_urls:
            continue
        if job.get("date_posted"):
            try:
                posted = datetime.fromisoformat(str(job["date_posted"]))
                if posted < cutoff_date:
                    continue
            except Exception:
                pass
        if not _matches_search_plan(job, search_queries):
            continue
        blocked, reason = prefilter_job(
            job,
            blocked_companies=BLOCKED_COMPANIES,
            blocked_keywords=BLOCKED_KEYWORDS,
            blocked_titles=BLOCKED_JOB_TITLES,
            max_years=MAX_YEARS_REQUIRED,
        )
        if blocked:
            logger.debug(
                "  Secondary SKIP [%s]: %s @ %s",
                reason,
                job.get("title", ""),
                job.get("company", ""),
            )
            continue
        seen_urls.add(url)
        accepted.append(job)
    return accepted


def _prompt_linkedin_credentials() -> tuple[str, str]:
    """
    Ask for LinkedIn credentials interactively unless both are in profile settings.
    Always prompts if either value is missing — session state doesn't matter.
    """
    email    = LINKEDIN_EMAIL
    password = LINKEDIN_PASSWORD

    # Only skip prompt if BOTH are already configured in profile settings
    if email and password:
        logger.info(f"LinkedIn: using credentials from profile settings ({email})")
        return email, password

    logger.warning("LinkedIn credentials missing - relying on saved session or skipping LinkedIn jobs")
    return email, password


# Inject email/phone into QA
APPLICATION_QA["email"] = APPLICANT_EMAIL
APPLICATION_QA["phone"] = APPLICANT_PHONE

# ── Tailored resume paths ──────────────────────────────────────────────────────
_BASE = Path(__file__).parent
RESUME_BY_ANGLE = {
    "quant":       str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "investments": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "pe":          str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "finance":     str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "trading":     str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "commodities": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "energy":      str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "ai":          str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "data":        str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "ml":          str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "research":    str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "space":       str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "defense":     str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "cyber":       str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "fintech":     str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "startup":     str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "strategy":    str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "climate":     str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
}
_DEFAULT_RESUME = str(_BASE / "Rashed_Alneyadi_Resume.pdf")

def _pick_resume(angle: str) -> str:
    """Return the tailored resume path for a given positioning angle."""
    key = (angle or "").lower().split("/")[0].strip()
    path = RESUME_BY_ANGLE.get(key, _DEFAULT_RESUME)
    return path if Path(path).exists() else _DEFAULT_RESUME

# URL dedup across runs (persisted to disk)
SEEN_URLS_FILE = Path("data/seen_urls.json")
SEEN_URLS_FILE.parent.mkdir(exist_ok=True)


def _load_seen_urls() -> set:
    if SEEN_URLS_FILE.exists():
        try:
            return set(json.loads(SEEN_URLS_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_seen_urls(seen: set):
    SEEN_URLS_FILE.write_text(json.dumps(list(seen), indent=2))


def _merge_apply_queue(new_jobs: list[dict], *, gcc_only: bool = False) -> list[dict]:
    """Combine newly scored jobs with the persisted retry queue without duplicates."""
    from agents.unified_engine import enrich_job_with_engine, job_eligible_for_auto_apply

    queued = fetch_pending_apply(gcc_only=gcc_only)
    merged: list[dict] = []
    seen: set[str] = set()
    deferred = 0
    for job in [*new_jobs, *queued]:
        enrich_job_with_engine(job)
        if not job_eligible_for_auto_apply(job):
            if job.get("decision") == "auto_apply":
                deferred += 1
            continue
        key = job.get("job_url_direct") or job.get("job_url") or str(job.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(job)
    if deferred:
        logger.info(
            f"  Engine deferred {deferred} job(s) to networking-only / referral-first"
        )
    return merged


def _apply_queued_jobs(
    jobs: list[dict],
    *,
    candidate_profile: str,
    dry_run: bool,
    headless: bool,
    validate_fit: bool,
) -> list[dict]:
    """Apply a persisted/new queue and write outcomes for jobs already in storage."""
    li_email, li_password = _prompt_linkedin_credentials()
    uncertain = fetch_confirmation_pending()
    if uncertain:
        logger.info(f"  Reconciling {len(uncertain)} uncertain submission(s)")
        reconciled = reconcile_confirmation_pending_jobs(
            uncertain,
            headless=headless,
            linkedin_email=li_email or LINKEDIN_EMAIL,
            linkedin_password=li_password or LINKEDIN_PASSWORD,
        )
        for job in reconciled:
            update_after_apply(job)

    # ── Retry email-verify pending jobs (Workday account creation that hit email wall) ──
    if not dry_run:
        jobs = merge_email_verify_retry_jobs(jobs)

    if not jobs:
        logger.info("  No pending auto-apply jobs")
        return []

    logger.info(f"  {len(jobs)} job(s) queued | mode: {'DRY RUN' if dry_run else 'LIVE'}")
    for job in jobs:
        angle = job.get("positioning_angle", "investments")
        job["_resume_path"] = _pick_resume(angle)
        job["job_id"] = job.get("job_id") or job.get("id")
        logger.info(
            f"  [{job.get('score', '?')}/100] {job.get('title', '')} @ "
            f"{job.get('company', '')} | resume: {Path(job['_resume_path']).name}"
        )

    results = apply_jobs_batch(
        jobs=jobs,
        qa=APPLICATION_QA,
        candidate_profile=candidate_profile,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        dry_run=dry_run,
        headless=headless,
        linkedin_email=li_email or LINKEDIN_EMAIL,
        linkedin_password=li_password or LINKEDIN_PASSWORD,
        vision_model=OLLAMA_VISION_MODEL,
        validate_fit=validate_fit,
    )
    for job in results:
        update_after_apply(job)
    return results


# ── Main run ───────────────────────────────────────────────────────────────────

def run_pipeline(
    dry_run: bool = True,
    apply_enabled: bool = False,
    headless: bool = False,
    discover_limit: int = 0,
    auto_enrich: bool = True,
    validate_fit: bool = True,
    include_previously_seen: bool = False,
    gcc_only: bool = False,
    progress_callback=None,
    run_focus: str = "",
):
    """
    Full pipeline run.

    Args:
        dry_run:        Fill forms but don't click submit (safe default)
        apply_enabled:  If True, fill applications and submit when dry_run is False
        headless:       Run browser headless (True) or visible (False)
        progress_callback: Optional callback after search results are locally visible
    """
    run_start = datetime.utcnow()
    logger.info("=" * 60)
    logger.info(f"JobHunter run started at {run_start.isoformat()}")
    logger.info(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'} | Apply: {apply_enabled}")
    logger.info(f"  Storage: {STORAGE_BACKEND} | Validate fit: {validate_fit}")
    if run_focus:
        logger.info(f"  Run focus: {run_focus[:300]}")
    logger.info("=" * 60)

    # ── 0. Optional auto profile enrich ───────────────────────────────────────
    if auto_enrich:
        try:
            from agents.profile_manager import maybe_auto_enrich_profile
            maybe_auto_enrich_profile(headless=headless)
        except Exception as e:
            logger.warning(f"Auto profile enrich skipped: {e}")

    # ── 1. Load seen URLs ──────────────────────────────────────────────────────
    persisted_seen_urls = _load_seen_urls()
    seen_urls = set() if include_previously_seen else set(persisted_seen_urls)
    logger.info(f"Loaded {len(persisted_seen_urls)} previously seen URLs")
    if include_previously_seen:
        logger.info("Previously seen listings will be revisited during discovery")

    # ── 2. Discovery ───────────────────────────────────────────────────────────
    logger.info("\n-- DISCOVERY ----------------------------------------------")
    from agents.search_planner import resolve_search_queries
    search_queries = resolve_search_queries(
        SEARCH_QUERIES,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        run_focus=run_focus,
    )
    logger.info(f"Search plan: {len(search_queries)} queries")
    cutoff_date = datetime.utcnow() - timedelta(hours=SEARCH_HOURS_FRESH)
    secondary_cap = _env_int("WEB_SIGNAL_MAX_RESULTS", 15)

    jobs = discover_jobs(
        queries=search_queries,
        sites=SEARCH_SITES,
        hours_fresh=SEARCH_HOURS_FRESH,
        blocked_companies=BLOCKED_COMPANIES,
        blocked_keywords=BLOCKED_KEYWORDS,
        blocked_titles=BLOCKED_JOB_TITLES,
        max_years=MAX_YEARS_REQUIRED,
        max_results=discover_limit if discover_limit > 0 else MAX_JOBS_PER_RUN,
        dedup_seen=seen_urls,
    )
    logger.info(f"Discovered {len(jobs)} new jobs via jobspy")

    # ── 2a. Native ATS feed fetcher (Greenhouse / Lever / Ashby public APIs) ──
    # Zero-cost, no API keys, queries publisher-controlled sources before
    # aggregators pick them up. Controlled by WEB_SIGNAL_SEARCH flag.
    if _env_flag("WEB_SIGNAL_SEARCH", default=True):
        try:
            from agents.ats_feed_fetcher import fetch_all_employers
            ats_candidates = fetch_all_employers()
            ats_jobs = _filter_secondary_jobs(
                ats_candidates,
                search_queries=search_queries,
                seen_urls=seen_urls,
                cutoff_date=cutoff_date,
                limit=secondary_cap,
            )
            if ats_jobs:
                jobs.extend(ats_jobs)
            logger.info(
                "  Native ATS feeds: +%d accepted from %d candidates",
                len(ats_jobs),
                len(ats_candidates),
            )
        except Exception as e:
            logger.warning(f"Native ATS feed fetch skipped: {e}")

    # ── 2b. Career page crawler (sitemap + JobPosting JSON-LD) ───────────────
    # For custom/Workday/Taleo employers in the registry that have no public API.
    if _env_flag("WEB_SIGNAL_SEARCH", default=True):
        try:
            from agents.career_page_crawler import crawl_all_custom_employers
            crawl_candidates = crawl_all_custom_employers()
            crawled_jobs = _filter_secondary_jobs(
                crawl_candidates,
                search_queries=search_queries,
                seen_urls=seen_urls,
                cutoff_date=cutoff_date,
                limit=secondary_cap,
            )
            if crawled_jobs:
                jobs.extend(crawled_jobs)
            logger.info(
                "  Career page crawl: +%d accepted from %d candidates",
                len(crawled_jobs),
                len(crawl_candidates),
            )
        except Exception as e:
            logger.warning(f"Career page crawler skipped: {e}")

    # ── 2c. Web signal discovery (LinkedIn posts + hidden ATS via search) ─────
    try:
        from agents.web_signal_discovery import discover_hiring_signals
        signals = discover_hiring_signals(
            queries=search_queries,
            days_fresh=SEARCH_HOURS_FRESH // 24 + 1,
            max_results=secondary_cap,
            dedup_seen=None,
        )
        signal_jobs = _filter_secondary_jobs(
            signals,
            search_queries=search_queries,
            seen_urls=seen_urls,
            cutoff_date=cutoff_date,
            limit=secondary_cap,
        )
        if signal_jobs:
            jobs.extend(signal_jobs)
        logger.info(
            "  Web signals: +%d accepted from %d candidates",
            len(signal_jobs),
            len(signals),
        )
    except Exception as e:
        logger.warning(f"Web signal discovery skipped: {e}")

    _save_seen_urls(persisted_seen_urls | seen_urls)

    if jobs:
        preview_results = persist_search_results(jobs)
        logger.info(
            f"  Visible in Jobs: {preview_results['logged']} new | "
            f"{preview_results['dedup_skipped']} already present | "
            f"{preview_results['failed']} failed"
        )
        if progress_callback:
            try:
                progress_callback()
            except Exception as e:
                logger.debug(f"Search-result refresh callback skipped: {e}")

    if not jobs:
        logger.info("No new jobs this run.")
        if apply_enabled:
            logger.info("\n-- APPLYING PERSISTED QUEUE -------------------------------")
            from config.md_loader import get_candidate_profile_for_prompt
            from config.config import reload_candidate_profile
            candidate_profile = get_candidate_profile_for_prompt() or reload_candidate_profile()
            queued = _merge_apply_queue([], gcc_only=gcc_only)
            results = _apply_queued_jobs(
                queued,
                candidate_profile=candidate_profile,
                dry_run=dry_run,
                headless=headless,
                validate_fit=validate_fit,
            )
            return _summary(results, run_start)
        return _summary([], run_start)

    # ── 2b. Description enrichment (crawl4ai / requests fallback) ─────────────
    # Jobs whose descriptions are thin (<400 chars) get a full-page fetch so the
    # scorer LLM has real signal instead of jobspy boilerplate.
    try:
        from agents.page_reader import enrich_jobs_descriptions
        jobs = enrich_jobs_descriptions(jobs, max_workers=3)
    except Exception as e:
        logger.warning(f"Description enrichment skipped: {e}")

    # ── 3. Scoring ─────────────────────────────────────────────────────────────
    logger.info("\n-- SCORING ------------------------------------------------")
    from config.md_loader import get_candidate_profile_for_prompt
    from config.config import reload_candidate_profile
    candidate_profile = get_candidate_profile_for_prompt() or reload_candidate_profile()
    if run_focus:
        candidate_profile += (
            "\n\n## Current run focus (temporary Jobs-tab instruction)\n"
            + run_focus.strip()[:2000]
        )

    def _persist_scored_job(scored_job: dict) -> None:
        """Expose each fresh decision in Jobs while the remaining rows are scored."""
        get_store().log_jobs_batch([scored_job], update_existing=True)
        if progress_callback:
            progress_callback()

    jobs = score_jobs_batch(
        jobs=jobs,
        candidate_profile=candidate_profile,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        score_thresholds=SCORE_THRESHOLDS,
        progress_callback=_persist_scored_job,
    )

    # Bucketize
    auto_apply_jobs   = [j for j in jobs if j["decision"] == "auto_apply"]
    manual_review_jobs = [j for j in jobs if j["decision"] == "manual_review"]
    skip_jobs         = [j for j in jobs if j["decision"] == "skip"]
    excluded_jobs     = [j for j in jobs if j["decision"] == "excluded"]

    if gcc_only and auto_apply_jobs:
        from storage.job_store import filter_gcc_jobs
        before = len(auto_apply_jobs)
        auto_apply_jobs = filter_gcc_jobs(auto_apply_jobs)
        skipped = before - len(auto_apply_jobs)
        if skipped:
            logger.info(
                f"  GCC filter: {skipped} non-GCC auto-apply job(s) held for manual review"
            )
            gcc_urls = {j.get("job_url") for j in auto_apply_jobs}
            for j in jobs:
                if j.get("decision") == "auto_apply" and j.get("job_url") not in gcc_urls:
                    j["decision"] = "manual_review"
                    j["apply_notes"] = (j.get("apply_notes") or "") + (
                        " Non-GCC location — not auto-applied."
                    ).strip()

    logger.info(f"  Auto-apply:    {len(auto_apply_jobs)}")
    logger.info(f"  Manual review: {len(manual_review_jobs)}")
    logger.info(f"  Skip:          {len(skip_jobs)}")

    # ── 4. Applications ────────────────────────────────────────────────────────
    if apply_enabled:
        logger.info("\n-- APPLYING -----------------------------------------------")
        queued = _merge_apply_queue(auto_apply_jobs, gcc_only=gcc_only)
        applied_results = _apply_queued_jobs(
            queued,
            candidate_profile=candidate_profile,
            dry_run=dry_run,
            headless=headless,
            validate_fit=validate_fit,
        )
        # Include persisted retry outcomes in the summary and merge new-job outcomes.
        applied_map = {j.get("job_url"): j for j in applied_results}
        jobs = [applied_map.get(j.get("job_url"), j) for j in jobs]
        new_urls = {j.get("job_url") for j in jobs}
        jobs.extend(j for j in applied_results if j.get("job_url") not in new_urls)
    else:
        logger.info("Application engine disabled - set apply_enabled=True to activate")

    # ── 5. Job logging (local SQLite default, or Notion) ─────────────────────
    logger.info(f"\n-- JOB LOGGING ({STORAGE_BACKEND}) ------------------------------")
    log_results = log_jobs_batch(jobs=jobs)
    logger.info(
        f"  Logged: {log_results['logged']} | "
        f"Dedup skipped: {log_results['dedup_skipped']} | "
        f"Failed: {log_results['failed']}"
    )
    if STORAGE_BACKEND == "local":
        from storage.job_store import JobStore
        logger.info(f"  Database: {JobStore().db_path}")

    return _summary(jobs, run_start)


def _summary(jobs: list, run_start: datetime) -> dict:
    """Print and return a run summary."""
    run_end  = datetime.utcnow()
    duration = (run_end - run_start).total_seconds()

    auto_apply    = [j for j in jobs if j.get("decision") == "auto_apply"]
    manual_review = [j for j in jobs if j.get("decision") == "manual_review"]
    applied       = [
        j for j in jobs
        if j.get("applied") and j.get("submission_status") == "confirmed"
    ]
    skipped       = [j for j in jobs if j.get("decision") == "skip"]

    # Top 5 by score
    top5 = sorted(jobs, key=lambda j: j.get("score") or 0, reverse=True)[:5]

    logger.info("\n" + "=" * 60)
    logger.info("DAILY SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Run duration:      {duration:.0f}s")
    logger.info(f"  Total discovered:  {len(jobs)}")
    logger.info(f"  Applications submitted: {len(applied)}")
    logger.info(f"  Saved for review:  {len(manual_review)}")
    logger.info(f"  Skipped:           {len(skipped)}")
    logger.info(f"\n  TOP 5 OPPORTUNITIES:")
    for j in top5:
        logger.info(
            f"    [{j.get('score', '?')}/100] {j.get('title', '')} @ "
            f"{j.get('company', '')} — {j.get('location', '')} "
            f"({j.get('decision', '')})"
        )
    logger.info("=" * 60 + "\n")

    return {
        "total":      len(jobs),
        "applied":    len(applied),
        "review":     len(manual_review),
        "skipped":    len(skipped),
        "top5":       top5,
        "duration_s": duration,
    }


# ── Scheduler ──────────────────────────────────────────────────────────────────

def run_scheduled(
    dry_run: bool = True,
    apply_enabled: bool = False,
    headless: bool = False,
    discover_limit: int = 0,
    auto_enrich: bool = True,
    validate_fit: bool = True,
    include_previously_seen: bool = False,
):
    """Start the scheduler — runs every RUN_EVERY_HOURS hours indefinitely."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install apscheduler")
        return

    scheduler = BlockingScheduler(timezone="Asia/Dubai")

    scheduler.add_job(
        func=run_pipeline,
        trigger="interval",
        hours=RUN_EVERY_HOURS,
        kwargs={
            "dry_run": dry_run,
            "apply_enabled": apply_enabled,
            "headless": headless,
            "discover_limit": discover_limit,
            "auto_enrich": auto_enrich,
            "validate_fit": validate_fit,
            "include_previously_seen": include_previously_seen,
        },
        id="jobhunter",
        name="JobHunter Pipeline",
        max_instances=1,
        next_run_time=datetime.now(),  # run immediately on start
    )

    logger.info(f"Scheduler started - running every {RUN_EVERY_HOURS} hours")
    logger.info("Press Ctrl+C to stop")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")


    pipe_kw = dict(
        dry_run=dry_run,
        apply_enabled=apply_enabled,
        headless=headless,
        discover_limit=args.limit,
        auto_enrich=not args.no_auto_enrich,
        validate_fit=not args.no_validate_fit,
        include_previously_seen=args.include_previously_seen,
    )

    if args.run_once:
        run_pipeline(**pipe_kw)
    else:
        run_scheduled(**pipe_kw)

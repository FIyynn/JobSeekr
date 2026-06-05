"""
Unified job logging: local SQLite (default) or Notion (optional).
Set STORAGE_BACKEND=local or notion in Profile Settings (profile_settings.json)
"""

import os
import logging

logger = logging.getLogger("job_logger")

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()


def get_store():
    """Return local JobStore instance."""
    from storage.job_store import JobStore
    return JobStore()


def persist_search_results(jobs: list[dict]) -> dict:
    """Mirror raw discovery results locally so the GUI can show them immediately."""
    discovered_jobs = []
    for job in jobs:
        preview = dict(job)
        preview["decision"] = "discovered"
        preview["decision_display"] = "Pending Score"
        discovered_jobs.append(preview)
    return get_store().log_jobs_batch(discovered_jobs)


def log_jobs_batch(jobs: list[dict]) -> dict:
    # The GUI always reads local SQLite, even when Notion is the configured backend.
    # Replace the initial Discovered rows with their final scored/apply state.
    local_results = get_store().log_jobs_batch(jobs, update_existing=True)
    if STORAGE_BACKEND == "notion":
        from agents.notion_logger import log_jobs_batch as notion_log
        token = os.getenv("NOTION_TOKEN", "")
        db_id = os.getenv("NOTION_DATABASE_ID", "")
        if not token or not db_id:
            logger.warning("Notion selected but NOTION_TOKEN/NOTION_DATABASE_ID missing — using local")
            return local_results
        return notion_log(token, db_id, jobs)
    return local_results


def fetch_pending_apply(gcc_only: bool = False) -> list[dict]:
    if STORAGE_BACKEND == "notion":
        from apply_from_notion import fetch_pending_jobs, filter_gcc_jobs, dedupe_jobs
        jobs = dedupe_jobs(fetch_pending_jobs())
        if gcc_only:
            jobs = filter_gcc_jobs(jobs)
        return jobs
    from storage.job_store import dedupe_jobs, filter_gcc_jobs
    jobs = get_store().fetch_pending_apply(gcc_only=False)
    jobs = dedupe_jobs(jobs)
    if gcc_only:
        jobs = filter_gcc_jobs(jobs)
    return jobs


def fetch_confirmation_pending(max_checks: int = 3) -> list[dict]:
    """Load click-only outcomes that need an evidence-only revisit."""
    if STORAGE_BACKEND == "notion":
        return []
    return get_store().fetch_confirmation_pending(max_checks=max_checks)


def merge_email_verify_retry_jobs(
    jobs: list[dict],
    *,
    max_retries: int = 3,
) -> list[dict]:
    """Append bounded email-verification retries without duplicating normal queue jobs."""
    if STORAGE_BACKEND == "notion":
        return list(jobs)
    try:
        from agents.account_signup import (
            clear_email_verify_pending,
            pop_email_verify_pending,
        )
        retry_items = pop_email_verify_pending(max_retries=max_retries)
    except Exception as exc:
        logger.debug("Email-verification retry queue unavailable: %s", exc)
        return list(jobs)

    merged = list(jobs)
    seen = {
        job.get("job_url_direct") or job.get("job_url") or str(job.get("id") or "")
        for job in merged
    }
    store = get_store()
    for item in retry_items:
        job_id = item.get("job_id")
        retry_job = store.get_job(job_id) if job_id else None
        if not retry_job:
            continue
        if retry_job.get("applied"):
            clear_email_verify_pending(retry_job)
            continue
        if retry_job.get("decision") not in ("auto_apply", "manual_review"):
            continue
        key = (
            retry_job.get("job_url_direct")
            or retry_job.get("job_url")
            or str(retry_job.get("id") or "")
        )
        if not key or key in seen:
            continue
        retry_job["job_id"] = retry_job.get("job_id") or retry_job.get("id")
        merged.append(retry_job)
        seen.add(key)
        logger.info(
            "Re-queued email-verification job: %s @ %s",
            retry_job.get("title", ""),
            retry_job.get("company", ""),
        )
    return merged


def update_after_apply(job: dict):
    """Persist apply result to storage."""
    job_id = job.get("job_id") or job.get("id")
    notes = job.get("apply_notes", "") or job.get("notes", "")
    applied = bool(job.get("applied"))
    submission_status = job.get("submission_status", "") or ""
    if applied and submission_status not in ("confirmed", "manual_confirmed"):
        logger.error(
            "Refusing to persist applied=True without confirmation evidence: %s @ %s",
            job.get("title", ""),
            job.get("company", ""),
        )
        applied = False
        job.update({
            "applied": False,
            "decision": "manual_review",
            "submission_status": "confirmation_pending",
        })
        notes = (
            notes
            or "Submission outcome lacked confirmation evidence - deferred to prevent duplicate submission"
        )
    if (
        not applied
        and int(job.get("apply_attempts") or 0) >= 3
        and job.get("decision") == "auto_apply"
        and submission_status != "dry_run"
    ):
        job["decision"] = "manual_review"
        notes = (
            notes
            or "Automatic apply stopped after three unsuccessful live attempts"
        )

    if STORAGE_BACKEND == "notion" and job.get("notion_page_id"):
        from apply_from_notion import update_notion_row
        dec = "skip" if job.get("decision") == "skip" else ("applied" if applied else "")
        update_notion_row(job["notion_page_id"], applied, notes, decision=dec)
        return

    if job_id:
        store = get_store()
        if applied:
            try:
                from agents.account_signup import clear_email_verify_pending
                clear_email_verify_pending(job)
            except Exception as exc:
                logger.debug("Email-verification queue cleanup failed: %s", exc)
            store.mark_applied(
                job_id,
                notes,
                applied=True,
                submission_status=submission_status,
                submission_confirmed_at=job.get("submission_confirmed_at", ""),
                confirmation_url=job.get("confirmation_url", ""),
                confirmation_text=job.get("confirmation_text", ""),
                confirmation_checks=job.get("confirmation_checks", 0),
                last_confirmation_check_at=job.get("last_confirmation_check_at", ""),
                apply_attempts=job.get("apply_attempts", 0),
                last_apply_attempt_at=job.get("last_apply_attempt_at", ""),
                apply_method=job.get("apply_method", ""),
                job_url_direct=job.get("job_url_direct", ""),
            )
        else:
            update_kwargs = dict(
                notes=notes,
                apply_notes=notes,
                applied=0,
                decision=job.get("decision", "auto_apply"),
                submission_status=job.get("submission_status", ""),
                submission_confirmed_at=job.get("submission_confirmed_at", ""),
                confirmation_url=job.get("confirmation_url", ""),
                confirmation_text=job.get("confirmation_text", ""),
                confirmation_checks=job.get("confirmation_checks", 0),
                last_confirmation_check_at=job.get("last_confirmation_check_at", ""),
                apply_attempts=job.get("apply_attempts", 0),
                last_apply_attempt_at=job.get("last_apply_attempt_at", ""),
            )
            # Persist skip_reason so skipped jobs don't re-enter the pending queue
            if job.get("decision") == "skip" and job.get("skip_reason"):
                update_kwargs["skip_reason"] = job["skip_reason"]
            store.update_job(job_id, **update_kwargs)

"""
Canonical apply-method resolution for JobHuntrr.

Single source of truth for the Jobs table "Method" column and Easy Apply-only
apply runs. Discovery writes granular values; legacy rows may still store
"LinkedIn" or blanks — resolve_apply_method() normalizes those for display
and filtering.
"""

from __future__ import annotations

_CANONICAL = frozenset({"Easy Apply", "Apply", "ATS"})
_LEGACY_UNVERIFIED = frozenset({"linkedin", "linked in"})


def _job_source(job: dict) -> str:
    src = (job.get("source") or "").strip().lower()
    if src:
        return src
    url = (job.get("job_url") or "").lower()
    if "linkedin.com" in url:
        return "linkedin"
    return src


def _is_linkedin_jobs_url(job: dict) -> bool:
    return "linkedin.com/jobs" in (job.get("job_url") or "").strip().lower()


def resolve_apply_method(job: dict) -> str:
    """
    Return the effective apply method: Easy Apply | Apply | ATS | other stored text.
    """
    stored = (job.get("apply_method") or "").strip()
    if stored in _CANONICAL:
        return stored
    if stored.lower() in _LEGACY_UNVERIFIED:
        return "Apply" if (job.get("job_url_direct") or "").strip() else "LinkedIn"

    src = _job_source(job)
    has_direct = bool((job.get("job_url_direct") or "").strip())

    if src == "linkedin" or _is_linkedin_jobs_url(job):
        return "Apply" if has_direct else "LinkedIn"

    if has_direct:
        return "ATS"
    return stored or ""


def is_linkedin_easy_apply(job: dict) -> bool:
    """True when this row should be included in LinkedIn Easy Apply-only runs."""
    if not _is_linkedin_jobs_url(job):
        return False
    if (job.get("job_url_direct") or "").strip():
        return False
    return resolve_apply_method(job) == "Easy Apply"


def normalize_stored_apply_method(job: dict) -> str:
    """Return the value that should be persisted in apply_method."""
    resolved = resolve_apply_method(job)
    return resolved if resolved in _CANONICAL else (job.get("apply_method") or "").strip()

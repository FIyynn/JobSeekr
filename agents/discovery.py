"""
Discovery Agent
Finds fresh jobs using JobSpy plus public indexed hiring signals.
Returns raw job list for scoring.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger("discovery")

try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
except ImportError:
    JOBSPY_AVAILABLE = False
    logger.warning("jobspy not installed. Run: pip install python-jobspy")


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value not in ("0", "false", "no", "off")


def _env_int(key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _is_blocked(job: dict, blocked_companies: list, blocked_keywords: list,
                blocked_titles: list, max_years: int) -> tuple[bool, str]:
    """Return (blocked, reason) for a job dict."""
    from agents.job_fit import prefilter_job
    return prefilter_job(
        job,
        blocked_companies=blocked_companies,
        blocked_keywords=blocked_keywords,
        blocked_titles=blocked_titles,
        max_years=max_years,
    )


def _normalize_job(row) -> dict:
    """Convert a jobspy DataFrame row into our standard job dict."""
    def safe(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ""
        return str(val).strip()

    return {
        "title":        safe(row.get("title")),
        "company":      safe(row.get("company")),
        "location":     safe(row.get("location")),
        "description":  safe(row.get("description")),
        "job_url":      safe(row.get("job_url")),
        "date_posted":  safe(row.get("date_posted")),
        "source":       safe(row.get("site")),
        "apply_method": (
            "Apply" if safe(row.get("site", "")).lower() == "linkedin" and safe(row.get("job_url_direct"))
            else (
                "LinkedIn" if safe(row.get("site", "")).lower() == "linkedin"
                else ("ATS" if safe(row.get("job_url_direct")) else "Apply")
            )
        ),
        "job_url_direct": safe(row.get("job_url_direct")),
        "emails":       safe(row.get("emails")),
        "company_url":  safe(row.get("company_url")),
        # enriched later
        "score":        None,
        "decision":     None,
        "skip_reason":  "",
        "fit_reason":   "",
        "applied":      False,
        "discovered_at": datetime.utcnow().isoformat(),
    }


def _scrape_sites_resilient(
    sites: list[str],
    *,
    term: str,
    location: str,
    hours_fresh: int,
) -> pd.DataFrame:
    """Keep working providers when JobSpy rejects or fails one requested site."""
    frames = []
    for site in ([sites] if isinstance(sites, str) else sites):
        try:
            frame = scrape_jobs(
                site_name=[site],
                search_term=term,
                location=location,
                results_wanted=15,
                hours_old=hours_fresh,
                country_indeed="united arab emirates",
                linkedin_fetch_description=True,
            )
        except Exception as exc:
            logger.warning(f"JobSpy site skipped [{site}] for '{term}' / '{location}': {exc}")
            continue
        if frame is not None and not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _append_if_eligible(
    job: dict,
    all_jobs: list[dict],
    dedup_seen: set,
    cutoff_date: datetime,
    blocked_companies: list,
    blocked_keywords: list,
    blocked_titles: list,
    max_years: int,
) -> bool:
    """Apply shared dedup, recency, and hard-filter rules to one discovered job."""
    url = job.get("job_url") or ""
    if url in dedup_seen or not url:
        return False
    dedup_seen.add(url)

    if job.get("date_posted"):
        try:
            posted = datetime.fromisoformat(str(job["date_posted"]))
            if posted < cutoff_date:
                return False
        except Exception:
            pass  # can't parse date - include anyway

    blocked, reason = _is_blocked(
        job, blocked_companies, blocked_keywords, blocked_titles, max_years
    )
    if blocked:
        logger.debug(f"  SKIP [{reason}]: {job['title']} @ {job['company']}")
        return False

    all_jobs.append(job)
    return True


def discover_jobs(
    queries: list[dict],
    sites: list[str],
    hours_fresh: int,
    blocked_companies: list,
    blocked_keywords: list,
    blocked_titles: list,
    max_years: int,
    max_results: int = 60,
    dedup_seen: Optional[set] = None,
) -> list[dict]:
    """
    Run all search queries and return a deduplicated, filtered list of job dicts.

    Args:
        queries:           List of {"term": ..., "location": ...}
        sites:             e.g. ["linkedin", "glassdoor", "indeed"]
        hours_fresh:       Only include jobs posted within N hours
        blocked_companies: Company names to skip
        blocked_keywords:  Keywords in title/desc that trigger skip
        blocked_titles:    Job title fragments to skip
        max_years:         Skip if role requires more years
        max_results:       Total cap across all queries
        dedup_seen:        Set of job URLs already processed (mutated in-place)

    Returns:
        List of normalized job dicts, filtered and deduplicated.
    """
    web_signals_enabled = _env_flag("WEB_SIGNAL_SEARCH", default=True)
    if not JOBSPY_AVAILABLE and not web_signals_enabled:
        raise RuntimeError(
            "jobspy is not installed and WEB_SIGNAL_SEARCH is disabled. "
            "Run: pip install python-jobspy"
        )

    if dedup_seen is None:
        dedup_seen = set()

    all_jobs: list[dict] = []
    cutoff_date = datetime.utcnow() - timedelta(hours=hours_fresh)
    jobspy_limit = max_results
    if web_signals_enabled and len(all_jobs) < max_results:
        try:
            from agents.web_signal_discovery import discover_web_signals
            signal_jobs = discover_web_signals(
                queries,
                max_results=min(_env_int("WEB_SIGNAL_MAX_RESULTS", 15), max_results),
                max_queries=_env_int("WEB_SIGNAL_MAX_QUERIES", 6),
                days_fresh=max(1, (hours_fresh + 23) // 24),
            )
            for job in signal_jobs:
                _append_if_eligible(
                    job,
                    all_jobs,
                    dedup_seen,
                    cutoff_date,
                    blocked_companies,
                    blocked_keywords,
                    blocked_titles,
                    max_years,
                )
                if len(all_jobs) >= max_results:
                    break
        except Exception as e:
            logger.warning(f"Indexed web-signal discovery skipped: {e}")

    if not JOBSPY_AVAILABLE:
        jobspy_limit = 0
        logger.warning("JobSpy unavailable - running indexed web-signal discovery only")

    for query in queries:
        if len(all_jobs) >= jobspy_limit:
            break
        try:
            from gui.stop_flag import check_stop
            check_stop("Stop requested — halting discovery")
        except ImportError:
            pass

        term     = query["term"]
        location = query["location"]
        logger.info(f"Searching: '{term}' in '{location}' across {sites}")

        try:
            df = _scrape_sites_resilient(
                sites,
                term=term,
                location=location,
                hours_fresh=hours_fresh,
            )
        except Exception as e:
            logger.error(f"Jobspy error for '{term}' / '{location}': {e}")
            continue

        if df is None or df.empty:
            logger.info(f"  -> No results")
            continue

        logger.info(f"  -> {len(df)} raw results")

        for _, row in df.iterrows():
            job = _normalize_job(row.to_dict())
            url = job["job_url"]

            if url in dedup_seen or not url:
                continue
            dedup_seen.add(url)

            if job["date_posted"]:
                try:
                    posted = datetime.fromisoformat(str(job["date_posted"]))
                    if posted < cutoff_date:
                        continue
                except Exception:
                    pass

            blocked, reason = _is_blocked(
                job, blocked_companies, blocked_keywords, blocked_titles, max_years
            )
            if blocked:
                logger.debug(f"  SKIP [{reason}]: {job['title']} @ {job['company']}")
                continue

            all_jobs.append(job)

            if len(all_jobs) >= jobspy_limit:
                break

    logger.info(f"Discovery complete: {len(all_jobs)} jobs after filtering")
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from config.config import (
        SEARCH_QUERIES, SEARCH_SITES, SEARCH_HOURS_FRESH,
        BLOCKED_COMPANIES, BLOCKED_KEYWORDS, BLOCKED_JOB_TITLES,
        MAX_YEARS_REQUIRED, MAX_JOBS_PER_RUN
    )
    jobs = discover_jobs(
        queries=SEARCH_QUERIES,
        sites=SEARCH_SITES,
        hours_fresh=SEARCH_HOURS_FRESH,
        blocked_companies=BLOCKED_COMPANIES,
        blocked_keywords=BLOCKED_KEYWORDS,
        blocked_titles=BLOCKED_JOB_TITLES,
        max_years=MAX_YEARS_REQUIRED,
        max_results=MAX_JOBS_PER_RUN,
    )
    for j in jobs[:5]:
        print(f"  [{j['source']}] {j['title']} @ {j['company']} — {j['location']}")
        print(f"    {j['job_url']}")

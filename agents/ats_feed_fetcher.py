"""
agents/ats_feed_fetcher.py — Native ATS feed fetcher (zero-cost, no API keys required).

Fetches jobs directly from public ATS endpoints:
  - Greenhouse:  boards-api.greenhouse.io/v1/boards/{slug}/jobs (unauthenticated GET)
  - Lever:       jobs.lever.co/{slug} (public posting API v0)
  - Ashby:       api.ashbyhq.com/posting-api/job-board/{slug} (public)

These are publisher-controlled sources that expose jobs before aggregators pick them up.
No rate-limiting concerns, no CAPTCHA, no web scraping.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("ats_feed_fetcher")

_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "employer_registry.json"
_CACHE_PATH    = Path(__file__).parent.parent / "data" / "ats_feed_cache.json"
_CACHE_TTL_H   = 6  # re-fetch each employer's feed at most every 6 hours

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "JobHuntrr/1.0 (job discovery bot; contact: jobhuntrr@example.com)",
    "Accept": "application/json",
})


# ── Registry helpers ──────────────────────────────────────────────────────────

def load_registry() -> list[dict]:
    """Load the employer registry from disk."""
    try:
        import json
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not load employer registry: %s", e)
        return []


def _load_cache() -> dict:
    try:
        import json
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        import json
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.debug("Cache save failed: %s", e)


def _cache_key(employer: dict) -> str:
    return f"{employer['ats']}:{employer['ats_slug']}"


def _is_cache_fresh(cache: dict, key: str) -> bool:
    entry = cache.get(key)
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        return age_h < _CACHE_TTL_H
    except Exception:
        return False


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize_job(
    title: str,
    company: str,
    location: str,
    job_url: str,
    description: str = "",
    date_posted: str = "",
    source: str = "ats_feed",
) -> dict:
    return {
        "title":       (title or "").strip()[:240],
        "company":     (company or "").strip()[:120],
        "location":    (location or "").strip()[:120],
        "description": (description or "").strip()[:3000],
        "job_url":     (job_url or "").strip(),
        "job_url_direct": (job_url or "").strip(),
        "date_posted": date_posted or "",
        "source":      source,
        "apply_method": "ATS",
        "score":       None,
        "decision":    None,
        "skip_reason": "",
        "fit_reason":  "",
        "applied":     False,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(slug: str, company_name: str) -> list[dict]:
    """
    Greenhouse public Job Board API — no auth required.
    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    """
    if not slug:
        return []
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = _SESSION.get(url, params={"content": "true"}, timeout=15)
        if resp.status_code == 404:
            logger.debug("Greenhouse board not found: %s", slug)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Greenhouse fetch failed for %s: %s", slug, e)
        return []

    jobs = []
    for item in data.get("jobs", []):
        title    = item.get("title", "")
        gh_url   = item.get("absolute_url", "")
        location = ""
        for loc in (item.get("offices") or item.get("location") and [item["location"]] or []):
            if isinstance(loc, dict):
                location = loc.get("name", "")
            elif isinstance(loc, str):
                location = loc
            if location:
                break
        desc = ""
        if item.get("content"):
            # Strip HTML tags from content
            desc = re.sub(r"<[^>]+>", " ", item["content"])
            desc = re.sub(r"\s+", " ", desc).strip()[:2000]
        jobs.append(_normalize_job(
            title=title,
            company=company_name,
            location=location,
            job_url=gh_url,
            description=desc,
            date_posted=item.get("updated_at", "")[:10],
            source="greenhouse_api",
        ))
    logger.info("Greenhouse [%s]: %d jobs", slug, len(jobs))
    return jobs


# ── Lever ─────────────────────────────────────────────────────────────────────

def fetch_lever(slug: str, company_name: str) -> list[dict]:
    """
    Lever public Postings API — no auth required.
    GET https://api.lever.co/v0/postings/{slug}
    Returns jobs with hostedUrl (apply URL) and text (description).
    """
    if not slug:
        return []
    url = f"https://api.lever.co/v0/postings/{slug}"
    try:
        resp = _SESSION.get(url, params={"mode": "json"}, timeout=15)
        if resp.status_code == 404:
            logger.debug("Lever board not found: %s", slug)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Lever fetch failed for %s: %s", slug, e)
        return []

    jobs = []
    for item in (data if isinstance(data, list) else data.get("data", [])):
        title      = item.get("text", "")
        apply_url  = item.get("applyUrl") or item.get("hostedUrl", "")
        location   = ""
        for cat in (item.get("categories") or {}).values() if isinstance(item.get("categories"), dict) else []:
            if isinstance(cat, str) and len(cat) < 60:
                location = cat
                break
        if not location:
            location = (item.get("categories") or {}).get("location", "") if isinstance(item.get("categories"), dict) else ""
        desc_parts = []
        for section in (item.get("descriptionPlain") and [item["descriptionPlain"]] or
                        [s.get("content", "") for s in (item.get("lists") or [])]):
            if section:
                desc_parts.append(section)
        desc = " ".join(desc_parts)[:2000]
        jobs.append(_normalize_job(
            title=title,
            company=company_name,
            location=location,
            job_url=apply_url,
            description=desc,
            date_posted=datetime.fromtimestamp(
                item["createdAt"] / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d") if item.get("createdAt") else "",
            source="lever_api",
        ))
    logger.info("Lever [%s]: %d jobs", slug, len(jobs))
    return jobs


# ── Ashby ─────────────────────────────────────────────────────────────────────

def fetch_ashby(slug: str, company_name: str) -> list[dict]:
    """
    Ashby public Job Board API — no auth required.
    POST https://api.ashbyhq.com/posting-api/job-board/{slug}
    """
    if not slug:
        return []
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = _SESSION.post(url, json={"includeCompensation": False}, timeout=15)
        if resp.status_code == 404:
            logger.debug("Ashby board not found: %s", slug)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("Ashby fetch failed for %s: %s", slug, e)
        return []

    jobs = []
    for item in data.get("jobPostings", []):
        title     = item.get("title", "")
        apply_url = item.get("jobUrl", "") or item.get("applyUrl", "")
        location  = item.get("location", "") or item.get("locationName", "")
        desc_html = item.get("descriptionHtml", "") or item.get("descriptionPlain", "")
        desc = re.sub(r"<[^>]+>", " ", desc_html)
        desc = re.sub(r"\s+", " ", desc).strip()[:2000]
        jobs.append(_normalize_job(
            title=title,
            company=company_name,
            location=location,
            job_url=apply_url,
            description=desc,
            date_posted=item.get("publishedDate", "")[:10],
            source="ashby_api",
        ))
    logger.info("Ashby [%s]: %d jobs", slug, len(jobs))
    return jobs


# ── Dispatcher ────────────────────────────────────────────────────────────────

_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever":      fetch_lever,
    "ashby":      fetch_ashby,
}


def fetch_employer(employer: dict, use_cache: bool = True) -> list[dict]:
    """Fetch jobs for a single employer entry from the registry."""
    ats  = employer.get("ats", "custom")
    slug = employer.get("ats_slug", "")
    name = employer.get("name", slug)

    if ats == "custom" or ats == "workday" or ats == "taleo" or not slug:
        # Workday/Taleo require tenant-specific URLs — use career-page crawler instead
        return []

    fetcher = _FETCHERS.get(ats)
    if not fetcher:
        return []

    if use_cache:
        cache = _load_cache()
        key = _cache_key(employer)
        if _is_cache_fresh(cache, key):
            return cache[key].get("jobs", [])

    jobs = fetcher(slug, name)
    time.sleep(0.3)  # polite pause between API calls

    if use_cache and jobs is not None:
        cache = _load_cache()
        cache[_cache_key(employer)] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "jobs": jobs,
        }
        _save_cache(cache)

    return jobs or []


def fetch_all_employers(
    max_employers: int = 0,
    ats_types: Optional[list[str]] = None,
    use_cache: bool = True,
    dedup_seen: Optional[set] = None,
) -> list[dict]:
    """
    Fetch jobs from all employers in the registry.

    Args:
        max_employers: 0 = all. Otherwise cap to this many employers.
        ats_types:     None = all. Otherwise only fetch these ATS types.
        use_cache:     Skip re-fetching recently fetched employers.
        dedup_seen:    Set of job_url strings already processed (mutated in place).

    Returns:
        Deduplicated list of normalized job dicts.
    """
    registry = load_registry()
    if not registry:
        logger.warning("Employer registry is empty — no ATS feed results")
        return []

    if ats_types:
        registry = [e for e in registry if e.get("ats") in ats_types]
    if max_employers > 0:
        registry = registry[:max_employers]

    seen = set(dedup_seen or set())
    all_jobs: list[dict] = []

    for employer in registry:
        try:
            jobs = fetch_employer(employer, use_cache=use_cache)
            for job in jobs:
                url = job.get("job_url", "")
                if url and url not in seen:
                    seen.add(url)
                    all_jobs.append(job)
        except Exception as e:
            logger.debug("Error fetching %s: %s", employer.get("name"), e)
            continue

    if dedup_seen is not None:
        dedup_seen.update(seen)

    logger.info("ATS feed fetcher: %d jobs from %d employers", len(all_jobs), len(registry))
    return all_jobs

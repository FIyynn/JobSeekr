"""
agents/career_page_crawler.py — Sitemap + JobPosting JSON-LD career page crawler.

For employers with custom career sites (ATS type "custom" or Workday/Taleo
where we don't have a public API): fetch robots.txt, discover sitemaps,
walk job URLs, and parse JobPosting schema.org JSON-LD from leaf pages.

This finds jobs that aggregators miss, at zero cost and with no API keys.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("career_page_crawler")

_CACHE_PATH = Path(__file__).parent.parent / "data" / "career_crawl_cache.json"
_CACHE_TTL_H = 12

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

# URL path fragments that suggest a job detail page (not a listing page)
_JOB_DETAIL_RE = re.compile(
    r"/(?:job|jobs|career|careers|position|opening|vacancy|requisition|"
    r"apply|posting|role|opportunity)/[^/]+/?$",
    re.I,
)
_JOB_LISTING_RE = re.compile(
    r"/(?:jobs|careers|openings|positions|opportunities|join-us|join-our-team)/?$",
    re.I,
)


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _is_fresh(cache: dict, key: str) -> bool:
    entry = cache.get(key)
    if not entry or not entry.get("crawled_at"):
        return False
    try:
        age_h = (
            datetime.now(timezone.utc) -
            datetime.fromisoformat(entry["crawled_at"])
        ).total_seconds() / 3600
        return age_h < _CACHE_TTL_H
    except Exception:
        return False


# ── robots.txt + sitemap discovery ───────────────────────────────────────────

def _find_sitemaps_from_robots(base_url: str) -> list[str]:
    """Parse robots.txt and return Sitemap: URLs."""
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        resp = _SESSION.get(robots_url, timeout=8)
        if resp.status_code != 200:
            return []
        sitemaps = []
        for line in resp.text.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                sm_url = line.split(":", 1)[1].strip()
                if sm_url.startswith("http"):
                    sitemaps.append(sm_url)
        return sitemaps
    except Exception:
        return []


def _try_common_sitemap_urls(base_url: str) -> list[str]:
    """Try well-known sitemap paths when robots.txt doesn't declare one."""
    candidates = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
        urljoin(base_url, "/careers/sitemap.xml"),
        urljoin(base_url, "/jobs/sitemap.xml"),
    ]
    found = []
    for url in candidates:
        try:
            resp = _SESSION.head(url, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                found.append(url)
        except Exception:
            continue
    return found


def _extract_urls_from_sitemap(sitemap_url: str, depth: int = 0) -> list[str]:
    """Recursively expand sitemap index files; return leaf <loc> URLs."""
    if depth > 3:
        return []
    try:
        resp = _SESSION.get(sitemap_url, timeout=10)
        if resp.status_code != 200:
            return []
        root = ElementTree.fromstring(resp.content)
    except Exception:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # Sitemap index — recurse
    index_locs = [el.text for el in root.findall(".//sm:sitemap/sm:loc", ns) if el.text]
    if index_locs:
        urls = []
        for sub in index_locs[:10]:  # cap recursion breadth
            urls.extend(_extract_urls_from_sitemap(sub, depth + 1))
            time.sleep(0.2)
        return urls

    # Regular sitemap — return loc values
    return [el.text for el in root.findall(".//sm:url/sm:loc", ns) if el.text]


# ── JobPosting JSON-LD parser ─────────────────────────────────────────────────

def _parse_jsonld_jobs(html: str, page_url: str, company_name: str) -> list[dict]:
    """Extract JobPosting schema.org JSON-LD blocks from a page's HTML."""
    jobs = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            # Could be a single object or a @graph array
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if data.get("@type") == "JobPosting":
                    items = [data]
                elif "@graph" in data:
                    items = [i for i in data["@graph"] if isinstance(i, dict)]

            for item in items:
                if item.get("@type") != "JobPosting":
                    continue
                title = item.get("title") or item.get("name") or ""
                apply_url = ""
                for key in ("url", "sameAs", "applicationContact"):
                    val = item.get(key, "")
                    if val and str(val).startswith("http"):
                        apply_url = str(val)
                        break
                if not apply_url:
                    apply_url = page_url

                # Location
                loc_data = item.get("jobLocation", {})
                if isinstance(loc_data, list):
                    loc_data = loc_data[0] if loc_data else {}
                location = ""
                if isinstance(loc_data, dict):
                    addr = loc_data.get("address", {})
                    if isinstance(addr, dict):
                        location = ", ".join(filter(None, [
                            addr.get("addressLocality", ""),
                            addr.get("addressCountry", ""),
                        ]))
                    elif isinstance(addr, str):
                        location = addr

                # Description
                desc = re.sub(r"<[^>]+>", " ", item.get("description", ""))
                desc = re.sub(r"\s+", " ", desc).strip()[:2000]

                # Date
                date_posted = (item.get("datePosted") or "")[:10]

                if title and apply_url:
                    jobs.append({
                        "title":       title.strip()[:240],
                        "company":     company_name,
                        "location":    location[:120],
                        "description": desc,
                        "job_url":     apply_url,
                        "job_url_direct": apply_url,
                        "date_posted": date_posted,
                        "source":      "jsonld_career",
                        "apply_method": "ATS",
                        "score":       None,
                        "decision":    None,
                        "skip_reason": "",
                        "fit_reason":  "",
                        "applied":     False,
                        "discovered_at": datetime.now(timezone.utc).isoformat(),
                    })
    except Exception as e:
        logger.debug("JSON-LD parse failed for %s: %s", page_url, e)
    return jobs


# ── Per-employer crawler ──────────────────────────────────────────────────────

def crawl_employer_careers(
    employer: dict,
    max_pages: int = 50,
    use_cache: bool = True,
) -> list[dict]:
    """
    Crawl a single employer's career site via sitemap + JSON-LD.

    Args:
        employer:   Registry entry with 'careers_url' and 'name'.
        max_pages:  Cap on individual job page fetches.
        use_cache:  Skip recently crawled employers.

    Returns:
        List of normalized job dicts.
    """
    careers_url = employer.get("careers_url", "")
    company_name = employer.get("name", "")
    if not careers_url:
        return []

    cache_key = "crawl:" + careers_url
    if use_cache:
        cache = _load_cache()
        if _is_fresh(cache, cache_key):
            cached = cache.get(cache_key, {}).get("jobs", [])
            logger.debug("Cache hit for %s (%d jobs)", company_name, len(cached))
            return cached

    parsed = urlparse(careers_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Discover sitemaps
    sitemaps = _find_sitemaps_from_robots(base_url)
    if not sitemaps:
        sitemaps = _try_common_sitemap_urls(base_url)

    # 2. Extract job-detail URLs from sitemaps
    job_urls: list[str] = []
    for sm in sitemaps[:3]:  # cap sitemap sources
        all_urls = _extract_urls_from_sitemap(sm)
        for url in all_urls:
            if _JOB_DETAIL_RE.search(url):
                job_urls.append(url)

    # 3. If no sitemap URLs found, try fetching the careers listing page
    if not job_urls:
        try:
            resp = _SESSION.get(careers_url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = urljoin(careers_url, a["href"])
                    if _JOB_DETAIL_RE.search(href) and href not in job_urls:
                        job_urls.append(href)
        except Exception:
            pass

    if not job_urls:
        logger.debug("No job URLs found for %s", company_name)
        return []

    # 4. Fetch each job page and parse JSON-LD
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    for url in job_urls[:max_pages]:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            resp = _SESSION.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            page_jobs = _parse_jsonld_jobs(resp.text, url, company_name)
            for job in page_jobs:
                if job["job_url"] not in seen_urls:
                    jobs.append(job)
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            continue
        time.sleep(0.3)

    logger.info("Career crawler [%s]: %d jobs from %d pages", company_name, len(jobs), len(seen_urls))

    # Cache result
    cache = _load_cache()
    cache[cache_key] = {
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "jobs": jobs,
    }
    _save_cache(cache)

    return jobs


def crawl_all_custom_employers(
    max_employers: int = 0,
    use_cache: bool = True,
    dedup_seen: Optional[set] = None,
) -> list[dict]:
    """
    Crawl all employers in the registry that have ATS type 'custom'
    (or workday/taleo where we have no public API).

    Returns deduplicated normalized job dicts.
    """
    try:
        from agents.ats_feed_fetcher import load_registry
        registry = load_registry()
    except Exception:
        logger.warning("Could not load employer registry")
        return []

    # Crawl custom + workday + taleo employers (no native API available)
    crawlable = [e for e in registry if e.get("ats") in ("custom", "workday", "taleo")]
    if max_employers > 0:
        crawlable = crawlable[:max_employers]

    seen = set(dedup_seen or set())
    all_jobs: list[dict] = []

    for employer in crawlable:
        try:
            jobs = crawl_employer_careers(employer, use_cache=use_cache)
            for job in jobs:
                url = job.get("job_url", "")
                if url and url not in seen:
                    seen.add(url)
                    all_jobs.append(job)
        except Exception as e:
            logger.debug("Crawl error for %s: %s", employer.get("name"), e)

    if dedup_seen is not None:
        dedup_seen.update(seen)

    logger.info("Career page crawler: %d total jobs", len(all_jobs))
    return all_jobs

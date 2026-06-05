"""
page_reader.py — clean job-page content for LLM scoring.

Uses crawl4ai when available (pip install crawl4ai) for JS-rendered pages.
Falls back to a lightweight requests + BeautifulSoup pass when crawl4ai is
not installed or the async overhead isn't worth it.

WHY: jobspy's raw description field often contains nav-bar noise, cookie
banners, and repeated boilerplate.  crawl4ai strips all that and returns
clean markdown — the scorer LLM gets 2–3x better signal.

NOT used for form filling — Playwright handles that.
"""

import logging
import re
import asyncio
from typing import Optional

logger = logging.getLogger("page_reader")

# Minimum chars before we consider a description "good enough" without fetching
MIN_DESCRIPTION_LENGTH = 400


def _strip_html(html: str) -> str:
    """Quick HTML tag stripper — used as a fallback before bs4."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


def _fetch_with_requests(url: str) -> str:
    """Lightweight fallback: requests + basic tag stripping. No JS rendering."""
    try:
        import requests
        resp = requests.get(url, timeout=12, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        resp.raise_for_status()
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header",
                              "aside", "[class*='cookie']", "[id*='cookie']"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)[:4000]
        except ImportError:
            return _strip_html(resp.text)[:4000]
    except Exception as e:
        logger.debug(f"requests fallback failed for {url}: {e}")
        return ""


async def _fetch_with_crawl4ai(url: str) -> str:
    """Use crawl4ai for JS-rendered pages — returns clean markdown."""
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        browser_cfg = BrowserConfig(headless=True, verbose=False)
        # Prune low-content blocks (nav, footer, sidebar)
        content_filter = PruningContentFilter(threshold=0.45, threshold_type="fixed")
        md_generator = DefaultMarkdownGenerator(content_filter=content_filter)
        run_cfg = CrawlerRunConfig(
            markdown_generator=md_generator,
            wait_until="networkidle",
            page_timeout=20000,
            verbose=False,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
            if result.success and result.markdown:
                return result.markdown.fit_markdown or result.markdown.raw_markdown or ""
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"crawl4ai fetch failed for {url}: {e}")
    return ""


def fetch_job_description(url: str, existing_description: str = "") -> str:
    """
    Return the best available description for a job URL.

    Priority:
      1. If existing description is already rich (>= MIN_DESCRIPTION_LENGTH chars), return as-is.
      2. Try crawl4ai (JS rendering + content pruning) → clean markdown.
      3. Fall back to requests + BeautifulSoup (static pages only).
      4. Return existing description unchanged on total failure.
    """
    existing = (existing_description or "").strip()
    if len(existing) >= MIN_DESCRIPTION_LENGTH:
        return existing  # already good

    if not url or not url.startswith("http"):
        return existing

    # Skip LinkedIn job URLs — they require auth and crawl4ai can't log in
    if "linkedin.com/jobs" in url:
        return existing

    logger.info(f"  Enriching description from: {url[:80]}")

    # Try crawl4ai async first
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an existing event loop (e.g. GUI thread) — run in thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, _fetch_with_crawl4ai(url))
                markdown = fut.result(timeout=25)
        else:
            markdown = loop.run_until_complete(_fetch_with_crawl4ai(url))
        if markdown and len(markdown) > MIN_DESCRIPTION_LENGTH:
            logger.info(f"  Enriched via crawl4ai ({len(markdown)} chars)")
            return markdown[:5000]
    except Exception as e:
        logger.debug(f"crawl4ai path failed: {e}")

    # requests fallback
    text = _fetch_with_requests(url)
    if text and len(text) > MIN_DESCRIPTION_LENGTH:
        logger.info(f"  Enriched via requests ({len(text)} chars)")
        return text

    return existing


def enrich_jobs_descriptions(jobs: list[dict], max_workers: int = 4) -> list[dict]:
    """
    Enrich all jobs whose descriptions are thin.
    Runs in a thread pool so we don't block the pipeline for long.
    """
    thin = [j for j in jobs if len((j.get("description") or "").strip()) < MIN_DESCRIPTION_LENGTH]
    if not thin:
        return jobs

    logger.info(f"Enriching {len(thin)} thin job descriptions (crawl4ai/requests)...")

    import concurrent.futures

    def _enrich(job: dict) -> None:
        url = job.get("job_url_direct") or job.get("job_url", "")
        enriched = fetch_job_description(url, job.get("description", ""))
        if enriched:
            job["description"] = enriched

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_enrich, thin))

    return jobs

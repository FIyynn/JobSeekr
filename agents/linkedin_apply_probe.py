"""
Re-check LinkedIn apply methods by opening each job page and reading the real CTA.

Updates local DB fields apply_method and job_url_direct from live DOM detection.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)


def clean_apply_direct_url(url: str) -> str:
    """Normalize safety redirects and strip ad/tracking wrapper URLs."""
    url = unwrap_linkedin_safety_url(url)
    low = url.lower()
    if "doubleclick.net" in low or "trackclk" in low:
        for match in re.finditer(r"https?://[^\s?;&]+", url):
            candidate = match.group(0).rstrip("?&,")
            if "doubleclick" not in candidate.lower() and "linkedin.com" not in candidate.lower():
                return candidate
    return url


def unwrap_linkedin_safety_url(href: str) -> str:
    """Decode linkedin.com/safety/go?url=... redirect links to the ATS URL."""
    href = (href or "").strip()
    if not href or "linkedin.com/safety/go" not in href.lower():
        return href
    try:
        raw = parse_qs(urlparse(href).query).get("url", [""])[0]
        return unquote(raw) if raw else href
    except Exception:
        return href


def probe_result_to_fields(probe: dict) -> dict[str, str]:
    """Map a live probe result to DB apply_method / job_url_direct."""
    atype = (probe.get("type") or "").strip().lower()
    direct = (probe.get("direct_url") or "").strip()
    label = (probe.get("label") or "").strip()

    if atype == "easy_apply":
        return {
            "apply_method": "Easy Apply",
            "job_url_direct": "",
            "apply_notes": f"Apply method verified on LinkedIn: Easy Apply ({label})".strip(),
        }
    if atype in ("apply", "company_website"):
        note = f"Apply method verified on LinkedIn: external ({label})"
        if direct:
            note += f" -> {direct[:120]}"
        return {
            "apply_method": "Apply",
            "job_url_direct": direct,
            "apply_notes": note,
        }
    if atype == "none":
        return {
            "apply_notes": "Apply method re-check: no Apply CTA found on LinkedIn page",
        }
    return {
        "apply_notes": f"Apply method re-check: status={probe.get('page_status', 'unknown')}",
    }


def verify_linkedin_jobs_apply_methods(
    jobs: list[dict] | None = None,
    *,
    gcc_only: bool = False,
    limit: int = 0,
    headless: bool = False,
    linkedin_email: str = "",
    linkedin_password: str = "",
    progress_callback: Optional[Callable[[], None]] = None,
) -> list[dict]:
    """
    Open each LinkedIn job URL, detect Easy Apply vs external Apply, persist to DB.

    Returns a list of result dicts (one per job): id, title, company, type, label,
    direct_url, apply_method, changed.
    """
    from playwright.sync_api import sync_playwright

    from agents.form_filler import (
        LINKEDIN_SESSION_DIR,
        PLAYWRIGHT_AVAILABLE,
        _dismiss_blocking_popups,
        _ensure_linkedin_login,
        _extract_apply_href,
        _linkedin_detect_apply_button,
        _linkedin_page_status,
        _pause,
    )
    from agents.job_logger import fetch_pending_apply, get_store

    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright is not installed")

    if jobs is None:
        jobs = fetch_pending_apply(gcc_only=gcc_only)

    jobs = [
        j for j in jobs
        if "linkedin.com/jobs" in (j.get("job_url") or "").lower()
    ]
    if limit > 0:
        jobs = jobs[:limit]

    if not jobs:
        logger.info("No LinkedIn jobs to verify")
        return []

    store = get_store()
    results: list[dict] = []
    os.makedirs(LINKEDIN_SESSION_DIR, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=LINKEDIN_SESSION_DIR,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        if not _ensure_linkedin_login(ctx, linkedin_email, linkedin_password):
            raise RuntimeError(
                "LinkedIn login required — log in via setup_linkedin.py or profile settings"
            )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for job in jobs:
            jid = job.get("id") or job.get("job_id")
            url = (job.get("job_url") or "").strip()
            title = job.get("title", "")
            company = job.get("company", "")
            old_method = (job.get("apply_method") or "").strip()
            old_direct = (job.get("job_url_direct") or "").strip()

            row = {
                "id": jid,
                "title": title,
                "company": company,
                "job_url": url,
                "type": "none",
                "label": "",
                "direct_url": "",
                "page_status": "",
                "apply_method": old_method,
                "changed": False,
            }

            try:
                from gui.stop_flag import check_stop
                check_stop("Stop requested — halting apply-method verification")
            except ImportError:
                pass

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                _pause(2.0, 3.0)
                _dismiss_blocking_popups(page)
                row["page_status"] = _linkedin_page_status(page)
                info = _linkedin_detect_apply_button(page, log=False)
                if info:
                    row["type"] = info.get("type") or "none"
                    row["label"] = info.get("label") or ""
                    href = _extract_apply_href(info)
                    row["direct_url"] = clean_apply_direct_url(href) if href else ""
                else:
                    row["type"] = "none"

                fields = probe_result_to_fields(row)
                row["apply_method"] = fields.get("apply_method", old_method)

                changed = (
                    row["apply_method"] != old_method
                    or fields.get("job_url_direct", old_direct) != old_direct
                )
                row["changed"] = changed

                if jid and fields:
                    store.update_job(int(jid), **fields)

                logger.info(
                    "  [%s] %s @ %s -> %s (%s)%s",
                    "UPDATED" if changed else "ok",
                    title[:50],
                    company[:30],
                    row["apply_method"] or row["type"],
                    (row["label"] or "")[:40],
                    f" | {row['direct_url'][:60]}" if row.get("direct_url") else "",
                )
            except Exception as exc:
                row["error"] = str(exc)
                logger.warning("  Verify failed %s @ %s: %s", title, company, exc)

            results.append(row)
            if progress_callback:
                try:
                    progress_callback()
                except Exception:
                    pass

        ctx.close()

    updated = sum(1 for r in results if r.get("changed"))
    logger.info(
        "Apply-method verification done: %s job(s), %s updated",
        len(results),
        updated,
    )
    return results


def main():
    import argparse
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.env_settings import bootstrap_settings

    bootstrap_settings()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    p = argparse.ArgumentParser(description="Re-check LinkedIn apply methods from live pages")
    p.add_argument("--gcc-only", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--headless", action="store_true")
    args = p.parse_args()

    email = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    results = verify_linkedin_jobs_apply_methods(
        gcc_only=args.gcc_only,
        limit=args.limit,
        headless=args.headless,
        linkedin_email=email,
        linkedin_password=password,
    )
    for r in results:
        status = "CHANGED" if r.get("changed") else "same"
        err = f" ERROR={r['error']}" if r.get("error") else ""
        print(
            f"  [{status}] {r.get('apply_method') or r.get('type')} | "
            f"{r.get('company')} — {r.get('title')}{err}"
        )


if __name__ == "__main__":
    main()

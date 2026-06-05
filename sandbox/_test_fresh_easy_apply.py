"""
Find a live LinkedIn Easy Apply posting (UAE search) and test the apply wizard.

Usage:
  python sandbox/_test_fresh_easy_apply.py --dry-run
  python sandbox/_test_fresh_easy_apply.py --live   # submits one real application
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

from config.env_settings import bootstrap_settings

bootstrap_settings()

from playwright.sync_api import sync_playwright
from apply_jobs import _pick_resume
from agents.form_filler import (
    LINKEDIN_SESSION_DIR,
    PLAYWRIGHT_AVAILABLE,
    _dismiss_blocking_popups,
    _ensure_linkedin_login,
    _linkedin_apply_job,
    _linkedin_detect_apply_button,
    _linkedin_page_status,
    _pause,
)
from config.config import (
    APPLICATION_QA,
    CANDIDATE_PROFILE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_VISION_MODEL,
)

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()

SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?f_AL=true&location=United%20Arab%20Emirates&sortBy=DD"
)


def _find_open_easy_apply_job(page, max_tries: int = 8) -> tuple[dict | None, str]:
    """Return (job_dict, '') when an open Easy Apply listing is on screen, else (None, reason)."""
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
    _pause(2.5, 3.5)
    _dismiss_blocking_popups(page)

    cards = page.locator("a[href*='/jobs/view/']")
    seen: set[str] = set()
    urls: list[str] = []
    for i in range(min(cards.count(), 25)):
        href = cards.nth(i).get_attribute("href") or ""
        if "/jobs/view/" not in href:
            continue
        if href.startswith("/"):
            href = "https://www.linkedin.com" + href
        base = href.split("?")[0]
        if base in seen:
            continue
        seen.add(base)
        urls.append(base)

    for url in urls[:max_tries]:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _pause(2.0, 3.0)
        _dismiss_blocking_popups(page)
        status = _linkedin_page_status(page)
        info = _linkedin_detect_apply_button(page, log=True)
        if status in ("closed", "already_applied"):
            print(f"  skip {status}: {url}")
            continue
        if info and info.get("type") == "easy_apply":
            title = ""
            company = ""
            try:
                title = page.locator("h1").first.inner_text(timeout=2000).strip()
            except Exception:
                pass
            try:
                company = page.locator(
                    ".job-details-jobs-unified-top-card__company-name a, "
                    ".jobs-unified-top-card__company-name a"
                ).first.inner_text(timeout=2000).strip()
            except Exception:
                pass
            job = {
                "id": 0,
                "job_id": 0,
                "title": title or "Easy Apply test",
                "company": company or "Test",
                "job_url": url,
                "source": "linkedin",
                "apply_method": "Easy Apply",
                "applied": False,
                "apply_notes": "",
                "_easy_apply_only_run": True,
            }
            return job, ""
        print(f"  skip no easy_apply ({info.get('type') if info else 'none'}): {url}")
    return None, "no open Easy Apply listing found"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="Submit (default: dry-run only)")
    p.add_argument("--url", type=str, default="", help="Use this job URL instead of search")
    args = p.parse_args()
    dry_run = not args.live

    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not installed")
        return 1

    qa = {**APPLICATION_QA}
    qa["email"] = os.getenv("APPLICANT_EMAIL", qa.get("email", ""))
    qa["phone"] = os.getenv("APPLICANT_PHONE", qa.get("phone", ""))

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=LINKEDIN_SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        if not _ensure_linkedin_login(ctx, LINKEDIN_EMAIL, LINKEDIN_PASSWORD):
            print("LinkedIn login required")
            return 1
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if args.url.strip():
            job = {
                "id": 0,
                "job_id": 0,
                "title": "Easy Apply test",
                "company": "Test",
                "job_url": args.url.strip(),
                "source": "linkedin",
                "apply_method": "Easy Apply",
                "applied": False,
                "apply_notes": "",
                "_easy_apply_only_run": True,
            }
            page.goto(job["job_url"], wait_until="domcontentloaded", timeout=30000)
            _pause(3.0, 4.0)
            _dismiss_blocking_popups(page)
        else:
            print("Searching LinkedIn for a fresh Easy Apply job in UAE...")
            job, err = _find_open_easy_apply_job(page)
            if not job:
                print(err or "No open Easy Apply job found in search results")
                ctx.close()
                return 1

        job["_resume_path"] = _pick_resume("investments")

        print("=" * 60)
        print(f"  TEST {'DRY-RUN' if dry_run else 'LIVE'} Easy Apply")
        print(f"  {job['title']} @ {job['company']}")
        print(f"  {job['job_url']}")
        print("=" * 60)

        ok = _linkedin_apply_job(
            ctx,
            page,
            job,
            qa,
            CANDIDATE_PROFILE,
            OLLAMA_MODEL,
            OLLAMA_BASE_URL,
            dry_run=dry_run,
            vision_model=OLLAMA_VISION_MODEL or "",
            validate_fit=False,
        )
        ctx.close()

    print("\n" + "=" * 60)
    print(f"  Result: {'SUCCESS' if ok else 'FAILED'}")
    print(f"  applied={job.get('applied')} decision={job.get('decision')}")
    print(f"  notes: {job.get('apply_notes', '')}")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

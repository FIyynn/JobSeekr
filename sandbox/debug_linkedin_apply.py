"""Dump Apply-related elements on a LinkedIn job page."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.env_settings import bootstrap_settings

bootstrap_settings()

from playwright.sync_api import sync_playwright
from agents.form_filler import (
    LINKEDIN_SESSION_DIR,
    _linkedin_detect_apply_button,
    _linkedin_page_status,
    _linkedin_job_top_card,
    _linkedin_scan_apply_buttons,
)

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.linkedin.com/jobs/view/4399725104"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=LINKEDIN_SESSION_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        viewport={"width": 1440, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    print("status:", _linkedin_page_status(page))
    print("url:", page.url)
    top = _linkedin_job_top_card(page)
    try:
        print("top card snippet:", top.inner_text(timeout=3000)[:400].replace("\n", " | "))
    except Exception as e:
        print("top card error:", e)
    cands = _linkedin_scan_apply_buttons(page)
    print(f"candidates: {len(cands)}")
    for c in cands[:6]:
        print(f"  - {c['type']}: {repr(c['label'][:80])}")
    best = _linkedin_detect_apply_button(page)
    print("best:", best)
    # Raw buttons in top card
    for sel in ["button", "a[role='button']"]:
        loc = top.locator(sel)
        n = min(loc.count(), 15)
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible(timeout=300):
                    continue
                t = (el.inner_text(timeout=300) or "").strip()[:60]
                a = (el.get_attribute("aria-label") or "")[:60]
                if "apply" in (t + a).lower():
                    print(f"  raw [{sel}]: text={repr(t)} aria={repr(a)}")
            except Exception:
                pass
    input("Press Enter to close...")
    ctx.close()

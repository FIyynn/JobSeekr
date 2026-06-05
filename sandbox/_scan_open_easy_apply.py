"""Scan pending Easy Apply jobs for open + CTA type."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.env_settings import bootstrap_settings

bootstrap_settings()

from playwright.sync_api import sync_playwright
from agents.apply_method import is_linkedin_easy_apply
from agents.form_filler import (
    LINKEDIN_SESSION_DIR,
    _dismiss_blocking_popups,
    _ensure_linkedin_login,
    _linkedin_detect_apply_button,
    _linkedin_page_status,
    _pause,
)
from agents.job_logger import fetch_pending_apply

jobs = [j for j in fetch_pending_apply(gcc_only=False) if is_linkedin_easy_apply(j)]
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=LINKEDIN_SESSION_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1440, "height": 900},
    )
    _ensure_linkedin_login(ctx, os.getenv("LINKEDIN_EMAIL", ""), os.getenv("LINKEDIN_PASSWORD", ""))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for j in jobs:
        page.goto(j.get("job_url", ""), wait_until="domcontentloaded", timeout=25000)
        _pause(1.2, 1.8)
        _dismiss_blocking_popups(page)
        st = _linkedin_page_status(page)
        info = _linkedin_detect_apply_button(page, log=False)
        at = info["type"] if info else "none"
        lbl = (info.get("label") or "")[:40] if info else ""
        title = (j.get("title") or "")[:30]
        print(f"{j['id']:4} {st:16} {at:16} {lbl:40} | {title}")
    ctx.close()

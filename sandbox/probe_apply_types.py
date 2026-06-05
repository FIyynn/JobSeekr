"""Probe LinkedIn pending GCC jobs for Easy Apply vs external apply (no submit)."""
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
    _dismiss_blocking_popups,
    _linkedin_detect_apply_button,
    _linkedin_page_status,
    _pause,
)
from storage.job_store import JobStore

store = JobStore()
pending = [
    j for j in store.list_jobs(decision="auto_apply", applied=False, limit=200)
    if "linkedin.com" in (j.get("job_url") or "").lower()
    and JobStore._is_gcc(j.get("location", ""))
]
# Also try manual_review GCC linkedin not applied
pending += [
    j for j in store.list_jobs(decision="manual_review", applied=False, limit=100)
    if "linkedin.com" in (j.get("job_url") or "").lower()
    and JobStore._is_gcc(j.get("location", ""))
    and j.get("id") not in {x.get("id") for x in pending}
]

seen = set()
unique = []
for j in pending:
    k = j.get("job_url")
    if k in seen:
        continue
    seen.add(k)
    unique.append(j)

print(f"Probing up to 12 GCC LinkedIn jobs ({len(unique)} in queue)...")

results = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=LINKEDIN_SESSION_DIR,
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        viewport={"width": 1440, "height": 900},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for job in unique[:12]:
        url = job.get("job_url", "")
        jid = job.get("id")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            _pause(2.5, 3.5)
            _dismiss_blocking_popups(page)
            status = _linkedin_page_status(page)
            info = _linkedin_detect_apply_button(page, log=False)
            atype = info["type"] if info else "none"
            label = (info.get("label") or "")[:50] if info else ""
            row = {
                "id": jid,
                "title": job.get("title"),
                "company": job.get("company"),
                "status": status,
                "apply_type": atype,
                "label": label,
            }
            results.append(row)
            print(
                f"  id={jid} [{atype}] status={status} | "
                f"{job.get('company')} — {job.get('title')[:40]}"
            )
            if label:
                print(f"    label: {label}")
        except Exception as e:
            print(f"  id={jid} ERROR: {e}")
    ctx.close()

easy = [r for r in results if r["apply_type"] == "easy_apply"]
ext = [r for r in results if r["apply_type"] in ("apply", "company_website")]
print("\n=== EASY APPLY CANDIDATES ===")
for r in easy:
    print(f"  {r['id']}: {r['company']} — {r['title']}")
print("\n=== EXTERNAL APPLY CANDIDATES ===")
for r in ext:
    print(f"  {r['id']}: {r['company']} — {r['title']} ({r['apply_type']})")

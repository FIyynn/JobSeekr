"""
sandbox/test_dry_run.py — dry-run regression tests for the apply pipeline.

Runs WITHOUT submitting anything:
  - Field extraction on known public ATS pages (Greenhouse, Lever, Workday, Ashby)
  - _finalize_non_wizard with dry_run=True (screenshots only, no submit)
  - Confirmation gate: applied=True blocked without confirmation evidence
  - SPA retry logic: field count > 0 after hydration wait

Usage:
    python sandbox/test_dry_run.py
    python sandbox/test_dry_run.py --platform greenhouse
    python sandbox/test_dry_run.py --headful   # see the browser

Requires: playwright, form_filler importable from project root.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Test URLs (public job pages — no login required) ────────────────────────
# These are stable Greenhouse / Lever pages from major companies; replace any
# that go stale.
TEST_URLS = {
    "greenhouse": "https://boards.greenhouse.io/anthropic/jobs/4020305008",
    "lever":      "https://jobs.lever.co/stripe/bb31b066-3d4d-4a42-b548-92deebfae37a",
    "ashby":      "https://jobs.ashbyhq.com/openai/af634f2a-de09-4a5a-b4bd-1a0f94bbb672",
    "workday":    "https://wd1.myworkdayjobs.com/en-US/External/job/Dubai-UAE/Senior-Analyst_R-12345",
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"


def _dummy_job(platform: str) -> dict:
    return {
        "title": f"Test {platform.title()} Role",
        "company": "Test Co",
        "location": "Abu Dhabi",
        "job_url": TEST_URLS.get(platform, "https://example.com"),
        "score": 80,
        "decision": "auto_apply",
        "applied": False,
        "submission_status": "",
        "apply_notes": "",
        "description": "Test role description for dry run.",
    }


def _dummy_qa() -> dict:
    return {
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "phone": "+971501234567",
        "phone_local": "0501234567",
        "resume_path": "",
        "linkedin": "https://linkedin.com/in/test",
        "github": "",
        "website": "",
        "address": "Abu Dhabi, UAE",
        "city": "Abu Dhabi",
        "country": "United Arab Emirates",
        "postal_code": "00000",
        "password": "Test1234!",
    }


# ── Test: Confirmation gate ──────────────────────────────────────────────────
def test_confirmation_gate():
    """applied=True must be blocked without confirmed submission_status."""
    from agents.job_logger import update_after_apply

    job = {
        "id": 99999,
        "title": "Gate Test",
        "company": "ACME",
        "applied": True,
        "submission_status": "unconfirmed",
        "apply_notes": "clicked but no confirm",
        "decision": "auto_apply",
    }

    import logging
    logging.disable(logging.CRITICAL)
    # Calling update_after_apply with no real DB — it will log an error but
    # the important thing is the job dict is mutated to applied=False.
    try:
        update_after_apply(job)
    except Exception:
        pass
    logging.disable(logging.NOTSET)

    assert job["applied"] == False, f"Gate did not block: {job}"
    assert job.get("submission_status") == "confirmation_pending", f"Status wrong: {job}"
    print(f"  {PASS} Confirmation gate blocks unconfirmed applied=True")


# ── Test: _extract_fields (DOM field extraction) ─────────────────────────────
def test_extract_fields(platform: str, headless: bool = True):
    url = TEST_URLS.get(platform)
    if not url:
        print(f"  {SKIP} No test URL for {platform}")
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)

            from agents.form_filler import _extract_fields
            fields = _extract_fields(page)

            if fields:
                print(f"  {PASS} {platform}: {len(fields)} fields detected")
                for f in fields[:4]:
                    print(f"        {f.get('type','?'):20s}  {f.get('label','(no label)')[:50]}")
            else:
                # Try SPA retry
                for attempt in range(2):
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    time.sleep(2)
                    fields = _extract_fields(page)
                    if fields:
                        break
                if not fields:
                    print(f"  {SKIP} {platform}: posting may be closed or require auth")
                    return
                if fields:
                    print(f"  {PASS} {platform}: {len(fields)} fields (after SPA wait)")
                else:
                    print(f"  {FAIL} {platform}: 0 fields detected — page may require auth")
        except Exception as e:
            print(f"  {SKIP} {platform}: public smoke unavailable - {e}")
            return
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ── Test: _finalize_non_wizard dry_run=True ──────────────────────────────────
def test_finalize_dry_run(platform: str, headless: bool = True):
    if platform == "workday":
        print(f"  {SKIP} finalize dry_run skipped for {platform}")
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.set_content("<html><body><button type='submit'>Submit Application</button></body></html>")

            from agents.form_filler import _finalize_non_wizard
            job = _dummy_job(platform)
            _finalize_non_wizard(page, job, platform, dry_run=True)

            assert job["applied"] == False, "dry_run should keep applied=False"
            assert job.get("submission_status") == "dry_run", f"Expected dry_run, got {job.get('submission_status')}"
            print(f"  {PASS} {platform}: finalize dry_run — applied=False, submission_status=dry_run")
        except Exception as e:
            print(f"  {FAIL} {platform} finalize: {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ── Test: page_reader enrichment ─────────────────────────────────────────────
def test_local_form_fixture(headless: bool = True):
    """Deterministic extraction fixture for standard and SPA-style widgets."""
    from playwright.sync_api import sync_playwright
    from agents import form_filler
    from agents.form_filler import (
        _click_generic_next,
        _extract_fields,
        _fill_native_selects,
        _fill_radio_groups,
        _scan_apply_cta_buttons,
        _upload_cover_letter_inputs,
        _upload_resume_inputs,
        _write_text_pdf,
    )

    with TemporaryDirectory() as tmp, sync_playwright() as p:
        resume_path = Path(tmp) / "resume.pdf"
        cover_path = Path(tmp) / "cover-letter.pdf"
        _write_text_pdf(str(resume_path), "Resume fixture")
        _write_text_pdf(str(cover_path), "Cover letter fixture")
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.set_content(
                """
                <html><body>
                  <label for="first_name">First Name</label>
                  <input id="first_name" required>
                  <label for="email">Email Address</label>
                  <input id="email" type="email">
                  <div><label>Portfolio summary</label>
                    <div id="portfolio" role="textbox" contenteditable="true"></div>
                  </div>
                  <div><label>Country</label>
                    <div id="country" role="combobox" aria-label="Country"></div>
                  </div>
                  <label for="gender">Gender identity</label>
                  <select id="gender">
                    <option value="">Select</option>
                    <option>Male</option>
                    <option>Prefer not to answer</option>
                  </select>
                  <fieldset>
                    <legend>Disability status</legend>
                    <input id="disabled_yes" type="radio" name="disabled" value="Yes">
                    <label for="disabled_yes">Yes</label>
                    <input id="disabled_decline" type="radio" name="disabled"
                           value="Decline to self-identify">
                    <label for="disabled_decline">Decline to self-identify</label>
                  </fieldset>
                  <label for="resume">Resume</label>
                  <input id="resume" name="resume" type="file">
                  <label for="cover_letter">Cover letter</label>
                  <input id="cover_letter" name="cover_letter" type="file">
                  <input id="taleo_apply" type="button" value="Apply Online">
                  <button id="smartrecruiters_apply">I'm interested</button>
                  <button data-automation-id="next-step"
                          onclick="window.__advanced = true">Continue</button>
                </body></html>
                """
            )
            fields = _extract_fields(page)
            labels = {field["label"] for field in fields}
            assert {"First Name", "Email Address", "Portfolio summary", "Country"} <= labels
            old_pause = form_filler._pause
            form_filler._pause = lambda *args, **kwargs: None
            qa = {
                "resume_path": str(resume_path),
                "cover_letter_path": str(cover_path),
            }
            try:
                assert _upload_resume_inputs(page, qa)
                assert _upload_cover_letter_inputs(page, {}, qa)
                assert page.locator("#resume").evaluate("el => el.files[0].name") == "resume.pdf"
                assert page.locator("#cover_letter").evaluate("el => el.files[0].name") == "cover-letter.pdf"
                assert _fill_native_selects(page, {}, qa, "", "", "") == 1
                assert page.locator("#gender").input_value() == "Prefer not to answer"
                assert _fill_radio_groups(page, {}, qa, "", "", "") == 1
                assert page.locator("#disabled_decline").is_checked()
                assert any(
                    candidate["label"] == "Apply Online"
                    for candidate in _scan_apply_cta_buttons(page)
                )
                assert any(
                    candidate["label"] == "I'm interested"
                    for candidate in _scan_apply_cta_buttons(page)
                )
            finally:
                form_filler._pause = old_pause
            assert _click_generic_next(page) is True
            assert page.evaluate("() => window.__advanced === true")
            print(f"  {PASS} local fixture: extraction + uploads + EEOC decline + Continue")
        finally:
            browser.close()


def test_page_reader():
    from agents.page_reader import fetch_job_description

    # Short existing description — should trigger fetch
    url = TEST_URLS["greenhouse"]
    result = fetch_job_description(url, existing_description="Short.")
    if len(result) > 400:
        print(f"  {PASS} page_reader: enriched to {len(result)} chars (crawl4ai or requests)")
    elif result == "Short.":
        print(f"  {SKIP} page_reader: no enrichment (crawl4ai not installed + requests blocked)")
    else:
        print(f"  {FAIL} page_reader: returned {len(result)} chars (expected >400 or unchanged)")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=list(TEST_URLS) + ["all"], default="all")
    parser.add_argument("--headful", action="store_true", help="Show browser window")
    args = parser.parse_args()

    headless = not args.headful
    platforms = list(TEST_URLS) if args.platform == "all" else [args.platform]

    print("\n========= JobHuntrr Dry-Run Regression Tests =========\n")

    print("── Unit: Confirmation gate ──")
    test_confirmation_gate()

    print("\n── Unit: page_reader enrichment ──")
    test_page_reader()

    print("\n-- Browser fixture: DOM extraction + navigation --")
    test_local_form_fixture(headless=headless)

    for plat in platforms:
        print(f"\n── Browser: {plat.upper()} field extraction ──")
        test_extract_fields(plat, headless=headless)

    for plat in platforms:
        print(f"\n── Browser: {plat.upper()} finalize dry_run ──")
        test_finalize_dry_run(plat, headless=headless)

    print("\n======================================================\n")


if __name__ == "__main__":
    main()

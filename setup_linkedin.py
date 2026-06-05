"""
LinkedIn One-Time Login Setup
Run ONCE: python setup_linkedin.py
Credentials are read/saved in data/profile_settings.json (not .env).
Session is saved to data/linkedin_session/ and reused automatically.
"""
import sys
import os
import time
import getpass

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from config.env_settings import bootstrap_settings, update_env_keys, load_env_settings
from playwright.sync_api import sync_playwright

bootstrap_settings()

SESSION_DIR = str(Path(__file__).parent / "data" / "linkedin_session")
os.makedirs(SESSION_DIR, exist_ok=True)

env = load_env_settings()
EMAIL = env.get("LINKEDIN_EMAIL", "").strip()
PASSWORD = env.get("LINKEDIN_PASSWORD", "").strip()

print("=" * 60)
print("  LinkedIn Session Setup")
print("=" * 60)
print(f"\nSession will be saved to:\n  {SESSION_DIR}\n")

if not EMAIL:
    print("LinkedIn email not in profile settings — enter it now.")
    EMAIL = input("  LinkedIn email: ").strip()

if not PASSWORD:
    print("LinkedIn password not in profile settings — enter it now.")
    PASSWORD = getpass.getpass("  LinkedIn password (hidden): ")

if not EMAIL or not PASSWORD:
    print("\nERROR: both email and password are required to continue.")
    sys.exit(1)

if not env.get("LINKEDIN_EMAIL") or not env.get("LINKEDIN_PASSWORD"):
    print()
    save = input("Save credentials to profile settings for future runs? [Y/n]: ").strip().lower()
    if save != "n":
        update_env_keys({
            "LINKEDIN_EMAIL": EMAIL,
            "LINKEDIN_PASSWORD": PASSWORD,
        })
        bootstrap_settings()
        print("  Saved to data/profile_settings.json\n")

print(f"\nLogging in as: {EMAIL}")
print("Opening browser...\n")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)

    if "feed" in page.url or "mynetwork" in page.url:
        print("Already logged in! Session is valid.")
        ctx.close()
    else:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        time.sleep(1)

        try:
            page.fill("#username", EMAIL, timeout=8000)
            time.sleep(0.5)
            page.fill("#password", PASSWORD, timeout=8000)
            time.sleep(0.5)
            page.click("button[type='submit']", timeout=8000)
            print("Credentials submitted. Waiting for redirect...")
        except Exception as e:
            print(f"Auto-fill failed: {e}")
            print("Please complete login manually in the browser window.")

        timeout = 120
        start = time.time()
        logged_in = False
        while time.time() - start < timeout:
            try:
                url = page.url
                if any(x in url for x in ["/feed", "/mynetwork", "/in/", "/jobs/"]):
                    print(f"\nLogin successful! URL: {url}")
                    logged_in = True
                    break
                elif "checkpoint" in url or "challenge" in url:
                    elapsed = int(time.time() - start)
                    if elapsed % 15 == 0 and elapsed > 0:
                        print(f"  2FA / CAPTCHA — complete in browser... ({elapsed}s)")
            except Exception:
                pass
            time.sleep(2)

        if not logged_in:
            print("\nWarning: Could not confirm login. Session may still be saved.")

        time.sleep(3)
        ctx.close()

print("\nDone. LinkedIn session saved.")
print("The bot will reuse this session for all Easy Apply submissions.")

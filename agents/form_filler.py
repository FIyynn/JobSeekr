"""
Form Filler Agent — Extended
Platforms supported:
  - LinkedIn Easy Apply        (persistent session, multi-step wizard)
  - Workday                    (*.myworkdayjobs.com — G42, Mubadala, ADNOC, banks)
  - Workable                   (workable.com — startups, mid-size UAE)
  - Greenhouse                 (boards.greenhouse.io)
  - Lever                      (jobs.lever.co)
  - Ashby                      (jobs.ashbyhq.com)
  - AI-driven custom portals   (LLM field mapping — handles any unknown ATS)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import logging
import re
import time
import random
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("form_filler")


# ── Per-application session cache ─────────────────────────────────────────────
# Computed once at job start; avoids re-reading disk for every field/LLM call.

class _ApplySession:
    """Lightweight container for objects that are expensive to rebuild per field."""
    __slots__ = ("rules", "facts", "profile", "anchors", "fast_model", "base_url")

    def __init__(self, qa: dict, model: str, base_url: str):
        from config.apply_agent_rules import rules_block
        from config.profile_grounding import (
            anchors_reference_block, format_applicant_facts, get_profile_excerpt,
        )
        self.rules = rules_block()
        self.facts = format_applicant_facts(qa)
        self.profile = get_profile_excerpt(4500)
        self.anchors = anchors_reference_block()
        self.base_url = base_url
        # Fast model for short/factual answers (qwen3:1.7b if configured, else same)
        try:
            import json as _json
            from pathlib import Path as _P
            _s = _P(__file__).parent.parent / "data" / "profile_settings.json"
            if _s.exists():
                _d = _json.loads(_s.read_text(encoding="utf-8"))
                self.fast_model = (_d.get("ollama_model_fast") or "").strip() or model
            else:
                self.fast_model = model
        except Exception:
            self.fast_model = model

    @classmethod
    def build(cls, qa: dict, model: str, base_url: str) -> "_ApplySession":
        try:
            return cls(qa, model, base_url)
        except Exception as e:
            logger.debug("_ApplySession build failed (%s), session will be None", e)
            return None


_THREAD_SESSION: Optional["_ApplySession"] = None  # set at batch start for current job


def _session_rules() -> str:
    if _THREAD_SESSION is not None:
        return _THREAD_SESSION.rules
    from config.apply_agent_rules import rules_block
    return rules_block()


def _session_facts(qa: dict) -> str:
    if _THREAD_SESSION is not None:
        return _THREAD_SESSION.facts
    from config.profile_grounding import format_applicant_facts
    return format_applicant_facts(qa)


def _session_profile() -> str:
    if _THREAD_SESSION is not None:
        return _THREAD_SESSION.profile
    from config.profile_grounding import get_profile_excerpt
    return get_profile_excerpt(4500)


def _session_anchors() -> str:
    if _THREAD_SESSION is not None:
        return _THREAD_SESSION.anchors
    from config.profile_grounding import anchors_reference_block
    return anchors_reference_block()


def _session_fast_model(default_model: str) -> str:
    if _THREAD_SESSION is not None:
        return _THREAD_SESSION.fast_model
    return default_model


def _ollama_post(base_url: str, model: str, prompt: str, **kwargs) -> dict:
    """Thin wrapper around /api/generate that disables qwen3 thinking mode."""
    if "qwen3" in model.lower():
        prompt = prompt.rstrip() + "\n/no_think"
    payload = {"model": model, "prompt": prompt, "stream": False, **kwargs}
    return requests.post(f"{base_url}/api/generate", json=payload)

try:
    from playwright.sync_api import (
        sync_playwright, Page, Browser, BrowserContext,
        TimeoutError as PWTimeout
    )
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed.")

LINKEDIN_SESSION_DIR = str(Path(__file__).parent.parent / "data" / "linkedin_session")


# ── Utilities ──────────────────────────────────────────────────────────────────

# Phrases that indicate a CTA is NOT a job-apply button (newsletter, chat, etc.)
_NON_APPLY_PHRASES = (
    "subscribe", "subscription", "newsletter", "mailing list",
    "sign up for updates", "get updates", "stay updated", "notify me",
    "join our community", "follow us", "chat with", "start chat",
    "live chat", "talk to us", "contact sales", "book a demo",
    "download brochure", "learn more", "read more", "watch video",
    "cookie preferences", "manage cookies",
    "apply manually",  # Workday resume path — not the job posting Apply CTA
)

_SKIP_APPLY_LABELS = frozenset({
    "saved", "save", "save job", "share", "follow", "unfollow", "message",
    "report", "more", "not interested", "dismiss", "close",
    "subscribe", "subscribed", "sign up", "signup", "register", "join",
})


def _apply_cta_score(text: str, aria: str = "") -> int:
    """
    Score how likely an element is a job Apply CTA (higher = better).
    Returns -1 if this is clearly NOT an apply button (e.g. Subscribe).
    """
    combined = f"{text} {aria}".lower().strip()
    if not combined:
        return -1
    # Sidebar job cards often include "Easy Apply" inside multi-line listing text
    if (text or "").count("\n") > 1 or len(text or "") > 100:
        return -1
    if "days ago" in combined and "posted" in combined:
        return -1
    if "actively reviewing applicants" in combined:
        return -1
    if combined.strip() in _SKIP_APPLY_LABELS:
        return -1
    for phrase in _NON_APPLY_PHRASES:
        if phrase in combined:
            return -1
    if any(combined.startswith(p) for p in ("save ", "share ", "follow ", "subscribe ")):
        return -1

    score = 0
    if "easy apply" in combined:
        return 100
    if combined in ("i'm interested", "im interested", "i am interested"):
        return 90
    if re.search(r"\bapply now\b", combined):
        score = 95
    elif re.search(r"\bapply for (this |the )?(job|position|role)\b", combined):
        score = 92
    elif re.search(r"\bapply to\b", combined):
        score = 90
    elif re.search(r"\bapply on company website\b", combined):
        score = 88
    elif combined in ("apply", "apply now", "apply today"):
        score = 85
    elif re.search(r"\bapply\b", combined):
        # Must be a real apply word, not "application for newsletter"
        if "newsletter" in combined or "subscription" in combined:
            return -1
        score = 75
    elif re.search(r"\bsubmit application\b", combined):
        score = 70
    else:
        return -1

    # Penalize mixed labels like "subscribe to apply for updates"
    if "subscribe" in combined and score < 90:
        return -1
    return score


def _dismiss_blocking_popups(page, max_rounds: int = 3) -> int:
    """
    Close chatbots, newsletter modals, cookie banners, and other overlays
    that block the Apply button. Does not close LinkedIn Easy Apply modals.
    """
    dismissed = 0
    dismiss_selectors = (
        # Chat widgets (G42 and similar career sites)
        "[class*='intercom' i] [aria-label*='close' i]",
        "button.intercom-launcher-close",
        "[class*='drift-widget' i] button[aria-label*='close' i]",
        "[class*='drift' i] [aria-label*='close' i]",
        "[class*='chat-widget' i] button[aria-label*='close' i]",
        "[class*='chatbot' i] button[aria-label*='close' i]",
        "[class*='chat-bot' i] button[aria-label*='close' i]",
        "[id*='chat-widget' i] button[aria-label*='close' i]",
        "[id*='chatbot' i] button[aria-label*='close' i]",
        "[class*='live-chat' i] button[aria-label*='close' i]",
        "[data-testid='close-button']",
        "[data-testid='IconButton-Close']",
        # Modal / dialog close (exclude LinkedIn Easy Apply wizard)
        "[role='dialog']:not(.jobs-easy-apply-modal) button[aria-label='Close']",
        "[role='dialog']:not(.jobs-easy-apply-modal) button[aria-label='Dismiss']",
        "[role='alertdialog'] button[aria-label='Close']",
        "div[role='dialog'] button.close",
        "[class*='modal' i]:not(.jobs-easy-apply-modal) button[class*='close' i]",
        "[class*='popup' i] button[class*='close' i]",
        "[class*='overlay' i] button[aria-label*='close' i]",
        # Polite dismiss copy
        "button:has-text('No thanks')",
        "button:has-text('No, thanks')",
        "button:has-text('Not now')",
        "button:has-text('Maybe later')",
        "button:has-text('Dismiss')",
        "button:has-text('Got it')",
        "button:has-text('Continue browsing')",
        # Cookie banners (clear screen so Apply is visible)
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept all cookies')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Allow all')",
        # Generic close
        "[aria-label='Close']:visible",
        "[aria-label='Dismiss']:visible",
        "[title='Close']:visible",
        "button.close:visible",
    )

    for _round in range(max_rounds):
        round_count = 0
        for sel in dismiss_selectors:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 4)):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible(timeout=400):
                            continue
                        # Never close the LinkedIn Easy Apply wizard
                        if el.locator("xpath=ancestor::*[contains(@class,'jobs-easy-apply-modal')]").count() > 0:
                            continue
                    except Exception:
                        continue
                    try:
                        el.click(timeout=2000, force=True)
                        round_count += 1
                        _pause(0.25, 0.5)
                    except Exception:
                        pass
            except Exception:
                pass

        # Chat iframes (Intercom, Drift, HubSpot, etc.)
        for frame in page.frames:
            try:
                furl = (frame.url or "").lower()
                if not any(k in furl for k in ("intercom", "drift", "hubspot", "chat", "zendesk", "tawk")):
                    continue
                for fsel in (
                    "button[aria-label*='close' i]",
                    "button[aria-label*='dismiss' i]",
                    "[data-testid='close-button']",
                    "button.close",
                ):
                    floc = frame.locator(fsel)
                    if floc.count() > 0 and floc.first.is_visible(timeout=400):
                        floc.first.click(timeout=2000)
                        round_count += 1
                        _pause(0.25, 0.5)
                        break
            except Exception:
                pass

        try:
            page.keyboard.press("Escape")
            _pause(0.2, 0.4)
        except Exception:
            pass

        dismissed += round_count
        if round_count == 0:
            break

    if dismissed:
        logger.info(f"  Dismissed {dismissed} popup/overlay(s)")
    return dismissed


def _scan_apply_cta_buttons(page, scope=None) -> list[dict]:
    """Find visible buttons/links ranked by apply relevance (best first)."""
    root = scope if scope is not None else page
    loc = root.locator(
        "button:visible, a:visible, input[type='submit']:visible, "
        "input[type='button']:visible, "
        "[role='button']:visible"
    )
    results: list[dict] = []
    seen: set[str] = set()

    for i in range(min(loc.count(), 80)):
        el = loc.nth(i)
        try:
            if not el.is_visible(timeout=500):
                continue
            text = (el.inner_text(timeout=800) or "").strip()
            aria = (el.get_attribute("aria-label") or "").strip()
            value = (el.get_attribute("value") or "").strip()
            label = text or aria or value
            score = _apply_cta_score(text, f"{aria} {value}")
            if score < 0:
                continue
            key = f"{score}:{label.lower()[:60]}"
            if key in seen:
                continue
            seen.add(key)
            results.append({"score": score, "label": label, "locator": el})
        except Exception:
            continue

    results.sort(key=lambda c: -c["score"])
    return results


def _pause(lo: float = 0.5, hi: float = 2.0):
    time.sleep(random.uniform(lo, hi))


# ── Vision helpers (screenshot -> Ollama vision model) ─────────────────────────

import base64

def _screenshot_b64(page, quality: int = 75) -> str:
    """Capture full-page screenshot and return as base64 JPEG string."""
    try:
        return base64.b64encode(
            page.screenshot(type="jpeg", quality=quality, full_page=False)
        ).decode()
    except Exception:
        return ""


def _vision_find_button(page, label: str, vision_model: str, base_url: str) -> Optional[tuple]:
    """
    Take a screenshot and ask the vision model where a button/link is.
    Returns (x, y) pixel coordinates to click, or None if not found.
    Viewport is always 1440x900.
    """
    b64 = _screenshot_b64(page)
    if not b64:
        return None

    prompt = (
        f"This is a screenshot of a webpage (1440x900 pixels).\n"
        f"I need to find and click: \"{label}\"\n"
        f"If you can clearly see it, respond EXACTLY with:  FOUND x y\n"
        f"where x and y are the pixel coordinates of the CENTER of that element.\n"
        f"If it is not visible, respond EXACTLY with:  NOT_FOUND\n"
        f"Nothing else - no explanation."
    )
    try:
        r = requests.post(f"{base_url}/api/generate", json={
            "model": vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 80},
        }, timeout=90)
        resp = r.json().get("response", "").strip()
        if not resp:
            logger.debug("  Vision: empty response for button find")
        elif "NOT_FOUND" in resp.upper():
            logger.debug(f"  Vision: {label} not on screen")
        if resp.upper().startswith("FOUND"):
            parts = resp.split()
            if len(parts) >= 3:
                x, y = int(float(parts[1])), int(float(parts[2]))
                logger.info(f"  Vision found '{label}' at ({x}, {y})")
                return x, y
    except Exception as e:
        logger.debug(f"  Vision button find error: {e}")
    return None


def _vision_fill_form(page, job: dict, qa: dict, profile: str,
                      vision_model: str, base_url: str) -> bool:
    """
    Take a screenshot and ask the vision model to identify all form fields.
    Fills each field by clicking at its coordinates and typing the value.
    Used as a fallback when DOM scraping finds no fields.
    """
    b64 = _screenshot_b64(page)
    if not b64:
        return False

    candidate = (
        f"Full name: {qa.get('full_name')} | First: {qa.get('first_name')} | "
        f"Last: {qa.get('last_name')} | Email: {qa.get('email')} | "
        f"Phone: {qa.get('phone')} | LinkedIn: {qa.get('linkedin')} | "
        f"Location: Abu Dhabi, UAE | Nationality: Emirati (UAE National) | "
        f"Languages: Arabic (fluent), English (fluent) | Excel: yes | "
        f"Work auth: Yes (no visa needed) | Visa sponsorship: No"
    )

    prompt = (
        f"This is a job application form for '{job.get('title')}' at '{job.get('company')}'.\n"
        f"Candidate info: {candidate}\n\n"
        f"Identify ALL visible text inputs, email fields, phone fields, dropdowns, and textareas.\n"
        f"For each field, provide the pixel coordinates of its center and the value to fill.\n"
        f"Return ONLY a valid JSON array. Example:\n"
        f'[{{"field":"First Name","x":320,"y":240,"value":"{qa.get("first_name", "Candidate")}"}},'
        f'{{"field":"Email","x":320,"y":300,"value":"{qa.get("email", "")}"}}]\n'
        f"Viewport is 1440x900. Return ONLY the JSON array, nothing else."
    )
    try:
        r = requests.post(f"{base_url}/api/generate", json={
            "model": vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 600},
        }, timeout=90)
        raw = r.json().get("response", "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        raw = raw.replace("```json", "").replace("```", "")
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            logger.warning("  Vision form: no JSON returned")
            return False

        candidate = re.sub(r",\s*([}\]])", r"\1", m.group())
        try:
            fields = json.loads(candidate)
        except Exception:
            logger.warning("  Vision form: unparseable JSON array")
            return False
        filled = 0
        for f in fields:
            try:
                x, y = int(f["x"]), int(f["y"])
                value = str(f.get("value", ""))
                if not value:
                    continue
                page.mouse.click(x, y)
                _pause(0.3, 0.6)
                page.keyboard.press("Control+a")
                page.keyboard.type(value)
                filled += 1
                _pause(0.2, 0.4)
            except Exception as e:
                logger.debug(f"  Vision fill field error: {e}")

        logger.info(f"  Vision form: filled {filled}/{len(fields)} fields")

        # Try resume upload after vision fill
        try:
            for fi in page.locator("input[type='file']").all():
                fi.set_input_files(qa["resume_path"])
                logger.info("  Resume uploaded")
                _pause(1.5, 3.0)
                break
        except Exception:
            pass

        return filled > 0
    except Exception as e:
        logger.error(f"  Vision form error: {e}")
        return False


def _vision_verify_step(page, vision_model: str, base_url: str) -> str:
    """
    Take a screenshot and ask vision what step/state the page is in.
    Returns a short description like 'login page', 'step 2 of 3', 'confirmation', etc.
    """
    b64 = _screenshot_b64(page)
    if not b64:
        return "unknown"
    prompt = (
        "This is a screenshot of a job application webpage.\n"
        "In ONE short sentence (max 10 words), describe what stage this is:\n"
        "e.g. 'Login page', 'Step 2 of 4 - work experience', 'Application submitted confirmation'\n"
        "Respond with ONLY that sentence."
    )
    try:
        r = requests.post(f"{base_url}/api/generate", json={
            "model": vision_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 30},
        }, timeout=45)
        return r.json().get("response", "").strip()
    except Exception:
        return "unknown"

def _screenshot(page, job: dict, suffix: str = "") -> str:
    os.makedirs("logs", exist_ok=True)
    company = re.sub(r"[^a-zA-Z0-9_]", "_", job.get("company", "unknown"))[:20]
    title   = re.sub(r"[^a-zA-Z0-9_]", "_", job.get("title",   "unknown"))[:25]
    path = f"logs/{company}_{title}{suffix}.png"
    try:
        page.screenshot(path=path, full_page=False)
    except Exception:
        pass
    return path

def _try_fill(page, selectors, value: str, timeout_ms: int = 800) -> bool:
    if not value:
        return False
    for sel in (selectors if isinstance(selectors, list) else [selectors]):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=timeout_ms):
                el.fill(value)
                _pause(0.2, 0.5)
                return True
        except Exception:
            pass
    return False


_DEFAULT_PHONE = "+971505612301"


def _ensure_qa_contact(qa: dict, force_reload: bool = False) -> None:
    """
    Always pull live phone/email/links from data/profile_settings.json.
    Set ``force_reload=True`` at the start of each apply batch so GUI edits
    propagate without restarting the process.
    """
    try:
        from config.env_settings import bootstrap_settings
        bootstrap_settings()
    except Exception:
        pass
    # Phone — refresh from settings every time (otherwise GUI edits never apply)
    env_phone = os.getenv("APPLICANT_PHONE", "").strip()
    if force_reload or not qa.get("phone"):
        qa["phone"] = env_phone or qa.get("phone") or _DEFAULT_PHONE
    env_phone_local = os.getenv("APPLICANT_PHONE_LOCAL", "").strip()
    if force_reload or not qa.get("phone_local"):
        qa["phone_local"] = env_phone_local or qa.get("phone_local", "")
    env_email = os.getenv("APPLICANT_EMAIL", "").strip()
    if force_reload or not qa.get("email"):
        qa["email"] = env_email or qa.get("email", "")
    try:
        from config.env_settings import load_signup_defaults
        signup = load_signup_defaults()
        if signup.get("email"):
            qa["email"] = signup["email"]
        for key, val in signup.items():
            if key in ("email",) or not val:
                continue
            if force_reload or not qa.get(key):
                qa[key] = val
    except Exception:
        pass
    # Pull saved links from profile_settings.json (so LinkedIn / website always match)
    try:
        from config.env_settings import load_all_settings
        prof = load_all_settings().get("profile", {})
        if prof.get("linkedin"):
            qa.setdefault("linkedin", prof["linkedin"])
            if force_reload:
                qa["linkedin"] = prof["linkedin"]
        if prof.get("website"):
            qa["website"] = prof["website"]
        if prof.get("github"):
            qa["github"] = prof["github"]
        if prof.get("resume_path") and Path(prof["resume_path"]).exists():
            if force_reload or not qa.get("resume_path"):
                qa["resume_path"] = prof["resume_path"]
        if prof.get("cover_letter_path") and Path(prof["cover_letter_path"]).exists():
            if force_reload or not qa.get("cover_letter_path"):
                qa["cover_letter_path"] = prof["cover_letter_path"]
        elif force_reload:
            qa.pop("cover_letter_path", None)
    except Exception:
        pass
    try:
        from config.apply_agent_rules import get_resume_path
        rp = get_resume_path()
        if rp and Path(rp).exists():
            if force_reload or not qa.get("resume_path"):
                qa["resume_path"] = rp
    except Exception:
        pass


def _phone_national_uae(phone: str, with_leading_zero: bool = True,
                         qa: Optional[dict] = None) -> str:
    """
    UAE local-format number for forms with a separate country-code field.
    Prefers the user-provided `phone_local` from Profile Settings when set.
    With leading zero (default): 0505612301
    Without leading zero:        505612301
    """
    if qa and qa.get("phone_local"):
        local = re.sub(r"\D", "", str(qa["phone_local"]))
        if local:
            if with_leading_zero and not local.startswith("0"):
                local = "0" + local
            elif not with_leading_zero and local.startswith("0"):
                local = local[1:]
            return local
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("971"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    return ("0" + digits) if with_leading_zero else digits


def _page_has_intl_tel_input(page) -> bool:
    """True if the page uses intl-tel-input (flag dropdown + national number)."""
    try:
        for sel in (
            ".iti input[type='tel']",
            "input.iti__tel-input",
            ".iti__flag-container",
        ):
            if page.locator(sel).first.is_visible(timeout=400):
                return True
    except Exception:
        pass
    return False


def _fill_intl_tel_phone(page, qa: dict) -> bool:
    """Set UAE (+971) and national number on intl-tel-input widgets (Greenhouse, etc.)."""
    _ensure_qa_contact(qa)
    if not _page_has_intl_tel_input(page):
        return False
    national = _phone_national_uae(
        qa.get("phone", ""), with_leading_zero=False, qa=qa
    )
    try:
        flag = page.locator(
            ".iti__selected-country, .iti__flag-container, .iti__selected-flag"
        ).first
        if flag.is_visible(timeout=800):
            flag.click(timeout=5000)
            _pause(0.4, 0.7)
            for country_sel in (
                "li.iti__country:has-text('United Arab Emirates')",
                ".iti__country-list li:has-text('United Arab Emirates')",
                "[data-country-code='ae']",
            ):
                try:
                    opt = page.locator(country_sel).first
                    if opt.is_visible(timeout=1200):
                        opt.click(timeout=5000)
                        _pause(0.3, 0.5)
                        break
                except Exception:
                    continue
        tel = page.locator(
            "input.iti__tel-input, .iti input[type='tel'], #phone"
        ).first
        if tel.is_visible(timeout=800):
            tel.click(timeout=3000)
            tel.fill("")
            tel.fill(national)
            logger.info(f"  intl-tel phone set (UAE national): {national}")
            return True
    except Exception as e:
        logger.debug(f"  intl-tel fill failed: {e}")
    return False


def _page_has_country_code_field(page) -> bool:
    """True if the current page/modal exposes a separate phone country-code input."""
    if _page_has_intl_tel_input(page):
        return True
    try:
        selectors = (
            "select[id*='phoneNumber-country'], select[id*='phone-country']",
            "input[id*='phoneNumber-country'], input[id*='phone-country']",
            "select[aria-label*='Phone country' i], select[aria-label*='Country code' i]",
            "input[aria-label*='Country code' i]",
            "select[name*='country_code' i], input[name*='country_code' i]",
            "[role='combobox'][aria-label*='Country' i]",
        )
        for sel in selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=400):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _select_uae_country_code(page) -> None:
    """Pick United Arab Emirates (+971) in any visible phone country-code selector."""
    selectors = (
        "select[id*='phoneNumber-country']",
        "select[id*='phone-country']",
        "select[name*='country_code' i]",
        "select[aria-label*='Phone country' i]",
        "select[aria-label*='Country code' i]",
    )
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if not el.is_visible(timeout=300):
                continue
            for label in (
                "United Arab Emirates (+971)",
                "United Arab Emirates",
                "UAE (+971)",
                "+971",
            ):
                try:
                    el.select_option(label=label)
                    logger.debug(f"  Country code set via {sel}: {label}")
                    return
                except Exception:
                    continue
            try:
                el.select_option(value="ae")
                return
            except Exception:
                pass
        except Exception:
            continue


def _qa_value_for_label(label: str, qa: dict) -> Optional[str]:
    """
    Map a form label to APPLICATION_QA — never use LLM for contact / identity / common-ATS fields.
    Returns None only for true free-text fields (why us, cover letter, etc.).
    """
    _ensure_qa_contact(qa)
    q = (label or "").lower().strip()
    if not q:
        return None
    if _is_sensitive_eeoc_label(q):
        return "Decline to self-identify"

    # Contact
    if re.search(r"\b(phone|mobile|cell|telephone|tel|whatsapp)\b", q):
        if "country" in q and "code" in q:
            return None  # handled by _select_uae_country_code
        if re.search(r"\bnational\b", q) or "phone number" in q or "mobile number" in q:
            return _phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
        return qa.get("phone", "")
    if re.search(r"\be-?mail\b", q):
        return qa.get("email", "")
    if re.search(r"\bconfirm.*password\b|\bpassword.*confirm\b|\bre-?type.*password\b", q):
        return qa.get("password", "")
    if re.search(r"\bpassword\b", q) and "current" not in q and "old" not in q:
        return qa.get("password", "")
    if re.search(r"\bfirst\s*name\b|\bgiven\s*name\b", q):
        return qa.get("first_name", "")
    if re.search(r"\blast\s*name\b|\bsurname\b|\bfamily\s*name\b", q):
        return qa.get("last_name", "")
    if re.search(r"\bmiddle\s*name\b", q):
        return qa.get("middle_name", "")
    if re.search(r"\bfull\s*name\b|\bpreferred\s*name\b", q) or q == "name":
        return qa.get("full_name", "")
    if "linkedin" in q:
        return qa.get("linkedin", "")
    if re.search(r"\bportfolio|personal\s*site|website|web\s*site|url\b", q):
        return qa.get("website", "")
    if "github" in q:
        return qa.get("github", "")

    # Location / citizenship
    if re.search(r"\bstreet\b|\baddress\s*line\b|\baddress\s*1\b", q):
        return qa.get("address", "") or qa.get("location", "Abu Dhabi, UAE")
    if re.search(r"\bcity\b|\btown\b", q) and "work" not in q and "preferred" not in q:
        return qa.get("city", "") or qa.get("location", "Abu Dhabi, UAE")
    if re.search(r"\bstate\b|\bprovince\b|\bregion\b", q):
        return qa.get("state", "")
    if re.search(
        r"\bcurrent\s+location\b|\bresidence\b|\baddress\b|\blocation\b|"
        r"\blocated\b|\breside\b|\blive\b|\bbased\b",
        q,
    ):
        if "work" not in q and "preferred" not in q and "email" not in q:
            return qa.get("location") or "Abu Dhabi, UAE"
    if "country" in q and "code" not in q:
        return qa.get("country", "United Arab Emirates")
    if "postal" in q or "zip" in q:
        return qa.get("postal_code", "00000")
    if re.search(r"\bnationalit|\bcitizenship\b", q):
        return qa.get("nationality", "Emirati (UAE National)")

    # Work auth / visa
    if re.search(r"\bauthori[sz]ed\s+to\s+work\b|\blegally\s+authori[sz]ed\b|"
                 r"\bright\s+to\s+work\b|\beligible\s+to\s+work\b", q):
        return "Yes"
    if re.search(r"\bvisa\s+sponsorship\b|\brequire(s|d)?\s+sponsorship\b|"
                 r"\bsponsor(ship)?\s+required\b", q):
        return "No"
    if re.search(r"\bwork\s+permit\b", q):
        return "Not required (UAE National)"

    # Education
    if re.search(r"\bwhat\s+is\s+your\s+qualification\b|\byour\s+qualification\b", q):
        return qa.get("education_level", "Bachelor's Degree")
    if re.search(r"\b(highest\s+)?(level\s+of\s+)?education\b|\beducation\s+level\b|"
                 r"\bdegree\b|\bhighest\s+qualification\b|\bqualification\s+level\b", q) \
            and "field" not in q and "study" not in q:
        return qa.get("education_level", "Bachelor's Degree")
    if "field of study" in q or "major" in q or "degree field" in q:
        return qa.get("degree_field", "Mathematics with Computer Science Minor")
    if re.search(r"\buniversity\b|\bschool\b|\binstitution\b", q) and not re.search(
        r"\bgrade\b|\bgpa\b|\bscore\b|\bstatus\b|\blevel\b|\byear\b|\bmajor\b|"
        r"\bfield\b|\bmark\b|\bpercentage\b|\bcgpa\b", q
    ):
        return qa.get("university", "New York University, New York (NYU New York)")
    if "graduation" in q or "grad year" in q or "year of graduation" in q:
        return qa.get("graduation_year", "2024")

    # Experience / role
    if re.search(r"\byears\s+(of\s+)?(experience|work)\b|\btotal\s+experience\b", q):
        val = qa.get("years_experience", "1-2 years")
        # Strip to a plain integer for number inputs; the caller handles type-safety
        nums = re.findall(r'\d+', str(val))
        return nums[-1] if nums else "2"
    if re.search(r"\bcurrent\s+(role|title|position|job)\b", q):
        return "Founder & CEO, Polygon Technical Infrastructures"
    if re.search(r"\bcurrent\s+(company|employer)\b", q):
        return "Polygon Technical Infrastructures"
    if re.search(r"\bnotice\s+period\b", q):
        return "Immediately available"
    if re.search(r"\bavailable\s+to\s+start\b|\bstart\s+date\b|\bavailability\b|"
                 r"\bwhen\s+can\s+you\s+start\b|\bearliest\s+start\b", q):
        return qa.get("start_date", "Immediately")
    if re.search(r"\brelocat", q):
        return qa.get("willing_to_relocate", "Yes, within UAE and GCC")

    # Compensation
    if re.search(r"\bsalary\s+type\b|\bpay\s+type\b|\bcompensation\s+type\b", q):
        return "Annual"
    if re.search(r"\bpay\s+period\b|\bpayment\s+frequency\b", q):
        return "Monthly"
    if re.search(r"\bcurrency\b", q):
        return "AED"
    if re.search(r"\bsalary\b|\bcompensation\b|\bexpected\s+pay\b|\bdesired\s+salary\b", q):
        return qa.get("salary_expectation", "Competitive / open to discussion")

    # Demographics
    if re.search(r"\bdate\s+of\s+birth\b|\bdob\b|\bbirth\s+date\b|\bbirthday\b", q):
        return qa.get("date_of_birth", "")
    if "pronoun" in q:
        return "He/Him"

    # Languages / skills
    if "language" in q:
        return qa.get("languages", "Arabic (fluent), English (fluent)")
    if "excel" in q:
        return qa.get("excel", "Advanced - financial models, analysis, reporting")

    # Employment / contract type
    if re.search(r"\bemployment\s+type\b|\bcontract\s+type\b|\bjob\s+type\b", q):
        return "Full-time"
    if re.search(r"\bwork\s+type\b|\bwork\s+arrangement\b|\bwork\s+mode\b", q):
        return "On-site"
    if re.search(r"\bfull.?time\b", q) and re.search(r"\bare you\b|\bdo you\b|\bwill you\b", q):
        return "Yes"
    if re.search(r"\bpart.?time\b", q) and re.search(r"\bare you\b|\bdo you\b", q):
        return "No"

    # Compliance / consent
    if re.search(r"\bcriminal\b|\bfelony\b|\bconvict", q):
        return "No"
    if re.search(r"\bbackground\s+check\b", q) and "consent" in q:
        return "Yes"
    if re.search(r"\bprivacy\b|\bterms\b|\bconsent\b|\bagree\b|\backnowledge\b", q):
        return "Yes"

    # Travel / remote
    if re.search(r"\bwilling\s+to\s+travel\b|\btravel\s+required\b|\btravel\s+availability\b", q):
        return "Yes"
    if re.search(r"\bopen\s+to\s+remote\b|\bremote\s+work\b", q):
        return "Yes"

    # Referral / source
    if "how did you hear" in q or "referral" in q or "source" in q:
        return "LinkedIn"

    # Age confirmation (UAE: 21+ for most roles)
    if re.search(r"\bage\b|\bover\s+18\b|\b18\+\b|\bof\s+legal\s+age\b|\bover 21\b", q):
        return "Yes"

    # Smoke / tobacco
    if re.search(r"\bsmoke\b|\btobacco\b", q):
        return "No"

    # Currently employed
    if re.search(r"\bcurrently\s+employed\b|\bcurrently\s+working\b", q):
        return "Yes"

    # Worked here before
    if re.search(r"\bpreviously\s+employed\b|\bformer\s+employee\b|\bworked\s+for\s+us\b", q):
        return "No"

    # Related to employee
    if re.search(r"\brelative\b.{0,30}\bemploy|\bfamily\s+member\b.{0,30}\bwork", q):
        return "No"

    # Non-compete / NDA
    if re.search(r"\bnon.?compete\b|\bnon.?disclosure\b", q) and "do you have" in q:
        return "No"

    # Yes/No — common ATS (fact sheet / rules; no LLM)
    from config.profile_grounding import try_rule_based_answer
    yn = try_rule_based_answer(label, qa)
    if yn is not None and yn.strip().lower() in ("yes", "no"):
        return yn

    return None


def _apply_qa_contact_fields(page, qa: dict) -> None:
    """Fill phone/email/name on any visible form — used before LLM loops."""
    _ensure_qa_contact(qa)
    if _fill_intl_tel_phone(page, qa):
        _try_fill(page, ["input[type='email']", "input[name*='email']",
                         "input[id*='email']"], qa.get("email", ""))
        _try_fill(page, ["input[name*='firstName']", "input[id*='firstName']",
                         "#first_name"], qa.get("first_name", ""))
        _try_fill(page, ["input[name*='lastName']", "input[id*='lastName']",
                         "#last_name"], qa.get("last_name", ""))
        return
    phone = qa.get("phone", "")
    has_cc = _page_has_country_code_field(page)
    if has_cc:
        _select_uae_country_code(page)
        phone_value = _phone_national_uae(phone, with_leading_zero=True, qa=qa)  # 0505612301
    else:
        phone_value = phone  # +971505612301
    _try_fill(page, [
        "input[id*='phoneNumber-nationalNumber']",
        "input[id*='phoneNumber']",
        "input[type='tel']",
        "input[id*='phone']",
        "input[name*='phone']",
        "input[aria-label*='Phone' i]",
        "input[placeholder*='Phone' i]",
        "input[placeholder*='Mobile' i]",
    ], phone_value)
    _try_fill(page, ["input[type='email']", "input[name*='email']",
                     "input[id*='email']"], qa.get("email", ""))
    _try_fill(page, ["input[name*='firstName']", "input[id*='firstName']"],
              qa.get("first_name", ""))
    _try_fill(page, ["input[name*='lastName']", "input[id*='lastName']"],
              qa.get("last_name", ""))


# ── Ollama answer generator ────────────────────────────────────────────────────

def _apply_rules_block() -> str:
    from config.apply_agent_rules import rules_block
    return rules_block()


def _format_facts(qa: dict) -> str:
    from config.profile_grounding import format_applicant_facts
    return format_applicant_facts(qa)


def _llm_answer(question: str, company: str, role: str, angle: str,
                profile: str, model: str, base_url: str, qa: dict = None) -> str:
    """Profile → saved answers → LLM estimate → ask user (saved to profile).
    Uses cached session data and routes short/factual questions to the fast model.
    """
    from agents.application_qa import resolve_application_answer, is_essay_question
    qa = qa if qa is not None else {}
    _ensure_qa_contact(qa)
    # Short/factual → fast model; essay → full model
    fast = _session_fast_model(model)
    use_model = model if is_essay_question(question) else fast
    # Use cached profile from session (avoids re-reading disk)
    effective_profile = _session_profile() if not (profile or "").strip() else profile
    return resolve_application_answer(
        question, company, role, angle, effective_profile, use_model, base_url, qa,
        qa_value_fn=_qa_value_for_label,
    )


def _fix_step_validation_errors(page, job: dict, qa: dict, profile: str,
                                model: str, base_url: str, root=None) -> int:
    """Read on-page errors and correct field values before clicking Next."""
    from agents.application_qa import fix_validation_errors_on_page
    return fix_validation_errors_on_page(
        page, qa, job, profile, model, base_url,
        qa_value_fn=_qa_value_for_label, root=root,
    )


def _handle_profile_gaps_before_apply(job: dict) -> None:
    """Log missing profile skills without interrupting unattended apply."""
    desc = (job.get("description") or "").strip()
    if not desc:
        return
    try:
        from agents.profile_manager import find_profile_gaps
        missing = find_profile_gaps(desc)
        if not missing:
            return
        logger.info(f"  Profile gaps for this job: {', '.join(missing[:8])}")
    except Exception as e:
        logger.debug(f"  Gap check skipped: {e}")


# ── AI-driven custom portal filler ────────────────────────────────────────────

_FIELD_MAP_PROMPT = """\
You are the applicant filling out YOUR OWN job application.
Use the candidate data verbatim. Never refuse, never say information is unavailable.

{agent_rules}

CANDIDATE DATA (use exactly these values):
{candidate}

Visible form fields (JSON):
{fields}

Rules:
- Free-text answers: first person only ("I am…", "I have…") — never third person.
- Match each field's "label" to the best CANDIDATE DATA key (phone -> phone, etc.).
- For multi-select dropdowns: choose the option text from "options" that best matches.
- For Yes/No questions about work authorization, eligibility, right to work: answer "Yes".
- For visa sponsorship / sponsorship required / work permit needed: answer "No".
- For salary / compensation: use the salary value or "Competitive".
- For unknown free-text / behavioral questions: short honest answer (under 60 words) using
  ONLY employers/projects listed in CANDIDATE DATA allowed_experience_anchors — never invent.
- Skip with "" only when no plausible value exists.
- Do NOT include file inputs.

Return ONLY a JSON object mapping field "sel" -> value. No prose, no markdown."""

def _extract_fields(page) -> list:
    return page.evaluate(r"""() => {
        const fields = [];
        const seen = new Set();
        // Standard inputs + select + textarea
        const els = document.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]),' +
            'textarea, select'
        );
        els.forEach(el => {
            if (!el.offsetParent) return;
            let label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
            if (!label && el.id) {
                const lbl = document.querySelector('label[for="' + el.id + '"]');
                if (lbl) label = lbl.textContent.trim();
            }
            if (!label) {
                let p = el.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!p) break;
                    const lbl = p.querySelector('label,legend,[class*="label"],[class*="title"],[class*="heading"]');
                    if (lbl && lbl !== el) { label = lbl.textContent.trim(); break; }
                    p = p.parentElement;
                }
            }
            const sel = el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : '');
            const key = sel + '|' + label;
            if (sel && label && !seen.has(key)) {
                seen.add(key);
                fields.push({
                    sel, label: label.slice(0, 100),
                    type: el.tagName.toLowerCase() + (el.type ? ':' + el.type : ''),
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => o.text).slice(0, 20) : []
                });
            }
        });
        // ARIA comboboxes and custom dropdowns not captured above
        const combos = document.querySelectorAll(
            '[role="combobox"]:not([aria-hidden="true"]), [aria-haspopup="listbox"]:not([aria-hidden="true"])'
        );
        combos.forEach(el => {
            if (!el.offsetParent) return;
            let label = el.getAttribute('aria-label') || '';
            if (!label) {
                let p = el.parentElement;
                for (let i = 0; i < 5; i++) {
                    if (!p) break;
                    const lbl = p.querySelector('label,legend,[class*="label"]');
                    if (lbl && lbl !== el) { label = lbl.textContent.trim(); break; }
                    p = p.parentElement;
                }
            }
            const sel = el.id ? '#' + el.id : (el.getAttribute('data-testid') ? '[data-testid="' + el.getAttribute('data-testid') + '"]' : '');
            const key = 'combo|' + label;
            if (label && !seen.has(key)) {
                seen.add(key);
                fields.push({
                    sel: sel || '[role="combobox"]', label: label.slice(0, 100),
                    type: 'combobox',
                    required: el.getAttribute('aria-required') === 'true',
                    options: []
                });
            }
        });
        // ARIA textbox role (Angular Material mat-input, Quill rich-text, etc.)
        const textboxes = document.querySelectorAll(
            '[role="textbox"]:not([aria-hidden="true"]), [contenteditable="true"]:not([aria-hidden="true"])'
        );
        textboxes.forEach(el => {
            if (!el.offsetParent) return;
            let label = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '';
            if (!label) {
                let p = el.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!p) break;
                    const lbl = p.querySelector('label,legend,[class*="label"],[class*="title"]');
                    if (lbl && lbl !== el) { label = lbl.textContent.trim(); break; }
                    p = p.parentElement;
                }
            }
            const sel = el.id ? '#' + el.id : (el.getAttribute('data-testid') ? '[data-testid="' + el.getAttribute('data-testid') + '"]' : '');
            const key = 'textbox|' + label;
            if (label && !seen.has(key)) {
                seen.add(key);
                fields.push({
                    sel: sel || '[role="textbox"]', label: label.slice(0, 100),
                    type: el.getAttribute('contenteditable') === 'true' ? 'contenteditable' : 'textbox',
                    required: el.getAttribute('aria-required') === 'true',
                    options: []
                });
            }
        });
        // Angular Material inputs (mat-input, mat-select)
        const matInputs = document.querySelectorAll(
            'mat-form-field input:not([aria-hidden="true"]), mat-form-field textarea:not([aria-hidden="true"]), mat-select:not([aria-hidden="true"])'
        );
        matInputs.forEach(el => {
            if (!el.offsetParent) return;
            let label = el.getAttribute('aria-label') || '';
            if (!label) {
                const ff = el.closest('mat-form-field');
                if (ff) {
                    const lbl = ff.querySelector('mat-label, label');
                    if (lbl) label = lbl.textContent.trim();
                }
            }
            const sel = el.id ? '#' + el.id : 'mat-form-field';
            const key = 'mat|' + label;
            if (label && !seen.has(key)) {
                seen.add(key);
                fields.push({
                    sel, label: label.slice(0, 100),
                    type: el.tagName === 'MAT-SELECT' ? 'mat-select' : ('mat-input:' + (el.type || 'text')),
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: []
                });
            }
        });
        return fields.slice(0, 80);
    }""")


def _loads_lenient_json(raw: str) -> Optional[dict]:
    """Parse the first JSON object from an LLM response, tolerating common defects
    (qwen3 <think> blocks, trailing commas, // comments, smart quotes, code fences).
    Returns a dict or None."""
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    text = text.replace("```json", "").replace("```", "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start:end + 1]
    attempts = [candidate]
    # Repair pass: drop // comments, trailing commas, normalise smart quotes
    repaired = re.sub(r"//[^\n\r]*", "", candidate)
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    attempts.append(repaired)
    for attempt in attempts:
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    # Last resort: salvage individual "selector": "value" pairs
    pairs = re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', candidate)
    if pairs:
        return {k: v for k, v in pairs}
    return None


def _fill_ai_driven_page(page, job: dict, qa: dict, profile: str,
                         model: str, base_url: str, vision_model: str = "") -> bool:
    """
    AI-powered form filler for custom/unknown portals.
    Scans visible form fields via DOM first; falls back to vision if none found.
    Used for: BambooHR, SmartRecruiters, government portals, any custom ATS.
    """
    logger.info("  Using AI-driven field mapper")
    _ensure_qa_contact(qa, force_reload=True)
    _prepare_application_resume(page, qa, platform="")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)
    has_cc = _page_has_country_code_field(page)
    qa_local = dict(qa)
    if has_cc:
        qa_local["phone"] = _phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
        logger.info(f"  Country-code field detected — using local phone {qa_local['phone']}")
    _apply_qa_contact_fields(page, qa_local)
    visual_filled = _fill_visual_custom_controls(page, job, qa_local, profile, model, base_url)
    visual_filled += _fill_native_selects(page, job, qa_local, profile, model, base_url)
    visual_filled += _fill_aria_comboboxes(page, job, qa_local, profile, model, base_url)
    visual_filled += _fill_radio_groups(page, job, qa_local, profile, model, base_url)

    fields = _extract_fields(page)

    # ── SPA / Angular hydration retry ─────────────────────────────────────────
    # Angular, React, and Vue portals (TALENTMATE, SmartRecruiters, etc.) render
    # form fields asynchronously after JS executes.  If the first extraction
    # returns nothing, wait for network idle + a grace period and retry up to 3x.
    if not fields:
        _is_angular = page.evaluate(
            "() => !!(window.getAllAngularRootElements || window.ng || "
            "document.querySelector('[ng-version],[_nghost-],[data-reactroot],[data-v-]'))"
        )
        if _is_angular:
            logger.info("  SPA framework detected — waiting for JS hydration")
        for _attempt in range(3):
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            _pause(1.5, 2.5)
            fields = _extract_fields(page)
            if fields:
                logger.info(f"  Fields found after {_attempt + 1} SPA wait attempt(s): {len(fields)}")
                break
            logger.debug(f"  SPA retry {_attempt + 1}/3 — still no fields")

    if not fields:
        logger.warning("  No form fields detected via DOM (tried SPA retry)")
        if visual_filled:
            logger.info(f"  Custom controls filled without DOM fields: {visual_filled}")
            return True
        if vision_model:
            logger.info("  Falling back to vision-based form fill")
            return _vision_fill_form(page, job, qa, profile, vision_model, base_url)
        return False

    # 1) Deterministic pre-fill for any field whose label maps to a known value.
    #    The LLM only needs to handle whatever is left.
    deterministic = 0
    remaining_fields = []
    for f in fields:
        try:
            val = _qa_value_for_label(f.get("label", ""), qa_local)
            if val is None or val == "":
                remaining_fields.append(f)
                continue
            sel = f.get("sel")
            if not sel:
                remaining_fields.append(f)
                continue
            el = page.locator(sel)
            if el.count() == 0:
                remaining_fields.append(f)
                continue
            first = el.first
            tag = first.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                first.select_option(label=str(val))
            else:
                first.fill(str(val))
            deterministic += 1
            _pause(0.15, 0.35)
        except Exception:
            remaining_fields.append(f)
    if deterministic:
        logger.info(f"  Deterministic fill: {deterministic} field(s) from profile settings")
    visual_filled += _fill_visual_custom_controls(page, job, qa_local, profile, model, base_url)
    if not remaining_fields:
        logger.info("  All fields filled deterministically — no LLM call needed")
        try:
            for fi in page.locator("input[type='file']").all():
                fi.set_input_files(qa.get("resume_path", ""))
                logger.info("  Resume uploaded")
                _pause(0.8, 1.5)
                break
        except Exception:
            pass
        return True

    candidate = {
        "first_name": qa_local.get("first_name"), "last_name": qa_local.get("last_name"),
        "full_name":  qa_local.get("full_name"),  "email":     qa_local.get("email"),
        "phone":      qa_local.get("phone"),
        "phone_country_code": "+971" if has_cc else "",
        "linkedin":   qa.get("linkedin", ""),
        "website":    qa.get("website", ""),
        "github":     qa.get("github", ""),
        "location":   qa.get("location", "Abu Dhabi, UAE"),
        "country":    "United Arab Emirates",
        "nationality": qa.get("nationality", "Emirati (UAE National)"),
        "languages":  qa.get("languages", "Arabic (fluent), English (fluent)"),
        "excel":      qa.get("excel", "Advanced"),
        "years_exp":  qa.get("years_experience", "1-2 years"),
        "education":  f"{qa.get('education_level', '')}, {qa.get('university', '')} ({qa.get('graduation_year', '')})",
        "degree":     qa.get("degree_field", ""),
        "university": qa.get("university", ""),
        "grad_year":  qa.get("graduation_year", ""),
        "current_title":   "Founder & CEO, Polygon Technical Infrastructures",
        "current_company": "Polygon Technical Infrastructures",
        "salary":     qa.get("salary_expectation", "Competitive"),
        "start_date": qa.get("start_date", "Immediately"),
        "notice_period":   "Immediately available",
        "willing_to_relocate": qa.get("willing_to_relocate", "Yes, within UAE and GCC"),
        "work_auth":  "Yes — UAE National, no visa required",
        "visa_needed": "No",
        "gender":     qa.get("gender", "Male"),
        "pronouns":   "He/Him",
    }
    try:
        from config.profile_grounding import anchors_reference_block
        candidate["allowed_experience_anchors"] = anchors_reference_block()
    except Exception:
        pass

    prompt = _FIELD_MAP_PROMPT.format(
        agent_rules=_apply_rules_block(),
        fields=json.dumps(remaining_fields, indent=2),
        candidate=json.dumps(candidate, indent=2))
    try:
        r = _ollama_post(base_url, model, prompt,
                         options={"temperature": 0.1, "num_predict": 800}, timeout=90)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        mappings = _loads_lenient_json(raw)
        if not mappings:
            logger.debug("  AI-driven: no parseable JSON returned (page may only have ARIA widgets)")
            return False
        filled = 0
        for sel, value in mappings.items():
            if not value:
                continue
            try:
                el = page.locator(sel)
                if el.count() == 0:
                    continue
                first = el.first
                tag = first.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    first.select_option(label=str(value))
                else:
                    first.fill(str(value))
                filled += 1
                _pause(0.2, 0.5)
            except Exception as e:
                logger.debug(f"  {sel}: {e}")
        logger.info(f"  AI-driven: filled {filled} fields")
    except Exception as e:
        logger.error(f"  AI-driven error: {e}")
        return False

    visual_filled += _fill_visual_custom_controls(page, job, qa_local, profile, model, base_url)
    if visual_filled:
        logger.info(f"  Custom controls total: {visual_filled} filled/selected")
    fixed = _fix_step_validation_errors(page, job, qa, profile, model, base_url)
    if filled == 0 and deterministic <= 1 and visual_filled == 0 and fixed == 0:
        logger.warning(
            "  AI-driven: insufficient fill evidence - treating page as incomplete"
        )
        job.update({
            "applied": False,
            "decision": "manual_review",
            "apply_notes": "AI-driven portal: could not map enough fields to complete application",
            "submission_status": "incomplete",
        })
        return False

    try:
        for fi in page.locator("input[type='file']").all():
            fi.set_input_files(qa["resume_path"])
            logger.info("  Resume uploaded")
            _pause(1.5, 3.0)
            break
    except Exception:
        pass
    return True


# ── LinkedIn Easy Apply ────────────────────────────────────────────────────────

def _is_oracle_email_gate(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "oraclecloud.com" in url and "/easy-apply/email" in url:
            return True
        body = page.locator("body").inner_text(timeout=1500).lower()
        return (
            "let's get started" in body
            and "what's your email" in body
            and "terms and conditions" in body
        )
    except Exception:
        return False


def _fill_oracle_email_gate(page, job: dict, qa: dict) -> bool:
    """Handle Oracle Recruiting's first email/terms gate."""
    _ensure_qa_contact(qa, force_reload=True)
    email = (qa.get("email") or "").strip()
    filled = 0

    for selector in (
        "input[type='email']",
        "input[name*='email' i]",
        "input[id*='email' i]",
        "input[autocomplete='email']",
        "input[type='text']",
    ):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 6)):
                item = loc.nth(i)
                if not item.is_visible(timeout=600):
                    continue
                if email:
                    current = item.input_value(timeout=600)
                    if current != email:
                        item.fill(email, timeout=4000)
                filled += 1
                break
            if filled:
                break
        except Exception:
            continue

    checked_terms = _click_oracle_terms_checkbox(page)
    if checked_terms:
        logger.info("  Oracle Recruiting: accepted terms and conditions")
    elif _oracle_terms_still_unchecked(page):
        logger.warning("  Oracle Recruiting: terms checkbox still appears unchecked")

    if filled:
        logger.info("  Oracle Recruiting: email gate filled")

    if not _click_oracle_continue(page):
        job.update({
            "applied": False,
            "decision": "manual_review",
            "apply_notes": "Oracle Recruiting: could not advance past email/terms gate",
            "submission_status": "incomplete",
        })
        return False
    return True


def _click_oracle_terms_checkbox(page) -> bool:
    selectors = (
        "label:has-text('I agree with the terms and conditions')",
        "input[type='checkbox']",
        "[role='checkbox']",
        "oj-checkboxset input",
        ".oj-checkboxset input",
    )
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 8)):
                item = loc.nth(i)
                if not item.is_visible(timeout=600):
                    continue
                try:
                    if item.get_attribute("type") == "checkbox" and item.is_checked(timeout=500):
                        return True
                except Exception:
                    pass
                item.click(timeout=4000)
                _pause(0.25, 0.5)
                return True
        except Exception:
            continue

    try:
        return bool(page.evaluate(
            """() => {
                const textRe = /terms and conditions|privacy|consent|agree/i;
                const candidates = Array.from(document.querySelectorAll(
                    'label, input[type=checkbox], [role=checkbox], oj-checkboxset, .oj-checkboxset'
                ));
                for (const el of candidates) {
                    const block = el.closest('label, div, li, section') || el;
                    const text = (block.innerText || el.getAttribute('aria-label') || '').trim();
                    if (!textRe.test(text)) continue;
                    const target = block.querySelector('input[type=checkbox], [role=checkbox]') || el;
                    target.click();
                    return true;
                }
                return false;
            }"""
        ))
    except Exception:
        return False


def _oracle_terms_still_unchecked(page) -> bool:
    try:
        return bool(page.evaluate(
            """() => {
                const textRe = /terms and conditions/i;
                for (const el of document.querySelectorAll('label, div, span')) {
                    if (!textRe.test(el.innerText || '')) continue;
                    const block = el.closest('label, div, li, section') || el;
                    const cb = block.querySelector('input[type=checkbox], [role=checkbox]');
                    if (!cb) return true;
                    if (cb.matches('input')) return !cb.checked;
                    return cb.getAttribute('aria-checked') !== 'true';
                }
                return false;
            }"""
        ))
    except Exception:
        return False


def _click_oracle_continue(page) -> bool:
    selectors = (
        "button:has-text('Continue')",
        "button:has-text('Next')",
        "button:has-text('Save and Continue')",
        "button[type='submit']",
        "oj-button button",
        ".oj-button-button",
        "[role='button'][aria-label*='Continue' i]",
        "[role='button'][aria-label*='Next' i]",
    )
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                btn = loc.nth(i)
                if not btn.is_visible(timeout=600):
                    continue
                label = " ".join(filter(None, [
                    btn.inner_text(timeout=600) if hasattr(btn, "inner_text") else "",
                    btn.get_attribute("aria-label") or "",
                    btn.get_attribute("title") or "",
                ])).strip().lower()
                if any(word in label for word in ("submit", "send application", "withdraw")):
                    continue
                btn.click(timeout=6000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                _pause(1.0, 1.8)
                logger.info(f"  Oracle Recruiting: advanced via {label or selector}")
                return True
        except Exception:
            continue

    try:
        clicked = page.evaluate(
            """() => {
                const blocked = /submit|send application|withdraw|cancel|back/i;
                const wanted = /continue|next|start|proceed/i;
                const candidates = Array.from(document.querySelectorAll(
                    'button, [role=button], oj-button, .oj-button, input[type=submit]'
                ));
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 20 && r.height > 15 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                let fallback = null;
                for (const el of candidates) {
                    if (!visible(el)) continue;
                    const target = el.querySelector('button, input') || el;
                    const label = [
                        el.innerText, target.innerText, target.value,
                        el.getAttribute('aria-label'), target.getAttribute('aria-label')
                    ].filter(Boolean).join(' ').trim();
                    if (blocked.test(label)) continue;
                    if (wanted.test(label)) {
                        target.click();
                        return true;
                    }
                    if (!label && !fallback) fallback = target;
                }
                if (fallback) {
                    fallback.click();
                    return true;
                }
                return false;
            }"""
        )
        if clicked:
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            _pause(1.0, 1.8)
            logger.info("  Oracle Recruiting: advanced via visual primary button")
            return True
    except Exception:
        pass
    return False


def _fill_oracle_recruiting(page, job: dict, qa: dict, profile: str,
                            model: str, base_url: str,
                            vision_model: str = "") -> bool:
    """Oracle Recruiting Cloud CandidateExperience flow."""
    logger.info("  Detected: Oracle Recruiting")
    _dismiss_blocking_popups(page)
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "Oracle Recruiting")
        return False

    if _is_oracle_email_gate(page):
        if not _fill_oracle_email_gate(page, job, qa):
            return False
        if _detect_captcha_challenge(page):
            _mark_captcha_required(page, job, "Oracle Recruiting")
            return False

    for step_num in range(8):
        logger.info(f"  Oracle Recruiting wizard page {step_num + 1}/8")
        if _is_oracle_email_gate(page):
            if not _fill_oracle_email_gate(page, job, qa):
                return False
            continue
        ok = _fill_ai_driven_page(
            page, job, qa, profile, model, base_url, vision_model=vision_model
        )
        if not ok:
            return False
        if _click_generic_next(page):
            continue
        if _click_oracle_continue(page):
            continue
        return True

    logger.warning("  Oracle Recruiting wizard reached the eight-page safety limit")
    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": "Oracle Recruiting wizard exceeded the eight-page safety limit",
        "submission_status": "incomplete",
    })
    return False


_GENERIC_NEXT_SELECTORS = (
    "button[data-automation-id*='next']",
    "button:has-text('Save and Continue')",
    "button:has-text('Save & Continue')",
    "button:has-text('Continue')",
    "button:has-text('Proceed')",
    "button:has-text('Next')",
    "a:has-text('Continue')",
    "a:has-text('Next')",
    "input[type='submit'][value*='Next' i]",
    "input[type='submit'][value*='Continue' i]",
    "input[type='submit'][value*='Proceed' i]",
)


def _click_generic_next(page) -> bool:
    """Advance a custom ATS only when the visible CTA is clearly non-terminal."""
    for selector in _GENERIC_NEXT_SELECTORS:
        try:
            button = page.locator(selector).first
            if not button.count() or not button.is_visible(timeout=600):
                continue
            label = " ".join(filter(None, [
                button.inner_text(timeout=600) if hasattr(button, "inner_text") else "",
                button.get_attribute("value") or "",
                button.get_attribute("aria-label") or "",
            ])).strip().lower()
            if any(word in label for word in ("submit", "apply", "send")):
                continue
            button.click(timeout=5000)
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            _pause(0.8, 1.5)
            logger.info(f"  AI-driven wizard: advanced via {label or selector}")
            return True
        except Exception:
            continue
    return False


def _fill_ai_driven(page, job: dict, qa: dict, profile: str,
                    model: str, base_url: str, vision_model: str = "") -> bool:
    """Fill custom ATS pages and conservatively walk explicit non-terminal steps."""
    for step_num in range(8):
        logger.info(f"  AI-driven wizard page {step_num + 1}/8")
        if not _fill_ai_driven_page(
            page, job, qa, profile, model, base_url, vision_model=vision_model
        ):
            return False
        if not _click_generic_next(page):
            return True
    logger.warning("  AI-driven wizard reached the eight-page safety limit")
    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": "AI-driven wizard exceeded the eight-page safety limit",
        "submission_status": "incomplete",
    })
    return False


def _fill_teamtailor(page, job: dict, qa: dict, profile: str,
                     model: str, base_url: str, vision_model: str = "") -> bool:
    """
    Teamtailor filler.

    Teamtailor uses many visual card controls and custom dropdowns rather than
    plain radio/select inputs. Use the generic deterministic/action sweeps, then
    walk explicit non-terminal steps if the tenant splits questions across pages.
    """
    logger.info("  Detected: Teamtailor")
    _ensure_qa_contact(qa, force_reload=True)
    _dismiss_blocking_popups(page)

    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "Teamtailor")
        return False

    if not _external_form_already_visible(page):
        _click_apply_on_external_page(page, vision_model, base_url)
        _pause(1.0, 2.0)
        page = _switch_to_latest_ats_page(page)

    for step in range(8):
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        _pause(0.5, 1.0)

        if _detect_captcha_challenge(page):
            _mark_captcha_required(page, job, "Teamtailor")
            return False

        has_cc = _page_has_country_code_field(page) or _page_has_intl_tel_input(page)
        qa_local = dict(qa)
        if has_cc:
            qa_local["phone"] = _phone_national_uae(
                qa.get("phone", ""), with_leading_zero=True, qa=qa
            )
        _apply_qa_contact_fields(page, qa_local)
        _upload_resume_inputs(page, qa, label="Resume (Teamtailor)")
        _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)

        fields = _extract_fields(page)
        if fields:
            _fill_fields_from_qa(page, fields, job, qa_local, profile, model, base_url)
        filled = _fill_visual_custom_controls(page, job, qa_local, profile, model, base_url)
        filled += _fill_native_selects(page, job, qa_local, profile, model, base_url)
        filled += _fill_aria_comboboxes(page, job, qa_local, profile, model, base_url)
        filled += _fill_radio_groups(page, job, qa_local, profile, model, base_url)
        filled += _fill_required_fields_pass(page, job, qa_local, profile, model, base_url)
        fixed = _fix_step_validation_errors(page, job, qa_local, profile, model, base_url)
        if filled or fixed:
            logger.info(f"  Teamtailor filled/selected {filled}; validation fixes {fixed}")

        if not _click_generic_next(page):
            return True

    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": "Teamtailor wizard exceeded the eight-page safety limit",
        "submission_status": "incomplete",
    })
    return False


def _get_linkedin_context(playwright):
    """
    Persistent Playwright context with saved LinkedIn session.
    First run: browser opens visibly so user can log in.
    All subsequent runs: session is reused automatically.
    """
    os.makedirs(LINKEDIN_SESSION_DIR, exist_ok=True)
    ctx = playwright.chromium.launch_persistent_context(
        user_data_dir=LINKEDIN_SESSION_DIR,
        headless=False,  # LinkedIn bans headless - always visible
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        viewport={"width": 1440, "height": 900}, locale="en-US",
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    return ctx


def _ensure_linkedin_login(ctx, email: str = "", password: str = "") -> bool:
    """
    Verify LinkedIn is logged in. If not:
      - Auto-login using email/password from profile settings (if provided)
      - Otherwise return immediately so unattended runs can skip LinkedIn jobs
    Session is saved to disk; subsequent runs skip this entirely.
    """
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        _pause(2, 3)
    except Exception:
        pass

    if "feed" in page.url or "mynetwork" in page.url:
        logger.info("LinkedIn: session active, already logged in")
        return True

    logger.info("LinkedIn: not logged in")

    if email and password:
        logger.info("LinkedIn: logging in with credentials from profile settings ...")
        try:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=15000)
            _pause(1, 2)
            page.fill("#username", email)
            _pause(0.5, 1.0)
            page.fill("#password", password)
            _pause(0.5, 1.0)
            page.click("button[type='submit']")
            _pause(4, 6)
            if "feed" in page.url or "mynetwork" in page.url:
                logger.info("LinkedIn: login successful - session saved for future runs")
                return True
            # Might need 2FA / CAPTCHA
            logger.warning("LinkedIn: verification required - skipping LinkedIn jobs for unattended run")
        except Exception as e:
            logger.error(f"LinkedIn auto-login error: {e}")
    else:
        logger.info("LinkedIn: no credentials or active session - skipping LinkedIn jobs")
        logger.info("  Add LINKEDIN_EMAIL and LINKEDIN_PASSWORD in GUI Profile Settings")
    return False

_PREFERRED_RADIO_KEYWORDS = (
    ("authorized", "Yes"), ("legally authorized", "Yes"),
    ("right to work", "Yes"), ("eligible to work", "Yes"),
    ("uae national", "Yes"), ("emirati", "Yes"), ("gcc national", "Yes"),
    ("sponsor", "No"), ("require visa", "No"), ("work permit", "No"),
    ("relocate", "Yes"), ("willing to relocate", "Yes"),
    ("commute", "Yes"), ("remote", "Yes"),
    # EEOC / voluntary self-identification — always decline to answer
    ("race", "Decline"), ("ethnicity", "Decline"), ("ethnic", "Decline"),
    ("gender", "Male"), ("sex", "Male"),
    ("disability", "No, I Don't Have a Disability"),
    ("disabled", "No, I Don't Have a Disability"),
    ("veteran", "I am not a protected veteran"),
    ("protected veteran", "I am not a protected veteran"),
    ("military", "None of the above"),
    ("criminal", "No"), ("felony", "No"), ("conviction", "No"),
)

# Keywords that always map to "decline to self-identify" answers regardless of options available
_EEOC_DECLINE_KEYWORDS = frozenset({
    "race", "ethnicity", "ethnic origin", "national origin",
    "color", "religion", "sexual orientation", "gender identity",
    "gender expression", "marital status", "gender", "sex",
    "disability", "disabled", "veteran", "military",
})

_EEOC_DECLINE_OPTIONS = (
    "decline", "prefer not", "do not wish", "don't wish", "choose not",
    "not disclose", "not wish to answer", "not applicable", "n/a",
)


def _is_sensitive_eeoc_label(label: str) -> bool:
    lower = (label or "").lower()
    return any(keyword in lower for keyword in _EEOC_DECLINE_KEYWORDS)


def _eeoc_decline_choice(label: str, options: list[str]) -> Optional[str]:
    if not _is_sensitive_eeoc_label(label):
        return None
    for option in options:
        if any(keyword in option.lower() for keyword in _EEOC_DECLINE_OPTIONS):
            return option
    return None


def _pick_option_from_label(label: str, options: list[str], qa: dict) -> Optional[str]:
    """Heuristic option chooser using profile facts. Returns None if uncertain."""
    if not options:
        return None
    lower = (label or "").lower()
    opt_low = [o.lower() for o in options]
    decline = _eeoc_decline_choice(label, options)
    if decline:
        return decline
    if _is_sensitive_eeoc_label(label):
        return None

    def find(*keywords):
        for kw in keywords:
            for i, o in enumerate(opt_low):
                if kw in o:
                    return options[i]
        return None

    # Experience
    if any(k in lower for k in ("year", "experience")):
        match = find("1-2", "1 - 2", "1 to 2", "1 year", "2 year", "less than 3", "1+")
        if match:
            return match
        return find("1", "2", "less")
    # Education
    if "education" in lower or "degree" in lower or "qualification" in lower:
        return find("bachelor", "undergraduate", "ba", "bs", "bsc", "ba/bs", "other")
    # Location / country
    if "country" in lower:
        return find("united arab emirates", "uae")
    if "city" in lower or "location" in lower:
        return find("abu dhabi", "dubai", "uae")
    # Nationality / citizenship
    if "nationality" in lower or "citizenship" in lower:
        return find("emirati", "united arab", "uae")
    # Gender
    if "gender" in lower:
        return find("male", "man", "he")
    # Language
    if "language" in lower:
        return find("english", "arabic")
    # Work authorization / visa
    if any(k in lower for k in ("authorized", "authorised", "right to work",
                                 "eligible to work", "uae national", "legally")):
        return find("yes")
    if any(k in lower for k in ("sponsor", "visa", "work permit", "require sponsorship")):
        return find("no")
    # Relocate / remote / travel / commute
    if any(k in lower for k in ("relocate", "remote", "commute", "willing", "travel")):
        return find("yes")
    # Employment type
    if any(k in lower for k in ("employment type", "job type", "contract type", "position type")):
        return find("full-time", "full time", "permanent")
    # Start date / availability / notice
    if any(k in lower for k in ("start date", "notice", "availability", "available")):
        return find("immediately", "immediate", "asap", "2 weeks", "notice")
    # Salary type / pay period
    if any(k in lower for k in ("salary type", "pay type", "compensation type", "pay period")):
        return find("annual", "yearly", "per year", "per annum")
    if "currency" in lower:
        return find("aed", "dirham")
    # Disability / veteran / criminal
    if any(k in lower for k in ("disability", "disabled", "veteran", "criminal", "felony")):
        return find("no", "prefer not", "decline", "not applicable", "n/a")
    # Race / ethnicity
    if "race" in lower or "ethnic" in lower:
        return find("decline", "prefer not", "not applicable", "n/a")
    # Currently employed
    if "currently employed" in lower or "currently working" in lower:
        return find("yes")
    # Phone device / type
    if any(k in lower for k in ("phone device", "phone type", "device type", "mobile")):
        return find("mobile", "cell")
    # Worked here before / previous worker
    if any(k in lower for k in (
        "previously employed", "worked for us", "former employee",
        "previous worker", "worked here", "ever worked for", "worked for this",
        "candidateispreviousworker",
    )):
        return find("no")
    return None


def _profile_has_any(profile: str, qa: dict, terms: tuple[str, ...]) -> bool:
    haystack = " ".join(
        str(v) for v in list((qa or {}).values()) + [profile or ""]
        if isinstance(v, (str, int, float))
    ).lower()
    return any(term.lower() in haystack for term in terms)


def _visible_choice_rules(qa: dict, profile: str) -> list[dict]:
    """Rules for custom visual option cards that are not native radio inputs."""
    gender = (qa.get("gender") or "Male").strip()
    qlik = "Yes" if _profile_has_any(profile, qa, ("qlik sense", "qliksense", "qlik")) else "No"
    oracle_ebs = "Yes" if _profile_has_any(
        profile, qa, ("oracle ebs", "oracle enterprise business suite")
    ) else "No"
    return [
        {"needles": ["confirm", "job description", "qualifications"], "answer": "Yes"},
        {"needles": ["qlik sense"], "answer": qlik},
        {"needles": ["complex sql"], "answer": "Yes"},
        {"needles": ["oracle ebs"], "answer": oracle_ebs},
        {"needles": ["based in uae"], "answer": "Yes"},
        {"needles": ["gender"], "answer": gender},
        {"needles": ["immediate joining"], "answer": "Yes"},
        {"needles": ["educational qualification"], "answer": "Others"},
        {"needles": ["your qualification"], "answer": "Others"},
        {"needles": ["salary mentioned"], "answer": "Yes"},
    ]


def _format_dob_values(raw: str) -> tuple[str, str]:
    """Return (text-field value, input[type=date] value) from profile DOB."""
    val = (raw or "").strip()
    if not val:
        return "", ""
    parts = re.split(r"[/-]", val)
    if len(parts) == 3:
        if len(parts[0]) == 4:
            yyyy, mm, dd = parts
            return f"{mm.zfill(2)}/{dd.zfill(2)}/{yyyy}", f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
        a, b, yyyy = parts
        # Existing settings may be DD-MM-YYYY or MM-DD-YYYY; preserve order for text fields.
        return f"{a.zfill(2)}/{b.zfill(2)}/{yyyy}", f"{yyyy}-{a.zfill(2)}-{b.zfill(2)}"
    return val.replace("-", "/"), val


def _fill_visible_date_fields(page, qa: dict) -> int:
    """Fill custom/date inputs missed by label extraction, especially Teamtailor DOB."""
    text_value, iso_value = _format_dob_values(str(qa.get("date_of_birth", "")))
    if not text_value and not iso_value:
        return 0
    try:
        return int(page.evaluate(
            r"""({textValue, isoValue}) => {
                const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const st = getComputedStyle(el);
                    return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
                };
                let filled = 0;
                for (const el of Array.from(document.querySelectorAll('input:not([type=hidden])'))) {
                    if (!visible(el)) continue;
                    const blob = norm([
                        el.type, el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
                        el.closest('label')?.innerText || '',
                        el.parentElement?.innerText || ''
                    ].join(' '));
                    if (!/(date of birth|birth date|birthday|\bdob\b|mm\/dd\/yyyy)/.test(blob)) continue;
                    if ((el.value || '').trim()) continue;
                    const value = (el.type || '').toLowerCase() === 'date' ? isoValue : textValue;
                    if (!value) continue;
                    el.focus();
                    el.value = value;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    filled += 1;
                }
                return filled;
            }""",
            {"textValue": text_value, "isoValue": iso_value},
        ) or 0)
    except Exception:
        return 0


def _fill_visible_choice_controls(page, job, qa, profile, model, base_url) -> int:
    """Click Teamtailor/custom visual radio-card answers that are not fieldsets."""
    rules = _visible_choice_rules(qa, profile)
    try:
        filled = int(page.evaluate(
            r"""(rules) => {
                const norm = s => (s || '').toLowerCase().replace(/[*]/g, '').replace(/\s+/g, ' ').trim();
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const st = getComputedStyle(el);
                    return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
                };
                const textOf = el => norm([
                    el.innerText || el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('value') || '',
                    el.closest('label')?.innerText || ''
                ].join(' '));
                const labels = Array.from(document.querySelectorAll(
                    'label, legend, h1, h2, h3, h4, p, span, div'
                )).filter(el => visible(el) && textOf(el).length >= 3 && textOf(el).length <= 500);
                const clickables = Array.from(document.querySelectorAll(
                    'button, label, [role=radio], [role=option], input[type=radio], input[type=checkbox]'
                )).filter(visible);
                const all = Array.from(document.querySelectorAll('body *'));
                let filled = 0;
                for (const rule of rules) {
                    const answer = norm(rule.answer);
                    if (!answer) continue;
                    const q = labels
                        .filter(el => rule.needles.every(n => textOf(el).includes(norm(n))))
                        .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                    if (!q) continue;
                    const qIndex = all.indexOf(q);
                    const qRect = q.getBoundingClientRect();
                    const options = clickables
                        .filter(el => all.indexOf(el) > qIndex)
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.top >= qRect.top - 4 && r.top <= qRect.top + 420;
                        })
                        .slice(0, 24);
                    let target = options.find(el => textOf(el) === answer);
                    if (!target) {
                        target = options.find(el => {
                            const t = textOf(el);
                            return t.includes(answer) || answer.includes(t);
                        });
                    }
                    if (!target) continue;
                    try {
                        const input = target.matches('input') ? target : target.querySelector('input[type=radio],input[type=checkbox]');
                        (input || target).click();
                        (input || target).dispatchEvent(new Event('change', {bubbles: true}));
                        filled += 1;
                    } catch (_) {}
                }
                return filled;
            }""",
            rules,
        ) or 0)
        if filled:
            logger.info(f"  Visual option cards: selected {filled} answer(s)")
    except Exception as e:
        logger.debug(f"  Visual option card fill skipped: {e}")
        filled = 0

    for group in _extract_visible_choice_groups(page):
        question = group.get("question") or ""
        options = group.get("options") or []
        if not question or not options:
            continue
        choice = _pick_option_from_label(question, options, qa)
        if not choice:
            direct = _qa_value_for_label(question, qa)
            if direct:
                direct_low = str(direct).strip().lower()
                for option in options:
                    opt_low = option.strip().lower()
                    if direct_low == opt_low or direct_low in opt_low or opt_low in direct_low:
                        choice = option
                        break
        if not choice:
            continue
        if _click_visible_choice_answer(page, question, choice):
            filled += 1
            logger.info(f"  Visual choice '{question[:50]}' -> '{choice[:40]}'")
            _pause(0.15, 0.35)
    return filled


def _extract_visible_choice_groups(page) -> list[dict]:
    """Return visible question/option-card groups from custom ATS markup."""
    try:
        return page.evaluate(r"""() => {
            const norm = s => (s || '').replace(/[*]/g, '').replace(/\s+/g, ' ').trim();
            const low = s => norm(s).toLowerCase();
            const visible = el => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
            };
            const textOf = el => norm([
                el.innerText || el.textContent || '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('value') || '',
                el.closest('label')?.innerText || ''
            ].join(' '));
            const all = Array.from(document.querySelectorAll('body *'));
            const labels = Array.from(document.querySelectorAll('label, legend, h1, h2, h3, h4, p, span, div'))
                .filter(el => visible(el))
                .map(el => ({el, text: textOf(el)}))
                .filter(x => x.text.length >= 4 && x.text.length <= 280)
                .filter(x => {
                    const t = low(x.text);
                    return t.includes('?') || /\b(gender|qualification|nationality|available|based in|salary|experience|confirm)\b/.test(t);
                })
                .sort((a, b) => a.text.length - b.text.length);
            const clickables = Array.from(document.querySelectorAll(
                'button, label, [role=radio], [role=option], input[type=radio], input[type=checkbox]'
            )).filter(visible);
            const seen = new Set();
            const groups = [];
            for (const q of labels) {
                const qText = q.text;
                const qLow = low(qText);
                if (seen.has(qLow)) continue;
                if (qLow.includes('select an option')) continue;
                const qIndex = all.indexOf(q.el);
                const qRect = q.el.getBoundingClientRect();
                const opts = [];
                const optSeen = new Set();
                for (const el of clickables) {
                    if (all.indexOf(el) <= qIndex) continue;
                    const r = el.getBoundingClientRect();
                    if (r.top < qRect.top - 4 || r.top > qRect.top + 430) continue;
                    const text = textOf(el);
                    const t = low(text);
                    if (!text || text.length > 80) continue;
                    if (t === qLow || t.includes('select an option')) continue;
                    if (/^(required|edit|view|upload|apply|submit|next|back|continue)$/i.test(text)) continue;
                    if (optSeen.has(t)) continue;
                    optSeen.add(t);
                    opts.push(text);
                    if (opts.length >= 8) break;
                }
                if (opts.length < 2) continue;
                if (!opts.some(o => /^(yes|no|male|female|others?|do not|prefer not)/i.test(o))) continue;
                seen.add(qLow);
                groups.push({question: qText, options: opts});
                if (groups.length >= 30) break;
            }
            return groups;
        }""") or []
    except Exception:
        return []


def _click_visible_choice_answer(page, question: str, answer: str) -> bool:
    try:
        return bool(page.evaluate(
            r"""({question, answer}) => {
                const norm = s => (s || '').toLowerCase().replace(/[*]/g, '').replace(/\s+/g, ' ').trim();
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const st = getComputedStyle(el);
                    return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
                };
                const textOf = el => norm([
                    el.innerText || el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('value') || '',
                    el.closest('label')?.innerText || ''
                ].join(' '));
                const labels = Array.from(document.querySelectorAll('label, legend, h1, h2, h3, h4, p, span, div'))
                    .filter(el => visible(el) && textOf(el).length >= 4 && textOf(el).length <= 300);
                const q = labels
                    .filter(el => {
                        const t = textOf(el);
                        const q = norm(question);
                        return t === q || t.includes(q) || q.includes(t);
                    })
                    .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                if (!q) return false;
                const all = Array.from(document.querySelectorAll('body *'));
                const qIndex = all.indexOf(q);
                const qRect = q.getBoundingClientRect();
                const clickables = Array.from(document.querySelectorAll(
                    'button, label, [role=radio], [role=option], input[type=radio], input[type=checkbox]'
                )).filter(visible);
                const answerNorm = norm(answer);
                const options = clickables
                    .filter(el => all.indexOf(el) > qIndex)
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.top >= qRect.top - 4 && r.top <= qRect.top + 430;
                    })
                    .slice(0, 24);
                let target = options.find(el => textOf(el) === answerNorm);
                if (!target) {
                    target = options.find(el => {
                        const t = textOf(el);
                        return t.includes(answerNorm) || answerNorm.includes(t);
                    });
                }
                if (!target) return false;
                const input = target.matches('input') ? target : target.querySelector('input[type=radio],input[type=checkbox]');
                (input || target).click();
                (input || target).dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            {"question": question, "answer": answer},
        ))
    except Exception:
        return False


def _fill_visible_dropdown_controls(page, job, qa, profile, model, base_url) -> int:
    """Fill custom dropdowns that expose only a visible placeholder, e.g. Teamtailor."""
    targets = [
        {
            "needles": ["nationality"],
            "choices": ["United Arab Emirates", "Emirati", "UAE"],
        },
    ]
    filled = 0
    for target in targets:
        try:
            opened = bool(page.evaluate(
                r"""(target) => {
                    const norm = s => (s || '').toLowerCase().replace(/[*]/g, '').replace(/\s+/g, ' ').trim();
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const st = getComputedStyle(el);
                        return !!(r.width && r.height && st.visibility !== 'hidden' && st.display !== 'none');
                    };
                    const textOf = el => norm([
                        el.innerText || el.textContent || '',
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('placeholder') || ''
                    ].join(' '));
                    const labels = Array.from(document.querySelectorAll('label, legend, h1, h2, h3, h4, p, span, div'))
                        .filter(el => visible(el) && textOf(el).length >= 3 && textOf(el).length <= 300);
                    const all = Array.from(document.querySelectorAll('body *'));
                    const q = labels
                        .filter(el => target.needles.every(n => textOf(el).includes(norm(n))))
                        .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                    if (!q) return false;
                    const qIndex = all.indexOf(q);
                    const qRect = q.getBoundingClientRect();
                    const controls = Array.from(document.querySelectorAll(
                        '[role=combobox], [aria-haspopup=listbox], button, input, div'
                    )).filter(visible).filter(el => all.indexOf(el) > qIndex).filter(el => {
                        const r = el.getBoundingClientRect();
                        const t = textOf(el);
                        return r.top >= qRect.top - 4 && r.top <= qRect.top + 220 &&
                               (t.includes('select') || el.getAttribute('role') === 'combobox' ||
                                el.getAttribute('aria-haspopup') === 'listbox' ||
                                /input/i.test(el.tagName));
                    });
                    const control = controls[0];
                    if (!control) return false;
                    control.click();
                    return true;
                }""",
                target,
            ))
            if not opened:
                continue
            _pause(0.25, 0.5)
            options = page.locator("[role='option']:visible, li:visible, button:visible, div:visible")
            picked = False
            for choice in target["choices"]:
                try:
                    opt = options.filter(has_text=re.compile(re.escape(choice), re.I)).first
                    if opt.count() and opt.is_visible(timeout=500):
                        opt.click(timeout=2000)
                        _pause(0.2, 0.4)
                        logger.info(f"  Custom dropdown '{target['needles'][0]}' -> '{choice}'")
                        filled += 1
                        picked = True
                        break
                except Exception:
                    continue
            if not picked:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
    return filled


def _fill_visual_custom_controls(page, job, qa, profile, model, base_url) -> int:
    filled = 0
    filled += _fill_visible_date_fields(page, qa)
    filled += _fill_visible_dropdown_controls(page, job, qa, profile, model, base_url)
    filled += _fill_visible_choice_controls(page, job, qa, profile, model, base_url)
    return filled


_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".g-recaptcha",
    ".h-captcha",
    ".cf-turnstile",
    "[data-sitekey]",
    "[id*='captcha' i]",
    "[class*='captcha' i]",
)

_CAPTCHA_TEXT_SIGNALS = (
    "captcha",
    "i'm not a robot",
    "im not a robot",
    "i am not a robot",
    "verify you are human",
    "verify that you are human",
    "security check",
    "checking your browser",
    "cloudflare",
    "attention required",
    "turnstile",
    "hcaptcha",
    "recaptcha",
)


def _detect_captcha_challenge(page) -> bool:
    """Detect CAPTCHA/security challenges across main page and iframes."""
    targets = []
    try:
        targets = list(getattr(page, "frames", []) or [])
    except Exception:
        targets = []
    if page not in targets:
        targets.insert(0, page)

    for target in targets:
        for selector in _CAPTCHA_SELECTORS:
            try:
                if target.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        try:
            url_text = (getattr(target, "url", "") or "").lower()
            if any(signal in url_text for signal in _CAPTCHA_TEXT_SIGNALS):
                return True
        except Exception:
            pass
        try:
            body = target.locator("body").inner_text(timeout=1200).lower()
            if any(signal in body for signal in _CAPTCHA_TEXT_SIGNALS):
                return True
        except Exception:
            pass
    return False


def _mark_captcha_required(page, job: dict, platform: str = "") -> None:
    try:
        url = page.url or ""
    except Exception:
        url = ""
    label = platform or "application portal"
    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": f"{label}: CAPTCHA/security challenge detected - deferred",
        "submission_status": "captcha_required",
        "confirmation_url": url,
        "confirmation_text": "",
    })


def _llm_pick_option(label: str, options: list[str], job: dict, qa: dict,
                     profile: str, model: str, base_url: str) -> Optional[str]:
    """Use a fast LLM call to choose from a dropdown. Always uses the fast model."""
    if not options:
        return None
    fast = _session_fast_model(model)
    opts_block = "\n".join(f"- {o}" for o in options[:25])
    facts = _session_facts(qa)
    # Minimal prompt — just facts + label + options, no profile excerpt
    prompt = (
        f"You are filling out a job application for this candidate:\n{facts}\n\n"
        f"Dropdown label: {label}\n"
        f"Choose the single best option from this list. Return ONLY the exact option text:\n"
        f"{opts_block}"
    )
    if "qwen3" in fast.lower():
        prompt = prompt.rstrip() + "\n/no_think"
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": fast, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.0, "num_predict": 60}},
            timeout=25,
        )
        r.raise_for_status()
        ans = r.json().get("response", "").strip()
    except Exception:
        return None
    if not ans:
        return None
    target = ans.strip().splitlines()[0].strip(" -*\"'`")
    low = target.lower()
    for o in options:
        if o.lower() == low:
            return o
    for o in options:
        if low in o.lower() or o.lower() in low:
            return o
    return None


def _fill_native_selects(page, job, qa, profile, model, base_url) -> int:
    """Iterate every visible <select>; pick option from facts then LLM."""
    filled = 0
    for sel_el in page.locator("select").all():
        try:
            if not sel_el.is_visible(timeout=400):
                continue
            current = sel_el.evaluate(
                "el => el.value && el.value !== '' && el.options[el.selectedIndex]"
                "      && (el.options[el.selectedIndex].text || '').trim().toLowerCase()"
                "      !== 'select' && el.value"
            )
            if current:
                continue
            sel_id = sel_el.get_attribute("id") or ""
            aria = sel_el.get_attribute("aria-label") or ""
            name_attr = sel_el.get_attribute("name") or ""
            label = aria or name_attr
            if sel_id and not label:
                lbl = page.locator(f"label[for='{sel_id}']")
                if lbl.count() > 0:
                    label = lbl.first.inner_text().strip()
            options_raw = sel_el.evaluate(
                "el => Array.from(el.options).map(o => o.text)"
            )
            options = [o.strip() for o in options_raw if o and o.strip()
                       and o.strip().lower() not in ("select", "select an option",
                                                      "please select", "choose", "-")]
            if not options:
                continue
            choice = _pick_option_from_label(label, options, qa)
            if not choice and not _is_sensitive_eeoc_label(label):
                choice = _llm_pick_option(
                    label or "(no label)", options, job, qa, profile, model, base_url)
            if not choice:
                continue
            try:
                sel_el.select_option(label=choice)
                _pause(0.2, 0.5)
                filled += 1
                logger.info(f"  Select '{label[:50]}' -> '{choice[:40]}'")
            except Exception:
                pass
        except Exception:
            continue
    return filled


def _fill_aria_comboboxes(page, job, qa, profile, model, base_url) -> int:
    """LinkedIn renders some dropdowns as ARIA listbox + button instead of <select>."""
    filled = 0
    for btn in page.locator(
        "[role='combobox'], [aria-haspopup='listbox']"
    ).all():
        try:
            if not btn.is_visible(timeout=400):
                continue
            label = (btn.get_attribute("aria-label") or btn.inner_text(timeout=400) or "").strip()
            if not label or len(label) > 200:
                continue
            value = (btn.get_attribute("aria-activedescendant")
                     or btn.get_attribute("value") or "").strip()
            if value:
                # aria-activedescendant is set even for placeholder options —
                # check the visible button text to confirm it isn't a placeholder.
                try:
                    btn_text = (btn.inner_text(timeout=400) or "").strip().lower()
                except Exception:
                    btn_text = ""
                _PLACEHOLDER_TEXTS = ("select", "select an option", "please select",
                                      "choose", "- select -", "")
                if btn_text not in _PLACEHOLDER_TEXTS and not btn_text.startswith("select "):
                    continue  # genuinely already filled
            btn.click(timeout=2000)
            _pause(0.3, 0.6)
            listbox = page.locator("[role='listbox']:visible").first
            option_locator = listbox.locator("[role='option']")
            count = option_locator.count()
            if count == 0:
                page.keyboard.press("Escape")
                continue
            options = []
            for i in range(min(count, 20)):
                try:
                    options.append(option_locator.nth(i).inner_text(timeout=300).strip())
                except Exception:
                    continue
            options = [o for o in options if o and o.lower() not in (
                "select", "select an option", "please select")]
            choice = _pick_option_from_label(label, options, qa)
            if not choice and not _is_sensitive_eeoc_label(label):
                choice = _llm_pick_option(label, options, job, qa, profile, model, base_url)
            if not choice:
                page.keyboard.press("Escape")
                continue
            try:
                option_locator.filter(has_text=choice).first.click(timeout=2000)
                _pause(0.3, 0.6)
                filled += 1
                logger.info(f"  Combobox '{label[:50]}' -> '{choice[:40]}'")
            except Exception:
                page.keyboard.press("Escape")
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
    return filled


def _fill_radio_groups(page, job, qa, profile, model, base_url) -> int:
    """Pick a Yes/No or other radio option for every visible fieldset."""
    filled = 0
    for group in page.locator("fieldset, [role='radiogroup']").all():
        try:
            legend_el = group.locator("legend, [class*='label']").first
            if legend_el.count() == 0:
                continue
            label = legend_el.inner_text(timeout=400).strip()
            if not label or len(label) > 300:
                continue
            # Skip groups where a radio is already selected
            if group.locator("input[type='radio']:checked").count() > 0:
                continue
            radios = group.locator("input[type='radio']")
            rcount = radios.count()
            if rcount == 0:
                continue
            options: list[tuple[str, str]] = []  # (text, value)
            for i in range(rcount):
                radio = radios.nth(i)
                rid = radio.get_attribute("id") or ""
                val = radio.get_attribute("value") or ""
                text = ""
                if rid:
                    lbl = group.locator(f"label[for='{rid}']")
                    if lbl.count() > 0:
                        try:
                            text = lbl.first.inner_text(timeout=400).strip()
                        except Exception:
                            text = ""
                options.append((text or val or f"option {i}", val or text or ""))
            option_labels = [t for t, _ in options]
            lower = label.lower()
            chosen = None

            # ── EEOC: demographic questions — find best "decline" option ──────────
            is_eeoc = _is_sensitive_eeoc_label(lower)
            if is_eeoc:
                chosen = _eeoc_decline_choice(label, option_labels)

            # ── Preferred keyword table ────────────────────────────────────────────
            if not chosen and not is_eeoc:
                for kw, want in _PREFERRED_RADIO_KEYWORDS:
                    if kw in lower:
                        # Exact match first
                        for t, _ in options:
                            if t.strip().lower() == want.lower():
                                chosen = t
                                break
                        # Partial match fallback
                        if not chosen:
                            for t, _ in options:
                                if want.lower() in t.lower():
                                    chosen = t
                                    break
                        if chosen:
                            break

            if not chosen:
                chosen = _pick_option_from_label(label, option_labels, qa)
            if not chosen and not is_eeoc:
                chosen = _llm_pick_option(label, option_labels, job, qa, profile, model, base_url)
            if not chosen:
                continue
            try:
                group.locator(f"label:has-text(\"{chosen[:40]}\")").first.click(timeout=2000)
                _pause(0.2, 0.4)
                filled += 1
                logger.info(f"  Radio '{label[:50]}' -> '{chosen[:40]}'")
            except Exception:
                pass
        except Exception:
            continue
    return filled


def _fill_linkedin_selectable_options(page, job, qa, profile, model, base_url, root=None) -> int:
    """
    Handle LinkedIn Easy Apply's custom tile-based single/multi-select questions.
    These appear as rows of clickable divs with [data-test-text-selectable-option__input]
    or similar non-standard markup — invisible to <select> or combobox scanners.
    """
    scope = root if root is not None else page
    filled = 0
    # Each question is wrapped in a fieldset or a div with a legend/label
    for group in scope.locator(
        "fieldset, div.jobs-easy-apply-form-section__grouping, "
        "div[data-test-form-element]"
    ).all():
        try:
            # Get the question label
            legend_el = group.locator("legend, label, [class*='legend'], [class*='label']").first
            if legend_el.count() == 0:
                continue
            question = legend_el.inner_text(timeout=400).strip()
            if not question or len(question) < 4:
                continue
            # Collect option tiles
            option_els = group.locator(
                "[data-test-text-selectable-option__input], "
                "input[type='radio'], input[type='checkbox']"
            ).all()
            if not option_els:
                continue
            # Already answered?
            already = any(
                o.is_checked() if o.get_attribute("type") in ("radio", "checkbox") else False
                for o in option_els
                if not o.is_closed()
            )
            if already:
                continue
            # Get option labels from sibling spans/labels
            options = []
            for opt in option_els:
                try:
                    lbl = (opt.get_attribute("aria-label") or
                           opt.evaluate("el => { const l = el.closest('label') || el.nextElementSibling; return l ? l.textContent.trim() : ''; }") or
                           "")
                    if lbl:
                        options.append((lbl, opt))
                except Exception:
                    continue
            if not options:
                continue
            opt_texts = [o[0] for o in options]
            choice = _pick_option_from_label(question, opt_texts, qa)
            if not choice and not _is_sensitive_eeoc_label(question):
                choice = _llm_pick_option(question, opt_texts, job, qa, profile, model, base_url)
            if not choice:
                continue
            # Click the matching option
            for lbl_text, opt_el in options:
                if lbl_text == choice:
                    try:
                        opt_el.click(timeout=2000)
                        _pause(0.2, 0.5)
                        filled += 1
                        logger.info(f"  Selectable option '{question[:50]}' -> '{choice[:40]}'")
                    except Exception:
                        pass
                    break
        except Exception:
            continue
    return filled


def _fill_required_fields_pass(page, job, qa, profile, model, base_url, root=None) -> int:
    """
    Final sweep: find any visible required field still empty and retry it.
    Catches fields the main loops missed (e.g. fields revealed after prior answers).
    """
    scope = root if root is not None else page
    filled = 0
    # Required text/email/tel inputs still empty
    for inp in scope.locator(
        "input[required]:not([type='hidden']):not([type='file']):not([type='submit']),"
        "input[aria-required='true']:not([type='hidden']):not([type='file'])"
    ).all():
        try:
            if not inp.is_visible(timeout=300):
                continue
            val = inp.input_value(timeout=300)
            if val and val.strip():
                continue  # already filled
            inp_id = inp.get_attribute("id") or ""
            label = inp.get_attribute("aria-label") or inp.get_attribute("placeholder") or ""
            if inp_id and not label:
                lbl = scope.locator(f"label[for='{inp_id}']")
                if lbl.count() > 0:
                    label = lbl.first.inner_text(timeout=300).strip()
            if not label:
                continue
            direct = _qa_value_for_label(label, qa)
            answer = str(direct) if direct is not None else _llm_answer(
                label, job.get("company", ""), job.get("title", ""),
                job.get("positioning_angle", "investments"),
                profile, model, base_url, qa=qa)
            if answer:
                inp.fill(answer)
                _pause(0.2, 0.4)
                filled += 1
                logger.info(f"  Required field retry: '{label[:50]}' -> '{answer[:40]}'")
        except Exception:
            continue
    # Required <select> still on default placeholder
    for sel_el in scope.locator("select[required], select[aria-required='true']").all():
        try:
            if not sel_el.is_visible(timeout=300):
                continue
            current = sel_el.evaluate(
                "el => { const t = (el.options[el.selectedIndex] ? "
                "(el.options[el.selectedIndex].text||'') : '').trim().toLowerCase(); "
                "return el.value && !['','select','please select','choose','-'].includes(t) "
                "? el.value : ''; }"
            )
            if current:
                continue
            aria = sel_el.get_attribute("aria-label") or sel_el.get_attribute("name") or ""
            sel_id = sel_el.get_attribute("id") or ""
            label = aria
            if sel_id and not label:
                lbl = scope.locator(f"label[for='{sel_id}']")
                if lbl.count() > 0:
                    label = lbl.first.inner_text(timeout=300).strip()
            options_raw = sel_el.evaluate("el => Array.from(el.options).map(o => o.text)")
            options = [o.strip() for o in options_raw
                       if o.strip() and o.strip().lower() not in
                       ("select", "select an option", "please select", "choose", "-", "")]
            if not options:
                continue
            choice = _pick_option_from_label(label or "", options, qa)
            if not choice and not _is_sensitive_eeoc_label(label):
                choice = _llm_pick_option(label or "(required select)", options,
                                          job, qa, profile, model, base_url)
            if choice:
                sel_el.select_option(label=choice)
                _pause(0.2, 0.4)
                filled += 1
                logger.info(f"  Required select retry: '{label[:50]}' -> '{choice[:40]}'")
        except Exception:
            continue
    return filled


def _linkedin_scroll_modal_content(modal, position: float = 1.0) -> None:
    """Scroll the Easy Apply modal body without touching the outer LinkedIn page."""
    try:
        modal.evaluate("""(el, pos) => {
            const scrollables = [
                el.querySelector('.jobs-easy-apply-modal--scrollable'),
                el.querySelector('.artdeco-modal__content'),
                ...Array.from(el.querySelectorAll('[class*="scrollable"]')),
                el.querySelector('form'),
            ].filter(Boolean);
            const s = scrollables.find(x => x.scrollHeight > x.clientHeight) || scrollables[0];
            if (s) s.scrollTop = Math.max(0, s.scrollHeight * pos);
        }""", max(0.0, min(1.0, float(position))))
    except Exception:
        pass


def _linkedin_easy_apply_needs_resume_upload(root) -> bool:
    """
    Return True only when LinkedIn is explicitly asking for a resume file.
    A visible "Change resume" control or selected resume card means a profile
    resume is already loaded and should not be replaced.
    """
    try:
        change = root.locator(
            "button:has-text('Change resume'), "
            "a:has-text('Change resume')"
        ).first
        if change.count() and change.is_visible(timeout=300):
            logger.info("  LinkedIn resume already selected - leaving it unchanged")
            return False
    except Exception:
        pass

    try:
        selected_resume = root.locator(
            "[aria-checked='true']:has-text('Resume'), "
            ".jobs-document-upload__title:has-text('Resume'), "
            "[class*='resume']:has-text('.pdf')"
        ).first
        if selected_resume.count() and selected_resume.is_visible(timeout=300):
            logger.info("  LinkedIn resume already selected - leaving it unchanged")
            return False
    except Exception:
        pass

    try:
        upload = root.locator(
            "button:has-text('Upload resume'), label:has-text('Upload resume'), "
            "button:has-text('Upload'), label[for*='upload' i]"
        ).first
        if upload.count() and upload.is_visible(timeout=300):
            return True
    except Exception:
        pass

    try:
        for fi in root.locator("input[type='file']").all():
            try:
                if fi.is_visible(timeout=200):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _linkedin_fill_step(page, job: dict, qa: dict, profile: str, model: str, base_url: str,
                        modal=None):
    """Fill all visible fields in one LinkedIn Easy Apply wizard step."""
    root = modal if modal is not None else page
    _ensure_qa_contact(qa)

    # Detect country-code widget BEFORE filling so every path in this step
    # uses the correct phone format (local when CC is present, intl otherwise).
    has_cc = _page_has_country_code_field(root) or _page_has_intl_tel_input(root)
    qa = dict(qa)  # local copy — don't mutate caller's dict
    if has_cc:
        qa["phone"] = _phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
        logger.debug(f"  LinkedIn step: CC field detected — phone set to {qa['phone']}")

    _apply_qa_contact_fields(root, qa)
    # Country dropdown
    for sel in ["select[id*='country']", "select[name*='country']"]:
        try:
            el = root.locator(sel).first
            if el.is_visible(timeout=600):
                el.select_option(label="United Arab Emirates")
                _pause(0.3, 0.6)
        except Exception:
            pass
    # City
    _try_fill(root, ["input[id*='city']", "input[name*='city']",
                     "input[placeholder*='City' i]"], "Abu Dhabi")
    # Languages / nationality / Excel (when fields appear)
    _try_fill(root, [
        "input[name*='language']", "input[placeholder*='language' i]",
        "textarea[name*='language']",
    ], qa.get("languages", ""))
    _try_fill(root, [
        "input[name*='nationality']", "input[placeholder*='nationality' i]",
    ], qa.get("nationality", ""))
    _try_fill(root, [
        "input[name*='excel']", "input[placeholder*='excel' i]",
    ], qa.get("excel", ""))
    # Generic dropdown handling — covers every visible <select>, including
    # custom labels the rule-based path doesn't know about.
    _fill_native_selects(root, job, qa, profile, model, base_url)
    _fill_aria_comboboxes(root, job, qa, profile, model, base_url)
    # LinkedIn custom selectable-option rows (radio-style tiles)
    _fill_linkedin_selectable_options(page, job, qa, profile, model, base_url, root=root)
    # Resume upload — LinkedIn Easy Apply shows a card with Upload/Change resume button
    resume_path = (qa.get("resume_path") or "").strip()
    if (
        resume_path
        and Path(resume_path).exists()
        and _linkedin_easy_apply_needs_resume_upload(root)
    ):
        try:
            upload_opened = False
            # Try visible upload/change-resume buttons first
            for btn_sel in (
                "button:has-text('Upload resume')",
                "button:has-text('Upload')",
                "label:has-text('Upload resume')",
                "label[for*='upload' i]",
            ):
                btn = root.locator(btn_sel).first
                if btn.count() and btn.is_visible(timeout=500):
                    btn.click(timeout=3000)
                    _pause(0.5, 1.0)
                    upload_opened = True
                    break
            # Now attach to whatever file input is present (visible or hidden)
            for fi in root.locator("input[type='file']").all():
                try:
                    if not upload_opened and not fi.is_visible(timeout=300):
                        continue
                    fi.set_input_files(resume_path)
                    logger.info("  Resume uploaded to LinkedIn Easy Apply")
                    _pause(2.0, 3.5)
                    break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"  Resume upload attempt: {e}")
    # Checkboxes — answer required checkboxes (e.g. "I agree", background check consent)
    try:
        for cb in root.locator(
            "input[type='checkbox'][required], input[type='checkbox'][aria-required='true']"
        ).all():
            try:
                if cb.is_visible(timeout=300) and not cb.is_checked(timeout=300):
                    cb.check(timeout=2000)
                    _pause(0.2, 0.4)
            except Exception:
                pass
    except Exception:
        pass
    # Yes/No + general radio groups
    _fill_radio_groups(root, job, qa, profile, model, base_url)
    # Open text questions via LLM — skip contact fields already filled above
    _SKIP_LABELS = re.compile(
        r"\b(phone|mobile|tel|e-?mail|first\s*name|last\s*name|full\s*name)\b", re.I)
    for q_el in root.locator(
            ".jobs-easy-apply-form-element, .fb-form-element, "
            "[class*='form-element'], [class*='formElement']").all():
        try:
            label_el = q_el.locator(
                "label, span.artdeco-text-input--label, [class*='label']").first
            if label_el.count() == 0:
                continue
            question = label_el.inner_text().strip()
            if not question or len(question) < 5:
                continue
            # Skip contact fields — already filled deterministically above
            if _SKIP_LABELS.search(question):
                continue
            input_el = q_el.locator(
                "input[type='text'], input[type='tel'], input[type='email'],"
                "input[type='number'], textarea"
            ).first
            if input_el.count() == 0 or not input_el.is_visible(timeout=400):
                continue
            is_numeric = (input_el.get_attribute("type") or "").lower() == "number"
            # If already has a non-empty value, don't overwrite
            try:
                existing = input_el.input_value(timeout=300)
                if existing and existing.strip():
                    continue
            except Exception:
                pass
            # Try deterministic answer first; fall back to LLM
            direct = _qa_value_for_label(question, qa)
            if direct is not None:
                answer = str(direct)
            else:
                answer = _llm_answer(
                    question, job.get("company", ""), job.get("title", ""),
                    job.get("positioning_angle", "investments"),
                    profile, model, base_url, qa=qa)
            # For number inputs, strip non-digits and use a plain integer
            if is_numeric:
                nums = re.findall(r'\d+', answer)
                answer = nums[-1] if nums else "2"
            input_el.fill(answer)
            _pause(0.5, 1.3)
        except Exception:
            pass

def _linkedin_job_top_card(page):
    """Narrow scope to the job posting header (avoids sidebar/footer noise)."""
    for sel in (
        ".jobs-details-jobs-unified-top-card",
        ".job-details-jobs-unified-top-card",
        ".jobs-unified-top-card",
        "div.jobs-apply-button--top-card",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible(timeout=800):
                return loc
        except Exception:
            continue
    # Left column job detail (excludes right-rail "similar jobs" on most layouts)
    loc = page.locator("div.jobs-details__main-content").first
    try:
        if loc.count() > 0 and loc.is_visible(timeout=800):
            return loc
    except Exception:
        pass
    return page


def _linkedin_description_snippet(page) -> str:
    """Extract visible LinkedIn job description text for live fit validation."""
    selectors = (
        ".jobs-description-content__text",
        ".jobs-box__html-content",
        ".jobs-description",
        "article.jobs-description",
        "div.jobs-details__main-content",
    )
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=800):
                text = loc.inner_text(timeout=3000)
                text = re.sub(r"\s+", " ", text or "").strip()
                if len(text) >= 120:
                    return text[:4000]
        except Exception:
            continue
    return ""


def _linkedin_page_status(page) -> str:
    """Quick scan for common LinkedIn job page states (top card + body)."""
    try:
        top_text = _linkedin_job_top_card(page).inner_text(timeout=5000).lower()
    except Exception:
        top_text = ""
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = top_text
    combined = f"{top_text}\n{body}"
    if "no longer accepting applications" in combined or "job is no longer available" in combined:
        return "closed"
    if "sign in" in combined and "join linkedin" in combined:
        return "login_required"

    # ── In-progress / saved draft (must win over loose "applied" copy) ─────────
    try:
        continue_btn = page.locator(
            "button:has-text('Continue'), "
            "button[aria-label*='Continue to apply'], "
            "button[aria-label*='Resume application']"
        ).first
        if continue_btn.count() > 0 and continue_btn.is_visible(timeout=600):
            return "in_progress"
    except Exception:
        pass
    if any(
        p in combined
        for p in (
            "you last modified this application",
            "saved this application",
            "continue applying",
            "application in progress",
            "draft application",
        )
    ):
        return "in_progress"
    if _linkedin_easy_apply_modal_visible(page):
        return "in_progress"

    # ── Already submitted (job page only — not an open wizard) ─────────────────
    already_phrases = (
        "application submitted",
        "you already applied",
        "already applied to this job",
        "your application was sent",
        "application was sent",
    )
    if any(p in combined for p in already_phrases):
        return "already_applied"

    if "apply on company website" in combined or "apply on the company" in combined:
        return "external_only"
    if "responses managed off linkedin" in combined or "promoted by hirer" in combined:
        return "off_linkedin_apply"
    info = _linkedin_detect_apply_button(page, log=False)
    if info:
        if info["type"] == "easy_apply":
            return "has_easy_apply"
        if info["type"] in ("apply", "company_website"):
            return "has_apply_button"
    if "easy apply" in top_text:
        return "has_easy_apply"
    return "unknown"


_CLOSED_JOB_PHRASES = (
    "no longer accepting applications",
    "job is no longer available",
    "this job is no longer available",
    "this position is no longer available",
    "position is no longer available",
    "posting is no longer available",
    "job posting has expired",
    "posting has expired",
    "this job has expired",
    "this position has been filled",
    "position has been filled",
    "applications are closed",
    "application period has closed",
    "we are no longer accepting applications",
    "vacancy is closed",
)


def _page_is_closed_job(page) -> bool:
    """Detect expired/closed job pages before attempting a form fill."""
    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        try:
            text = page.content().lower()
        except Exception:
            text = ""
    return any(phrase in text for phrase in _CLOSED_JOB_PHRASES)


def _mark_job_closed(job: dict, page=None, note: str = "Job closed - removed from active list") -> None:
    job.update({
        "applied": False,
        "apply_notes": note,
        "decision": "closed",
        "submission_status": "closed",
    })
    if page is not None:
        try:
            _screenshot(page, job, "_closed")
        except Exception:
            pass


def _page_needs_signup(page) -> tuple[bool, str]:
    """Detect if an external page requires sign-up or login before applying."""
    from agents.account_signup import page_auth_wall_visible
    return page_auth_wall_visible(page)


def _linkedin_wait_for_apply_button(page, timeout_ms: int = 20000) -> bool:
    """Wait for an Apply CTA to appear in the job top card."""
    top = _linkedin_job_top_card(page)
    per_try = max(3000, timeout_ms // 5)
    for sel in (
        "button:has-text('Easy Apply')",
        "button:has-text('Apply')",
        "button.jobs-apply-button",
        "a.jobs-apply-button--link",
        "[aria-label*='Apply to']",
        "[aria-label*='Easy Apply to']",
    ):
        try:
            top.locator(sel).first.wait_for(state="visible", timeout=per_try)
            return True
        except Exception:
            continue
    return False


def _linkedin_easy_apply_modal_visible(page) -> bool:
    """True if the Easy Apply modal / wizard is currently open."""
    # Prioritise data-attribute selectors which are stable across LinkedIn UI refreshes
    for sel in (
        "button[data-easy-apply-next-button]",
        "button[data-live-test-easy-apply-next-button]",
        "button[data-live-test-easy-apply-review-button]",
        "button[data-live-test-easy-apply-submit-button]",
        ".jobs-easy-apply-modal",
        "div[role='dialog']:has(.jobs-easy-apply-footer)",
        "div[role='dialog'][aria-label*='Apply']",
    ):
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


_LINKEDIN_EA_MODAL_SELECTORS = (
    ".jobs-easy-apply-modal",
    "[data-test-modal-id='easy-apply-modal']",
    "div[role='dialog']:has(.jobs-easy-apply-footer)",
    # anchor on the nav buttons — most stable across UI refreshes
    "div[role='dialog']:has(button[data-easy-apply-next-button])",
    "div[role='dialog']:has(button[data-live-test-easy-apply-submit-button])",
    "div[role='dialog']:has(button[data-live-test-easy-apply-review-button])",
    "div[role='dialog'][aria-label*='Apply']",
    "div[role='dialog']",  # last resort — scope narrows automatically via footer/button checks
)


def _linkedin_easy_apply_modal(page):
    """Return the visible Easy Apply modal locator, or None."""
    for sel in _LINKEDIN_EA_MODAL_SELECTORS:
        try:
            found = page.locator(sel)
            loc = getattr(found, "first", found)
            if loc.count() and loc.is_visible(timeout=600):
                return loc
        except Exception:
            continue
    return None


def _linkedin_easy_apply_step_signature(modal) -> str:
    """Fingerprint wizard step (progress, labels, footer buttons) for advance detection."""
    try:
        return modal.evaluate(r"""(el) => {
            const parts = [];
            const prog = el.querySelector(
                '[data-easy-apply-progress], [class*="progress"], header h2, header h3'
            );
            if (prog) parts.push('p:' + (prog.textContent || '').trim().slice(0, 100));
            el.querySelectorAll(
                '.jobs-easy-apply-form-element label, legend, ' +
                '[class*="form-element"] [class*="label"]'
            ).forEach(l => {
                const t = (l.textContent || '').trim();
                if (t.length > 3 && t.length < 120) parts.push('l:' + t);
            });
            const footer = el.querySelector('footer, .jobs-easy-apply-footer');
            if (footer) {
                footer.querySelectorAll('button').forEach(b => {
                    if (b.offsetParent === null) return;
                    const t = (b.getAttribute('aria-label') || b.textContent || '').trim();
                    if (t) parts.push('b:' + t);
                });
            }
            return parts.join('|').slice(0, 2500);
        }""") or ""
    except Exception:
        return ""


def _linkedin_modal_blocking_error_count(modal) -> int:
    try:
        return int(modal.evaluate(r"""(el) => {
            let n = 0;
            const vis = x => x && x.offsetParent !== null;
            el.querySelectorAll(
                '.artdeco-inline-feedback--error, [data-test-form-element-error-message]'
            ).forEach(err => {
                const t = (err.textContent || '').trim();
                if (vis(err) && t.length > 2) n++;
            });
            el.querySelectorAll('[aria-invalid="true"]').forEach(x => {
                if (vis(x)) n++;
            });
            return n;
        }""") or 0)
    except Exception:
        return 0


def _linkedin_modal_unanswered_required(modal) -> int:
    try:
        return int(modal.evaluate(r"""(el) => {
            let n = 0;
            const empty = v => !v || !String(v).trim();
            const vis = x => x && x.offsetParent !== null;
            const badSelect = t => !t || ['select', 'please select', 'choose', '-', ''].includes(t);
            el.querySelectorAll(
                'input[required]:not([type="hidden"]):not([type="file"]), ' +
                'input[aria-required="true"]:not([type="hidden"]):not([type="file"])'
            ).forEach(inp => {
                if (!vis(inp) || !empty(inp.value)) return;
                n++;
            });
            el.querySelectorAll('select[required], select[aria-required="true"]').forEach(sel => {
                if (!vis(sel)) return;
                const t = (sel.options[sel.selectedIndex]
                    ? (sel.options[sel.selectedIndex].text || '') : '').trim().toLowerCase();
                if (!sel.value || badSelect(t)) n++;
            });
            el.querySelectorAll(
                'fieldset, div[data-test-form-element], div.jobs-easy-apply-form-section__grouping'
            ).forEach(group => {
                if (!group.querySelector('[required], [aria-required="true"]')) return;
                const radios = group.querySelectorAll(
                    'input[type="radio"], input[type="checkbox"], ' +
                    '[data-test-text-selectable-option__input]'
                );
                if (!radios.length) return;
                const answered = [...radios].some(r =>
                    r.checked || r.getAttribute('aria-checked') === 'true'
                );
                if (!answered) n++;
            });
            return n;
        }""") or 0)
    except Exception:
        return 0


def _linkedin_easy_apply_ready_to_advance(modal) -> tuple[bool, str]:
    """Pre-nav gate: visible errors and empty required fields inside the modal."""
    errors = _linkedin_modal_blocking_error_count(modal)
    if errors:
        return False, f"{errors} visible validation error(s) in modal"
    missing = _linkedin_modal_unanswered_required(modal)
    if missing:
        return False, f"{missing} required field(s) still empty in modal"
    return True, ""


def _linkedin_easy_apply_step_advanced(modal, before_sig: str, wait_s: float = 2.0) -> bool:
    """Post-nav proof: wizard content or footer changed after Next/Review click."""
    time.sleep(wait_s)
    after_sig = _linkedin_easy_apply_step_signature(modal)
    if not before_sig and after_sig:
        return True
    return bool(before_sig and after_sig and before_sig != after_sig)


def _linkedin_unwrap_safety_url(href: str) -> str:
    """Decode linkedin.com/safety/go?url=... to the destination ATS URL."""
    href = (href or "").strip()
    if not href or "linkedin.com/safety/go" not in href.lower():
        return href
    try:
        from urllib.parse import parse_qs, unquote, urlparse
        raw = parse_qs(urlparse(href).query).get("url", [""])[0]
        return unquote(raw) if raw else href
    except Exception:
        return href


def _linkedin_element_has_external_icon(el) -> bool:
    try:
        for sel in (
            "svg[id*='link-external']",
            "svg[data-test-icon='link-external']",
            "svg[data-supported-dps][id*='link-external']",
            "use[href*='link-external']",
        ):
            if el.locator(sel).count() > 0:
                return True
    except Exception:
        pass
    return False


def _linkedin_element_is_easy_apply_button(el) -> bool:
    """
    LinkedIn Easy Apply uses a primary button labeled 'Apply' with
    aria-label 'LinkedIn Apply to …' and a LinkedIn bug icon (not link-external).
    """
    try:
        aria = (el.get_attribute("aria-label") or "").strip().lower()
        if aria.startswith("linkedin apply to") or aria.startswith("easy apply to"):
            return True
        if _linkedin_element_has_external_icon(el):
            return False
        if el.locator(
            "svg[data-test-icon='linkedin-bug-xxsmall'], "
            "svg[data-test-icon='linkedin-bug-small'], "
            "use[href*='linkedin-bug']"
        ).count() > 0:
            return True
        cls = (el.get_attribute("class") or "").lower()
        tag = (el.evaluate("el => el.tagName") or "").lower()
        if tag == "button" and "jobs-apply-button" in cls:
            return True
    except Exception:
        pass
    return False


def _extract_apply_href(apply_info: dict) -> str:
    """Return href from a detected apply CTA, if any."""
    if not apply_info:
        return ""
    try:
        return (apply_info["locator"].get_attribute("href") or "").strip()
    except Exception:
        return ""


def _linkedin_classify_apply_element(el, page) -> tuple[str, str]:
    """Return (apply_type, label) or ('', '') if not an apply CTA."""
    try:
        if not el.is_visible(timeout=1200):
            return "", ""
    except Exception:
        return "", ""
    try:
        text = (el.inner_text(timeout=1200) or "").strip()
        aria = (el.get_attribute("aria-label") or "").strip()
        tag = (el.evaluate("el => el.tagName") or "").lower()
    except Exception:
        return "", ""
    combined = f"{text} {aria}".lower()
    label = aria or text
    if not label:
        return "", ""
    if _apply_cta_score(text, aria) < 0:
        return "", ""
    # Real apply controls are buttons/links, not whole job-card containers
    if tag not in ("button", "a") and not aria.lower().startswith(
        ("easy apply to", "apply to", "linkedin apply to")
    ):
        return "", ""

    href = ""
    try:
        href = (el.get_attribute("href") or "").strip()
    except Exception:
        pass

    # External / company website (check before generic "Apply" text)
    if "apply on company website" in combined or (
        "company website" in combined and re.search(r"\bapply\b", combined)
    ):
        return "company_website", label
    if "offsite" in combined:
        return "company_website", label
    if href and "linkedin.com/safety/go" in href.lower():
        return "company_website", label
    if _linkedin_element_has_external_icon(el):
        return "company_website", label
    if href and href.startswith("http") and "linkedin.com" not in href.lower():
        return "apply", label

    # Easy Apply (LinkedIn modal) — before plain "Apply" text heuristic
    if "easy apply" in combined:
        return "easy_apply", label
    if _linkedin_element_is_easy_apply_button(el):
        return "easy_apply", label

    if re.search(r"\bapply\b", combined) and "easy" not in combined:
        try:
            top_text = _linkedin_job_top_card(page).inner_text(timeout=2000).lower()
        except Exception:
            top_text = ""
        if "responses managed off linkedin" in top_text:
            return "apply", label
        if tag == "a":
            return "company_website", label
        return "apply", label

    return "", ""


def _linkedin_scan_apply_buttons(page) -> list[dict]:
    """Collect visible Apply / Easy Apply CTAs from the job top card."""
    top = _linkedin_job_top_card(page)
    seen_keys: set[str] = set()
    candidates: list[dict] = []

    def _add(el, priority: int):
        apply_type, label = _linkedin_classify_apply_element(el, page)
        if not apply_type:
            return
        key = f"{apply_type}:{label.lower()}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        candidates.append({
            "type": apply_type,
            "label": label,
            "locator": el,
            "priority": priority,
        })

    # 1) ARIA labels (most reliable on current LinkedIn)
    for pattern, prio in (
        (r"Easy Apply to", 0),
        (r"LinkedIn Apply to", 0),
        (r"^Easy Apply$", 0),
        (r"Apply on company website", 1),
        (r"Apply to", 2),
        (r"^Apply$", 4),
    ):
        for role in ("button", "link"):
            try:
                loc = top.get_by_role(role, name=re.compile(pattern, re.I))
                for i in range(min(loc.count(), 3)):
                    _add(loc.nth(i), prio)
            except Exception:
                pass

    # 2) Legacy + modern class-based selectors
    css_selectors = (
        "a[aria-label*='Apply on company website']",
        "button[aria-label*='LinkedIn Apply to']",
        "button[aria-label*='Easy Apply to']",
        "a[href*='linkedin.com/safety/go']",
        "button.jobs-apply-button",
        "a.jobs-apply-button--link",
        "button.jobs-apply-button--top-card",
        "div.jobs-s-apply button",
        "div.jobs-apply-button--top-card button",
        "div.jobs-apply-button--top-card a",
        "button[data-control-name='jobdetails_topcard_inapply']",
        "a[data-control-name='jobdetails_topcard_inapply']",
        "button[data-view-name='job-apply-button']",
        "a[data-view-name='job-apply-button']",
        ".jobs-details-jobs-unified-top-card button",
        ".jobs-details-jobs-unified-top-card a[href]",
    )
    for sel in css_selectors:
        loc = top.locator(sel)
        for i in range(min(loc.count(), 4)):
            _add(loc.nth(i), 4)

    # 3) Text match on button/link in top card only
    for sel in (
        "button:has-text('Easy Apply')",
        "button:has-text('Apply')",
        "a:has-text('Apply')",
        "button:has-text('Apply on company website')",
        "a:has-text('Apply on company website')",
    ):
        loc = top.locator(sel)
        for i in range(min(loc.count(), 3)):
            _add(loc.nth(i), 5)

    type_order = {"easy_apply": 0, "apply": 1, "company_website": 2}

    def _sort_key(c: dict) -> tuple:
        label = (c.get("label") or "").lower()
        aria_bonus = 0
        if label.startswith(("easy apply to", "linkedin apply to", "apply to")):
            aria_bonus = -2
        if "\n" in c.get("label", ""):
            aria_bonus = 5
        return (type_order.get(c["type"], 9), aria_bonus, c["priority"])

    candidates.sort(key=_sort_key)
    return candidates


def _linkedin_detect_apply_button(page, log: bool = True) -> Optional[dict]:
    """
    Find the primary apply CTA on a LinkedIn job page.
    Returns {type, label, locator} or None.
    """
    candidates = _linkedin_scan_apply_buttons(page)
    if not candidates:
        # Retry on the apply-button row only (tighter than full main content)
        row = page.locator("div.jobs-apply-button--top-card").first
        try:
            if row.count() > 0 and row.is_visible(timeout=600):
                seen: set[str] = set()
                tmp: list[dict] = []

                def _add_row(el, priority: int):
                    apply_type, label = _linkedin_classify_apply_element(el, page)
                    if not apply_type:
                        return
                    key = f"{apply_type}:{label.lower()}"
                    if key in seen:
                        return
                    seen.add(key)
                    tmp.append({
                        "type": apply_type,
                        "label": label,
                        "locator": el,
                        "priority": priority,
                    })

                for sel in (
                    "a[aria-label*='Apply on company website']",
                    "button[aria-label*='LinkedIn Apply to']",
                    "button:has-text('Easy Apply')",
                    "button.jobs-apply-button",
                    "[aria-label*='Easy Apply to']",
                    "[aria-label*='Apply on company website']",
                ):
                    loc = row.locator(sel)
                    for i in range(min(loc.count(), 2)):
                        _add_row(loc.nth(i), 0)
                candidates = tmp
        except Exception:
            pass
    if candidates:
        best = candidates[0]
        if log:
            logger.info(
                f"  Found apply CTA: '{best['label']}' (type={best['type']}, "
                f"{len(candidates)} candidate(s))"
            )
        return best
    return None


def _linkedin_click_apply_button(apply_info: dict, page=None):
    """Click the apply CTA; prefer the inner jobs-apply-button when wrapped in a div."""
    btn = apply_info["locator"]
    try:
        inner = btn.locator("button.jobs-apply-button").first
        if inner.count() > 0 and inner.is_visible(timeout=1500):
            btn = inner
    except Exception:
        pass
    btn.scroll_into_view_if_needed(timeout=5000)
    try:
        btn.click(timeout=10000)
    except Exception:
        if page is not None:
            top = _linkedin_job_top_card(page)
            alt = top.locator(
                "button.jobs-apply-button, button[aria-label*='LinkedIn Apply to']"
            ).first
            if alt.count() > 0:
                alt.scroll_into_view_if_needed(timeout=5000)
                alt.click(timeout=10000, force=True)
                return
        btn.click(timeout=10000, force=True)


def _linkedin_open_apply_target(page, ctx, apply_info: dict, job: dict,
                                  qa: dict, vision_model: str, base_url: str) -> tuple:
    """
    Click the detected apply button.
    Returns (target_page, outcome).
    outcome: easy_apply_modal | external_page | signup_required | failed
    """
    apply_type = apply_info["type"]
    label = apply_info.get("label", apply_type)
    logger.info(f"  Apply button: '{label}' -> type={apply_type}")

    if apply_type == "easy_apply":
        for attempt in range(3):
            _linkedin_click_apply_button(apply_info, page=page)
            _pause(2.0, 3.0)
            if _linkedin_easy_apply_modal_visible(page):
                return page, "easy_apply_modal"
            if attempt == 0:
                try:
                    top = _linkedin_job_top_card(page)
                    alt = top.locator(
                        "div.jobs-s-apply button.jobs-apply-button, "
                        "button.jobs-apply-button--top-card"
                    ).first
                    if alt.count() > 0 and alt.is_visible(timeout=2000):
                        apply_info = {**apply_info, "locator": alt}
                except Exception:
                    pass
        if vision_model and base_url:
            coords = _vision_find_button(page, "Easy Apply button", vision_model, base_url)
            if coords:
                page.mouse.click(*coords)
                _pause(2.0, 3.0)
                if _linkedin_easy_apply_modal_visible(page):
                    return page, "easy_apply_modal"
        return page, "failed"

    if apply_type in ("apply", "company_website"):
        pages_before = list(ctx.pages)
        url_before = page.url
        target = page
        new_tab = False

        # Single click — a second click on timeout often opens duplicate ATS tabs.
        _linkedin_click_apply_button(apply_info)
        for _ in range(24):
            new_pages = [
                p for p in ctx.pages
                if p not in pages_before and p != page and not p.is_closed()
            ]
            if new_pages:
                target = new_pages[-1]
                new_tab = True
                break
            cur = (page.url or "").lower()
            if cur != url_before.lower() and "linkedin.com" not in cur:
                target = page
                break
            _pause(0.5, 0.5)

        try:
            target.wait_for_load_state("domcontentloaded", timeout=25000)
        except Exception:
            pass
        if new_tab:
            logger.info(f"  Apply opened new tab: {target.url[:90]}")
        elif target.url != url_before:
            logger.info(f"  Apply navigated same tab: {target.url[:90]}")
        _dismiss_blocking_popups(target)

        from agents.account_signup import clear_auth_wall
        if not clear_auth_wall(target, job, qa, screenshot_fn=_screenshot):
            return target, "signup_required"

        if "linkedin.com" in target.url.lower():
            for p in ctx.pages:
                if p is page or p.is_closed():
                    continue
                u = (p.url or "").lower()
                if "linkedin.com" not in u:
                    target = p
                    new_tab = True
                    try:
                        target.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    logger.info(f"  ATS tab found (sibling): {target.url[:90]}")
                    _dismiss_blocking_popups(target)
                    break
            if "linkedin.com" in target.url.lower():
                if _linkedin_easy_apply_modal_visible(target):
                    logger.info("  Apply opened LinkedIn Easy Apply modal (same page)")
                    return target, "easy_apply_modal"
                return target, "failed"

        return target, "external_page"

    return page, "failed"


def _external_form_already_visible(page) -> bool:
    """True if the page already shows the application form (no Apply click needed)."""
    indicators = (
        "input[type='file']",
        "input[name='first_name'], input[id='first_name']",
        "input[name='last_name'],  input[id='last_name']",
        "input[name='email'],      input[id='email']",
        "button:has-text('Autofill with Resume')",
        "button:has-text('Autofill with resume')",
        "button:has-text('Fill with resume')",
        "[data-automation-id*='fillWithResume']",
        "form[action*='greenhouse']",
        "form[action*='workday']",
    )
    for sel in indicators:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def _click_apply_on_external_page(page, vision_model: str = "", base_url: str = "") -> bool:
    """On an external ATS page, dismiss overlays then click the best Apply CTA."""
    _dismiss_blocking_popups(page)
    _pause(0.5, 1.0)

    if _external_form_already_visible(page):
        logger.info("  External form already visible — skipping Apply CTA click")
        return True

    candidates = _scan_apply_cta_buttons(page)
    if candidates:
        best = candidates[0]
        try:
            pages_before = list(page.context.pages)
            best["locator"].scroll_into_view_if_needed(timeout=5000)
            best["locator"].click(timeout=10000)
            _pause(2.0, 3.5)
            logger.info(
                f"  Clicked Apply CTA: '{best['label'][:50]}' (score={best['score']})"
            )
            # If the click spawned a duplicate tab to the same ATS, switch to it
            # and close the old one to keep one window per application.
            try:
                new_pages = [p for p in page.context.pages if p not in pages_before]
                if new_pages:
                    fresh = new_pages[-1]
                    try:
                        fresh.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    logger.info(
                        f"  Apply CTA opened new tab — using {fresh.url[:80]}"
                    )
            except Exception:
                pass
            return True
        except Exception as e:
            logger.debug(f"  Apply CTA click failed: {e}")

    if vision_model and base_url:
        coords = _vision_find_button(
            page,
            "Apply or Apply Now button for this job (NOT Subscribe, newsletter, or chat)",
            vision_model, base_url,
        )
        if coords:
            page.mouse.click(*coords)
            _pause(2.0, 3.5)
            return True
    return False


_ATS_HOST_FRAGMENTS = (
    "greenhouse.io", "myworkdayjobs.com", "workday.com",
    "lever.co", "ashbyhq.com", "workable.com", "people-jobs.com",
    "smartrecruiters.com", "icims.com", "successfactors.com",
    "taleo.net", "bamboohr.com", "recruitee.com", "personio.com",
)


def _is_ats_url(url: str) -> bool:
    u = (url or "").lower()
    return any(host in u for host in _ATS_HOST_FRAGMENTS)


def _switch_to_latest_ats_page(page):
    """If a sibling tab was opened, return the most recent ATS page and close duplicates."""
    try:
        pages = page.context.pages
        if not pages:
            return page
        ats_pages = []
        for p in reversed(pages):
            if p.is_closed():
                continue
            if _is_ats_url(p.url or ""):
                ats_pages.append(p)
        if not ats_pages:
            return page
        latest = ats_pages[0]
        try:
            latest.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        for dup in ats_pages[1:]:
            try:
                if not dup.is_closed():
                    dup.close()
                    logger.info("  Closed duplicate ATS tab")
            except Exception:
                pass
        return latest
    except Exception:
        pass
    return page


def _prepare_generic_application_page(
    page,
    job: dict,
    qa: dict,
    vision_model: str = "",
    base_url: str = "",
):
    """Open a generic ATS application CTA, then clear any resumable auth wall."""
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "application portal")
        return page, False
    if not _external_form_already_visible(page):
        _click_apply_on_external_page(page, vision_model, base_url)
        _pause(1.0, 2.0)
        page = _switch_to_latest_ats_page(page)
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "application portal")
        return page, False
    from agents.account_signup import clear_auth_wall
    if not clear_auth_wall(page, job, qa, screenshot_fn=_screenshot):
        return page, False
    return page, True


def _finalize_non_wizard(page, job: dict, platform: str, dry_run: bool,
                         vision_model: str = "", base_url: str = "") -> bool:
    """Submit (or screenshot for dry-run) after a non-wizard ATS form is filled.

    Called after _fill_greenhouse / _fill_lever / _fill_ashby / _fill_workable /
    _fill_ai_driven.  Sets job['applied'] and job['apply_notes'] in-place.
    """
    _pause(1.0, 2.0)
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, platform)
        _screenshot(page, job, "_captcha_required")
        return False
    if dry_run:
        _screenshot(page, job, "_dryrun")
        job.update({
            "applied": False,
            "apply_notes": f"Dry run — {platform} filled, not submitted",
            "submission_status": "dry_run",
        })
        return True

    submitted = False
    submit_selectors = [
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "button:has-text('Send')",
        "button[type='submit']",
        "input[type='submit']",
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                label = " ".join(filter(None, [
                    btn.inner_text(timeout=600) if hasattr(btn, "inner_text") else "",
                    btn.get_attribute("value") or "",
                    btn.get_attribute("aria-label") or "",
                ])).strip().lower()
                if not any(word in label for word in ("submit", "apply", "send")):
                    continue
                if any(word in label for word in (
                    "next", "continue", "search", "sign in", "log in", "register",
                )):
                    continue
                btn.click()
                _pause(2.0, 4.0)
                submitted = True
                if _detect_captcha_challenge(page):
                    _mark_captcha_required(page, job, platform)
                    _screenshot(page, job, "_captcha_required")
                    return False
                break
        except Exception:
            pass

    confirmed_page = _wait_for_submission_confirmation(
        page, job, platform, timeout_s=10.0 if submitted else 0.0
    )
    if confirmed_page:
        logger.info(f"  Submitted: {job.get('title', '')} @ {job.get('company', '')} ({platform})")
        _screenshot(confirmed_page, job, "_submitted")
        return True
    elif submitted:
        _mark_submission_unconfirmed(page, job, platform)
        _screenshot(page, job, "_submit_unconfirmed")
        return False
    else:
        job.update({
            "applied": False,
            "apply_notes": f"Submit button not found — manual review ({platform})",
            "decision": "manual_review",
            "submission_status": "incomplete",
        })
        _screenshot(page, job, "_submit_failed")
        return False


def _fill_external_ats_page(page, job: dict, qa: dict, profile: str,
                            model: str, base_url: str, dry_run: bool,
                            vision_model: str = "") -> bool:
    """Fill application form on a page that left LinkedIn (new tab or redirect)."""
    _dismiss_blocking_popups(page)
    _pause(0.5, 1.0)
    page = _switch_to_latest_ats_page(page)
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "external ATS")
        _screenshot(page, job, "_captcha_required")
        return False
    from agents.account_signup import clear_auth_wall
    if not clear_auth_wall(page, job, qa, screenshot_fn=_screenshot):
        return False

    _click_apply_on_external_page(page, vision_model, base_url)
    _pause(1.0, 2.0)
    page = _switch_to_latest_ats_page(page)
    if _detect_captcha_challenge(page):
        _mark_captcha_required(page, job, "external ATS")
        _screenshot(page, job, "_captcha_required")
        return False

    platform = _detect_platform(page.url, page)
    logger.info(f"  External ATS platform: {platform}")

    if platform == "workday":
        return _fill_workday(page, job, qa, profile, model, base_url, dry_run)
    if platform == "greenhouse":
        ok = _fill_greenhouse(page, job, qa, profile, model, base_url, dry_run)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "lever":
        ok = _fill_lever(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "ashby":
        ok = _fill_ashby(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "workable":
        ok = _fill_workable(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "teamtailor":
        ok = _fill_teamtailor(page, job, qa, profile, model, base_url, vision_model=vision_model)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "icims":
        ok = _fill_icims(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "smartrecruiters":
        ok = _fill_smartrecruiters(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "taleo":
        ok = _fill_taleo(page, job, qa, profile, model, base_url)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    if platform == "oracle_recruiting":
        ok = _fill_oracle_recruiting(page, job, qa, profile, model, base_url, vision_model=vision_model)
        return ok and _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
    ok = _fill_ai_driven(page, job, qa, profile, model, base_url, vision_model=vision_model)
    return ok and _finalize_non_wizard(
        page, job, platform or "ai_driven", dry_run, vision_model, base_url
    )


def _submission_confirmation_evidence(page) -> dict:
    """Return acceptance evidence exposed by the portal after final submission."""
    url = ""
    try:
        url = page.url or ""
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return {"confirmed": False, "url": url, "text": "", "signal": ""}
    signals = (
        "thank you for applying",
        "application submitted",
        "application was submitted",
        "your application was sent",
        "we received your application",
        "successfully submitted",
        "application has been submitted",
        "application has been received",
        "application sent",
        "you applied",
        "you've applied",
    )
    for signal in signals:
        index = body.find(signal)
        if index < 0:
            continue
        start = max(0, index - 120)
        end = min(len(body), index + len(signal) + 180)
        text = re.sub(r"\s+", " ", body[start:end]).strip()
        return {"confirmed": True, "url": url, "text": text, "signal": signal}
    return {"confirmed": False, "url": url, "text": "", "signal": ""}


def _record_confirmed_submission(
    job: dict,
    platform: str,
    *,
    confirmation_url: str = "",
    confirmation_text: str = "",
    note: str = "",
) -> None:
    """Persist a terminal application outcome with the evidence that justified it."""
    platform_low = (platform or "").lower()
    if "linkedin" in platform_low and "easy apply" in platform_low:
        if not job.get("_final_submit_clicked"):
            logger.error(
                "Refusing applied=True for LinkedIn Easy Apply without Submit application click"
            )
            job.update({
                "applied": False,
                "decision": "manual_review",
                "submission_status": "incomplete",
                "apply_notes": (
                    "LinkedIn Easy Apply: cannot mark submitted without clicking "
                    "Submit application"
                ),
            })
            return
    job.update({
        "applied": True,
        "decision": "applied",
        "apply_notes": note or f"Confirmed submitted via {platform}",
        "submission_status": "confirmed",
        "submission_confirmed_at": datetime.now(timezone.utc).isoformat(),
        "confirmation_url": confirmation_url,
        "confirmation_text": confirmation_text,
    })


def _record_live_apply_attempt(job: dict, dry_run: bool) -> None:
    """Persist a live apply attempt counter before browser work starts."""
    timestamp = datetime.now(timezone.utc).isoformat()
    attempts = int(job.get("apply_attempts") or 0)
    job["last_apply_attempt_at"] = timestamp
    if dry_run:
        return
    job_id = job.get("job_id") or job.get("id")
    if not job_id:
        return
    try:
        from agents.job_logger import get_store
        get_store().update_job(
            int(job_id),
            applied=False,
            apply_attempts=attempts,
            last_apply_attempt_at=timestamp,
        )
    except Exception as exc:
        logger.debug("Live apply attempt counter not persisted: %s", exc)


def _mark_submission_confirmed(page, job: dict, platform: str) -> bool:
    evidence = _submission_confirmation_evidence(page)
    if not evidence["confirmed"]:
        return False
    _record_confirmed_submission(
        job,
        platform,
        confirmation_url=evidence["url"],
        confirmation_text=evidence["text"],
    )
    return True


def _submission_candidate_pages(page) -> list:
    """Return live tabs newest-first so redirects and confirmation popups are checked."""
    try:
        pages = list(page.context.pages)
    except Exception:
        pages = []
    candidates = []
    for candidate in [*reversed(pages), page]:
        try:
            if candidate in candidates or candidate.is_closed():
                continue
        except Exception:
            continue
        candidates.append(candidate)
    return candidates


def _wait_for_submission_confirmation(
    page, job: dict, platform: str, timeout_s: float = 10.0
):
    """Wait for portal acceptance evidence on the current page or a newly opened tab."""
    deadline = time.monotonic() + timeout_s
    while True:
        for candidate in _submission_candidate_pages(page):
            try:
                if _mark_submission_confirmed(candidate, job, platform):
                    return candidate
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def reconcile_confirmation_pending_jobs(
    jobs: list[dict],
    *,
    headless: bool = False,
    linkedin_email: str = "",
    linkedin_password: str = "",
    max_checks: int = 3,
) -> list[dict]:
    """Revisit uncertain outcomes for evidence without clicking Submit again."""
    if not jobs or not PLAYWRIGHT_AVAILABLE:
        return jobs

    with sync_playwright() as playwright:
        browser = None
        linkedin_ctx = None
        linkedin_page = None
        linkedin_ready = None
        try:
            for job in jobs:
                checks = int(job.get("confirmation_checks") or 0) + 1
                job["confirmation_checks"] = checks
                job["last_confirmation_check_at"] = datetime.now(timezone.utc).isoformat()
                url = (
                    job.get("confirmation_url")
                    or job.get("job_url_direct")
                    or job.get("job_url")
                    or ""
                )
                if not url:
                    job["apply_notes"] = (
                        f"Confirmation check {checks}/{max_checks}: no URL available"
                    )
                    continue

                context = None
                page = None
                try:
                    is_linkedin = "linkedin.com" in url.lower()
                    if is_linkedin:
                        if linkedin_ctx is None:
                            linkedin_ctx = _get_linkedin_context(playwright)
                            linkedin_ready = _ensure_linkedin_login(
                                linkedin_ctx, linkedin_email, linkedin_password
                            )
                            linkedin_page = (
                                linkedin_ctx.pages[0]
                                if linkedin_ctx.pages
                                else linkedin_ctx.new_page()
                            )
                        if not linkedin_ready:
                            job["apply_notes"] = (
                                f"Confirmation check {checks}/{max_checks}: "
                                "LinkedIn login or verification required"
                            )
                            continue
                        page = linkedin_page
                    else:
                        if browser is None:
                            browser = playwright.chromium.launch(
                                headless=headless,
                                args=[
                                    "--disable-blink-features=AutomationControlled",
                                    "--no-sandbox",
                                    "--disable-dev-shm-usage",
                                ],
                            )
                        context = browser.new_context(
                            viewport={"width": 1440, "height": 900},
                            locale="en-US",
                        )
                        context.add_init_script(
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{get: () => undefined});"
                        )
                        page = context.new_page()

                    logger.info(
                        f"Reconciling submission [{job.get('title', '')} @ "
                        f"{job.get('company', '')}]: {url}"
                    )
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _pause(1.0, 2.0)
                    platform = _detect_platform(url, page)
                    confirmed_page = _wait_for_submission_confirmation(
                        page, job, platform, timeout_s=4.0
                    )
                    if (
                        not confirmed_page
                        and is_linkedin
                        and _linkedin_page_status(page) == "already_applied"
                    ):
                        _record_confirmed_submission(
                            job,
                            "LinkedIn",
                            confirmation_url=page.url or "",
                            confirmation_text=(
                                "LinkedIn job page showed an already-applied status"
                            ),
                            note="Already submitted (detected during reconciliation)",
                        )
                        confirmed_page = page
                    if confirmed_page:
                        logger.info("  Submission confirmation recovered")
                        _screenshot(confirmed_page, job, "_reconciled")
                        continue

                    job.update({
                        "applied": False,
                        "decision": "manual_review",
                        "submission_status": "confirmation_pending",
                        "apply_notes": (
                            f"Confirmation check {checks}/{max_checks}: no acceptance "
                            "evidence found; automatic resubmission remains blocked"
                        ),
                    })
                except Exception as exc:
                    job.update({
                        "applied": False,
                        "decision": "manual_review",
                        "submission_status": "confirmation_pending",
                        "apply_notes": (
                            f"Confirmation check {checks}/{max_checks} failed: {exc}"
                        ),
                    })
                finally:
                    try:
                        if context is not None:
                            context.close()
                    except Exception:
                        pass
        finally:
            try:
                if linkedin_ctx is not None:
                    linkedin_ctx.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
    return jobs


def _mark_submission_unconfirmed(page, job: dict, platform: str) -> None:
    """Quarantine click-only outcomes so a retry cannot accidentally double-submit."""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": (
            f"{platform}: submit clicked but no confirmation detected - "
            "deferred to prevent a duplicate submission"
        ),
        "submission_status": "confirmation_pending",
        "confirmation_url": url,
        "confirmation_text": "",
    })


def _application_submitted_on_page(page) -> bool:
    """Backward-compatible boolean acceptance check."""
    return bool(_submission_confirmation_evidence(page)["confirmed"])


def _linkedin_submit_application_label(btn) -> str:
    try:
        return " ".join(filter(None, [
            btn.inner_text(timeout=400) or "",
            btn.get_attribute("aria-label") or "",
            btn.get_attribute("title") or "",
        ])).strip()
    except Exception:
        return ""


def _linkedin_is_submit_application_label(label: str) -> bool:
    """True only for the final LinkedIn Easy Apply submit CTA (not Next/Review/Save)."""
    low = (label or "").strip().lower()
    if "submit application" in low:
        return True
    return low in ("submit application",)


def _linkedin_submit_button(page, modal=None):
    """Return the visible final Submit application button, if present."""
    root = modal if modal is not None else (_linkedin_easy_apply_modal(page) or page)
    selectors = (
        "footer button[data-live-test-easy-apply-submit-button]",
        "button[data-live-test-easy-apply-submit-button]",
        "footer button[aria-label='Submit application']",
        "footer button[aria-label*='Submit application']",
        "footer button:has-text('Submit application')",
        "button[aria-label='Submit application']",
        "button[aria-label*='Submit application']",
        "button:has-text('Submit application')",
    )
    for sel in selectors:
        try:
            btn = root.locator(sel).first
            if btn.count() and btn.is_visible(timeout=600):
                label = _linkedin_submit_application_label(btn)
                if _linkedin_is_submit_application_label(label):
                    return btn
        except Exception:
            continue
    if modal is None:
        for sel in (
            ".jobs-easy-apply-modal button[aria-label*='Submit application']",
            "button:has-text('Submit application')",
        ):
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=600):
                    label = _linkedin_submit_application_label(btn)
                    if _linkedin_is_submit_application_label(label):
                        return btn
            except Exception:
                continue
    for source in (root, page):
        try:
            buttons = source.locator("button")
            for i in range(min(buttons.count(), 30)):
                btn = buttons.nth(i)
                try:
                    if not btn.is_visible(timeout=250) or btn.is_disabled(timeout=250):
                        continue
                    label = _linkedin_submit_application_label(btn)
                    if _linkedin_is_submit_application_label(label):
                        return btn
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _linkedin_easy_apply_submission_evidence(page) -> dict:
    """
  LinkedIn Easy Apply: accept submission only when the wizard is gone and the
  page shows a real post-submit message (not a saved draft / in-progress state).
    """
    if _linkedin_easy_apply_modal_visible(page):
        return {"confirmed": False, "url": "", "text": "", "signal": ""}
    status = _linkedin_page_status(page)
    if status == "in_progress":
        return {"confirmed": False, "url": "", "text": "", "signal": ""}
    try:
        url = page.url or ""
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return {"confirmed": False, "url": "", "text": "", "signal": ""}
    signals = (
        "application submitted",
        "your application was sent",
        "application was sent",
        "we received your application",
        "successfully submitted your application",
        "you applied to",
    )
    for signal in signals:
        index = body.find(signal)
        if index < 0:
            continue
        if signal == "you applied to" and "submit application" in body:
            continue
        start = max(0, index - 120)
        end = min(len(body), index + len(signal) + 180)
        text = re.sub(r"\s+", " ", body[start:end]).strip()
        return {"confirmed": True, "url": url, "text": text, "signal": signal}
    if status == "already_applied":
        return {
            "confirmed": True,
            "url": url,
            "text": "LinkedIn job page showed submitted status",
            "signal": "already_applied",
        }
    return {"confirmed": False, "url": url, "text": "", "signal": ""}


def _linkedin_click_submit_application(page, job: dict, modal=None) -> bool:
    """Click only the final Submit application control inside the Easy Apply wizard."""
    btn = _linkedin_submit_button(page, modal=modal)
    if btn is None:
        return False
    label = _linkedin_submit_application_label(btn)
    if not _linkedin_is_submit_application_label(label):
        logger.warning(f"  Refusing non-submit CTA: {label[:80]!r}")
        return False
    try:
        _linkedin_scroll_modal_content(modal or _linkedin_easy_apply_modal(page) or page, 1.0)
    except Exception:
        pass
    try:
        btn.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    btn.click(timeout=10000)
    job["_final_submit_clicked"] = True
    logger.info(f"  Easy Apply: clicked Submit application ({label[:60]})")
    return True


def _linkedin_discard_incomplete_application(page) -> None:
    """Try to close the wizard without leaving a saved draft on LinkedIn."""
    try:
        modal = _linkedin_easy_apply_modal(page)
        if modal is None:
            return
        for sel in (
            "button[aria-label='Dismiss']",
            "button[aria-label*='Dismiss']",
            "button.artdeco-modal__dismiss",
        ):
            try:
                btn = modal.locator(sel).first
                if btn.count() and btn.is_visible(timeout=500):
                    btn.click(timeout=3000)
                    _pause(0.5, 1.0)
                    break
            except Exception:
                continue
        _linkedin_easy_apply_dismiss_save_dialog(page)
        for sel in (
            "button:has-text('Discard')",
            "button[aria-label*='Discard']",
        ):
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=800):
                    btn.click(timeout=3000)
                    logger.info("  Discarded incomplete LinkedIn Easy Apply draft")
                    _pause(0.5, 1.0)
                    return
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Could not discard LinkedIn draft: %s", exc)


def _linkedin_confirm_after_submit(page, job: dict) -> bool:
    """Wait for LinkedIn confirmation after Submit application was clicked."""
    if not job.get("_final_submit_clicked"):
        return False
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        for candidate in _submission_candidate_pages(page):
            try:
                evidence = _linkedin_easy_apply_submission_evidence(candidate)
                if evidence["confirmed"]:
                    _record_confirmed_submission(
                        job,
                        "LinkedIn Easy Apply",
                        confirmation_url=evidence["url"],
                        confirmation_text=evidence["text"],
                        note=(
                            "Submitted via LinkedIn Easy Apply "
                            f"({evidence['signal']})"
                        ),
                    )
                    logger.info("  LinkedIn application submitted (confirmed)")
                    return True
            except Exception:
                continue
        time.sleep(0.5)
    logger.warning("  Submit application clicked but no post-submit confirmation")
    return False


def _linkedin_click_next_or_submit(page, allow_submit: bool = True) -> str:
    """
    Click the Next / Review / Submit button inside the LinkedIn Easy Apply modal.
    DOM-first; vision is NOT used here.
    Returns: 'submit' | 'review' | 'next' | 'not_found'
    All selectors are scoped to the modal to avoid accidentally clicking outside it.
    """
    modal = _linkedin_easy_apply_modal(page)
    if modal is None:
        found = page.locator(
            ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], "
            "div[role='dialog']:has(button[data-easy-apply-next-button]), "
            "div[role='dialog']:has(button[data-live-test-easy-apply-review-button]), "
            "div[role='dialog']:has(button[data-live-test-easy-apply-submit-button])"
        )
        modal = getattr(found, "first", found)

    submit_selectors = [
        "button[data-live-test-easy-apply-submit-button]",
        "button[aria-label='Submit application']",
        "button[aria-label*='Submit application']",
        "button:has-text('Submit application')",
        "footer button:has-text('Submit')",
        ".jobs-easy-apply-footer button:has-text('Submit')",
    ]
    review_selectors = [
        "button[data-live-test-easy-apply-review-button]",
        "button[aria-label='Review your application']",
        "button[aria-label*='Review']",
        "button:has-text('Review your application')",
        "button:has-text('Review')",
        ".jobs-easy-apply-footer button:has-text('Review')",
    ]
    next_selectors = [
        "button[data-easy-apply-next-button]:not([disabled])",
        "button[data-live-test-easy-apply-next-button]:not([disabled])",
        "button[aria-label='Continue to next step']:not([disabled])",
        "button[aria-label*='Continue to next step']:not([disabled])",
        "button[aria-label='Next']:not([disabled])",
        "button[aria-label*='Next']:not([disabled])",
        "footer button:has-text('Next'):not([disabled])",
        ".jobs-easy-apply-footer button:has-text('Next'):not([disabled])",
        "button[data-easy-apply-next-button]",
        # Broad last-resort: primary footer button that isn't Back/Save/Dismiss
        ".jobs-easy-apply-footer button[type='button']:not([disabled])"
        ":not([aria-label*='ack']):not([aria-label*='iscard'])"
        ":not([aria-label*='ave']):not([aria-label*='lose'])",
    ]

    # Scroll the modal's INNER content area to the bottom so all fields are filled
    # and the sticky footer buttons remain accessible.
    # Never call scroll_into_view_if_needed on the footer itself — that scrolls
    # the outer page and can trigger LinkedIn's "save and exit" dialog.
    _linkedin_scroll_modal_content(modal, 1.0)

    # LinkedIn's wizard must be advanced in order. If Next is visible, this is
    # not the final step. Submit is only allowed after Next and Review disappear.
    groups = [("next", next_selectors), ("review", review_selectors)]
    if allow_submit:
        groups.append(("submit", submit_selectors))

    for kind, selectors in groups:
        for sel in selectors:
            try:
                btn = modal.locator(sel).first
                if btn.count() and btn.is_visible(timeout=500):
                    try:
                        if btn.is_disabled(timeout=300):
                            continue
                    except Exception:
                        pass
                    btn.click(timeout=5000)
                    logger.info(f"  Easy Apply: clicked {kind} ({sel[:60]})")
                    return kind
            except Exception:
                pass

    # Last resort — try outside the modal locator in case the footer renders outside
    for kind, selectors in groups:
        for sel in selectors[:5]:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=500):
                    try:
                        if btn.is_disabled(timeout=300):
                            continue
                    except Exception:
                        pass
                    btn.click(timeout=5000)
                    logger.info(f"  Easy Apply: clicked {kind} (page-scope, {sel[:60]})")
                    return kind
            except Exception:
                pass

    logger.warning("  Easy Apply: no Next/Review/Submit button found via DOM")
    return "not_found"


def _linkedin_click_next_or_submit_robust(page, allow_submit: bool = True) -> str:
    """More tolerant LinkedIn Easy Apply navigation using selectors plus button-label scan."""
    first = _linkedin_click_next_or_submit(page, allow_submit=allow_submit)
    if first != "not_found":
        return first

    modal = _linkedin_easy_apply_modal(page)
    if modal is None:
        found = page.locator(
            ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], "
            "div[role='dialog']"
        )
        modal = getattr(found, "first", found)
    # Scroll inner content — NOT the footer (footer scroll triggers the save dialog)
    _linkedin_scroll_modal_content(modal, 1.0)

    button_sources = []
    try:
        button_sources.append(("modal", modal.locator("button")))
    except Exception:
        pass
    try:
        button_sources.append(("dialog", page.locator("div[role='dialog'] button")))
    except Exception:
        pass
    try:
        button_sources.append(("page", page.locator("button")))
    except Exception:
        pass

    desired_order = ["next", "review"]
    if allow_submit:
        desired_order.append("submit")

    for desired_kind in desired_order:
        for source_name, buttons in button_sources:
            try:
                count = min(buttons.count(), 25)
            except Exception:
                continue
            for i in range(count):
                try:
                    btn = buttons.nth(i)
                    if not btn.is_visible(timeout=300) or btn.is_disabled(timeout=300):
                        continue
                    label = " ".join(filter(None, [
                        btn.inner_text(timeout=300) or "",
                        btn.get_attribute("aria-label") or "",
                        btn.get_attribute("title") or "",
                    ])).strip().lower()
                    if not label:
                        continue
                    if any(bad in label for bad in (
                        "back", "save", "discard", "cancel", "close", "dismiss",
                        "learn more", "view",
                    )):
                        continue
                    kind = ""
                    if _linkedin_is_submit_application_label(label):
                        kind = "submit"
                    elif "review" in label:
                        kind = "review"
                    elif "next" in label or "continue" in label:
                        kind = "next"
                    if kind != desired_kind:
                        continue
                    try:
                        btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    btn.click(timeout=5000)
                    logger.info(f"  Easy Apply: clicked {kind} ({source_name} label='{label[:50]}')")
                    return kind
                except Exception:
                    continue

    logger.warning("  Easy Apply: robust nav still found no Next/Review/Submit button")
    return "not_found"


def _linkedin_easy_apply_dismiss_save_dialog(page) -> None:
    """Dismiss 'Save this application?' without leaving the wizard."""
    try:
        save_dialog = page.locator(
            "div[role='dialog']:has-text('Save this application')"
            ", div[role='alertdialog']:has-text('Save this application')"
        ).first
        if save_dialog.count() > 0 and save_dialog.is_visible(timeout=500):
            logger.warning("  'Save this application?' dialog detected — dismissing")
            for resume_btn_sel in (
                "button:has-text('Continue applying')",
                "button:has-text('Continue')",
                "button:has-text('Keep editing')",
            ):
                try:
                    btn_loc = save_dialog.locator(resume_btn_sel).first
                    if btn_loc.count() > 0 and btn_loc.is_visible(timeout=500):
                        btn_loc.click(timeout=3000)
                        _pause(0.5, 1.0)
                        break
                except Exception:
                    pass
    except Exception:
        pass


def _linkedin_easy_apply_fill_cycle(page, job, qa, profile, model, base_url, modal) -> tuple[bool, str]:
    """Fill modal, sweep required fields, fix validation. Returns (ready, reason)."""
    # Sweep through the scrollable modal body. LinkedIn lazily reveals some
    # fields/questions as the popup scrolls, especially on compact viewports.
    for pos in (0.0, 0.45, 0.9, 1.0):
        _linkedin_scroll_modal_content(modal, pos)
        _pause(0.15, 0.3)
        _linkedin_fill_step(page, job, qa, profile, model, base_url, modal=modal)
    _pause(0.4, 0.8)
    req_filled = _fill_required_fields_pass(
        page, job, qa, profile, model, base_url, root=modal)
    if req_filled:
        logger.info(f"  Required-field sweep filled {req_filled} missed field(s)")
        _pause(0.3, 0.6)
    fixed = _fix_step_validation_errors(
        page, job, qa, profile, model, base_url, root=modal)
    if fixed:
        logger.info(f"  Corrected {fixed} validation error(s) in modal")
        _pause(0.4, 0.8)
    return _linkedin_easy_apply_ready_to_advance(modal)


def _linkedin_easy_apply_wizard(page, job: dict, qa: dict, profile: str,
                                model: str, base_url: str, dry_run: bool,
                                vision_model: str = "") -> bool:
    """Step through LinkedIn Easy Apply modal after it is already open."""
    if not _linkedin_easy_apply_modal_visible(page):
        logger.warning("  Easy Apply modal not open — wrong Apply button may have been clicked")
        job["apply_notes"] = "Easy Apply modal did not open"
        return False

    for step_num in range(15):
        _pause(0.8, 1.5)
        modal = _linkedin_easy_apply_modal(page)
        if modal is None:
            # Give LinkedIn a moment then retry once
            _pause(1.5, 2.5)
            modal = _linkedin_easy_apply_modal(page)
            if modal is None:
                logger.warning("  Easy Apply modal closed unexpectedly")
                if job.get("_final_submit_clicked") and _linkedin_confirm_after_submit(page, job):
                    return True
                job.update({
                    "applied": False,
                    "apply_notes": "Easy Apply modal closed before Submit application",
                    "decision": "manual_review",
                    "submission_status": "incomplete",
                })
                return False

        _linkedin_easy_apply_dismiss_save_dialog(page)

        # Do not ask vision to classify the Easy Apply state. It can misread a
        # normal wizard step as a submission confirmation. Submission is accepted
        # only after the DOM helper clicks the final Submit button and LinkedIn
        # confirmation evidence is detected.

        # Two fill attempts
        ready, reason = _linkedin_easy_apply_fill_cycle(
            page, job, qa, profile, model, base_url, modal)
        if not ready:
            logger.info(f"  Step {step_num + 1}: not ready ({reason}) — second fill pass")
            ready, reason = _linkedin_easy_apply_fill_cycle(
                page, job, qa, profile, model, base_url, modal)
        if not ready:
            logger.info(f"  Step {step_num + 1}: proceeding despite ({reason}) — LinkedIn will surface exact errors")

        step_sig = _linkedin_easy_apply_step_signature(modal)
        modal_after = modal

        # Final step: only the Submit application control may complete the job.
        submit_btn = _linkedin_submit_button(page, modal=modal)
        if submit_btn is not None:
            if dry_run:
                job.update({
                    "applied": False,
                    "apply_notes": (
                        "Dry run - reached Submit application step; "
                        "did not click Submit"
                    ),
                    "submission_status": "dry_run",
                    "decision": "auto_apply",
                })
                _linkedin_discard_incomplete_application(page)
                return False
            if _linkedin_click_submit_application(page, job, modal=modal):
                _pause(2.0, 3.5)
                if _linkedin_confirm_after_submit(page, job):
                    return True
                logger.warning("  Submit application clicked but not confirmed")
                _mark_submission_unconfirmed(page, job, "LinkedIn Easy Apply")
                _linkedin_discard_incomplete_application(page)
                return False
            logger.warning("  Submit application button visible but click failed")
            _linkedin_discard_incomplete_application(page)
            job.update({
                "applied": False,
                "apply_notes": "Submit application button found but could not be clicked",
                "decision": "manual_review",
                "submission_status": "incomplete",
            })
            return False

        # Wizard navigation: Next / Review only — never Submit via the nav helper.
        nav_result = _linkedin_click_next_or_submit_robust(page, allow_submit=False)

        if nav_result in ("next", "review"):
            _pause(0.5, 1.0)
            modal_after = _linkedin_easy_apply_modal(page) or modal
            advanced = _linkedin_easy_apply_step_advanced(modal_after, step_sig)
            if not advanced:
                logger.info(f"  Step {step_num + 1}: did not advance — fixing validation and retrying")
                _fix_step_validation_errors(page, job, qa, profile, model, base_url, root=modal_after)
                _linkedin_easy_apply_fill_cycle(page, job, qa, profile, model, base_url, modal_after)
                _pause(0.5, 1.0)
                nav_retry = _linkedin_click_next_or_submit_robust(page, allow_submit=False)
                if nav_retry in ("next", "review"):
                    _pause(1.0, 1.5)
                    modal_retry = _linkedin_easy_apply_modal(page) or modal_after
                    advanced = _linkedin_easy_apply_step_advanced(modal_retry, step_sig)
                    if _linkedin_easy_apply_step_signature(modal_retry) != step_sig:
                        advanced = True
            if advanced:
                logger.info(f"  Easy Apply: advanced past step {step_num + 1} ({nav_result})")
                continue
            logger.warning(f"  Easy Apply: step {step_num + 1} stuck after retry")
            _screenshot(page, job, "_li_stuck")
            _linkedin_discard_incomplete_application(page)
            job.update({
                "applied": False,
                "apply_notes": (
                    f"Easy Apply step {step_num + 1}: wizard did not advance "
                    f"({reason or 'validation may be blocking'})"
                ),
                "decision": "manual_review",
                "submission_status": "incomplete",
            })
            return False

        if False and not ready:
            _screenshot(page, job, "_li_stuck")
            job.update({
                "applied": False,
                "apply_notes": (
                    f"Easy Apply step {step_num + 1}: pre-nav blocked — {reason}"
                ),
                "decision": "manual_review",
            })
            return False

        if vision_model:
            for label in (
                "Next button", "Continue button", "Review button",
            ):
                coords = _vision_find_button(page, label, vision_model, base_url)
                if coords:
                    logger.info(f"  Vision found nav: {label}")
                    page.mouse.click(*coords)
                    _pause(1.0, 2.0)
                    if _linkedin_easy_apply_step_advanced(modal, step_sig):
                        continue
                    break

        logger.warning(
            f"  Easy Apply: stuck on step {step_num + 1} — "
            "no nav button found via DOM or vision"
        )
        _screenshot(page, job, "_li_stuck")
        _linkedin_discard_incomplete_application(page)
        job.update({
            "applied": False,
            "apply_notes": (
                f"Easy Apply stuck on step {step_num + 1} — no nav button; "
                "wizard closed without submitting"
            ),
            "decision": "manual_review",
            "submission_status": "incomplete",
        })
        return False

    _screenshot(page, job, "_li_incomplete")
    _linkedin_discard_incomplete_application(page)
    job.update({
        "applied": False,
        "apply_notes": "Easy Apply wizard exceeded step limit without Submit application",
        "decision": "manual_review",
        "submission_status": "incomplete",
    })
    return False


def _linkedin_apply_job(ctx, page, job: dict, qa: dict, profile: str,
                        model: str, base_url: str, dry_run: bool,
                        vision_model: str = "", validate_fit: bool = True) -> bool:
    """
    LinkedIn job apply entry: detect Easy Apply vs Apply, route accordingly.
    - easy_apply: stay on LinkedIn, run Easy Apply wizard
    - apply / company_website: open new tab or redirect, fill external ATS
    - signup_required: defer for later review without blocking the batch
    """
    if "linkedin.com/login" in page.url or "checkpoint" in page.url:
        logger.warning("  LinkedIn session expired or checkpointed - skipped unattended")
        job["apply_notes"] = "LinkedIn login or verification required - deferred"
        job["decision"] = "manual_review"
        return False

    status = _linkedin_page_status(page)
    logger.info(f"  LinkedIn page status: {status}")

    if status == "closed":
        _mark_job_closed(job, page)
        _persist_closed_job(job)
        return False

    if status == "unknown":
        reason = "LinkedIn job page not recognized — broken or non-existent URL"
        logger.warning(f"  LinkedIn: skipping — {reason}")
        job.update({
            "applied": False,
            "apply_notes": reason,
            "decision": "skip",
            "skip_reason": reason,
        })
        _persist_fit_skip(job, reason)
        _screenshot(page, job, "_unknown_page")
        return False

    # ── Already submitted: mark applied and skip ───────────────────────────────
    if status == "already_applied":
        logger.info("  LinkedIn: application already submitted — marking applied")
        _record_confirmed_submission(
            job,
            "LinkedIn",
            confirmation_url=page.url or "",
            confirmation_text="LinkedIn job page showed an already-applied status",
            note="Already submitted (detected on LinkedIn job page)",
        )
        _screenshot(page, job, "_already_applied")
        return True

    ok_fit, fit_msg = _validate_job_before_apply(
        job, page, profile, model, base_url, validate_fit
    )
    if not ok_fit:
        job.update({
            "applied": False,
            "apply_notes": f"Skipped before apply: {fit_msg}",
            "decision": "skip",
            "skip_reason": fit_msg,
        })
        logger.warning(f"  Skipped (fit): {fit_msg}")
        _persist_fit_skip(job, fit_msg)
        _screenshot(page, job, "_skipped_fit")
        return False

    # ── In-progress: resume the existing application via Continue button ───────
    if status == "in_progress":
        logger.info("  LinkedIn: resuming in-progress Easy Apply application")
        _dismiss_blocking_popups(page)
        try:
            continue_btn = page.locator(
                "button:has-text('Continue'), "
                "button[aria-label*='Continue to apply'], "
                "button[aria-label*='Resume application']"
            ).first
            if continue_btn.count() > 0 and continue_btn.is_visible(timeout=2000):
                continue_btn.click(timeout=8000)
                _pause(1.5, 2.5)
                if _linkedin_easy_apply_modal_visible(page):
                    logger.info("  Easy Apply modal reopened — resuming wizard")
                    ok = _linkedin_easy_apply_wizard(
                        page, job, qa, profile, model, base_url, dry_run, vision_model)
                    if ok:
                        if dry_run:
                            job["apply_notes"] = "Dry run - resumed Easy Apply, not submitted"
                    else:
                        if not job.get("apply_notes"):
                            job["apply_notes"] = "Resumed Easy Apply wizard incomplete — manual review"
                        job.update({"applied": False, "decision": "manual_review"})
                    return ok
        except Exception as e:
            logger.warning(f"  Could not resume in-progress application: {e}")
        # Fallback: treat it as manual review if Continue click fails
        job.update({
            "applied": False,
            "apply_notes": "In-progress Easy Apply — could not resume automatically",
            "decision": "manual_review",
        })
        _screenshot(page, job, "_in_progress_resume_failed")
        return False

    _dismiss_blocking_popups(page)
    _linkedin_wait_for_apply_button(page)
    apply_info = _linkedin_detect_apply_button(page)
    if apply_info:
        href = _extract_apply_href(apply_info)
        if href:
            direct = _linkedin_unwrap_safety_url(href)
            if direct and direct != href:
                job["job_url_direct"] = direct
        if apply_info["type"] == "easy_apply":
            job["apply_method"] = "Easy Apply"
        elif apply_info["type"] in ("apply", "company_website"):
            job["apply_method"] = "Apply"
    if not apply_info and vision_model and base_url:
        logger.info("  DOM detect failed - trying vision for Apply / Easy Apply button")
        coords = _vision_find_button(
            page, "Apply or Easy Apply button on the job posting", vision_model, base_url)
        if coords:
            page.mouse.click(*coords)
            _pause(2.0, 3.0)
            apply_info = _linkedin_detect_apply_button(page)
    if not apply_info:
        job.update({
            "applied": False,
            "apply_notes": "No Apply or Easy Apply button found — manual review",
            "decision": "manual_review",
        })
        _screenshot(page, job, "_no_apply_button")
        logger.warning("  No Apply CTA detected — saved for manual review")
        return False

    # ── External apply (company website / plain Apply button) ─────────────────
    # Handle directly here for reliable new-tab tracking; avoids _linkedin_open_apply_target
    # returning "failed" when tab takes longer to open than the legacy 12-second loop.
    if apply_info["type"] in ("company_website", "apply"):
        if job.get("_easy_apply_only_run"):
            label = apply_info.get("label") or apply_info["type"]
            logger.warning(
                f"  Easy Apply only: skipped external LinkedIn CTA '{label}'"
            )
            job.update({
                "applied": False,
                "apply_method": "Apply",
                "apply_notes": (
                    "Skipped by Easy Apply-only run: LinkedIn shows an external "
                    f"apply CTA ({label})"
                ),
                "decision": "manual_review",
            })
            return False
        logger.info(
            f"  External apply: clicking '{apply_info.get('label')}' -> opening company ATS"
        )
        pages_before = set(ctx.pages)
        new_page = None

        # Use expect_popup() — the reliable way to catch window.open() new tabs
        try:
            with page.expect_popup(timeout=15000) as popup_info:
                try:
                    apply_info["locator"].scroll_into_view_if_needed(timeout=4000)
                except Exception:
                    pass
                apply_info["locator"].click(timeout=10000)
            new_page = popup_info.value
            logger.info(f"  External ATS tab opened (popup): {new_page.url}")
        except Exception as popup_err:
            logger.debug(f"  expect_popup did not fire ({popup_err}), checking ctx.pages and same-tab redirect")
            _pause(2.5, 4.0)
            # Fallback 1: new page appeared in context
            new_tabs = [p for p in ctx.pages if p not in pages_before and not p.is_closed()]
            if new_tabs:
                new_page = new_tabs[0]
                logger.info(f"  External ATS tab detected via ctx.pages: {new_page.url}")
            else:
                # Fallback 2: same-tab navigation away from LinkedIn
                cur_url = (page.url or "").lower()
                if "linkedin.com" not in cur_url and cur_url not in ("", (job.get("job_url") or "").lower()):
                    new_page = page
                    logger.info(f"  External ATS: same-tab redirect to {page.url}")

        if new_page is None:
            if _linkedin_easy_apply_modal_visible(page):
                logger.info("  Apply opened LinkedIn Easy Apply modal after external-click fallback")
                ok = _linkedin_easy_apply_wizard(
                    page, job, qa, profile, model, base_url, dry_run, vision_model)
                if ok and dry_run:
                    job["apply_notes"] = "Dry run - Easy Apply filled, not submitted"
                return ok
            logger.warning("  External apply: no ATS page detected despite click")
            job.update({
                "applied": False,
                "apply_notes": "External apply: no ATS page opened — manual review",
                "decision": "manual_review",
            })
            try:
                if not page.is_closed():
                    _screenshot(page, job, "_ext_no_tab")
            except Exception:
                pass
            return False

        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        _dismiss_blocking_popups(new_page)

        logger.info(f"  External ATS page: {new_page.url[:90]}")
        success = _fill_external_ats_page(
            new_page, job, qa, profile, model, base_url, dry_run,
            vision_model=vision_model,
        )
        if success and not job.get("apply_notes"):
            job["apply_notes"] = (
                f"Dry run - external site filled ({new_page.url[:60]})" if dry_run
                else "Submitted via external ATS"
            )
        elif not success and not job.get("apply_notes"):
            job.update({
                "applied": False,
                "apply_notes": "External ATS page — could not complete — manual review",
                "decision": "manual_review",
            })
        # Close the external ATS tab so the LinkedIn tab stays clean
        if new_page is not page and not new_page.is_closed():
            try:
                new_page.close()
            except Exception:
                pass
        return success

    # ── Easy Apply (and any other types) ──────────────────────────────────────
    target_page, outcome = _linkedin_open_apply_target(
        page, ctx, apply_info, job, qa, vision_model, base_url)

    if outcome == "signup_required":
        return False

    if outcome == "easy_apply_modal":
        logger.info("  Continuing Easy Apply on LinkedIn")
        ok = _linkedin_easy_apply_wizard(
            target_page, job, qa, profile, model, base_url, dry_run, vision_model)
        if ok:
            if dry_run:
                job["apply_notes"] = "Dry run - Easy Apply filled, not submitted"
        else:
            if not job.get("apply_notes"):
                job["apply_notes"] = "Easy Apply wizard incomplete — manual review"
            job.update({"applied": False, "decision": "manual_review"})
        return ok

    if outcome == "external_page":
        logger.info("  Continuing application on external site (new tab or redirect)")
        ok = _fill_external_ats_page(
            target_page, job, qa, profile, model, base_url, dry_run, vision_model)
        if ok and not job.get("apply_notes"):
            job["apply_notes"] = (
                f"Dry run - external site filled ({target_page.url[:60]})" if dry_run
                else "Submitted via external site")
        elif not ok:
            if not job.get("apply_notes"):
                job["apply_notes"] = "External apply page — could not complete — manual review"
            job.update({"applied": False, "decision": "manual_review"})
        return ok

    job.update({
        "applied": False,
        "apply_notes": f"Could not start apply flow ({outcome}) — manual review",
        "decision": "manual_review",
    })
    _screenshot(page, job, "_apply_failed")
    return False


def _fill_linkedin_easy_apply(page, job: dict, qa: dict, profile: str,
                               model: str, base_url: str, dry_run: bool,
                               vision_model: str = "", ctx=None) -> bool:
    """Apply to a LinkedIn job posting (requires browser context for new tabs)."""
    if ctx is None:
        ctx = page.context
    return _linkedin_apply_job(ctx, page, job, qa, profile, model, base_url, dry_run, vision_model)


# ── Resume autofill (Workday / Greenhouse) ─────────────────────────────────────

_RESUME_AUTOFILL_RE = re.compile(
    r"fill\s+(?:with|from|using)\s+resume|autofill\s+(?:with|from)?\s*resume|"
    r"apply\s+with\s+resume|parse\s+resume|import\s+(?:from\s+)?resume|"
    r"use\s+(?:my\s+)?resume\s+to\s+fill|resume\s+autofill|auto[\s-]?fill\s+from\s+resume|"
    r"pre[\s-]?fill\s+(?:with|from)\s+resume|upload\s+resume\s+to\s+fill|"
    r"fill\s+application\s+(?:with|using)\s+resume|extract\s+from\s+resume",
    re.I,
)


def _upload_resume_inputs(page, qa: dict, label: str = "Resume") -> bool:
    """Attach resume to any file input on the page (visible or hidden)."""
    resume_path = qa.get("resume_path") or ""
    if not resume_path or not Path(resume_path).exists():
        logger.warning(f"  {label} upload skipped — file not found: {resume_path}")
        return False
    uploaded = False
    for fi in page.locator("input[type='file']").all():
        try:
            # Skip file inputs that are clearly for cover letters — don't upload resume to them.
            fi_id = fi.get_attribute("id") or ""
            fi_name = fi.get_attribute("name") or ""
            fi_aria = fi.get_attribute("aria-label") or ""
            # Also check the associated <label> text
            label_text = ""
            if fi_id:
                lbl = page.locator(f"label[for='{fi_id}']")
                if lbl.count() > 0:
                    try:
                        label_text = lbl.first.inner_text(timeout=300)
                    except Exception:
                        pass
            combined = (fi_id + " " + fi_name + " " + fi_aria + " " + label_text).lower()
            if "cover" in combined and "letter" in combined:
                logger.info(f"  Skipping cover letter file input (not uploading resume there)")
                continue
            fi.set_input_files(resume_path)
            logger.info(f"  {label} uploaded: {Path(resume_path).name}")
            uploaded = True
            _pause(0.8, 1.5)
        except Exception:
            continue
    if not uploaded:
        try:
            page.locator(
                "label:has-text('Resume'), label:has-text('CV'), "
                "label:has-text('Upload')"
            ).first.click(timeout=3000)
            _pause(0.3, 0.6)
            for fi in page.locator("input[type='file']").all():
                fi_id = fi.get_attribute("id") or ""
                fi_name = fi.get_attribute("name") or ""
                fi_aria = fi.get_attribute("aria-label") or ""
                combined = f"{fi_id} {fi_name} {fi_aria}".lower()
                if "cover" in combined and "letter" in combined:
                    continue
                fi.set_input_files(resume_path)
                logger.info(f"  {label} uploaded via label click: {Path(resume_path).name}")
                uploaded = True
                _pause(0.8, 1.5)
                break
        except Exception:
            pass
    return uploaded


def _write_text_pdf(path: str, text: str) -> None:
    """Write a small dependency-free PDF suitable for ATS file uploads."""
    import textwrap

    lines = []
    for paragraph in (text or "").splitlines():
        lines.extend(textwrap.wrap(paragraph, width=92) or [""])
    pages = [lines[i:i + 48] for i in range(0, len(lines), 48)] or [[""]]
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_id = add_object(b"")
    pages_id = add_object(b"")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page_lines in pages:
        commands = [b"BT", b"/F1 10 Tf", b"54 738 Td", b"14 TL"]
        for line in page_lines:
            safe = line.encode("latin-1", "replace").replace(b"\\", b"\\\\")
            safe = safe.replace(b"(", b"\\(").replace(b")", b"\\)")
            commands.extend((b"(" + safe + b") Tj", b"T*"))
        commands.append(b"ET")
        stream = b"\n".join(commands)
        content_id = add_object(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
        page_id = add_object(
            b"<< /Type /Page /Parent " + str(pages_id).encode("ascii")
            + b" 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 "
            + str(font_id).encode("ascii") + b" 0 R >> >> /Contents "
            + str(content_id).encode("ascii") + b" 0 R >>"
        )
        page_ids.append(page_id)
    objects[catalog_id - 1] = (
        b"<< /Type /Catalog /Pages " + str(pages_id).encode("ascii") + b" 0 R >>"
    )
    objects[pages_id - 1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
        + b"] /Count " + str(len(page_ids)).encode("ascii") + b" >>"
    )

    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, payload in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{object_id} 0 obj\n".encode("ascii"))
        data.extend(payload)
        data.extend(b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        b"trailer\n<< /Size " + str(len(objects) + 1).encode("ascii")
        + b" /Root " + str(catalog_id).encode("ascii")
        + b" 0 R >>\nstartxref\n" + str(xref).encode("ascii") + b"\n%%EOF\n"
    )
    Path(path).write_bytes(bytes(data))


def _generate_cover_letter(job: dict, qa: dict, model: str, base_url: str) -> str:
    """Generate a short cover letter using the LLM if no file is provided."""
    name = f"{qa.get('first_name', '')} {qa.get('last_name', '')}".strip()
    role = job.get("title", "this role")
    company = job.get("company", "your company")
    profile = _session_profile()
    prompt = (
        f"Write a concise, professional cover letter (3 short paragraphs, under 200 words) "
        f"for {name} applying to {role} at {company}.\n\n"
        f"Candidate profile:\n{profile}\n\n"
        f"Job description excerpt:\n{(job.get('description') or '')[:600]}\n\n"
        f"Output only the cover letter text, no subject line, no address headers."
    )
    try:
        resp = _ollama_post(base_url, model, prompt, temperature=0.4)
        resp.raise_for_status()
        generated = (resp.json().get("response") or "").strip()
        if generated:
            return generated
    except Exception:
        pass
    return (
        f"Dear Hiring Manager,\n\n"
        f"I am writing to express my interest in the {role} position at {company}. "
        f"With my background in quantitative finance and technology, I am confident I "
        f"would be a strong contributor to your team.\n\n"
        f"I look forward to discussing this opportunity further.\n\n"
        f"Best regards,\n{name}"
    )


def _upload_cover_letter_inputs(page, job: dict, qa: dict,
                                 model: str = "", base_url: str = "") -> bool:
    """
    Upload a cover letter file to any cover-letter file input on the page.
    If no cover_letter_path is set in qa, generate a small PDF attachment.
    """
    _CL_LABEL_RE = re.compile(
        r"cover[\s_-]?letter|motivation[\s_-]?letter|lettre[\s_-]?de[\s_-]?motivation",
        re.I,
    )

    # Find cover-letter file inputs
    cl_inputs = []
    for fi in page.locator("input[type='file']").all():
        try:
            fi_id   = fi.get_attribute("id") or ""
            fi_name = fi.get_attribute("name") or ""
            fi_aria = fi.get_attribute("aria-label") or ""
            label_text = ""
            if fi_id:
                lbl = page.locator(f"label[for='{fi_id}']")
                if lbl.count():
                    try:
                        label_text = lbl.first.inner_text(timeout=300)
                    except Exception:
                        pass
            combined = (fi_id + " " + fi_name + " " + fi_aria + " " + label_text)
            if _CL_LABEL_RE.search(combined):
                cl_inputs.append(fi)
        except Exception:
            continue

    # Also look for cover-letter label buttons that trigger hidden inputs
    if not cl_inputs:
        for lbl in page.locator("label").all():
            try:
                txt = lbl.inner_text(timeout=300) or ""
                if _CL_LABEL_RE.search(txt):
                    for_id = lbl.get_attribute("for") or ""
                    if for_id:
                        fi = page.locator(f"input[type='file']#{for_id}")
                        if fi.count():
                            cl_inputs.append(fi.first)
            except Exception:
                continue

    if not cl_inputs:
        return False  # No cover letter input found on this page

    # Determine the file to upload
    cl_path = qa.get("cover_letter_path") or ""
    if not cl_path or not Path(cl_path).exists():
        # Generate one and save to a portable PDF accepted by common ATS portals.
        logger.info("  Cover letter: no file set — generating with LLM")
        cl_text = _generate_cover_letter(job, qa, model or "llama3", base_url or "http://localhost:11434")
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            suffix=".pdf", prefix="cover_letter_", delete=False
        )
        tmp.close()
        cl_path = tmp.name
        _write_text_pdf(cl_path, cl_text)
        logger.info(f"  Cover letter generated ({len(cl_text)} chars): {cl_path}")

    uploaded = False
    for fi in cl_inputs:
        try:
            fi.set_input_files(cl_path)
            logger.info(f"  Cover letter uploaded: {Path(cl_path).name}")
            uploaded = True
            _pause(0.5, 1.0)
        except Exception as e:
            logger.debug(f"  Cover letter upload failed: {e}")
    return uploaded


def _text_matches_resume_autofill(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 120:
        return False
    if _RESUME_AUTOFILL_RE.search(t):
        return True
    low = t.lower()
    return ("resume" in low and "autofill" in low) or (
        "resume" in low and "fill" in low and "with" in low
    )


def _page_has_resume_autofill_option(page) -> bool:
    """True if Workday/Greenhouse shows an autofill-with-resume CTA (do not click Apply Manually)."""
    for sel in (
        "button:has-text('Autofill with Resume')",
        "button:has-text('Autofill with resume')",
        "button:has-text('Fill with Resume')",
        "button:has-text('Fill with resume')",
        "[data-automation-id*='fillWithResume']",
        "[data-automation-id*='resumeFillButton']",
        "[data-automation-id*='autofillResume']",
    ):
        try:
            if page.locator(sel).first.is_visible(timeout=400):
                return True
        except Exception:
            continue
    for el in page.locator(
        "button:visible, a:visible, [role='button']:visible"
    ).all()[:40]:
        try:
            if not el.is_visible(timeout=250):
                continue
            label = (
                (el.inner_text(timeout=300) or "")
                + " "
                + (el.get_attribute("aria-label") or "")
            ).strip()
            if _text_matches_resume_autofill(label):
                return True
        except Exception:
            continue
    return False


def _prepare_application_resume(page, qa: dict, platform: str = "") -> bool:
    """
    Attach resume and trigger ATS autofill per platform rules.
    Workday: Autofill with resume. Greenhouse: Autofill with Resume.
    Others: upload file inputs first, then click any autofill CTA.
    """
    _ensure_qa_contact(qa, force_reload=True)
    plat = (platform or "").lower()
    if plat == "workday":
        return _try_resume_autofill(page, qa)
    if plat == "greenhouse":
        if _try_resume_autofill(page, qa):
            return True
        return _upload_resume_inputs(page, qa, label="Resume (Greenhouse)")
    uploaded = _upload_resume_inputs(page, qa, label="Resume (upload)")
    if _try_resume_autofill(page, qa):
        return True
    return uploaded


def _try_resume_autofill(page, qa: dict) -> bool:
    """
    Click 'Fill with resume' / 'Autofill with resume' (Workday, Greenhouse, etc.)
    then attach the candidate resume from qa['resume_path'].
    Handles both hidden-input uploads and native OS file-chooser dialogs.
    """
    resume_path = qa.get("resume_path") or ""
    if not resume_path or not Path(resume_path).exists():
        return False

    _AUTOFILL_SELECTORS = (
        "button:has-text('Autofill with Resume')",
        "button:has-text('Autofill with resume')",
        "button:has-text('Autofill with my resume')",
        "button:has-text('Fill with Resume')",
        "button:has-text('Fill with resume')",
        "a:has-text('Autofill with Resume')",
        "a:has-text('Autofill with resume')",
        "button:has-text('Apply with Resume')",
        "button:has-text('Use resume to fill')",
        "[data-automation-id*='fillWithResume']",
        "[data-automation-id*='resumeFillButton']",
        "[data-automation-id*='autofillResume']",
        "button[aria-label*='Fill with resume' i]",
        "button[aria-label*='Autofill with resume' i]",
    )

    clicked = False
    el_to_click = None

    # Known platform selectors (fast path)
    for sel in _AUTOFILL_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                el_to_click = el
                break
        except Exception:
            continue

    # Fuzzy text scan fallback
    if el_to_click is None:
        for el in page.locator(
            "button:visible, a:visible, [role='button']:visible"
        ).all()[:50]:
            try:
                if not el.is_visible(timeout=300):
                    continue
                label = (
                    (el.inner_text(timeout=400) or "")
                    + " "
                    + (el.get_attribute("aria-label") or "")
                    + " "
                    + (el.get_attribute("title") or "")
                ).strip()
                if _text_matches_resume_autofill(label):
                    el_to_click = el
                    break
            except Exception:
                continue

    if el_to_click is not None:
        try:
            btn_label = (
                (el_to_click.inner_text(timeout=400) or "")
                + " "
                + (el_to_click.get_attribute("aria-label") or "")
            ).strip()
            # Intercept native OS file-chooser that may open on click
            try:
                with page.expect_file_chooser(timeout=4000) as fc_info:
                    el_to_click.click(timeout=8000)
                fc_info.value.set_files(resume_path)
                clicked = True
                logger.info(
                    f"  Resume autofill CTA clicked (file-chooser): '{btn_label[:70]}'"
                )
                _pause(1.5, 2.5)
            except Exception:
                # No file chooser appeared — plain click (hidden input path)
                el_to_click.click(timeout=8000)
                clicked = True
                logger.info(
                    f"  Resume autofill CTA clicked: '{btn_label[:70]}'"
                )
                _pause(1.5, 2.5)
        except Exception:
            pass

    # Upload resume via hidden file input (after CTA click or when no CTA present)
    uploaded = _upload_resume_inputs(page, qa, label="Resume (autofill)")
    if clicked or uploaded:
        if clicked:
            logger.info("  Waiting for resume parse / autofill...")
            _pause(2.5, 4.0)  # resume parse needs time
        return True
    return False


# ── Workday ────────────────────────────────────────────────────────────────────

def _workday_already_on_start_page(page) -> bool:
    """
    Returns True when the browser is already on Workday's "Start Your Application"
    landing screen — i.e. the Apply CTA has been bypassed and Workday is showing
    the autofill / manual / last-application choice buttons.
    No Apply button click is needed in this case.
    """
    # Definitive signals: the three-choice start screen
    start_signals = [
        "button:has-text('Autofill with Resume')",
        "button:has-text('Autofill with resume')",
        "button:has-text('Apply Manually')",
        "button:has-text('Use My Last Application')",
        "[data-automation-id*='fillWithResume']",
        "[data-automation-id*='startApplication']",
    ]
    for sel in start_signals:
        try:
            if page.locator(sel).first.is_visible(timeout=400):
                return True
        except Exception:
            continue
    # Also catch the page heading "Start Your Application"
    try:
        heading = page.locator("h2, h3, h1").first.inner_text(timeout=500)
        if "start your application" in heading.lower():
            return True
    except Exception:
        pass
    return False


def _workday_signin_gate_visible(page) -> bool:
    """True when Workday shows Sign In / Create Account (often after Apply)."""
    from agents.account_signup import workday_auth_wall_visible
    return workday_auth_wall_visible(page)


def _workday_fill_contact(page, qa: dict):
    _try_fill(page, ["input[data-automation-id='legalNameSection_firstName']",
                     "input[aria-label*='First Name' i]"], qa["first_name"])
    _try_fill(page, ["input[data-automation-id='legalNameSection_lastName']",
                     "input[aria-label*='Last Name' i]"], qa["last_name"])
    _try_fill(page, ["input[data-automation-id='email']", "input[type='email']",
                     "input[aria-label*='Email' i]"], qa["email"])
    _try_fill(page, ["input[data-automation-id='phone-number']",
                     "input[aria-label*='Phone' i]"], qa["phone"])
    # Phone device type — Workday ARIA combobox (search box → pick "Mobile")
    for phone_type_sel in [
        "button[data-automation-id='phone-device-type']",
        "[data-automation-id='phoneDeviceType'] button",
        "button[aria-label*='Phone Device' i]",
        "button[aria-label*='Phone Type' i]",
    ]:
        try:
            btn = page.locator(phone_type_sel).first
            if btn.is_visible(timeout=600):
                btn.click()
                _pause(0.4, 0.8)
                page.keyboard.type("Mobile")
                _pause(0.5, 0.9)
                page.locator(
                    "li:has-text('Mobile'), [data-automation-id*='Mobile']"
                ).first.click()
                _pause(0.3, 0.6)
                break
        except Exception:
            pass
    # Fallback: if it's a native <select> for phone type
    for sel in ["select[data-automation-id='phone-device-type']",
                "select[aria-label*='Phone Type' i]",
                "select[aria-label*='Phone Device' i]"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                el.select_option(label="Mobile")
                _pause(0.3, 0.6)
                break
        except Exception:
            pass
    _try_fill(page, ["input[data-automation-id='addressSection_city']",
                     "input[aria-label*='City' i]"], "Abu Dhabi")
    # Country typeahead
    for sel in ["button[data-automation-id='countryDropdown']",
                "button[aria-label*='Country' i]"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                el.click()
                _pause(0.5, 1.0)
                page.keyboard.type("United Arab")
                _pause(0.7, 1.2)
                page.locator(
                    "li:has-text('United Arab Emirates'), "
                    "[data-automation-id*='United Arab']").first.click()
                _pause(0.4, 0.8)
                break
        except Exception:
            pass

def _workday_fill_experience(page, qa: dict):
    _try_fill(page, ["input[data-automation-id='school']",
                     "input[aria-label*='School' i]",
                     "input[aria-label*='Institution' i]"], qa["university"])
    _try_fill(page, ["input[data-automation-id='field']",
                     "input[aria-label*='Field of Study' i]",
                     "input[aria-label*='Major' i]"], qa["degree_field"])
    _try_fill(page, ["input[data-automation-id='gpa']",
                     "input[aria-label*='GPA' i]"], "3.4")
    # Degree level
    for sel in ["select[data-automation-id='degree']", "button[aria-label*='Degree' i]"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    el.select_option(label="Bachelor of Arts")
                else:
                    el.click()
                    _pause(0.4, 0.8)
                    page.locator(
                        "li:has-text('Bachelor'), [data-automation-id*='Bachelor']"
                    ).first.click()
                _pause(0.3, 0.6)
                break
        except Exception:
            pass

def _workday_accept_consent_checkboxes(page) -> int:
    """Check any required consent/acknowledgment checkboxes on Workday forms."""
    checked = 0
    consent_patterns = re.compile(
        r"candid|acknowledge|i have read|agree|consent|accept|certify|confirm|"
        r"terms|privacy|gdpr|policy|disclosure|authorize|authorise",
        re.I,
    )
    # Workday checkboxes via data-automation-id
    for cb in page.locator(
        "input[type='checkbox'], [role='checkbox'], "
        "[data-automation-id*='checkbox'], [data-automation-id*='Checkbox']"
    ).all():
        try:
            if not cb.is_visible(timeout=300):
                continue
            # Already checked?
            checked_state = (
                cb.is_checked() if cb.evaluate("el => el.tagName === 'INPUT'")
                else cb.get_attribute("aria-checked") == "true"
            )
            if checked_state:
                continue
            # Get label text from parent context
            label_text = ""
            try:
                label_text = cb.get_attribute("aria-label") or ""
            except Exception:
                pass
            if not label_text:
                try:
                    # Walk up to find associated label text
                    label_text = page.evaluate("""el => {
                        let p = el.parentElement;
                        for (let i=0; i<5; i++) {
                            if (!p) break;
                            const t = p.innerText || '';
                            if (t.trim().length > 3) return t.trim().slice(0,200);
                            p = p.parentElement;
                        }
                        return '';
                    }""", cb)
                except Exception:
                    pass
            if _is_sensitive_eeoc_label(label_text):
                continue
            if consent_patterns.search(label_text):
                cb.click()
                checked += 1
                _pause(0.2, 0.4)
                logger.info(f"  Workday consent checkbox checked: '{label_text[:60]}'")
        except Exception:
            continue
    return checked


def _workday_answer_previous_worker(page) -> bool:
    """Specifically target Workday's candidateIsPreviousWorker radio — always answer No."""
    for sel in [
        "[data-automation-id='previousWorker--candidateIsPreviousWorker']",
        "[data-automation-id*='candidateIsPreviousWorker']",
        "[data-automation-id*='previousWorker']",
    ]:
        try:
            container = page.locator(sel).first
            if container.is_visible(timeout=500):
                # Try clicking a 'No' label inside the container
                no_lbl = container.locator("label:has-text('No')").first
                if no_lbl.is_visible(timeout=500):
                    no_lbl.click()
                    _pause(0.3, 0.6)
                    return True
        except Exception:
            pass
    # Fallback: find any fieldset/div whose label mentions previous worker
    for block in page.locator("fieldset, [role='group']").all():
        try:
            heading = block.locator("legend, label, span").first.inner_text(timeout=300).lower()
            if any(k in heading for k in (
                "previous worker", "previously work", "worked here",
                "ever worked for", "former employee",
            )):
                no_lbl = block.locator("label:has-text('No')").first
                if no_lbl.is_visible(timeout=400):
                    no_lbl.click()
                    _pause(0.3, 0.6)
                    return True
        except Exception:
            continue
    return False


def _workday_fill_questions(page, job: dict, qa: dict, profile: str, model: str, base_url: str):
    # Always answer the previous-worker gate first (Autodesk and similar)
    _workday_answer_previous_worker(page)
    # Accept any consent/acknowledgment checkboxes (Candid, privacy, terms, etc.)
    _workday_accept_consent_checkboxes(page)
    yn_map = {
        # Work authorisation
        "authorized": "Yes", "legally authorized": "Yes", "right to work": "Yes",
        "eligible to work": "Yes", "work in the": "Yes", "permitted to work": "Yes",
        # Sponsorship
        "visa sponsor": "No", "require sponsorship": "No", "need sponsorship": "No",
        "require a visa": "No", "work permit": "No", "immigration sponsorship": "No",
        # Relocation / remote
        "willing to relocate": "Yes", "open to relocation": "Yes", "relocate": "Yes",
        "remote": "Yes", "work remotely": "Yes",
        # Background / agreements
        "background check": "Yes", "agree to": "Yes", "acknowledge": "Yes",
        "confirm that": "Yes", "certify": "Yes",
        # UAE national / Emirati
        "uae national": "Yes", "emirati": "Yes", "gcc national": "Yes",
        "are you a uae": "Yes", "are you emirati": "Yes",
        # Availability
        "immediately": "Yes", "available immediately": "Yes",
        # Previously worked here (previous employee / former worker)
        "previous worker": "No", "previously work": "No", "worked here before": "No",
        "former employee": "No", "worked for us": "No", "worked for this": "No",
        "ever worked for": "No", "previously employed": "No",
        "candidateispreviousworker": "No",
        # Disability / veteran (US forms that reach UAE roles — decline to answer)
        "disability": "Decline to self-identify", "veteran": "I am not a protected veteran",
        "protected veteran": "I am not a protected veteran",
    }
    for block in page.locator(
            "[data-automation-id*='Question'], fieldset, "
            "[class*='question'], [class*='Question']").all():
        try:
            label_el = block.locator("label,legend,span").first
            if label_el.count() == 0:
                continue
            label = label_el.inner_text().strip()
            label_lower = label.lower()
            # Skip already-answered blocks
            if block.locator("input:checked, [aria-checked='true']").count() > 0:
                continue
            if _is_sensitive_eeoc_label(label):
                options = []
                for lbl in block.locator("label").all():
                    try:
                        text = lbl.inner_text(timeout=400).strip()
                        if text:
                            options.append(text)
                    except Exception:
                        pass
                choice = _eeoc_decline_choice(label, options)
                if choice:
                    try:
                        block.locator(f"label:has-text('{choice[:40]}')").first.click(timeout=1500)
                        logger.info(f"  Workday Q (declined): '{label[:50]}' -> '{choice[:40]}'")
                        _pause(0.3, 0.7)
                    except Exception:
                        pass
                continue
            matched = False
            for key, answer in yn_map.items():
                if key in label_lower:
                    try:
                        block.locator(f"label:has-text('{answer}')").first.click(timeout=1500)
                        logger.info(f"  Workday Q (keyword): '{label[:50]}' -> '{answer}'")
                        _pause(0.3, 0.7)
                        matched = True
                    except Exception:
                        pass
                    break
            if not matched:
                # Collect available radio/checkbox options and let LLM pick
                options = []
                for lbl in block.locator("label").all():
                    try:
                        t = lbl.inner_text(timeout=400).strip()
                        if t:
                            options.append(t)
                    except Exception:
                        pass
                if options and label:
                    choice = _llm_pick_option(label, options, job, qa, profile, model, base_url)
                    if choice:
                        try:
                            block.locator(f"label:has-text('{choice[:40]}')").first.click(timeout=1500)
                            logger.info(f"  Workday Q (LLM): '{label[:50]}' -> '{choice[:40]}'")
                            _pause(0.3, 0.7)
                        except Exception:
                            pass
        except Exception:
            pass
    for ta in page.locator("textarea").all():
        try:
            if not ta.is_visible(timeout=400):
                continue
            ta_id = ta.get_attribute("id") or ""
            question = ""
            if ta_id:
                lbl = page.locator(f"label[for='{ta_id}']")
                if lbl.count() > 0:
                    question = lbl.first.inner_text().strip()
            if not question:
                question = ta.get_attribute("aria-label") or ta.get_attribute("placeholder") or ""
            if not question:
                continue
            ta.fill(_llm_answer(question, job.get("company",""), job.get("title",""),
                                  job.get("positioning_angle","investments"),
                                  profile, model, base_url, qa=qa))
            _pause(0.6, 1.5)
        except Exception:
            pass

def _fill_workday(page, job: dict, qa: dict, profile: str,
                   model: str, base_url: str, dry_run: bool) -> bool:
    """
    Workday multi-step handler.
    Covers *.myworkdayjobs.com — G42, Mubadala, ADNOC, McKinsey UAE, most large employers.
    """
    logger.info("  Detected: Workday")
    _dismiss_blocking_popups(page)

    # Direct Workday apply URLs can open on auth before an Apply CTA exists.
    if _workday_signin_gate_visible(page):
        from agents.account_signup import clear_auth_wall
        qa["_current_job"] = job  # hint for email-verify pending queue
        if not clear_auth_wall(
            page, job, qa, platform="workday", screenshot_fn=_screenshot
        ):
            if not job.get("apply_notes"):
                job["apply_notes"] = (
                    "Workday sign-in/account creation blocked - deferred"
                )
            job["decision"] = "manual_review"
            _screenshot(page, job, "_workday_signin_required")
            return False
        _pause(0.8, 1.5)

    # Detect if we are already on the Workday "Start Your Application" page
    # (Autofill with Resume / Apply Manually / Use My Last Application visible).
    # In this case the Apply CTA has already been bypassed — skip straight to autofill.
    _on_start_page = _workday_already_on_start_page(page)

    apply_clicked = _on_start_page  # treat as already clicked
    if _on_start_page:
        logger.info("  Workday: already on Start Your Application page — skipping Apply CTA")

    if not apply_clicked:
        candidates = _scan_apply_cta_buttons(page)
        if candidates:
            try:
                candidates[0]["locator"].click(timeout=10000)
                apply_clicked = True
                logger.info(f"  Workday Apply: '{candidates[0]['label'][:50]}'")
                _pause(1.0, 2.0)
            except Exception:
                pass
    if not apply_clicked:
        apply_btn = page.locator(
            "a[data-automation-id='applyButton'], a[href*='apply']")
        try:
            apply_btn.first.wait_for(state="visible", timeout=8000)
            text = apply_btn.first.inner_text(timeout=1500) or ""
            aria = apply_btn.first.get_attribute("aria-label") or ""
            if _apply_cta_score(text, aria) >= 0:
                apply_btn.first.click()
                apply_clicked = True
                _pause(1.0, 2.0)
        except PWTimeout:
            pass
    if not apply_clicked:
        logger.warning("  Workday Apply button not found")
        return False

    # Prefer resume autofill before any other post-Apply choice (Workday shows this
    # on the same screen as "Apply Manually" — that is NOT a guest-login button).
    if _try_resume_autofill(page, qa):
        logger.info("  Workday: using Autofill with resume path")
        # Wait for Workday to re-render the form DOM after resume parse
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            _pause(2.0, 3.0)
    else:
        # Account-less entry only (never treat "Apply Manually" as guest)
        try:
            guest = page.locator(
                "button:has-text('Continue as Guest'), "
                "a:has-text('Continue as Guest'), "
                "button:has-text('Apply as Guest')").first
            if guest.is_visible(timeout=2500):
                guest.click()
                _pause(0.8, 1.5)
        except Exception:
            pass
        # Manual form only when autofill CTA is not on the page
        if not _page_has_resume_autofill_option(page):
            try:
                manual = page.locator("button:has-text('Apply Manually')").first
                if manual.is_visible(timeout=2500):
                    manual.click()
                    logger.info("  Workday: Apply Manually (no autofill CTA on page)")
                    _pause(0.8, 1.5)
            except Exception:
                pass

    # Email gate (may appear after resume choice or on later step)
    try:
        email_input = page.locator(
            "input[type='email'], input[data-automation-id='email']").first
        if email_input.is_visible(timeout=2500):
            email_input.fill(qa["email"])
            _pause(0.4, 0.8)
            page.locator("button:has-text('Next'), button:has-text('Continue')").first.click()
            _pause(1.0, 2.0)
            _try_resume_autofill(page, qa)
    except Exception:
        pass

    # Sign In / Create Account wall (common after Apply) — try profile-based signup first.
    _pause(0.8, 1.5)
    if _workday_signin_gate_visible(page):
        from agents.account_signup import clear_auth_wall
        if not clear_auth_wall(
            page, job, qa, platform="workday", screenshot_fn=_screenshot
        ):
            if not job.get("apply_notes"):
                job["apply_notes"] = (
                    "Workday sign-in/account creation blocked - deferred"
                )
            job["decision"] = "manual_review"
            _screenshot(page, job, "_workday_signin_required")
            return False
        _pause(0.8, 1.5)

    # Walk sections (up to 15 steps to handle multi-page Workday wizards)
    _prev_heading = None
    _stuck_count = 0
    for section_num in range(15):
        _pause(0.8, 1.5)
        # CTA may appear on resume / my-information steps mid-flow
        _try_resume_autofill(page, qa)
        heading = ""
        try:
            heading = page.locator(
                "h2, h3, [data-automation-id='formHeader'], "
                "[class*='sectionTitle']").first.inner_text()
        except Exception:
            pass
        logger.info(f"  Section {section_num+1}: {heading.strip()[:50] or '(unknown)'}")
        h = heading.lower()

        # Stuck-section guard: if heading hasn't changed after a Next click,
        # there's a validation error — attempt to fix then move on.
        if heading and heading == _prev_heading:
            _stuck_count += 1
            logger.warning(f"  Workday: section heading unchanged ({_stuck_count}x) — checking validation errors")
            _fix_step_validation_errors(page, job, qa, profile, model, base_url)
            _fill_required_fields_pass(page, job, qa, profile, model, base_url)
            if _stuck_count >= 3:
                logger.warning("  Workday: stuck on same section after 3 attempts — deferring")
                _mark_submission_unconfirmed(page, job, "workday")
                job["apply_notes"] = f"Workday stuck on section '{heading.strip()[:40]}' — deferred"
                return False
        else:
            _stuck_count = 0
        _prev_heading = heading

        # ── Review / Summary page — look for the final Submit button ──────────
        # Workday tenants use many different labels for the final review step
        is_review_page = any(k in h for k in [
            "review", "summary", "confirm", "submit", "application summary",
            "review & submit", "review and submit", "final step",
            "voluntary disclosures",   # some tenants end on EEOC page
        ])
        if is_review_page:
            logger.info("  Workday: on Review page — looking for final Submit")
            _screenshot(page, job, "_workday_review")
            if dry_run:
                return True
            # Try the automation-id submit button first, then text-based fallbacks
            for submit_sel in [
                "button[data-automation-id='bottom-navigation-next-button']",
                "button[data-automation-id='submitButton']",
                "button:has-text('Submit')",
                "button:has-text('Submit Application')",
                "input[type='submit']",
            ]:
                try:
                    btn = page.locator(submit_sel).first
                    if btn.is_visible(timeout=1500):
                        btn_text = btn.inner_text(timeout=800).lower() if hasattr(btn, "inner_text") else ""
                        # On the automation-id next button, only click if text says submit
                        if submit_sel == "button[data-automation-id='bottom-navigation-next-button']" and "submit" not in btn_text:
                            continue
                        btn.click()
                        _pause(3.0, 5.0)
                        confirmed_page = _wait_for_submission_confirmation(
                            page, job, "workday"
                        )
                        if confirmed_page:
                            logger.info("  Workday application submitted (Review page)")
                            _screenshot(confirmed_page, job, "_submitted")
                            return True
                        logger.warning("  Workday submit clicked on Review page but no confirmation")
                        _mark_submission_unconfirmed(page, job, "workday")
                        _screenshot(page, job, "_submit_unconfirmed")
                        return False
                except Exception:
                    pass
            # Could not find submit on review page
            logger.warning("  Workday: could not find Submit on Review page")
            _mark_submission_unconfirmed(page, job, "workday")
            return False

        if any(k in h for k in ["my information", "contact", "personal"]):
            _workday_fill_contact(page, qa)
        elif any(k in h for k in ["my experience", "work history", "education"]):
            _workday_fill_experience(page, qa)
        elif any(k in h for k in ["question", "screening", "additional"]):
            _workday_fill_questions(page, job, qa, profile, model, base_url)
        else:
            _workday_fill_contact(page, qa)
            _fill_ai_driven_page(page, job, qa, profile, model, base_url)

        # Consent/acknowledgment checkboxes on any section (Candid, privacy, etc.)
        _workday_accept_consent_checkboxes(page)
        # Previous-worker radio on any section
        _workday_answer_previous_worker(page)

        # Resume upload if autofill CTA was not used this section
        _upload_resume_inputs(page, qa, label="Resume (Workday)")
        _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)

        _fill_required_fields_pass(page, job, qa, profile, model, base_url)
        _fix_step_validation_errors(page, job, qa, profile, model, base_url)

        # Navigation
        try:
            next_btn = page.locator(
                "button[data-automation-id='bottom-navigation-next-button']")
            if next_btn.count() > 0 and next_btn.first.is_visible(timeout=800):
                btn_text = next_btn.first.inner_text().lower()
                if "submit" in btn_text:
                    _screenshot(page, job, "_workday_review")
                    if not dry_run:
                        next_btn.first.click()
                        _pause(2.0, 4.0)
                        confirmed_page = _wait_for_submission_confirmation(
                            page, job, "workday"
                        )
                        if confirmed_page:
                            logger.info("  Workday application submitted")
                            _screenshot(confirmed_page, job, "_submitted")
                            return True
                        logger.warning("  Workday submit clicked but no confirmation detected")
                        _mark_submission_unconfirmed(page, job, "workday")
                        _screenshot(page, job, "_submit_unconfirmed")
                        return False
                    return True
                else:
                    next_btn.first.click()
                    _pause(1.0, 2.0)
                    continue
        except Exception:
            pass
        _WD_NEXT_SELS = [
            "button:has-text('Next')",
            "button:has-text('Save and Continue')",
            "button:has-text('Continue')",
            "button:has-text('Save & Continue')",
            "button:has-text('Proceed')",
            "[data-automation-id='wd-CommandButton_uic_nextButton']",
            "[data-automation-id='wd-CommandButton_uic_okButton']",
        ]
        for sel in _WD_NEXT_SELS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=600):
                    btn_text = ""
                    try:
                        btn_text = btn.inner_text(timeout=400).lower()
                    except Exception:
                        pass
                    if any(w in btn_text for w in ("submit",)):
                        continue  # Let the review-page handler deal with Submit
                    btn.click()
                    _pause(1.0, 2.0)
                    break
            except Exception:
                pass
        else:
            logger.warning(f"  No Next on Workday section {section_num+1}")
            break

    _screenshot(page, job, "_workday_incomplete")
    if _workday_signin_gate_visible(page):
        from agents.account_signup import clear_auth_wall
        if not clear_auth_wall(
            page, job, qa, platform="workday", screenshot_fn=_screenshot
        ):
            job["apply_notes"] = (
                "Workday sign-in/account creation blocked - deferred"
            )
    elif not job.get("apply_notes"):
        job["apply_notes"] = "Workday application incomplete - deferred"
    job["decision"] = "manual_review"
    return False


# ── Workable ───────────────────────────────────────────────────────────────────

def _fill_workable(page, job: dict, qa: dict, profile: str,
                    model: str, base_url: str) -> bool:
    logger.info("  Detected: Workable")
    _prepare_application_resume(page, qa, platform="workable")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)
    _try_fill(page, ["input[name='firstname']", "input[id*='firstname' i]",
                     "input[placeholder*='First' i]"], qa["first_name"])
    _try_fill(page, ["input[name='lastname']",  "input[id*='lastname' i]",
                     "input[placeholder*='Last' i]"],  qa["last_name"])
    _try_fill(page, ["input[name='email']",   "input[type='email']"],  qa["email"])
    _try_fill(page, ["input[name='phone']",   "input[type='tel']"],    qa["phone"])
    _try_fill(page, ["input[name='linkedin']", "input[placeholder*='LinkedIn' i]"], qa["linkedin"])
    _try_fill(page, ["input[name='website']"], qa["linkedin"])
    # Cover letter
    try:
        ta = page.locator("textarea[name='cover_letter'], textarea[placeholder*='cover' i]").first
        if ta.is_visible(timeout=800):
            ta.fill(_llm_answer(
                "Write a brief cover letter (2-3 sentences) for this role.",
                job.get("company",""), job.get("title",""),
                job.get("positioning_angle","investments"), profile, model, base_url, qa=qa))
            _pause(0.5, 1.2)
    except Exception:
        pass
    # Custom questions
    for q_el in page.locator("[class*='question'], [class*='field']").all():
        try:
            label_el = q_el.locator("label").first
            if label_el.count() == 0:
                continue
            question = label_el.inner_text().strip()
            if not question:
                continue
            input_el = q_el.locator("input[type='text'], textarea").first
            if input_el.count() == 0 or not input_el.is_visible(timeout=400):
                continue
            direct = _qa_value_for_label(question, qa)
            if direct is not None:
                input_el.fill(direct)
            else:
                input_el.fill(_llm_answer(question, job.get("company",""), job.get("title",""),
                                          job.get("positioning_angle","investments"),
                                          profile, model, base_url, qa=qa))
            _pause(0.4, 1.0)
        except Exception:
            pass
    return True


# ── Greenhouse ────────────────────────────────────────────────────────────────

def _fill_greenhouse(page, job: dict, qa: dict, profile: str, model: str, base_url: str,
                     dry_run: bool = True) -> bool:
    logger.info("  Detected: Greenhouse")
    _ensure_qa_contact(qa, force_reload=True)
    _dismiss_blocking_popups(page)
    # Contact first (intl-tel / country-code split) — never pre-fill #phone with +971…
    _apply_qa_contact_fields(page, qa)
    # Greenhouse: "Autofill with Resume" — click then attach resume before manual fields
    if _try_resume_autofill(page, qa):
        logger.info("  Greenhouse resume autofill triggered — filling any remaining gaps")
    else:
        _upload_resume_inputs(page, qa, label="Resume (Greenhouse)")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)
    for sel, val in [("#first_name", qa["first_name"]), ("#last_name", qa["last_name"]),
                     ("#email", qa["email"])]:
        try:
            if val and page.locator(sel).count() > 0:
                page.locator(sel).first.fill(val)
                _pause(0.3, 0.7)
        except Exception:
            pass
    for lsel in ["#job_application_answers_attributes_0_text_value",
                 "input[name*='linkedin']", "input[placeholder*='linkedin' i]"]:
        try:
            if page.locator(lsel).count() > 0:
                page.locator(lsel).first.fill(qa["linkedin"])
                break
        except Exception:
            pass
    _apply_qa_contact_fields(page, qa)
    _fill_native_selects(page, job, qa, profile, model, base_url)
    _fill_aria_comboboxes(page, job, qa, profile, model, base_url)
    _fill_radio_groups(page, job, qa, profile, model, base_url)
    for ta in page.locator("textarea").all():
        try:
            label = ta.get_attribute("aria-label") or ta.get_attribute("placeholder") or ""
            if not label:
                ta_id = ta.get_attribute("id")
                if ta_id:
                    lbl = page.locator(f"label[for='{ta_id}']")
                    if lbl.count() > 0:
                        label = lbl.first.inner_text()
            if label:
                direct = _qa_value_for_label(label, qa)
                if direct is not None:
                    ta.fill(direct)
                else:
                    ta.fill(_llm_answer(label, job.get("company",""), job.get("title",""),
                                        job.get("positioning_angle","investments"),
                                        profile, model, base_url, qa=qa))
                _pause(0.5, 1.5)
        except Exception:
            pass
    _fill_required_fields_pass(page, job, qa, profile, model, base_url)
    _fix_step_validation_errors(page, job, qa, profile, model, base_url)
    return True


# ── Lever ─────────────────────────────────────────────────────────────────────

def _fill_lever(page, job: dict, qa: dict, profile: str, model: str, base_url: str) -> bool:
    logger.info("  Detected: Lever")
    _prepare_application_resume(page, qa, platform="lever")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)
    # Use local phone format if the page has a country-code field
    _phone = (_phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
              if (_page_has_country_code_field(page) or _page_has_intl_tel_input(page))
              else qa.get("phone", ""))
    for sel, val in [
        ("input[name='name']",           qa["full_name"]),
        ("input[name='email']",          qa["email"]),
        ("input[name='phone']",          _phone),
        ("input[name='org']",            "Polygon Technical Infrastructures"),
        ("input[name='urls[LinkedIn]']", qa["linkedin"]),
    ]:
        try:
            if val and page.locator(sel).count() > 0:
                page.locator(sel).first.fill(val)
                _pause(0.2, 0.6)
        except Exception:
            pass
    for q_el in page.locator(".application-question").all():
        try:
            label = q_el.locator("label, .question-basic-label").first.inner_text()
            input_el = q_el.locator("input[type='text'], textarea").first
            if label and input_el.count() > 0:
                direct = _qa_value_for_label(label, qa)
                if direct is not None:
                    input_el.fill(direct)
                else:
                    input_el.fill(_llm_answer(label, job.get("company",""), job.get("title",""),
                                              job.get("positioning_angle","investments"),
                                              profile, model, base_url, qa=qa))
                _pause(0.5, 1.2)
        except Exception:
            pass
    return True


# ── Ashby ─────────────────────────────────────────────────────────────────────

def _fill_ashby(page, job: dict, qa: dict, profile: str, model: str, base_url: str) -> bool:
    logger.info("  Detected: Ashby")
    _prepare_application_resume(page, qa, platform="ashby")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)
    # Use local phone format if the page has a country-code field
    _phone = (_phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
              if (_page_has_country_code_field(page) or _page_has_intl_tel_input(page))
              else qa.get("phone", ""))
    for sel, val in [
        ("input[data-label*='First' i]", qa["first_name"]),
        ("input[data-label*='Last' i]",  qa["last_name"]),
        ("input[data-label*='Email' i]", qa["email"]),
        ("input[data-label*='Phone' i]", _phone),
        ("input[data-label*='LinkedIn' i]", qa["linkedin"]),
        ("input[placeholder*='First' i]",  qa["first_name"]),
        ("input[placeholder*='Last' i]",   qa["last_name"]),
        ("input[placeholder*='Email' i]",  qa["email"]),
        ("input[placeholder*='Phone' i]",  _phone),
    ]:
        try:
            if val and page.locator(sel).count() > 0:
                page.locator(sel).first.fill(val)
                _pause(0.2, 0.5)
        except Exception:
            pass
    return True


# ── iCIMS ─────────────────────────────────────────────────────────────────────

def _fill_icims(page, job: dict, qa: dict, profile: str,
                model: str, base_url: str) -> bool:
    """
    iCIMS ATS filler.

    iCIMS flow:
      1. Auth wall (sign-in or create account) → handled by clear_auth_wall
      2. Multi-step wizard: Profile → Experience → Education → Questions → Review
      Each step has a "Next" or "Save & Continue" button.
      Fields use standard <input>/<select>/<textarea> with aria-labels or <label> elements.
    """
    logger.info("  Detected: iCIMS")
    _ensure_qa_contact(qa, force_reload=True)
    _dismiss_blocking_popups(page)

    # Auth wall (iCIMS always requires an account)
    from agents.account_signup import clear_auth_wall
    qa["_current_job"] = job
    if not clear_auth_wall(page, job, qa, platform="icims", screenshot_fn=_screenshot):
        job.update({
            "applied": False,
            "apply_notes": "iCIMS: account creation/sign-in blocked",
            "decision": "manual_review",
        })
        return False
    _pause(1.0, 2.0)

    # iCIMS uses a wizard — walk up to 10 steps
    for step in range(10):
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        _pause(0.8, 1.5)

        # Check for review/confirmation page
        try:
            body = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            body = ""
        if any(k in body for k in ("application submitted", "thank you for applying",
                                    "successfully submitted", "application received")):
            return True  # Submitted — _finalize_non_wizard will confirm

        # Fill contact fields on every step (they repeat across pages)
        has_cc = _page_has_country_code_field(page)
        qa_local = dict(qa)
        if has_cc:
            qa_local["phone"] = _phone_national_uae(qa.get("phone", ""), with_leading_zero=True, qa=qa)
        _apply_qa_contact_fields(page, qa_local)

        # Resume upload
        _upload_resume_inputs(page, qa, label="Resume (iCIMS)")
        _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)

        # Fill all visible fields with AI
        fields = _extract_fields(page)
        if not fields:
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            _pause(1.0, 1.5)
            fields = _extract_fields(page)

        if fields:
            _fill_fields_from_qa(page, fields, job, qa_local, profile, model, base_url)

        # iCIMS-specific: fill native selects, radio groups, comboboxes
        _fill_native_selects(page, job, qa_local, profile, model, base_url)
        _fill_radio_groups(page, job, qa_local, profile, model, base_url)
        _fill_aria_comboboxes(page, job, qa_local, profile, model, base_url)
        _workday_accept_consent_checkboxes(page)

        # Try Next / Save & Continue
        _ICIMS_NEXT = (
            "button[data-icims-id*='next'], button[data-icims-id*='continue']",
            "button:has-text('Next')",
            "button:has-text('Save & Continue')",
            "button:has-text('Save and Continue')",
            "button:has-text('Continue')",
            "input[type='submit'][value*='Next' i]",
            "input[type='submit'][value*='Continue' i]",
            "input[type='submit'][value*='Save' i]",
        )
        advanced = False
        for sel in _ICIMS_NEXT:
            try:
                btn = page.locator(sel).first
                if not btn.count() or not btn.is_visible(timeout=500):
                    continue
                label_text = ""
                try:
                    label_text = (btn.inner_text(timeout=400) or btn.get_attribute("value") or "").lower()
                except Exception:
                    pass
                if any(w in label_text for w in ("submit", "apply")):
                    return True  # Let _finalize_non_wizard handle submit
                btn.click(timeout=4000)
                _pause(1.0, 2.0)
                advanced = True
                logger.info(f"  iCIMS step {step+1}: advanced via '{label_text or sel}'")
                break
            except Exception:
                continue
        if not advanced:
            logger.info(f"  iCIMS: no Next button on step {step+1} — reached final page")
            return True

    logger.warning("  iCIMS: exceeded 10-step wizard limit")
    return True  # Let _finalize_non_wizard attempt submit


def _fill_fields_from_qa(page, fields: list, job: dict, qa: dict,
                          profile: str, model: str, base_url: str) -> int:
    """Fill extracted fields using QA facts + LLM fallback. Returns count filled."""
    filled = 0
    for field in fields:
        ftype  = field.get("type", "text")
        label  = field.get("label", "")
        sel    = field.get("sel") or field.get("selector", "")
        if not sel:
            continue
        try:
            el = page.locator(sel).first
            if not el.count() or not el.is_visible(timeout=400):
                continue
            if "file" in ftype:
                continue  # handled separately
            if any(kind in ftype for kind in ("checkbox", "radio", "select", "combobox")):
                continue  # handled by dedicated radio/checkbox fillers
            # Get answer
            answer = _qa_value_for_label(label, qa)
            if not answer:
                answer = _llm_answer(label, job.get("company",""), job.get("title",""),
                                     job.get("positioning_angle", "investments"),
                                     profile, model, base_url, qa=qa)
            if not answer:
                continue
            el.fill(str(answer))
            filled += 1
            _pause(0.1, 0.3)
        except Exception:
            continue
    return filled


# ── SmartRecruiters ───────────────────────────────────────────────────────────

def _fill_smartrecruiters(page, job: dict, qa: dict, profile: str,
                           model: str, base_url: str) -> bool:
    """
    SmartRecruiters ATS filler.

    SR flow:
      1. "I'm interested" / "Apply" button on job page
      2. Modal or full page: First Name, Last Name, Email, Phone, Resume upload
      3. Optional: custom screening questions (text, select, radio)
      4. Submit button
    SR does NOT require account creation for basic apply.
    """
    logger.info("  Detected: SmartRecruiters")
    _ensure_qa_contact(qa, force_reload=True)
    _dismiss_blocking_popups(page)

    # Click "I'm interested" / "Apply" CTA if not already on the form
    _SR_CTA = (
        "button:has-text('I\\'m Interested')",
        "button:has-text(\"I'm interested\")",
        "a:has-text('I\\'m Interested')",
        "[data-hook='btn-apply'], [data-hook='apply-button']",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        ".apply-button",
    )
    if not _external_form_already_visible(page):
        for sel in _SR_CTA:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=800):
                    btn.click(timeout=3000)
                    _pause(1.5, 2.5)
                    break
            except Exception:
                continue

    # Wait for form to appear
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    _pause(0.8, 1.5)

    # Fill contact fields using SR-specific selectors first
    _SR_FIELDS = [
        ("[data-hook='firstName'], input[name='firstName'], #firstName", qa.get("first_name","")),
        ("[data-hook='lastName'], input[name='lastName'], #lastName",   qa.get("last_name","")),
        ("[data-hook='email'], input[name='email'], input[type='email']", qa.get("email","")),
        ("[data-hook='phone'], input[name='phone'], input[type='tel']",
         _phone_national_uae(qa.get("phone",""), with_leading_zero=True, qa=qa)
         if _page_has_country_code_field(page) else qa.get("phone","")),
        ("[data-hook='currentLocation'], input[name='location']",        qa.get("address","")),
    ]
    for sel, val in _SR_FIELDS:
        if not val:
            continue
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=500):
                el.fill(val)
                _pause(0.2, 0.4)
        except Exception:
            continue

    # Resume upload
    _upload_resume_inputs(page, qa, label="Resume (SmartRecruiters)")
    _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)

    # AI-driven fill for any remaining fields + screening questions
    fields = _extract_fields(page)
    if fields:
        _fill_fields_from_qa(page, fields, job, qa, profile, model, base_url)
    _fill_native_selects(page, job, qa, profile, model, base_url)
    _fill_radio_groups(page, job, qa, profile, model, base_url)
    _fill_aria_comboboxes(page, job, qa, profile, model, base_url)
    _workday_accept_consent_checkboxes(page)

    # Check for phone country code
    if _page_has_country_code_field(page) or _page_has_intl_tel_input(page):
        _select_uae_country_code(page)
        _fill_intl_tel_phone(page, qa)

    logger.info("  SmartRecruiters: form filled — ready for submit")
    return True


# ── Taleo ─────────────────────────────────────────────────────────────────────

def _fill_taleo(page, job: dict, qa: dict, profile: str,
                model: str, base_url: str) -> bool:
    """
    Taleo ATS filler (Oracle Taleo / SuccessFactors-compatible URL patterns).

    Taleo flow:
      1. "Apply Online" button → new tab or redirect
      2. Account wall (sign in / create account)
      3. Multi-step wizard: Personal Info → Work Experience → Education →
         Screening Questions → Attachments → Review & Submit
      Fields use Taleo-specific IDs like 'firstName', 'lastName', etc.
    """
    logger.info("  Detected: Taleo")
    _ensure_qa_contact(qa, force_reload=True)
    _dismiss_blocking_popups(page)

    # Click "Apply Online" / "Apply for this Job" if not on form yet
    _TALEO_CTA = (
        "a:has-text('Apply Online')", "button:has-text('Apply Online')",
        "a:has-text('Apply for this Job')", "button:has-text('Apply for this Job')",
        "a:has-text('Apply Now')", "button:has-text('Apply Now')",
        "#applyButton", ".applyButton", "[id*='applyBtn']",
    )
    if not _external_form_already_visible(page):
        for sel in _TALEO_CTA:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible(timeout=600):
                    btn.click(timeout=3000)
                    _pause(1.5, 2.5)
                    page = _switch_to_latest_ats_page(page)
                    break
            except Exception:
                continue

    # Auth wall
    from agents.account_signup import clear_auth_wall
    qa["_current_job"] = job
    if not clear_auth_wall(page, job, qa, platform="taleo", screenshot_fn=_screenshot):
        job.update({
            "applied": False,
            "apply_notes": "Taleo: account creation/sign-in blocked",
            "decision": "manual_review",
        })
        return False
    _pause(1.0, 2.0)

    # Walk Taleo wizard steps (up to 10)
    for step in range(10):
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        _pause(0.8, 1.5)

        # Submission confirmation check
        try:
            body = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            body = ""
        if any(k in body for k in ("application submitted", "thank you for applying",
                                    "successfully submitted", "application complete")):
            return True

        # Fill Taleo-specific known field IDs
        _TALEO_FIELDS = [
            ("#firstName, input[id*='firstName']",    qa.get("first_name","")),
            ("#lastName, input[id*='lastName']",      qa.get("last_name","")),
            ("#email, input[id*='email']",             qa.get("email","")),
            ("#phone, input[id*='phone']",
             _phone_national_uae(qa.get("phone",""), with_leading_zero=True, qa=qa)
             if _page_has_country_code_field(page) else qa.get("phone","")),
            ("#address1, input[id*='address']",        qa.get("address","")),
            ("#city, input[id*='city']",               qa.get("city","")),
            ("#postalCode, input[id*='postalCode'], input[id*='zipCode']",
             qa.get("postal_code","")),
        ]
        for sel, val in _TALEO_FIELDS:
            if not val:
                continue
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=500):
                    current = el.input_value(timeout=300)
                    if not current:
                        el.fill(val)
                        _pause(0.1, 0.3)
            except Exception:
                continue

        # Resume / attachments
        _upload_resume_inputs(page, qa, label="Resume (Taleo)")
        _upload_cover_letter_inputs(page, job, qa, model=model, base_url=base_url)

        # AI-driven for remaining fields
        fields = _extract_fields(page)
        if fields:
            _fill_fields_from_qa(page, fields, job, qa, profile, model, base_url)
        _fill_native_selects(page, job, qa, profile, model, base_url)
        _fill_radio_groups(page, job, qa, profile, model, base_url)
        _fill_aria_comboboxes(page, job, qa, profile, model, base_url)
        _workday_accept_consent_checkboxes(page)

        # Taleo next-step selectors
        _TALEO_NEXT = (
            "input[type='submit'][value*='Next' i]",
            "input[type='submit'][value*='Continue' i]",
            "input[type='submit'][value*='Save' i]",
            "button:has-text('Next')",
            "button:has-text('Continue')",
            "button:has-text('Save and Continue')",
            "a:has-text('Next')",
            "#btnNext, #nextButton, [id*='btnNext']",
        )
        advanced = False
        for sel in _TALEO_NEXT:
            try:
                btn = page.locator(sel).first
                if not btn.count() or not btn.is_visible(timeout=500):
                    continue
                label_text = ""
                try:
                    label_text = (
                        btn.inner_text(timeout=400) or
                        btn.get_attribute("value") or ""
                    ).lower()
                except Exception:
                    pass
                if any(w in label_text for w in ("submit", "apply", "finish")):
                    return True  # Let _finalize_non_wizard handle final submit
                btn.click(timeout=4000)
                _pause(1.0, 2.0)
                advanced = True
                logger.info(f"  Taleo step {step+1}: advanced via '{label_text or sel}'")
                break
            except Exception:
                continue
        if not advanced:
            logger.info(f"  Taleo: no Next button on step {step+1} — reached final page")
            return True

    logger.warning("  Taleo: exceeded 10-step wizard limit")
    return True


# ── Platform detection ────────────────────────────────────────────────────────

def _detect_platform(url: str, page) -> str:
    u = url.lower()
    if "linkedin.com/jobs" in u:
        return "linkedin"
    if "myworkdayjobs.com" in u or ("workday.com" in u and "/job/" in u):
        return "workday"
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "apply.workable.com" in u or "jobs.workable.com" in u:
        return "workable"
    if "teamtailor.com" in u:
        return "teamtailor"
    if "jobs.icims.com" in u or "careers.icims.com" in u:
        return "icims"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "taleo.net" in u:
        return "taleo"
    if "oraclecloud.com" in u and "candidateexperience" in u:
        return "oracle_recruiting"
    if "bamboohr.com" in u:
        return "ai_driven"
    try:
        html = page.content().lower()
        if "boards.greenhouse.io" in html: return "greenhouse"
        if "jobs.lever.co"        in html: return "lever"
        if "ashbyhq.com"          in html: return "ashby"
        if "myworkdayjobs"        in html: return "workday"
        if "workable.com"         in html: return "workable"
        if "teamtailor"           in html: return "teamtailor"
    except Exception:
        pass
    return "ai_driven"  # AI-driven is the default fallback


# ── Main apply entry point ────────────────────────────────────────────────────

def apply_to_job(
    job: dict,
    qa: dict,
    candidate_profile: str,
    model: str,
    base_url: str,
    dry_run: bool = True,
    headless: bool = False,
) -> dict:
    """
    Navigate to job URL, detect ATS, fill form, optionally submit.

    dry_run=True  -> fills form + takes screenshot, does NOT submit (default/safe)
    dry_run=False -> submits the application
    headless=False -> browser window visible (recommended — avoids bot detection)
    """
    if not PLAYWRIGHT_AVAILABLE:
        job.update({"applied": False, "apply_notes": "Playwright not installed"})
        return job

    url = job.get("job_url_direct") or job.get("job_url", "")
    if not url:
        job.update({"applied": False, "apply_notes": "No apply URL"})
        return job

    if not os.path.exists(qa.get("resume_path", "")):
        job.update({"applied": False, "apply_notes": f"Resume not found: {qa.get('resume_path')}"})
        return job

    _record_live_apply_attempt(job, dry_run)
    try:
        with sync_playwright() as p:
            if "linkedin.com" in url.lower():
                ctx = _get_linkedin_context(p)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
            else:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-sandbox", "--disable-dev-shm-usage"])
                ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"),
                    viewport={"width": 1440, "height": 900}, locale="en-US")
                ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = ctx.new_page()

            logger.info(f"Navigating: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _pause(2.0, 3.5)

            platform = _detect_platform(url, page)
            logger.info(f"Platform: {platform}")
            if _detect_captcha_challenge(page):
                _mark_captcha_required(page, job, platform)
                _screenshot(page, job, "_captcha_required")
                return job
            if platform != "linkedin" and _page_is_closed_job(page):
                _mark_job_closed(job, page)
                _persist_closed_job(job)
                return job

            if platform == "linkedin":
                success = _linkedin_apply_job(
                    ctx, page, job, qa, candidate_profile, model, base_url, dry_run)
            elif platform == "workday":
                success = _fill_workday(
                    page, job, qa, candidate_profile, model, base_url, dry_run)
            elif platform == "greenhouse":
                success = _fill_greenhouse(page, job, qa, candidate_profile, model, base_url)
            elif platform == "lever":
                success = _fill_lever(page, job, qa, candidate_profile, model, base_url)
            elif platform == "ashby":
                success = _fill_ashby(page, job, qa, candidate_profile, model, base_url)
            elif platform == "workable":
                success = _fill_workable(page, job, qa, candidate_profile, model, base_url)
            elif platform == "teamtailor":
                success = _fill_teamtailor(page, job, qa, candidate_profile, model, base_url)
            elif platform == "icims":
                success = _fill_icims(page, job, qa, candidate_profile, model, base_url)
                if success:
                    success = _finalize_non_wizard(page, job, platform, dry_run, "", base_url)
            elif platform == "smartrecruiters":
                success = _fill_smartrecruiters(page, job, qa, candidate_profile, model, base_url)
                if success:
                    success = _finalize_non_wizard(page, job, platform, dry_run, "", base_url)
            elif platform == "taleo":
                success = _fill_taleo(page, job, qa, candidate_profile, model, base_url)
                if success:
                    success = _finalize_non_wizard(page, job, platform, dry_run, "", base_url)
            elif platform == "oracle_recruiting":
                success = _fill_oracle_recruiting(
                    page, job, qa, candidate_profile, model, base_url
                )
                if success:
                    success = _finalize_non_wizard(page, job, platform, dry_run, "", base_url)
            else:
                page, ready = _prepare_generic_application_page(
                    page, job, qa, base_url=base_url
                )
                success = ready and _fill_ai_driven(
                    page, job, qa, candidate_profile, model, base_url
                )

            if not success:
                job["applied"] = False
                if not job.get("apply_notes"):
                    job["apply_notes"] = f"Form fill failed ({platform})"
                _screenshot(page, job, "_failed")
                return job

            # Platforms that handle finalization internally (workday + dedicated ATS fillers)
            # only need the submission-status gate; everything else needs _finalize_non_wizard.
            _SELF_FINALIZING = ("linkedin", "workday", "icims", "smartrecruiters", "taleo")
            if platform in _SELF_FINALIZING:
                if dry_run:
                    job["applied"] = False
                elif job.get("submission_status") != "confirmed":
                    job["applied"] = False
                    if success:
                        job["decision"] = "manual_review"
                        job["submission_status"] = "confirmation_pending"
                if not job.get("apply_notes") and dry_run:
                    job["apply_notes"] = (
                        f"Dry run - {platform} filled, not submitted"
                    )
            else:
                _finalize_non_wizard(page, job, platform, dry_run)

            page.close()
            ctx.close()

    except PWTimeout as e:
        job.update({"applied": False, "apply_notes": f"Timeout: {e}"})
    except Exception as e:
        logger.error(f"Error: {e}")
        job.update({"applied": False, "apply_notes": f"Error: {e}"})

    return job


def _validate_job_before_apply(
    job: dict,
    page,
    candidate_profile: str,
    model: str,
    base_url: str,
    validate_fit: bool,
) -> tuple[bool, str]:
    """Re-check fit using live page description (LinkedIn) + scorer.

    Bug 1 fix: if the job already has score > 0 in the DB, reuse it — never
    re-run the LLM scorer for an already-scored job.
    """
    from agents.job_fit import prefilter_job

    # Enrich description from live LinkedIn page only when we don't already have one
    if page and "linkedin.com" in (page.url or ""):
        snippet = _linkedin_description_snippet(page)
        if snippet:
            from agents.job_profile import merge_linkedin_page_text, build_structured_job_profile
            merge_linkedin_page_text(job, snippet)
            if not (job.get("score") or 0) > 0:
                try:
                    build_structured_job_profile(job, model, base_url, use_llm=True)
                except Exception:
                    pass

    blocked, reason = prefilter_job(job)
    if blocked:
        return False, reason

    if not validate_fit:
        return True, ""

    existing_decision = (job.get("decision") or "").lower()
    if existing_decision == "auto_apply" and job.get("_easy_apply_only_run"):
        return True, ""

    existing_score = job.get("score") or 0
    if existing_score > 0:
        if existing_decision == "skip":
            reason = (
                job.get("skip_reason")
                or job.get("fit_reason")
                or f"Skipped: score {existing_score}/100"
            )
            return False, reason
        return True, ""

    from agents.scorer import score_job
    from config.config import SCORE_THRESHOLDS
    score_job(job, candidate_profile, model, base_url, SCORE_THRESHOLDS)
    decision = (job.get("decision") or "").lower()
    if decision == "skip":
        reason = job.get("skip_reason") or job.get("fit_reason") or "Scored as skip"
        return False, reason
    return True, ""


def _persist_fit_skip(job: dict, reason: str) -> None:
    """Persist a fit-skip outcome to the database immediately."""
    try:
        from agents.job_logger import update_after_apply
        job.update({"applied": False, "apply_notes": reason})
        update_after_apply(job)
    except Exception:
        pass


def _persist_closed_job(job: dict) -> None:
    """Persist a closed-job outcome immediately so the GUI can hide it mid-run."""
    try:
        from agents.job_logger import update_after_apply
        update_after_apply(job)
    except Exception:
        pass


# ── Batch apply entry point ───────────────────────────────────────────────────

def apply_jobs_batch(
    jobs: list,
    qa: dict,
    candidate_profile: str,
    model: str,
    base_url: str,
    dry_run: bool = True,
    headless: bool = False,
    linkedin_email: str = "",
    linkedin_password: str = "",
    vision_model: str = "",
    validate_fit: bool = True,
) -> list:
    """
    Apply to a batch of jobs using a SINGLE browser instance.
    - LinkedIn jobs: one persistent context (session saved to disk)
    - All other jobs: one shared browser, fresh context per job (no multiple windows)
    Each job dict gets 'applied' and 'apply_notes' fields set on return.
    """
    if not PLAYWRIGHT_AVAILABLE:
        for job in jobs:
            job.update({"applied": False, "apply_notes": "Playwright not installed"})
        return jobs

    _ensure_qa_contact(qa, force_reload=True)
    logger.info(f"Apply contact: phone={qa.get('phone')} | email={qa.get('email')}")

    resume_path = qa.get("resume_path", "")
    if resume_path and not os.path.exists(resume_path):
        logger.warning(f"Default resume not found: {resume_path}")

    global _THREAD_SESSION
    linkedin_jobs = [j for j in jobs if "linkedin.com" in (j.get("job_url") or "").lower()]
    other_jobs    = [j for j in jobs if j not in linkedin_jobs]
    _THREAD_SESSION = _ApplySession.build(qa, model, base_url)
    if _THREAD_SESSION:
        logger.info(f"Apply session cache built (fast model: {_THREAD_SESSION.fast_model})")

    with sync_playwright() as p:

        # ── LinkedIn: one persistent context for all LinkedIn jobs ──────────────
        if linkedin_jobs:
            logger.info(f"LinkedIn: {len(linkedin_jobs)} job(s) to process")
            ctx = _get_linkedin_context(p)
            linkedin_ready = _ensure_linkedin_login(ctx, linkedin_email, linkedin_password)
            li_page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for job in linkedin_jobs:
                if not linkedin_ready:
                    job.update({
                        "applied": False,
                        "apply_notes": "LinkedIn login or verification required - skipped unattended",
                        "decision": "manual_review",
                    })
                    continue
                try:
                    from gui.stop_flag import check_stop
                    check_stop("Stop requested — halting apply batch")
                except ImportError:
                    pass
                try:
                    job["apply_attempts"] = int(job.get("apply_attempts") or 0) + 1
                    _record_live_apply_attempt(job, dry_run)
                    job_qa = {**qa, "resume_path": job.get("_resume_path", resume_path)}
                    job_url = job.get("job_url") or job.get("job_url_direct") or ""
                    if not job_url:
                        job.update({
                            "applied": False,
                            "apply_notes": "No LinkedIn job URL",
                            "decision": "manual_review",
                        })
                        continue
                    try:
                        if li_page.is_closed():
                            li_page = ctx.new_page()
                    except Exception:
                        li_page = ctx.new_page()
                    logger.info(f"  LinkedIn navigating: {job_url}")
                    li_page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                    _pause(2.5, 3.5)
                    _linkedin_apply_job(
                        ctx, li_page, job, job_qa, candidate_profile,
                        model, base_url, dry_run,
                        vision_model=vision_model, validate_fit=validate_fit,
                    )
                except Exception as e:
                    err = str(e)
                    if "Target page, context or browser has been closed" in err:
                        logger.error(f"  Browser page closed unexpectedly for {job.get('title')} — skipping")
                        job.update({
                            "applied": False,
                            "apply_notes": f"Browser closed unexpectedly: {err[:120]}",
                            "decision": "manual_review",
                        })
                        try:
                            recovered = None
                            for candidate in ctx.pages:
                                if not candidate.is_closed():
                                    recovered = candidate
                                    break
                            li_page = recovered or ctx.new_page()
                        except Exception:
                            pass
                    else:
                        logger.error(f"LinkedIn error for {job.get('title')}: {e}")
                        job.update({"applied": False, "apply_notes": f"Error: {e}"})
                finally:
                    try:
                        for tab in list(ctx.pages):
                            if tab is li_page or tab.is_closed():
                                continue
                            try:
                                tab.close()
                            except Exception:
                                pass
                    except Exception:
                        pass
            try:
                ctx.close()
            except Exception:
                pass

        # ── Other ATSs: one shared browser, new context per job ─────────────────
        if other_jobs:
            browser = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"])
            for job in other_jobs:
                try:
                    from gui.stop_flag import check_stop
                    check_stop("Stop requested — halting apply batch")
                except ImportError:
                    pass
                url = job.get("job_url_direct") or job.get("job_url", "")
                if not url:
                    job.update({"applied": False, "apply_notes": "No apply URL"})
                    continue
                _handle_profile_gaps_before_apply(job)
                job_qa = {**qa, "resume_path": job.get("_resume_path", resume_path)}
                if job_qa["resume_path"] and not os.path.exists(job_qa["resume_path"]):
                    job.update({"applied": False,
                                "apply_notes": f"Resume not found: {job_qa['resume_path']}"})
                    continue
                job["apply_attempts"] = int(job.get("apply_attempts") or 0) + 1
                _record_live_apply_attempt(job, dry_run)

                ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"),
                    viewport={"width": 1440, "height": 900}, locale="en-US")
                ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = ctx.new_page()
                try:
                    logger.info(f"Applying [{job.get('title')} @ {job.get('company')}]: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _pause(2.0, 3.5)
                    try:
                        job["description"] = page.locator("body").inner_text(timeout=5000)[:2500]
                    except Exception:
                        pass
                    ok_fit, fit_msg = _validate_job_before_apply(
                        job, page, candidate_profile, model, base_url, validate_fit)
                    if not ok_fit:
                        job.update({
                            "applied": False,
                            "apply_notes": f"Skipped before apply: {fit_msg}",
                            "decision": "skip",
                            "skip_reason": fit_msg,
                        })
                        logger.warning(f"  Skipped (fit): {fit_msg}")
                        _persist_fit_skip(job, fit_msg)
                        _screenshot(page, job, "_skipped_fit")
                        continue

                    platform = _detect_platform(url, page)
                    logger.info(f"  Platform: {platform}")
                    if platform != "linkedin" and _page_is_closed_job(page):
                        _mark_job_closed(job, page)
                        _persist_closed_job(job)
                        continue

                    if platform == "workday":
                        success = _fill_workday(
                            page, job, job_qa, candidate_profile, model, base_url, dry_run)
                        if not job.get("apply_notes"):
                            job["apply_notes"] = ("Dry run - workday filled, not submitted"
                                                  if dry_run else
                                                  ("Submitted via workday" if success else "Workday fill failed"))
                    elif platform == "greenhouse":
                        success = _fill_greenhouse(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "lever":
                        success = _fill_lever(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "ashby":
                        success = _fill_ashby(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "workable":
                        success = _fill_workable(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "icims":
                        success = _fill_icims(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "smartrecruiters":
                        success = _fill_smartrecruiters(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    elif platform == "taleo":
                        success = _fill_taleo(page, job, job_qa, candidate_profile, model, base_url)
                        if success:
                            success = _finalize_non_wizard(page, job, platform, dry_run, vision_model, base_url)
                    else:
                        page, ready = _prepare_generic_application_page(
                            page, job, job_qa, vision_model, base_url)
                        success = ready and _fill_ai_driven(
                            page, job, job_qa, candidate_profile, model, base_url,
                            vision_model=vision_model)
                        if success:
                            success = _finalize_non_wizard(
                                page, job, platform or "ai_driven", dry_run, vision_model, base_url)

                    if not success and not job.get("apply_notes"):
                        job.update({"applied": False,
                                    "apply_notes": f"Form fill failed ({platform})"})
                        _screenshot(page, job, "_failed")

                except PWTimeout as e:
                    job.update({"applied": False, "apply_notes": f"Timeout: {e}"})
                except Exception as e:
                    logger.error(f"Error applying to {job.get('title', '')}: {e}")
                    job.update({"applied": False, "apply_notes": f"Error: {e}"})
                finally:
                    try:
                        page.close()
                        ctx.close()
                    except Exception:
                        pass

            try:
                browser.close()
            except Exception:
                pass

    return jobs

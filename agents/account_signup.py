"""
Automatic account creation / sign-in at ATS login walls.

Uses ``signup_defaults`` from ``data/profile_settings.json`` (GUI: Sign up &
application defaults). When a portal shows register or sign-in, we try to
create an account or sign in before falling back to manual prompts.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("account_signup")

# ── Email-verify pending queue ─────────────────────────────────────────────────
_PENDING_VERIFY_FILE = Path(__file__).resolve().parent.parent / "data" / "pending_email_verify.json"


def _load_email_verify_pending() -> list[dict]:
    """Return jobs waiting for manual email verification."""
    try:
        if _PENDING_VERIFY_FILE.exists():
            data = json.loads(_PENDING_VERIFY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_email_verify_pending(jobs: list[dict]) -> None:
    try:
        _PENDING_VERIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PENDING_VERIFY_FILE.write_text(
            json.dumps(jobs, indent=2, default=str), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("Could not save email-verify pending queue: %s", e)


def add_email_verify_pending(job: dict, portal_url: str, email: str) -> None:
    """
    Queue a job for retry after the user manually verifies their email.
    Called when Workday (or any portal) blocks at the email-verify wall.
    """
    pending = _load_email_verify_pending()
    job_url = job.get("job_url") or ""
    job_id = job.get("job_id") or job.get("id")
    existing = next(
        (
            item for item in pending
            if (job_id and item.get("job_id") == job_id)
            or (job_url and item.get("job_url") == job_url)
        ),
        None,
    )
    payload = {
        "job_id":     job_id,
        "job_url":    job_url,
        "job_url_direct": job.get("job_url_direct") or "",
        "title":      job.get("title", ""),
        "company":    job.get("company", ""),
        "portal_url": portal_url,
        "email":      email,
        "queued_at":  datetime.now(timezone.utc).isoformat(),
        "retries":    int((existing or {}).get("retries") or 0),
        "last_retry_at": (existing or {}).get("last_retry_at") or "",
    }
    if existing is not None:
        existing.update(payload)
    else:
        pending.append(payload)
    _save_email_verify_pending(pending)
    logger.info(
        "  Email-verify required for %s @ %s — queued for retry after manual verification",
        job.get("title", ""), job.get("company", "")
    )


def pop_email_verify_pending(max_retries: int = 3) -> list[dict]:
    """
    Claim pending email-verify jobs for one live retry.

    Entries stay on disk until authentication succeeds, so retries cannot reset
    indefinitely when the portal still waits for verification.
    """
    pending = _load_email_verify_pending()
    ready = []
    now = datetime.now(timezone.utc).isoformat()
    for item in pending:
        retries = int(item.get("retries") or 0)
        if retries >= max_retries:
            continue
        item["retries"] = retries + 1
        item["last_retry_at"] = now
        ready.append(dict(item))
    if ready:
        _save_email_verify_pending(pending)
    return ready


def clear_email_verify_pending(job: dict) -> None:
    """Remove a resumable auth entry once the saved portal session works."""
    job_id = job.get("job_id") or job.get("id")
    job_url = job.get("job_url") or ""
    pending = _load_email_verify_pending()
    remaining = [
        item for item in pending
        if not (
            (job_id and item.get("job_id") == job_id)
            or (job_url and item.get("job_url") == job_url)
        )
    ]
    if len(remaining) != len(pending):
        _save_email_verify_pending(remaining)

_CREATE_TAB_SELECTORS = (
    "a:has-text('Create Account')",
    "button:has-text('Create Account')",
    "[data-automation-id='createAccountLink']",
    "a:has-text('Create account')",
    "button:has-text('Create account')",
    "a:has-text('Sign up')",
    "button:has-text('Sign up')",
    "a:has-text('Register')",
    "button:has-text('Register')",
    "a:has-text('Join')",
    "button:has-text('Get started')",
    "a:has-text('Get Started')",
)

_SIGN_IN_TAB_SELECTORS = (
    "a:has-text('Sign In')",
    "button:has-text('Sign In')",
    "[data-automation-id='signInLink']",
    "a:has-text('Log in')",
    "button:has-text('Log in')",
    "a:has-text('Login')",
)

_SUBMIT_SELECTORS = (
    "button[data-automation-id='createAccountSubmitButton']",
    "button[data-automation-id='signInSubmitButton']",
    "button:has-text('Create Account')",
    "button:has-text('Create account')",
    "button:has-text('Sign up')",
    "button:has-text('Register')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "button:has-text('Login')",
    "button:has-text('Continue')",
    "button:has-text('Submit')",
    "input[type='submit']",
)


def _pause(lo: float = 0.25, hi: float = 0.55) -> None:
    time.sleep(random.uniform(lo, hi))


def load_signup_identity(qa: dict | None = None, portal_url: str = "") -> dict:
    """Merge profile signup_defaults with application QA (phone, links)."""
    identity: dict = {}
    try:
        from config.env_settings import load_signup_defaults
        identity.update(load_signup_defaults())
    except Exception:
        pass
    if qa:
        for key in (
            "first_name", "last_name", "middle_name", "full_name", "gender",
            "email", "password", "address", "city", "state", "country",
            "postal_code", "location", "nationality", "date_of_birth",
            "phone", "phone_local", "linkedin", "website", "github",
        ):
            val = qa.get(key)
            if val is not None and str(val).strip():
                identity[key] = str(val).strip()
    if not identity.get("email"):
        identity["email"] = (
            os.getenv("APPLICANT_EMAIL", "") or (qa or {}).get("email", "")
        ).strip()
    if not identity.get("phone"):
        identity["phone"] = (
            os.getenv("APPLICANT_PHONE", "") or (qa or {}).get("phone", "")
        ).strip()
    if not identity.get("phone_local"):
        identity["phone_local"] = (
            os.getenv("APPLICANT_PHONE_LOCAL", "")
            or (qa or {}).get("phone_local", "")
        ).strip()
    if not identity.get("full_name"):
        parts = [
            identity.get("first_name", ""),
            identity.get("middle_name", ""),
            identity.get("last_name", ""),
        ]
        identity["full_name"] = " ".join(p for p in parts if p).strip()
    if portal_url:
        try:
            from agents.credential_vault import get_portal_credential
            credential = get_portal_credential(portal_url)
            if credential.get("email") and credential.get("password"):
                identity["email"] = credential["email"]
                identity["password"] = credential["password"]
        except Exception as exc:
            logger.debug("  Portal vault read failed: %s", exc)
    return identity


def auto_signup_enabled(identity: dict) -> bool:
    flag = os.getenv("AUTO_ACCOUNT_SIGNUP", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(identity.get("email") and identity.get("password"))


def workday_auth_wall_visible(page) -> bool:
    """Workday Sign In / Create Account gate (often after Apply)."""
    sel_signals = [
        "[data-automation-id='createAccountLink']",
        "[data-automation-id='signInLink']",
        "[data-automation-id='createAccountSubmitButton']",
        "button[data-automation-id='signInSubmitButton']",
        "[data-automation-id='email'][type='email']",
    ]
    visible_auth = 0
    for sel in sel_signals:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                visible_auth += 1
        except Exception:
            continue
    if visible_auth >= 2:
        return True
    try:
        heading = page.locator("h1, h2, h3").first.inner_text(timeout=500).lower()
        if any(k in heading for k in ("sign in", "create account", "verify your email")):
            if page.locator("input[type='password']").count() > 0 or visible_auth >= 1:
                return True
    except Exception:
        pass
    return False


def page_auth_wall_visible(page) -> tuple[bool, str]:
    """Generic login / signup wall on external ATS pages."""
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return False, ""
    signup_signals = (
        "sign up to apply", "signup to apply", "create an account to apply",
        "register to apply", "create account", "create your account",
        "join to apply", "get started", "register now", "sign up for free",
    )
    login_signals = (
        "log in to apply", "login to apply", "sign in to apply",
        "you must be logged in", "please log in to continue",
    )
    for phrase in signup_signals:
        if phrase in body:
            return True, "Sign-up required before applying"
    for phrase in login_signals:
        if phrase in body:
            return True, "Login required before applying"
    if ("sign up" in body or "create account" in body or "register" in body):
        if page.locator(
            "input[type='password'], form:has-text('Sign up'), form:has-text('Register')"
        ).count() > 0:
            return True, "Sign-up form visible"
    if page.locator("input[type='password']").count() > 0:
        if page.locator(
            "input[type='email'], input[name*='email' i], input[id*='email' i]"
        ).count() > 0:
            return True, "Email/password auth form visible"
    return False, ""


def auth_wall_visible(page, platform: str = "") -> tuple[bool, str]:
    url = (page.url or "").lower()
    if platform == "workday" or "myworkdayjobs.com" in url or "workday" in url:
        if workday_auth_wall_visible(page):
            return True, "Workday sign-in or account creation required"
    return page_auth_wall_visible(page)


def signup_value_for_field(
    label: str,
    name: str = "",
    field_id: str = "",
    input_type: str = "",
    autocomplete: str = "",
    identity: dict | None = None,
) -> Optional[str]:
    """Map a form field to a value from profile signup settings."""
    identity = identity or {}
    blob = " ".join(
        filter(None, [label, name, field_id, autocomplete])
    ).lower()
    itype = (input_type or "").lower()

    if itype == "password":
        if re.search(r"confirm|re-?type|verify|again", blob):
            return identity.get("password", "")
        return identity.get("password", "")

    rules: list[tuple[str, str]] = [
        (r"confirm.*password|password.*confirm|re-?type.*password", "password"),
        (r"\bpassword\b|\bpasswd\b", "password"),
        (r"e-?mail|username", "email"),
        (r"first\s*name|given\s*name|fname", "first_name"),
        (r"last\s*name|family\s*name|surname|lname", "last_name"),
        (r"middle\s*name|middle\s*initial", "middle_name"),
        (r"full\s*name|legal\s*name|your\s*name", "full_name"),
        (r"phone|mobile|tel|cell", "phone"),
        (r"street|address\s*line|addr(ess)?(?!\s*email)", "address"),
        (r"\bcity\b|\btown\b", "city"),
        (r"\bstate\b|\bprovince\b|\bregion\b", "state"),
        (r"postal|zip\s*code|post\s*code", "postal_code"),
        (r"\bcountry\b|nation", "country"),
        (r"location|where\s+are\s+you", "location"),
        (r"nationality|citizen", "nationality"),
        (r"date\s*of\s*birth|\bdob\b|birth\s*date", "date_of_birth"),
        (r"\bgender\b|\bsex\b", "gender"),
        (r"linkedin", "linkedin"),
        (r"github", "github"),
        (r"website|portfolio|personal\s*site", "website"),
    ]
    for pattern, key in rules:
        if re.search(pattern, blob):
            val = identity.get(key, "")
            if val:
                return str(val)
    if itype == "email":
        return identity.get("email", "")
    if itype == "tel":
        return identity.get("phone_local") or identity.get("phone", "")
    return None


def _click_first_visible(page, selectors: tuple[str, ...], timeout_ms: int = 1200) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=timeout_ms):
                el.click()
                _pause(0.6, 1.2)
                return True
        except Exception:
            continue
    return False


def _try_fill_selector(page, selectors: tuple[str, ...], value: str) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                el.fill(value)
                _pause(0.15, 0.35)
                return True
        except Exception:
            continue
    return False


_WORKDAY_AUTH_ERRORS = (
    "incorrect password",
    "invalid password",
    "password is incorrect",
    "account not found",
    "no account found",
    "email address not found",
    "we couldn't find",
    "invalid credentials",
    "sign in failed",
    "authentication failed",
)

_CAPTCHA_SIGNALS = (
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "captcha",
    "i am not a robot",
    "i'm not a robot",
    "verify you are human",
)

_EMAIL_VERIFY_SIGNALS = (
    "verify your email",
    "check your email",
    "confirmation email",
    "verify your account",
    "email verification",
    "link sent to your email",
)


def _page_body_lower(page) -> str:
    """Quick snapshot of visible page text for heuristic checks."""
    targets = getattr(page, "frames", None) or [page]
    chunks = []
    for target in targets:
        try:
            chunks.append(target.locator("body").inner_text(timeout=4000).lower())
        except Exception:
            continue
    return "\n".join(chunks)


def _detect_captcha(page) -> bool:
    """Return True if a CAPTCHA widget is visible on the page."""
    targets = getattr(page, "frames", None) or [page]
    for target in targets:
        try:
            text = f"{getattr(target, 'url', '')}\n{target.content()}".lower()
            if any(sig in text for sig in _CAPTCHA_SIGNALS):
                return True
        except Exception:
            continue
    return False


def _detect_email_verification(page) -> bool:
    """Return True if the portal is waiting for an email verification click."""
    body = _page_body_lower(page)
    return any(sig in body for sig in _EMAIL_VERIFY_SIGNALS)


def _detect_access_blocked(page) -> bool:
    """Return True when the portal refuses automated access before a form loads."""
    body = _page_body_lower(page)
    return any(
        signal in body
        for signal in (
            "403 forbidden",
            "access denied",
            "access is temporarily restricted",
            "unusual activity from your device or network",
        )
    )


def _detect_signin_error(page) -> bool:
    """Return True if the sign-in form shows a wrong-password / not-found error."""
    body = _page_body_lower(page)
    return any(sig in body for sig in _WORKDAY_AUTH_ERRORS)


def _fill_workday_auth(page, identity: dict, prefer_create: bool = True) -> bool:
    """Fill Workday create-account or sign-in panel.

    Returns True when the submit button was clicked (not necessarily cleared).
    Caller must check auth_wall_visible() after waiting.
    """
    email = identity.get("email", "")
    password = identity.get("password", "")
    if not email or not password:
        return False

    on_create = False
    if prefer_create:
        on_create = _click_first_visible(page, _CREATE_TAB_SELECTORS)
    if not on_create:
        _click_first_visible(page, _SIGN_IN_TAB_SELECTORS)

    _try_fill_selector(
        page,
        (
            "input[data-automation-id='email']",
            "input[type='email']",
            "input[name*='email' i]",
        ),
        email,
    )
    for sel in ("input[type='password']", "input[data-automation-id='password']"):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                if el.is_visible(timeout=500):
                    el.fill(password)
                    _pause(0.15, 0.3)
        except Exception:
            pass

    _try_fill_selector(
        page,
        ("input[data-automation-id='legalNameSection_firstName']",),
        identity.get("first_name", ""),
    )
    _try_fill_selector(
        page,
        ("input[data-automation-id='legalNameSection_lastName']",),
        identity.get("last_name", ""),
    )

    clicked = _click_first_visible(
        page,
        (
            "button[data-automation-id='createAccountSubmitButton']",
            "button[data-automation-id='signInSubmitButton']",
        ),
        timeout_ms=1500,
    )
    if not clicked:
        clicked = _click_first_visible(page, _SUBMIT_SELECTORS, timeout_ms=1500)
    if clicked:
        # Workday can take 3–5s to process auth — wait longer than the generic 1.5s
        _pause(3.0, 5.0)
    return clicked


def _accept_terms_checkboxes(page) -> int:
    checked = 0
    for cb in page.locator("input[type='checkbox']").all():
        try:
            if not cb.is_visible(timeout=400):
                continue
            if cb.is_checked():
                continue
            label = _field_label_for_element(page, cb)
            if re.search(
                r"terms|privacy|agree|consent|acknowledge|accept|gdpr|policy",
                label,
                re.I,
            ):
                cb.check()
                checked += 1
                _pause(0.1, 0.25)
        except Exception:
            continue
    return checked


def _field_label_for_element(page, el) -> str:
    parts: list[str] = []
    try:
        parts.append(el.get_attribute("aria-label") or "")
        parts.append(el.get_attribute("placeholder") or "")
        parts.append(el.get_attribute("name") or "")
        parts.append(el.get_attribute("id") or "")
        fid = el.get_attribute("id") or ""
        if fid:
            lbl = page.locator(f"label[for='{fid}']")
            if lbl.count() > 0:
                parts.append(lbl.first.inner_text(timeout=300))
    except Exception:
        pass
    return " ".join(p for p in parts if p).strip()


def _fill_generic_auth_inputs(page, identity: dict) -> int:
    """Fill visible inputs on a login/signup form from profile settings."""
    filled = 0
    selectors = (
        "input:not([type='hidden']):not([type='submit']):not([type='button'])"
        ":not([type='image']):not([type='radio']):not([type='checkbox'])"
    )
    for inp in page.locator(selectors).all():
        try:
            if not inp.is_visible(timeout=400):
                continue
            cur = (inp.input_value(timeout=300) or "").strip()
            if cur:
                continue
            label = _field_label_for_element(page, inp)
            itype = (inp.get_attribute("type") or "text").lower()
            name = inp.get_attribute("name") or ""
            fid = inp.get_attribute("id") or ""
            ac = inp.get_attribute("autocomplete") or ""
            value = signup_value_for_field(
                label, name, fid, itype, ac, identity
            )
            if not value:
                continue
            if itype == "tel" and value.startswith("+971"):
                value = identity.get("phone_local") or value.replace("+971", "0", 1)
            inp.fill(value)
            filled += 1
            _pause(0.12, 0.28)
        except Exception:
            continue
    return filled


def attempt_auto_signup(
    page,
    qa: dict | None = None,
    *,
    platform: str = "",
    prefer_create: bool = False,
) -> tuple[bool, str]:
    """
    Try to register or sign in using profile signup_defaults.

    Returns (success, message).
    """
    portal_url = page.url or ""
    identity = load_signup_identity(qa, portal_url)
    if not auto_signup_enabled(identity):
        return False, "Set email and password in Profile Settings → Sign up defaults"

    url = portal_url.lower()
    is_workday = (
        platform == "workday"
        or "myworkdayjobs.com" in url
        or "workday" in url
    )

    try:
        from agents.credential_vault import portal_has_account
        if portal_has_account(portal_url):
            prefer_create = False
    except Exception as exc:
        logger.debug("  Portal registry read failed: %s", exc)

    if prefer_create:
        _click_first_visible(page, _CREATE_TAB_SELECTORS)
    else:
        _click_first_visible(page, _SIGN_IN_TAB_SELECTORS)

    def _wall_cleared() -> bool:
        wall, _ = auth_wall_visible(page, platform)
        if wall:
            return False
        if is_workday and workday_auth_wall_visible(page):
            return False
        return True

    def _check_blocking_states() -> str | None:
        """Return a human-readable reason if the portal is in a state we can't unblock."""
        if _detect_captcha(page):
            return "CAPTCHA required — deferred for manual review"
        if _detect_email_verification(page):
            return "Email verification required — deferred for manual review"
        return None

    def _queue_email_verification(blocker: str) -> None:
        if "email verification" not in (blocker or "").lower() or not qa:
            return
        add_email_verify_pending(
            qa.get("_current_job") or {},
            portal_url=portal_url,
            email=identity.get("email", ""),
        )

    if is_workday:
        if _fill_workday_auth(page, identity, prefer_create=prefer_create):
            pass  # already waited 3–5s inside _fill_workday_auth
        else:
            return False, "Workday auth form could not be submitted"
    else:
        n = _fill_generic_auth_inputs(page, identity)
        _accept_terms_checkboxes(page)
        if n == 0:
            logger.debug("  Auto signup: no empty fields matched profile settings")
        if not _click_first_visible(page, _SUBMIT_SELECTORS, timeout_ms=2000):
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        _pause(2.0, 3.0)

    # ── Check for hard blockers (CAPTCHA, email verify) before wall check ─────
    blocker = _check_blocking_states()
    if blocker:
        logger.warning("  Auth blocker: %s", blocker)
        _queue_email_verification(blocker)
        return False, blocker

    if _wall_cleared():
        _remember_portal_auth(portal_url, identity, platform, prefer_create)
        return True, (
            "Account created from portal defaults"
            if prefer_create else "Signed in with saved portal credentials"
        )

    # ── Sign-in failed — try to detect why and flip to sign-up ───────────────
    if not prefer_create:
        signin_err = _detect_signin_error(page)
        if signin_err:
            logger.info("  Sign-in error detected — falling back to account creation")
        else:
            logger.info("  Sign-in did not clear wall — falling back to account creation")
        _click_first_visible(page, _CREATE_TAB_SELECTORS)
        if is_workday:
            _fill_workday_auth(page, identity, prefer_create=True)
        else:
            _fill_generic_auth_inputs(page, identity)
            _accept_terms_checkboxes(page)
            _click_first_visible(page, _SUBMIT_SELECTORS, timeout_ms=2000)
            _pause(2.0, 3.0)
        blocker = _check_blocking_states()
        if blocker:
            logger.warning("  Auth blocker after signup attempt: %s", blocker)
            _queue_email_verification(blocker)
            return False, blocker
        if _wall_cleared():
            _remember_portal_auth(portal_url, identity, platform, True)
            return True, "Account created from portal defaults (sign-in fallback)"

    # ── Create-account first, then try sign-in ────────────────────────────────
    if prefer_create and not is_workday:
        logger.info("  Create-account did not clear wall — trying sign-in with saved password")
        _click_first_visible(page, _SIGN_IN_TAB_SELECTORS)
        _fill_generic_auth_inputs(page, identity)
        _click_first_visible(page, _SUBMIT_SELECTORS, timeout_ms=2000)
        _pause(2.0, 3.0)
        blocker = _check_blocking_states()
        if blocker:
            _queue_email_verification(blocker)
            return False, blocker
        if _wall_cleared():
            _remember_portal_auth(portal_url, identity, platform, False)
            return True, "Signed in with profile email/password"

    return False, "Auth wall still visible after all attempts — CAPTCHA or unknown form"


def _remember_portal_auth(
    portal_url: str, identity: dict, platform: str, account_created: bool
) -> None:
    try:
        from agents.credential_vault import record_portal_result, save_portal_credential
        save_portal_credential(
            portal_url, identity.get("email", ""), identity.get("password", "")
        )
        record_portal_result(
            portal_url,
            platform=platform,
            outcome="account_created" if account_created else "signed_in",
        )
    except Exception as exc:
        logger.warning("  Could not save portal credentials: %s", exc)


def clear_auth_wall(
    page,
    job: dict,
    qa: dict | None,
    *,
    platform: str = "",
    screenshot_fn=None,
) -> bool:
    """Clear a portal auth wall or defer the job without blocking an AFK run."""
    qa = qa if qa is not None else {}
    qa["_current_job"] = job
    portal_url = page.url or ""

    if _detect_captcha(page):
        message = "CAPTCHA required - deferred for manual review"
        status = "captcha_required"
    elif _detect_email_verification(page):
        message = "Email verification required - deferred for manual review"
        status = "email_verification_required"
        identity = load_signup_identity(qa, portal_url)
        add_email_verify_pending(job, portal_url, identity.get("email", ""))
    elif _detect_access_blocked(page):
        message = "Portal access blocked - deferred for manual review"
        status = "portal_blocked"
    else:
        wall, reason = auth_wall_visible(page, platform)
        if not wall:
            return True
        prefer_create = any(
            term in (reason or "").lower()
            for term in ("create", "register", "sign up", "signup")
        )
        ok, message = attempt_auto_signup(
            page,
            qa,
            platform=platform,
            prefer_create=prefer_create,
        )
        if ok:
            clear_email_verify_pending(job)
            return True
        lower_message = (message or "").lower()
        if "email verification" in lower_message:
            status = "email_verification_required"
        elif "captcha" in lower_message:
            status = "captcha_required"
        else:
            status = "auth_required"

    job.update({
        "applied": False,
        "decision": "manual_review",
        "apply_notes": message,
        "submission_status": status,
    })
    if screenshot_fn:
        try:
            screenshot_fn(page, job, "_auth_required")
        except Exception:
            pass
    return False

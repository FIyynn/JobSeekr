"""
Level 3 email discovery — stub + optional Hunter.io when API key configured.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("email_discovery")


def _get_api_key(provider: str) -> str:
    try:
        from config.env_settings import get_profile_settings
        env = get_profile_settings().get("env") or {}
        return (env.get(f"{provider}_api_key") or "").strip()
    except Exception:
        return ""


def discover_work_email(
    name: str,
    company: str,
    *,
    domain: str = "",
) -> dict:
    """
    Return {email, confidence, source} or empty email.
    Uses Hunter.io when HUNTER_API_KEY is set in profile_settings env section.
    """
    result = {"email": "", "confidence": 0.0, "source": "none", "verified_email": False}

    if not name or not company:
        return result

    hunter_key = _get_api_key("hunter")
    if hunter_key and domain:
        try:
            import requests
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else ""
            r = requests.get(
                "https://api.hunter.io/v2/email-finder",
                params={
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": hunter_key,
                },
                timeout=15,
            )
            data = r.json().get("data") or {}
            email = data.get("email") or ""
            score = float(data.get("score") or 0)
            if email and score >= 50:
                result.update({
                    "email": email,
                    "confidence": score / 100.0,
                    "source": "hunter",
                    "verified_email": score >= 80,
                })
                return result
        except Exception as exc:
            logger.debug("Hunter lookup failed: %s", exc)

    guess_domain = domain or _guess_domain(company)
    if guess_domain and name:
        parts = re.sub(r"[^a-zA-Z\s]", "", name).lower().split()
        if len(parts) >= 2:
            patterns = [
                f"{parts[0]}.{parts[-1]}@{guess_domain}",
                f"{parts[0][0]}{parts[-1]}@{guess_domain}",
                f"{parts[0]}@{guess_domain}",
            ]
            result.update({
                "email": patterns[0],
                "confidence": 0.35,
                "source": "pattern_guess",
                "verified_email": False,
            })

    return result


def _guess_domain(company: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", company.lower())[:20]
    if not slug:
        return ""
    return f"{slug}.com"

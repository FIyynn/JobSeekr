"""
Social listening — outreach hooks from posts, signals, and company context (draft only).
"""

from __future__ import annotations

import re
from typing import Optional


def extract_hooks(text: str, max_hooks: int = 3) -> list[str]:
    """Pull short hook phrases from post/signal text."""
    if not text:
        return []
    hooks = []
    for sentence in re.split(r"[.!?\n]+", text):
        s = sentence.strip()
        if 20 < len(s) < 180:
            if any(k in s.lower() for k in (
                "hiring", "growing", "team", "excited", "launch", "expand",
                "dm me", "referral", "open role", "join",
            )):
                hooks.append(s)
        if len(hooks) >= max_hooks:
            break
    return hooks


def build_outreach_hook(
    job: dict,
    contact: Optional[dict] = None,
) -> str:
    """
    Generate a short personalized outreach draft (human reviews before send).
    """
    company = job.get("company") or "your team"
    title = job.get("title") or "the role"
    person = (contact or {}).get("name") or ""
    greeting = f"Hi {person.split()[0]}," if person else "Hi,"

    hooks = []
    for key in ("description", "hiring_language", "raw_snippet", "cta", "fit_reason"):
        hooks.extend(extract_hooks(job.get(key) or ""))
    hook = hooks[0] if hooks else f"I noticed {company} is building out the {title} side of the business."

    return (
        f"{greeting} {hook} "
        f"I'd value a brief perspective on team priorities for this area — "
        f"would you have 10 minutes for a quick question?"
    ).strip()


def enrich_job_with_hooks(job: dict) -> dict:
    """Attach outreach_hook for unified engine outreach_quality."""
    job = dict(job)
    job["outreach_hook"] = build_outreach_hook(job)
    return job

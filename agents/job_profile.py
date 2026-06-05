"""
Build structured job profile from posting text (role, requirements, company, department).
"""

import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger("job_profile")

PROFILE_EXTRACT_PROMPT = """Extract a structured job profile from this posting. Return ONLY JSON:
{{
  "role_title": "",
  "department": "",
  "company_about": "",
  "role_summary": "",
  "requirements": [],
  "responsibilities": [],
  "experience_level": "",
  "workplace_type": ""
}}

Posting:
Company: {company}
Title: {title}
Location: {location}

Text:
{text}
"""


def _extract_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


def build_structured_job_profile(
    job: dict,
    model: str,
    base_url: str,
    use_llm: bool = True,
) -> dict:
    """
    Populate job['job_profile'] dict from description (+ optional extra page text).
    """
    desc = (job.get("description") or "").strip()
    extra = (job.get("_page_text_extra") or "").strip()
    blob = f"{desc}\n\n{extra}".strip()[:6000]

    profile = {
        "role_title": job.get("title", ""),
        "department": "",
        "company_about": "",
        "role_summary": "",
        "requirements": [],
        "responsibilities": [],
        "experience_level": "",
        "workplace_type": "",
    }

    if not blob or len(blob) < 60:
        job["job_profile"] = profile
        job["job_profile_json"] = json.dumps(profile)
        return profile

    # Light regex hints before LLM
    for label, key in [
        (r"(?i)about (?:the )?company[:\s]+(.{80,800}?)(?=\n\n|requirements|responsibilities|$)", "company_about"),
        (r"(?i)(?:department|team)[:\s]+(.{20,200})", "department"),
    ]:
        m = re.search(label, blob, re.DOTALL)
        if m and key == "company_about":
            profile["company_about"] = m.group(1).strip()[:800]
        elif m and key == "department":
            profile["department"] = m.group(1).strip()[:200]

    if use_llm and model and base_url:
        prompt = PROFILE_EXTRACT_PROMPT.format(
            company=job.get("company", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            text=blob[:5000],
        )
        try:
            r = requests.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 1200},
                },
                timeout=90,
            )
            r.raise_for_status()
            parsed = _extract_json(r.json().get("response", ""))
            if parsed:
                for k in profile:
                    if parsed.get(k):
                        profile[k] = parsed[k]
        except Exception as e:
            logger.debug(f"Job profile LLM extract failed: {e}")

    job["job_profile"] = profile
    job["job_profile_json"] = json.dumps(profile, ensure_ascii=False)
    return profile


def job_profile_summary_for_scorer(job: dict) -> str:
    """Compact text block for scorer prompt."""
    p = job.get("job_profile")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            p = {}
    if not p:
        try:
            p = json.loads(job.get("job_profile_json") or "{}")
        except Exception:
            p = {}
    if not p:
        return (job.get("description") or "")[:1500]

    parts = []
    if p.get("role_summary"):
        parts.append(f"Role summary: {p['role_summary']}")
    if p.get("department"):
        parts.append(f"Department: {p['department']}")
    if p.get("company_about"):
        parts.append(f"Company: {p['company_about'][:400]}")
    if p.get("requirements"):
        reqs = p["requirements"] if isinstance(p["requirements"], list) else [p["requirements"]]
        parts.append("Requirements: " + "; ".join(str(x) for x in reqs[:12]))
    if p.get("responsibilities"):
        resp = p["responsibilities"] if isinstance(p["responsibilities"], list) else [p["responsibilities"]]
        parts.append("Responsibilities: " + "; ".join(str(x) for x in resp[:8]))
    if p.get("experience_level"):
        parts.append(f"Level: {p['experience_level']}")
    return "\n".join(parts)[:2000] or (job.get("description") or "")[:1500]


def merge_linkedin_page_text(job: dict, page_text: str) -> None:
    """Append live LinkedIn job page text and rebuild profile fields."""
    if not page_text or len(page_text) < 80:
        return
    job["_page_text_extra"] = page_text[:4000]
    existing = (job.get("description") or "")
    if page_text not in existing:
        job["description"] = (existing + "\n\n" + page_text).strip()[:8000]

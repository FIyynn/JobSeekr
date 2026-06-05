"""
Notion Logger Agent
Writes every scored job to a Notion database with full metadata.
Creates the database schema on first run if the DB is empty.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
from datetime import datetime
from typing import Optional
import requests

logger = logging.getLogger("notion_logger")

# ── Notion API helpers ─────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _safe_str(val, max_len: int = 2000) -> str:
    if val is None:
        return ""
    return str(val)[:max_len]


def _decision_to_status(decision: str) -> str:
    mapping = {
        "auto_apply":    "Auto Apply",
        "manual_review": "Manual Review",
        "skip":          "Skipped",
    }
    return mapping.get(decision, "Unknown")


# ── Database setup ─────────────────────────────────────────────────────────────

DATABASE_SCHEMA = {
    "Company":          {"title": {}},
    "Role":             {"rich_text": {}},
    "Location":         {"rich_text": {}},
    "Score":            {"number": {"format": "number"}},
    "Decision":         {"select": {"options": [
        {"name": "Auto Apply",    "color": "green"},
        {"name": "Manual Review", "color": "yellow"},
        {"name": "Skipped",       "color": "red"},
        {"name": "Applied",       "color": "blue"},
        {"name": "Excluded",      "color": "gray"},
    ]}},
    "Positioning Angle": {"select": {"options": [
        {"name": "quant",       "color": "purple"},
        {"name": "investments", "color": "blue"},
        {"name": "AI",          "color": "orange"},
        {"name": "space",       "color": "gray"},
        {"name": "energy",      "color": "yellow"},
        {"name": "fintech",     "color": "green"},
        {"name": "climate",     "color": "green"},
        {"name": "strategy",    "color": "pink"},
        {"name": "cyber",       "color": "red"},
    ]}},
    "Source":           {"select": {"options": [
        {"name": "linkedin",  "color": "blue"},
        {"name": "glassdoor", "color": "green"},
        {"name": "indeed",    "color": "purple"},
        {"name": "ATS",       "color": "orange"},
        {"name": "employee_post", "color": "yellow"},
        {"name": "web_indexed", "color": "brown"},
    ]}},
    "Apply Method":     {"rich_text": {}},
    "Date Posted":      {"rich_text": {}},
    "Discovered At":    {"date": {}},
    "Job URL":          {"url": {}},
    "Fit Reason":       {"rich_text": {}},
    "Skip Reason":      {"rich_text": {}},
    "Applied":          {"checkbox": {}},
    "Notes":            {"rich_text": {}},
}


def create_database(token: str, parent_page_id: str, title: str = "JobHunter Tracker") -> str:
    """
    Create a new Notion database and return its ID.
    Call this once to bootstrap — then save the DB ID in Profile Settings.
    """
    url = "https://api.notion.com/v1/databases"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": DATABASE_SCHEMA,
        "is_inline": False,
    }
    resp = requests.post(url, headers=_headers(token), json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to create Notion DB: {resp.status_code} - {resp.text}")
    db_id = resp.json()["id"]
    logger.info(f"Created Notion database: {db_id}")
    return db_id


# ── Row existence check ────────────────────────────────────────────────────────

def _job_exists(token: str, database_id: str, job_url: str) -> bool:
    """Check if a job URL already exists in the database (dedup)."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = {
        "filter": {
            "property": "Job URL",
            "url": {"equals": job_url}
        },
        "page_size": 1,
    }
    resp = requests.post(url, headers=_headers(token), json=payload)
    if resp.status_code == 200:
        return len(resp.json().get("results", [])) > 0
    return False


# ── Row creation ──────────────────────────────────────────────────────────────

def _build_page_properties(job: dict) -> dict:
    """Convert a job dict to Notion page properties."""

    def rt(text: str) -> dict:
        """Rich text property."""
        return {"rich_text": [{"text": {"content": _safe_str(text, 2000)}}]}

    def sel(value: str) -> dict:
        """Select property."""
        return {"select": {"name": _safe_str(value, 100)}}

    # Discovered at as ISO date
    discovered_at = job.get("discovered_at", datetime.utcnow().isoformat())
    try:
        # Notion expects ISO 8601 with timezone
        dt_str = discovered_at[:19] + "Z"
    except Exception:
        dt_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    props = {
        # Title = Company
        "Company": {
            "title": [{"text": {"content": _safe_str(job.get("company", "Unknown"), 100)}}]
        },
        "Role":     rt(job.get("title", "")),
        "Location": rt(job.get("location", "")),
        "Score":    {"number": job.get("score") or 0},
        "Decision": sel(_decision_to_status(job.get("decision", "skip"))),
        "Source":   sel(job.get("source", "linkedin")),
        "Apply Method":      rt(job.get("apply_method", "")),
        "Date Posted":       rt(str(job.get("date_posted", ""))),
        "Discovered At":     {"date": {"start": dt_str}},
        "Job URL":           {"url": job.get("job_url") or None},
        "Fit Reason":        rt(job.get("fit_reason", "")),
        "Skip Reason":       rt(job.get("skip_reason", "")),
        "Applied": {"checkbox": bool(job.get("applied", False))},
        "Notes":   rt(""),
    }

    # Only include Positioning Angle if non-empty (empty string causes Notion 400 error)
    angle = job.get("positioning_angle", "").strip()
    if angle:
        props["Positioning Angle"] = sel(angle)

    return props


def log_job(token: str, database_id: str, job: dict, skip_if_exists: bool = True) -> Optional[str]:
    """
    Write a single job to Notion. Returns the page ID or None on failure.
    """
    job_url = job.get("job_url", "")

    # Dedup check
    if skip_if_exists and job_url:
        if _job_exists(token, database_id, job_url):
            logger.debug(f"Already logged: {job['title']} @ {job['company']}")
            return None

    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent":     {"database_id": database_id},
        "properties": _build_page_properties(job),
    }

    resp = requests.post(url, headers=_headers(token), json=payload)

    if resp.status_code == 200:
        page_id = resp.json()["id"]
        logger.info(
            f"  [OK] Logged [{job.get('score', '?')}/100 {job.get('decision', '?')}]: "
            f"{job['title']} @ {job['company']}"
        )
        return page_id
    else:
        logger.error(
            f"  [FAIL] Failed to log {job['title']}: "
            f"{resp.status_code} - {resp.text[:300]}"
        )
        return None


def log_jobs_batch(token: str, database_id: str, jobs: list[dict]) -> dict:
    """Log all jobs to Notion. Returns summary counts."""
    logged = 0
    skipped_dedup = 0
    failed = 0

    for job in jobs:
        result = log_job(token, database_id, job)
        if result is None:
            skipped_dedup += 1
        elif result:
            logged += 1
        else:
            failed += 1

    return {"logged": logged, "dedup_skipped": skipped_dedup, "failed": failed}


# ── Update row status ──────────────────────────────────────────────────────────

def mark_applied(token: str, page_id: str, notes: str = "") -> bool:
    """Mark a Notion row as Applied after form submission."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Decision": {"select": {"name": "Applied"}},
            "Applied":  {"checkbox": True},
            "Notes":    {"rich_text": [{"text": {"content": notes[:2000]}}]},
        }
    }
    resp = requests.patch(url, headers=_headers(token), json=payload)
    return resp.status_code == 200


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Notion logger ready.")
    print("To bootstrap a new DB, call:")
    print("  create_database(token, parent_page_id)")
    print("Then save the returned DB ID in Profile Settings as NOTION_DATABASE_ID")

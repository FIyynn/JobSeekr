"""
Sync jobs between local SQLite (data/jobs.db) and Notion database.
"""

import logging
import os
from typing import Optional

import requests

from storage.job_store import JobStore, DECISION_DISPLAY, filter_gcc_jobs

logger = logging.getLogger("notion_sync")

NOTION_VERSION = "2022-06-28"

DISPLAY_TO_DECISION = {v: k for k, v in DECISION_DISPLAY.items()}
DISPLAY_TO_DECISION["Applied"] = "applied"
DISPLAY_TO_DECISION["Unknown"] = "skip"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _prop_text(props: dict, key: str):
    p = props.get(key, {})
    t = p.get("type")
    if t == "title":
        return "".join(x["plain_text"] for x in p.get("title", []))
    if t == "rich_text":
        return "".join(x["plain_text"] for x in p.get("rich_text", []))
    if t == "select":
        s = p.get("select")
        return s["name"] if s else ""
    if t == "number":
        return p.get("number")
    if t == "url":
        return p.get("url") or ""
    if t == "checkbox":
        return bool(p.get("checkbox"))
    if t == "date":
        d = p.get("date") or {}
        return d.get("start") or ""
    return ""


def notion_row_to_job(row: dict) -> dict:
    """Convert a Notion database page to local job dict."""
    props = row.get("properties", {})

    # Decision: read the select value and map back to internal key
    decision_display = _prop_text(props, "Decision") or "Skipped"
    decision = DISPLAY_TO_DECISION.get(decision_display, "skip")

    # Applied: checkbox → bool; if checked, decision is always "applied"
    applied_raw = _prop_text(props, "Applied")
    applied_bool = bool(applied_raw)  # True/False from checkbox
    if applied_bool:
        decision = "applied"

    # Score: Notion number property, may be None
    score_raw = _prop_text(props, "Score")
    try:
        score = int(score_raw) if score_raw is not None else 0
    except (TypeError, ValueError):
        score = 0

    notes = _prop_text(props, "Notes") or ""

    return {
        "notion_page_id": row["id"],
        "company":  _prop_text(props, "Company") or "",
        "title":    _prop_text(props, "Role") or "",
        "location": _prop_text(props, "Location") or "",
        "score":    score,
        "decision": decision,
        "decision_display": decision_display,
        "positioning_angle": (_prop_text(props, "Positioning Angle") or "investments").lower(),
        "source":       (_prop_text(props, "Source") or "linkedin").lower(),
        "apply_method": _prop_text(props, "Apply Method") or "",
        "date_posted":  str(_prop_text(props, "Date Posted") or ""),
        "discovered_at": str(_prop_text(props, "Discovered At") or ""),
        "job_url":      _prop_text(props, "Job URL") or "",
        "fit_reason":   _prop_text(props, "Fit Reason") or "",
        "skip_reason":  _prop_text(props, "Skip Reason") or "",
        "applied":      applied_bool,
        "notes":        notes,
        "apply_notes":  notes,
    }


def fetch_all_notion_jobs(token: str, database_id: str) -> list[dict]:
    """Fetch every row from the Notion jobs database (handles pagination)."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = {"page_size": 100}
    jobs = []
    cursor = None
    page_num = 0
    while True:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, json=body, headers=_headers(token), timeout=60)
        r.raise_for_status()
        data = r.json()
        page_num += 1
        results = data.get("results", [])
        logger.debug(f"  Notion page {page_num}: {len(results)} row(s)")
        for row in results:
            job = notion_row_to_job(row)
            # Accept any row that has at least a title or company
            if job.get("job_url") or job.get("title") or job.get("company"):
                jobs.append(job)
            else:
                logger.debug(f"  Skipping empty Notion row: {row.get('id')}")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    logger.info(f"Notion: fetched {len(jobs)} rows across {page_num} API page(s)")
    return jobs


def _url_to_page_map(token: str, database_id: str) -> dict[str, str]:
    """job_url -> notion page id"""
    mapping = {}
    for job in fetch_all_notion_jobs(token, database_id):
        u = (job.get("job_url") or "").strip().lower()
        if u:
            mapping[u] = job["notion_page_id"]
    return mapping


def validate_notion_config(token: str = "", database_id: str = "") -> tuple[bool, str]:
    token = token or os.getenv("NOTION_TOKEN", "").strip()
    database_id = database_id or os.getenv("NOTION_DATABASE_ID", "").strip()
    if not token:
        return False, "NOTION_TOKEN is missing. Set it in Profile Settings → Save all settings."
    if not database_id:
        return False, "NOTION_DATABASE_ID is missing. Run setup_notion.py or paste DB id from Notion URL."
    return True, ""


def _pull_upsert(store: JobStore, job: dict) -> tuple[bool, bool]:
    """
    Insert or selectively update a job from Notion.
    Returns (existed, success).

    On UPDATE: only Notion-owned fields are written (decision, score, applied,
    notes, positioning_angle, fit_reason, skip_reason, notion_page_id).
    Local-only fields (description, job_profile_json, salary_snippet, etc.)
    are PRESERVED so a pull never wipes local enrichment data.

    On INSERT: all available fields are written.
    """
    url = (job.get("job_url") or "").strip()
    page_id = (job.get("notion_page_id") or "").strip()

    # Try to find an existing row by URL first, then by notion_page_id
    existing_id = None
    with store._connect() as conn:
        if url:
            row = conn.execute(
                "SELECT id FROM jobs WHERE job_url = ?", (url,)
            ).fetchone()
            if row:
                existing_id = row["id"]
        if existing_id is None and page_id:
            row = conn.execute(
                "SELECT id FROM jobs WHERE notion_page_id = ?", (page_id,)
            ).fetchone()
            if row:
                existing_id = row["id"]
        # Fallback: match by company + title (case-insensitive) for URL-less rows
        if existing_id is None and job.get("company") and job.get("title"):
            row = conn.execute(
                "SELECT id FROM jobs WHERE lower(company)=lower(?) AND lower(title)=lower(?)",
                (job["company"], job["title"]),
            ).fetchone()
            if row:
                existing_id = row["id"]

    if existing_id:
        # Selective update — only Notion-owned fields
        store.update_job(
            existing_id,
            decision=job.get("decision", "skip"),
            score=int(job.get("score") or 0),
            applied=1 if job.get("applied") else 0,
            notes=job.get("notes", ""),
            apply_notes=job.get("apply_notes", ""),
            positioning_angle=job.get("positioning_angle", ""),
            fit_reason=job.get("fit_reason", ""),
            skip_reason=job.get("skip_reason", ""),
            notion_page_id=page_id or None,
        )
        return True, True

    # New row — full insert
    rid = store.upsert_job(job, skip_if_exists=False)
    return False, bool(rid)


def pull_from_notion(
    token: str = "",
    database_id: str = "",
    gcc_only: bool = False,
    store: JobStore = None,
) -> dict:
    """
    Import / merge all Notion rows into local SQLite.

    Match priority: job_url → notion_page_id → company+title.
    On update: only Notion-owned fields are written (decision, score, applied,
    notes, angle, fit/skip reason). Local-only enrichment data is preserved.
    Rows without any URL are still imported using title+company as the key.
    """
    ok, err = validate_notion_config(token, database_id)
    if not ok:
        raise ValueError(err)
    token = token or os.getenv("NOTION_TOKEN", "").strip()
    database_id = database_id or os.getenv("NOTION_DATABASE_ID", "").strip()
    store = store or JobStore()

    jobs = fetch_all_notion_jobs(token, database_id)
    logger.info(f"Notion pull: fetched {len(jobs)} row(s) from Notion")
    if gcc_only:
        before = len(jobs)
        jobs = filter_gcc_jobs(jobs)
        logger.info(f"GCC filter: kept {len(jobs)} of {before}")

    imported, updated, skipped = 0, 0, 0
    skipped_reasons: list[str] = []

    for job in jobs:
        # Must have at least title or company to be useful
        if not job.get("title") and not job.get("company"):
            skipped += 1
            skipped_reasons.append("no title or company")
            continue

        url = (job.get("job_url") or "").strip()
        if not url:
            logger.debug(
                f"  No URL for '{job.get('title')} @ {job.get('company')}' "
                "— matching by title+company"
            )

        try:
            existed, ok = _pull_upsert(store, job)
            if ok:
                if existed:
                    updated += 1
                    logger.debug(
                        f"  Updated: [{job.get('decision')}] "
                        f"{job.get('title')} @ {job.get('company')}"
                    )
                else:
                    imported += 1
                    logger.info(
                        f"  Imported: [{job.get('decision')}] "
                        f"{job.get('title')} @ {job.get('company')}"
                    )
            else:
                skipped += 1
                skipped_reasons.append(
                    f"{job.get('title')} @ {job.get('company')}: upsert failed"
                )
        except Exception as e:
            skipped += 1
            logger.warning(
                f"  Error importing {job.get('title')} @ {job.get('company')}: {e}"
            )

    if skipped_reasons:
        logger.info(f"  Skipped {skipped} row(s). Reasons: {'; '.join(skipped_reasons[:5])}")

    return {
        "total_notion": len(jobs),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
    }


def push_to_notion(
    token: str = "",
    database_id: str = "",
    gcc_only: bool = False,
    limit: int = 0,
    store: JobStore = None,
) -> dict:
    """
    Push local jobs to Notion. Creates new pages or updates existing (by job URL).
    """
    from agents.notion_logger import log_job, _build_page_properties

    ok, err = validate_notion_config(token, database_id)
    if not ok:
        raise ValueError(err)
    token = token or os.getenv("NOTION_TOKEN", "").strip()
    database_id = database_id or os.getenv("NOTION_DATABASE_ID", "").strip()
    store = store or JobStore()

    jobs = store.list_jobs(limit=5000)
    if gcc_only:
        jobs = filter_gcc_jobs(jobs)
    if limit > 0:
        jobs = jobs[:limit]

    url_map = _url_to_page_map(token, database_id)
    created, updated, failed = 0, 0, 0

    for job in jobs:
        url = (job.get("job_url") or "").strip()
        if not url:
            failed += 1
            continue
        page_id = job.get("notion_page_id") or url_map.get(url.lower())

        if page_id:
            patch_url = f"https://api.notion.com/v1/pages/{page_id}"
            payload = {"properties": _build_page_properties(job)}
            try:
                r = requests.patch(
                    patch_url, headers=_headers(token), json=payload, timeout=30
                )
                if r.status_code == 200:
                    updated += 1
                    if job.get("id"):
                        store.update_job(job["id"], notion_page_id=page_id)
                else:
                    failed += 1
                    logger.warning(f"Notion update failed: {r.status_code} {r.text[:200]}")
            except Exception as e:
                failed += 1
                logger.warning(f"Notion update error: {e}")
        else:
            new_id = log_job(token, database_id, job, skip_if_exists=True)
            if new_id:
                created += 1
                if job.get("id"):
                    store.update_job(job["id"], notion_page_id=new_id)
            elif new_id is None:
                # dedup skip counts as ok
                updated += 1
            else:
                failed += 1

    return {
        "total_local": len(jobs),
        "created": created,
        "updated": updated,
        "failed": failed,
    }

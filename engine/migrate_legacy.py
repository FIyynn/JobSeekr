"""
One-shot migration: jobs.db + signals.db + linkedin_outreach.json → opportunities layer.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("migrate_legacy")

ROOT = Path(__file__).resolve().parent.parent
JOBS_DB = ROOT / "data" / "jobs.db"
SIGNALS_DB = ROOT / "data" / "signals.db"
OUTREACH_JSON = ROOT / "data" / "linkedin_outreach.json"


def migrate_all(*, dry_run: bool = False) -> dict:
    """Import legacy stores into opportunities + link job rows."""
    from storage.job_store import JobStore
    from storage.opportunity_store import get_opportunity_store
    from engine.recommend_action import apply_recommendation_to_job
    from agents.unified_engine import enrich_job_with_engine

    store = JobStore()
    opp_store = get_opportunity_store()
    stats = {"jobs": 0, "signals": 0, "outreach_linked": 0, "errors": 0}

    jobs = store.list_jobs(limit=5000, include_closed=True)
    for job in jobs:
        try:
            if not job.get("recommended_action"):
                enrich_job_with_engine(job)
                apply_recommendation_to_job(job)
            if not dry_run:
                oid = opp_store.upsert_from_job(job)
                if job.get("id"):
                    store.update_job(
                        job["id"],
                        opportunity_id=oid,
                        recommended_action=job.get("recommended_action"),
                        referral_status=job.get("referral_status") or "none",
                        track=job.get("track") or "visible",
                    )
            stats["jobs"] += 1
        except Exception as exc:
            logger.warning("Job migrate failed %s: %s", job.get("id"), exc)
            stats["errors"] += 1

    if SIGNALS_DB.exists():
        stats["signals"] = _migrate_signals(opp_store, dry_run=dry_run)

    if OUTREACH_JSON.exists():
        stats["outreach_linked"] = _link_outreach(opp_store, dry_run=dry_run)

    logger.info("Migration complete: %s", stats)
    return stats


def _migrate_signals(opp_store, *, dry_run: bool) -> int:
    from engine.opportunity import signal_to_opportunity

    count = 0
    try:
        conn = sqlite3.connect(str(SIGNALS_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM signals").fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("Signals DB read failed: %s", exc)
        return 0

    for row in rows:
        sig = dict(row)
        try:
            if not dry_run:
                opp_store.upsert_from_signal(sig)
            count += 1
        except Exception as exc:
            logger.warning("Signal migrate failed: %s", exc)
    return count


def _link_outreach(opp_store, *, dry_run: bool) -> int:
    """Attach opportunity_id to outreach rows when company/title match."""
    try:
        rows = json.loads(OUTREACH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(rows, list):
        return 0

    queue = opp_store.fetch_action_queue(limit=5000, exclude_ignore=False)
    by_company: dict[str, list] = {}
    for opp in queue:
        key = (opp.get("company") or "").lower().strip()
        if key:
            by_company.setdefault(key, []).append(opp)

    linked = 0
    for row in rows:
        if row.get("opportunity_id"):
            continue
        company = (row.get("Company") or row.get("company") or "").lower().strip()
        matches = by_company.get(company, [])
        if matches and not dry_run:
            row["opportunity_id"] = matches[0]["id"]
            linked += 1

    if linked and not dry_run:
        OUTREACH_JSON.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return linked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(migrate_all(dry_run=args.dry_run))

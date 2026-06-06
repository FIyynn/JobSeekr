"""Backfill SPS/IPS for existing jobs in jobs.db that are missing those scores."""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

from storage.job_store import get_store
from agents.unified_engine import enrich_job_with_engine


def backfill_sps_ips() -> int:
    store = get_store()
    jobs = store.list_jobs(limit=1000)
    pending = [j for j in jobs if j.get("sps") is None or j.get("ips") is None]
    total = len(pending)
    count = 0

    for i, job in enumerate(pending, 1):
        job_id = job.get("id") or job.get("job_id")
        enrich_job_with_engine(job)
        store.update_job(job_id, sps=job["sps"], ips=job["ips"])
        count += 1
        logger.info(
            "[%d/%d] %s @ %s — SPS=%s, IPS=%s",
            i,
            total,
            job.get("company"),
            job.get("title"),
            job["sps"],
            job["ips"],
        )

    logger.info("Backfilled SPS/IPS for %d jobs", count)
    return count


if __name__ == "__main__":
    backfill_sps_ips()

"""
Unified pipeline: visible + hidden discovery → score → recommend → persist.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("engine.pipeline")


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value not in ("0", "false", "no", "off")


def discover_hidden_signals(
    *,
    extra_companies: Optional[list[str]] = None,
    max_queries: int = 20,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """Track B — hidden market signal discovery."""
    from agents.hidden_opportunity_discovery import run_signal_discovery

    signals = run_signal_discovery(
        extra_companies=extra_companies,
        max_queries=max_queries,
        progress_callback=progress_callback,
    )
    logger.info("Hidden signals discovered: %d", len(signals))
    return signals


def enrich_signals_as_opportunities(signals: list[dict]) -> list[dict]:
    """Persist signals in the unified opportunity store."""
    from storage.opportunity_store import get_opportunity_store

    store = get_opportunity_store()
    out = []
    for sig in signals:
        try:
            store.upsert_from_signal(sig)
            out.append(sig)
        except Exception as exc:
            logger.debug("Signal opportunity upsert failed: %s", exc)
    return out


def auto_push_signals_to_outreach(signals: list[dict]) -> tuple[int, int]:
    """
    Auto-queue HIGH/MEDIUM signals with profile URLs to outreach (pipeline path).
    Manual GUI push remains available as override.
    """
    from agents.hidden_opportunity_discovery import push_signals_to_outreach

    ids = [
        s["id"] for s in signals
        if s.get("signal_strength") in ("HIGH", "MEDIUM")
        and "linkedin.com/in/" in (s.get("post_url") or "").lower()
    ]
    if not ids:
        return 0, 0
    pushed, skipped, _ = push_signals_to_outreach(signal_ids=ids, skip_no_url=True)
    return pushed, skipped


def run_unified_cycle(
    *,
    hidden_market_enabled: Optional[bool] = None,
    extra_companies: Optional[list[str]] = None,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """
    Run hidden-market leg of the unified engine (called from orchestrator).
    Returns stats dict.
    """
    from config.engine_config import ENGINE_CONFIG

    enabled = hidden_market_enabled
    if enabled is None:
        cfg_val = ENGINE_CONFIG.get("hidden_market_enabled", 1)
        enabled = _env_flag("HIDDEN_MARKET_ENABLED", default=bool(cfg_val))

    stats = {"signals_found": 0, "opportunities_upserted": 0, "outreach_pushed": 0}

    if not enabled:
        logger.info("Hidden market discovery disabled (HIDDEN_MARKET_ENABLED=0)")
        return stats

    signals = discover_hidden_signals(
        extra_companies=extra_companies,
        progress_callback=progress_callback,
    )
    stats["signals_found"] = len(signals)

    enriched = enrich_signals_as_opportunities(signals)
    stats["opportunities_upserted"] = len(enriched)

    pushed, _ = auto_push_signals_to_outreach(enriched)
    stats["outreach_pushed"] = pushed

    return stats


def sync_jobs_to_opportunities(jobs: list[dict]) -> int:
    """After scoring, mirror job rows into opportunity store."""
    from storage.opportunity_store import get_opportunity_store

    store = get_opportunity_store()
    count = 0
    for job in jobs:
        try:
            store.upsert_from_job(job)
            count += 1
        except Exception as exc:
            logger.debug("Job opportunity sync failed: %s", exc)
    return count

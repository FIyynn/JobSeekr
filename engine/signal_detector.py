"""
Unified signal detection facade — expansion, leadership, vacancy triggers.

Delegates to existing search modules; single entry point for the pipeline.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("signal_detector")


def discover_expansion_signals(
    companies: Optional[list[str]] = None,
    max_results: int = 15,
) -> list[dict]:
    """Trigger A — funding, offices, acquisitions."""
    from agents.hidden_opportunity_discovery import run_signal_discovery
    extra = companies or []
    return run_signal_discovery(
        extra_companies=extra,
        max_queries=min(10, 5 + len(extra)),
        results_per_query=6,
    )


def discover_leadership_signals(
    companies: Optional[list[str]] = None,
    max_results: int = 15,
) -> list[dict]:
    """Trigger B — new VP/Director/Head appointments (via web search)."""
    try:
        from agents.web_signal_discovery import search_public_web
        queries = [
            'site:linkedin.com/posts "excited to announce" "Head of" Abu Dhabi',
            'site:linkedin.com/posts "joining as" Director Dubai',
        ]
        if companies:
            for c in companies[:5]:
                queries.append(f'"{c}" "new" "Head of" OR "VP" site:linkedin.com/posts')
        results = []
        for q in queries[:8]:
            try:
                hits = search_public_web(q, max_results=5)
                results.extend(hits or [])
            except Exception:
                continue
        return results[:max_results]
    except Exception as exc:
        logger.debug("Leadership signal search failed: %s", exc)
        return []


def discover_vacancy_signals(
    companies: Optional[list[str]] = None,
    max_results: int = 15,
) -> list[dict]:
    """Trigger C — hiring posts before formal listings."""
    try:
        from agents.web_signal_discovery import discover_hiring_signals
        from agents.search_planner import resolve_search_queries
        from config.config import SEARCH_QUERIES, OLLAMA_MODEL, OLLAMA_BASE_URL

        queries = resolve_search_queries(
            SEARCH_QUERIES,
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            run_focus="",
        )
        return discover_hiring_signals(
            queries=queries,
            days_fresh=7,
            max_results=max_results,
        )
    except Exception as exc:
        logger.debug("Vacancy signal search failed: %s", exc)
        return []


def discover_all_signals(
    *,
    companies: Optional[list[str]] = None,
    include_hidden_db: bool = True,
) -> dict:
    """
    Run all signal detectors and return categorized results.
    """
    out = {
        "expansion": discover_expansion_signals(companies),
        "leadership": discover_leadership_signals(companies),
        "vacancy": discover_vacancy_signals(companies),
    }
    if include_hidden_db:
        from engine.pipeline import discover_hidden_signals
        out["hidden_posts"] = discover_hidden_signals(extra_companies=companies)
    return out

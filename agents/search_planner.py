"""
Turn a natural-language search prompt into JobSpy query list.
"""

import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger("search_planner")

DEFAULT_LOCATIONS = ("Abu Dhabi", "Dubai", "UAE", "DIFC")


def expand_search_prompt(
    prompt: str,
    model: str,
    base_url: str,
    max_queries: int = 12,
) -> list[dict]:
    """Convert free-text search instructions into [{term, location}, ...]."""
    prompt = (prompt or "").strip()
    if not prompt:
        return []

    llm_prompt = f"""Convert this job search instruction into LinkedIn search queries for UAE/GCC.
Return ONLY a JSON array of objects with "term" and "location" keys.
Max {max_queries} queries. Use locations: Abu Dhabi, Dubai, UAE, DIFC, Qatar, Riyadh when relevant.

Instruction:
{prompt[:2000]}

Example: [{{"term": "quantitative researcher", "location": "Abu Dhabi"}}]
"""
    try:
        if "qwen3" in model.lower():
            llm_prompt = llm_prompt.rstrip() + "\n/no_think"
        r = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": llm_prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 800},
            },
            timeout=90,
        )
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return _fallback_from_prompt(prompt)
        data = json.loads(m.group())
        out = []
        for item in data[:max_queries]:
            if isinstance(item, dict) and item.get("term"):
                out.append({
                    "term": str(item["term"]).strip(),
                    "location": str(item.get("location") or "UAE").strip(),
                })
        return out or _fallback_from_prompt(prompt)
    except Exception as e:
        logger.warning(f"Search prompt expand failed: {e}")
        return _fallback_from_prompt(prompt)


def _fallback_from_prompt(prompt: str) -> list[dict]:
    """Use non-empty lines as search terms."""
    out = []
    for line in prompt.splitlines():
        line = line.strip().strip("-*")
        if line and not line.startswith("#"):
            out.append({"term": line[:80], "location": "UAE"})
    return out[:10]


def resolve_search_queries(
    default_queries: list[dict],
    model: str = "",
    base_url: str = "",
    run_focus: str = "",
) -> list[dict]:
    """
    Priority:
    1. Explicit ## Search queries lines (term | location)
    2. ## Custom search prompt expanded via LLM
    3. defaults from config.py
    """
    from config.md_loader import (
        load_search_queries_from_requirements,
        load_custom_search_prompt,
    )

    explicit = load_search_queries_from_requirements()
    base_queries = None
    if explicit:
        logger.info(f"Using {len(explicit)} search queries from requirements file")
        base_queries = explicit

    nl = load_custom_search_prompt()
    if base_queries is None and nl and model and base_url:
        expanded = expand_search_prompt(nl, model, base_url)
        if expanded:
            logger.info(f"Expanded search prompt into {len(expanded)} queries")
            base_queries = expanded

    if base_queries is None:
        base_queries = list(default_queries)

    focus_queries = []
    if run_focus and model and base_url:
        focus_queries = expand_search_prompt(run_focus, model, base_url, max_queries=8)
        if focus_queries:
            logger.info(f"Run focus added {len(focus_queries)} search queries")

    if not focus_queries:
        return base_queries

    merged = []
    seen = set()
    for item in [*focus_queries, *base_queries]:
        key = ((item.get("term") or "").lower(), (item.get("location") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged

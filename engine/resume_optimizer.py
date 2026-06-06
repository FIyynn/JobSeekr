"""
Per-job resume optimization — reorder existing content, ATS keyword estimate (no fabrication).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("resume_optimizer")


def extract_job_keywords(description: str, max_keywords: int = 30) -> list[str]:
    """Extract likely ATS keywords from a job description."""
    text = (description or "").lower()
    tokens = re.findall(r"[a-z][a-z0-9+#.]{2,}", text)
    stop = {
        "the", "and", "for", "with", "you", "our", "will", "have", "this",
        "that", "from", "your", "are", "job", "role", "team", "work",
    }
    freq: dict[str, int] = {}
    for t in tokens:
        if t in stop or len(t) < 3:
            continue
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in ranked[:max_keywords]]


def estimate_ats_match_score(resume_text: str, keywords: list[str]) -> float:
    """0–100 keyword overlap estimate."""
    if not keywords:
        return 50.0
    resume = (resume_text or "").lower()
    hits = sum(1 for k in keywords if k in resume)
    return min(100.0, round(100.0 * hits / max(len(keywords), 1) * 1.2, 1))


def reorder_resume_bullets(resume_text: str, keywords: list[str]) -> str:
    """
    Reorder bullet blocks to surface keyword-relevant lines first.
    Does not add or fabricate content.
    """
    if not resume_text or not keywords:
        return resume_text
    blocks = [b.strip() for b in re.split(r"\n\s*\n", resume_text) if b.strip()]
    if len(blocks) <= 1:
        return resume_text

    def score_block(block: str) -> int:
        low = block.lower()
        return sum(1 for k in keywords if k in low)

    ranked = sorted(blocks, key=score_block, reverse=True)
    return "\n\n".join(ranked)


def optimize_resume_for_job(
    job: dict,
    *,
    resume_path: Optional[str] = None,
) -> dict:
    """
    Return optimization metadata and optional reordered text path.
    PDF generation deferred — returns text artifact path when possible.
    """
    from config.apply_agent_rules import get_resume_path

    path = resume_path or get_resume_path()
    description = job.get("description") or ""
    keywords = extract_job_keywords(description)

    resume_text = ""
    try:
        from agents.profile_manager import extract_resume_text
        resume_text = extract_resume_text(path) or ""
    except Exception as exc:
        logger.debug("Resume text extract failed: %s", exc)

    ats_score = estimate_ats_match_score(resume_text, keywords)
    reordered = reorder_resume_bullets(resume_text, keywords)

    out_dir = Path(__file__).resolve().parent.parent / "data" / "tailored_resumes"
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = job.get("id") or job.get("job_id") or "job"
    text_path = out_dir / f"tailored_{job_id}.txt"
    if reordered:
        text_path.write_text(reordered, encoding="utf-8")

    return {
        "ats_score_estimate": ats_score,
        "keywords_matched": sum(1 for k in keywords if k in resume_text.lower()),
        "keywords_total": len(keywords),
        "keywords": keywords[:15],
        "source_resume": path,
        "tailored_text_path": str(text_path) if text_path.exists() else "",
        "fabrication": False,
    }


def attach_resume_optimization(job: dict) -> dict:
    """Hook for apply pipeline — sets job['_resume_optimization']."""
    job = dict(job)
    job["_resume_optimization"] = optimize_resume_for_job(job)
    return job

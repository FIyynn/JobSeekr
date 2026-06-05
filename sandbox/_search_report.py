"""
Search-function test harness for JobHuntrr.

Exercises the REAL discovery pipeline (agents.discovery.discover_jobs) the same
way orchestrator.py does, across every target role family, and writes a
human-readable markdown report for external judging.
"""
import sys, os, json, time, datetime, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

from config.env_settings import bootstrap_settings
bootstrap_settings()

from config.config import (
    SEARCH_QUERIES, SEARCH_SITES, SEARCH_HOURS_FRESH,
    BLOCKED_COMPANIES, BLOCKED_KEYWORDS, BLOCKED_JOB_TITLES, MAX_YEARS_REQUIRED,
    OLLAMA_MODEL, OLLAMA_BASE_URL,
)
from agents.discovery import discover_jobs
from agents.search_planner import resolve_search_queries

# Wider window than production 48h so the snapshot has enough jobs to judge.
HOURS_FRESH = int(os.getenv("SEARCH_TEST_HOURS", "168"))  # 7 days
PER_FAMILY_MAX = int(os.getenv("SEARCH_TEST_PER_FAMILY", "6"))

# Representative queries per target family (subset of the user's full list).
FAMILIES = {
    "Quant / Trading": [
        {"term": "quantitative researcher", "location": "Abu Dhabi"},
        {"term": "quantitative analyst", "location": "Dubai"},
        {"term": "trading analyst", "location": "DIFC"},
    ],
    "Investments / Private Capital": [
        {"term": "investment analyst", "location": "Abu Dhabi"},
        {"term": "private equity analyst", "location": "UAE"},
        {"term": "venture capital analyst", "location": "Dubai"},
    ],
    "AI / Data": [
        {"term": "data scientist", "location": "Abu Dhabi"},
        {"term": "machine learning engineer", "location": "UAE"},
        {"term": "AI engineer", "location": "Dubai"},
    ],
    "Space / Defense / Geospatial": [
        {"term": "space systems analyst", "location": "UAE"},
        {"term": "geospatial data scientist", "location": "UAE"},
        {"term": "robotics engineer", "location": "Abu Dhabi"},
    ],
    "Energy / Commodities / Climate": [
        {"term": "energy trading analyst", "location": "UAE"},
        {"term": "commodities analyst", "location": "UAE"},
        {"term": "sustainability analyst", "location": "Abu Dhabi"},
    ],
    "Fintech / Strategy / Product": [
        {"term": "fintech analyst", "location": "DIFC"},
        {"term": "strategy analyst", "location": "Abu Dhabi"},
        {"term": "product analyst", "location": "Dubai"},
    ],
}


def snippet(text, n=240):
    text = (text or "").replace("\n", " ").replace("\r", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return (text[:n] + "…") if len(text) > n else text


def main():
    t0 = time.time()
    out = io.StringIO()
    w = out.write

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    w(f"# JobHuntrr — Search Function Test Report\n\n")
    w(f"_Generated: {now} (local) — live LinkedIn discovery via JobSpy_\n\n")

    # ── Part 1: query resolver ────────────────────────────────────────────────
    resolved = resolve_search_queries(SEARCH_QUERIES, model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    w("## 1. Query resolver\n\n")
    w(f"- `resolve_search_queries()` returned **{len(resolved)} queries**.\n")
    src = "requirements file (`## Search queries`)" if len(resolved) != len(SEARCH_QUERIES) else "config defaults"
    w(f"- Source: **{src}**.\n")
    w(f"- Production freshness window: **{SEARCH_HOURS_FRESH}h**; this test widened to **{HOURS_FRESH}h** for a richer sample.\n")
    w(f"- First 12 resolved queries:\n\n")
    for q in resolved[:12]:
        w(f"  - `{q['term']}` | {q['location']}\n")
    w("\n")

    # ── Part 2: discovery per family ──────────────────────────────────────────
    w("## 2. Live discovery results by target family\n\n")
    w("Each family runs the real `discover_jobs()` (JobSpy + prefilter + dedup + blocklists).\n\n")

    seen = set()
    all_jobs = []
    family_stats = {}
    for fam, queries in FAMILIES.items():
        ft0 = time.time()
        try:
            jobs = discover_jobs(
                queries=queries,
                sites=SEARCH_SITES,
                hours_fresh=HOURS_FRESH,
                blocked_companies=BLOCKED_COMPANIES,
                blocked_keywords=BLOCKED_KEYWORDS,
                blocked_titles=BLOCKED_JOB_TITLES,
                max_years=MAX_YEARS_REQUIRED,
                max_results=PER_FAMILY_MAX,
                dedup_seen=seen,
            )
        except Exception as e:
            jobs = []
            w(f"### {fam}\n\n> ERROR: {e}\n\n")
            continue
        family_stats[fam] = (len(jobs), round(time.time() - ft0, 1))
        for j in jobs:
            j["_family"] = fam
        all_jobs.extend(jobs)
        w(f"### {fam}  ({len(jobs)} jobs, {round(time.time()-ft0,1)}s)\n\n")
        w(f"Queries: " + "; ".join(f"`{q['term']} | {q['location']}`" for q in queries) + "\n\n")
        if not jobs:
            w("_No jobs returned in the window._\n\n")
            continue
        for j in jobs:
            w(f"- **{j['title']}** — {j['company']}\n")
            w(f"  - Location: {j['location'] or 'n/a'} | Posted: {j['date_posted'] or 'n/a'} | Apply: {j['apply_method']}\n")
            url = j.get('job_url_direct') or j.get('job_url')
            w(f"  - URL: {url}\n")
            if j.get("description"):
                w(f"  - Desc: {snippet(j['description'])}\n")
            w("\n")

    # ── Part 3: summary ───────────────────────────────────────────────────────
    w("## 3. Summary\n\n")
    w(f"- Total unique jobs discovered across families: **{len(all_jobs)}**\n")
    w(f"- Families with zero results: {[f for f,(n,_) in family_stats.items() if n==0] or 'none'}\n")
    w(f"- Total wall time: {round(time.time()-t0,1)}s\n\n")
    w("| Family | Jobs | Time (s) |\n|---|---|---|\n")
    for fam, (n, t) in family_stats.items():
        w(f"| {fam} | {n} | {t} |\n")
    w("\n")

    # Company spread (signal for relevance/alternatives)
    from collections import Counter
    comp = Counter(j["company"] for j in all_jobs)
    w("### Company spread (top 15)\n\n")
    for c, n in comp.most_common(15):
        w(f"- {c}: {n}\n")
    w("\n")

    # ── Write files ───────────────────────────────────────────────────────────
    report_md = out.getvalue()
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SEARCH_TEST_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandbox", "_search_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_jobs, f, indent=2, ensure_ascii=False, default=str)

    print(f"Wrote report: {report_path}  ({len(report_md)} chars)")
    print(f"Wrote raw JSON: {json_path}  ({len(all_jobs)} jobs)")
    print(f"Done in {round(time.time()-t0,1)}s")


if __name__ == "__main__":
    main()

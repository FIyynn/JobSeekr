"""
Re-score jobs in Notion using updated fit rules (fresh grad, AI-agent filter, skill match).
Updates Decision, Score, Fit Reason, and Skip Reason on each row.

  python rescore_notion.py              # all rows with a Job URL
  python rescore_notion.py --auto-only  # only Decision = Auto Apply
  python rescore_notion.py --gcc-only
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("rescore_notion")

from config.env_settings import bootstrap_settings
bootstrap_settings()

import requests
from config.config import (
    CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS,
)
from agents.scorer import score_job
from agents.job_fit import prefilter_job
from apply_from_notion import HEADERS, NOTION_TOKEN, NOTION_DB_ID, dedupe_jobs

NOTION_DECISION_MAP = {
    "auto_apply": "Auto Apply",
    "manual_review": "Manual Review",
    "skip": "Skipped",
}


def fetch_notion_jobs(auto_only: bool) -> list[dict]:
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {"page_size": 100}
    if auto_only:
        payload["filter"] = {
            "property": "Decision",
            "select": {"equals": "Auto Apply"},
        }
    jobs = []
    cursor = None
    while True:
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(url, json=payload, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        for row in data.get("results", []):
            props = row.get("properties", {})

            def txt(key):
                p = props.get(key, {})
                if p.get("type") == "title":
                    return "".join(t["plain_text"] for t in p.get("title", []))
                if p.get("type") == "rich_text":
                    return "".join(t["plain_text"] for t in p.get("rich_text", []))
                if p.get("type") == "select":
                    s = p.get("select")
                    return s["name"] if s else ""
                if p.get("type") == "url":
                    return p.get("url", "")
                return ""

            def checked(key):
                p = props.get(key, {})
                return bool(p.get("checkbox")) if p.get("type") == "checkbox" else False

            job_url = txt("Job URL")
            if not job_url:
                continue
            jobs.append({
                "notion_page_id": row["id"],
                "title": txt("Role"),
                "company": txt("Company"),
                "location": txt("Location"),
                "job_url": job_url,
                "description": txt("Fit Reason") or "",
                "fit_reason": txt("Fit Reason"),
                "skip_reason": txt("Skip Reason"),
                "applied": checked("Applied"),
            })
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return jobs


def update_notion_score(page_id: str, job: dict):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    decision = job.get("decision", "skip")
    props = {
        "Score": {"number": job.get("score", 0)},
        "Decision": {"select": {"name": NOTION_DECISION_MAP.get(decision, "Skipped")}},
        "Fit Reason": {
            "rich_text": [{"text": {"content": (job.get("fit_reason") or "")[:2000]}}]
        },
        "Skip Reason": {
            "rich_text": [{"text": {"content": (job.get("skip_reason") or "")[:2000]}}]
        },
    }
    angle = (job.get("positioning_angle") or "").strip()
    if angle:
        props["Positioning Angle"] = {"select": {"name": angle[:50]}}
    requests.patch(url, json={"properties": props}, headers=HEADERS, timeout=15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-only", action="store_true",
                        help="Only re-score rows currently marked Auto Apply")
    parser.add_argument("--gcc-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not NOTION_TOKEN or not NOTION_DB_ID:
        print("ERROR: set NOTION_TOKEN and NOTION_DATABASE_ID in Profile Settings")
        sys.exit(1)

    jobs = [
        job for job in dedupe_jobs(fetch_notion_jobs(args.auto_only))
        if not job.get("applied")
    ]
    if args.gcc_only:
        from apply_from_notion import filter_gcc_jobs
        jobs = filter_gcc_jobs(jobs)
    if args.limit > 0:
        jobs = jobs[: args.limit]

    print(f"Re-scoring {len(jobs)} Notion job(s)...")
    counts = {"auto_apply": 0, "manual_review": 0, "skip": 0}

    for i, job in enumerate(jobs, 1):
        title = job.get("title", "")
        company = job.get("company", "")
        print(f"\n[{i}/{len(jobs)}] {title} @ {company}")

        if not job.get("description") or len(job.get("description", "")) < 100:
            job["description"] = (
                f"Title: {title}\nCompany: {company}\n"
                f"Location: {job.get('location', '')}\n"
                f"URL: {job.get('job_url', '')}\n"
                "(Full description not stored in Notion — score from title/company.)"
            )

        blocked, reason = prefilter_job(job)
        if blocked:
            job.update(score=0, decision="skip", skip_reason=reason, fit_reason="")
            print(f"  -> SKIP (prefilter): {reason}")
        else:
            score_job(job, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS)
            print(f"  -> {job.get('score')}/100 {job.get('decision').upper()}")
            if job.get("fit_reason"):
                print(f"     Fit: {job['fit_reason'][:120]}")
            if job.get("skip_reason"):
                print(f"     Skip: {job['skip_reason'][:120]}")

        counts[job.get("decision", "skip")] = counts.get(job.get("decision", "skip"), 0) + 1
        if job.get("notion_page_id"):
            try:
                update_notion_score(job["notion_page_id"], job)
            except Exception as e:
                print(f"  Notion update failed: {e}")

    print("\n" + "=" * 50)
    print(f"  Auto Apply:     {counts.get('auto_apply', 0)}")
    print(f"  Manual Review:  {counts.get('manual_review', 0)}")
    print(f"  Skipped:        {counts.get('skip', 0)}")
    print("=" * 50)


if __name__ == "__main__":
    main()

"""
Apply to jobs already logged in Notion.
Reads all rows with Decision=Auto Apply and Applied=No, runs them through the form filler,
then updates Notion with the result.

Run: python apply_from_notion.py [--live]   (default: dry run)

Uses a SINGLE browser window for all jobs — LinkedIn jobs share one persistent session,
all other ATS jobs share one browser (tabs opened/closed per job, no new windows).
"""
import sys, os, argparse, logging

# Force UTF-8 so Unicode log chars (arrows, dashes) don't crash the Windows cp1252 console
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("apply_from_notion")

from pathlib import Path
from config.env_settings import bootstrap_settings
bootstrap_settings()

import requests

NOTION_TOKEN      = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID      = os.getenv("NOTION_DATABASE_ID", "")

from config.config import (
    CANDIDATE_PROFILE, APPLICATION_QA, RESUME_PATH,
    OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_VISION_MODEL,
)
OLLAMA_VISION     = OLLAMA_VISION_MODEL

LINKEDIN_EMAIL    = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()
from agents.form_filler import apply_jobs_batch
from agents.job_fit import prefilter_job

APPLICATION_QA["email"] = os.getenv("APPLICANT_EMAIL", APPLICATION_QA.get("email",""))
APPLICATION_QA["phone"] = os.getenv("APPLICANT_PHONE", APPLICATION_QA.get("phone",""))

BASE = Path(__file__).parent
RESUME_BY_ANGLE = {
    "quant":       str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "investments": str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "pe":          str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "finance":     str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "trading":     str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "energy":      str(BASE/"resumes"/"Resume_Rashed_Quant_Investment.pdf"),
    "ai":          str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "data":        str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "ml":          str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "research":    str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "space":       str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "defense":     str(BASE/"resumes"/"Resume_Rashed_AI_DataScience.pdf"),
    "cyber":       str(BASE/"resumes"/"Resume_Rashed_Tech_Startup.pdf"),
    "fintech":     str(BASE/"resumes"/"Resume_Rashed_Tech_Startup.pdf"),
    "startup":     str(BASE/"resumes"/"Resume_Rashed_Tech_Startup.pdf"),
    "strategy":    str(BASE/"resumes"/"Resume_Rashed_Tech_Startup.pdf"),
    "climate":     str(BASE/"resumes"/"Resume_Rashed_Tech_Startup.pdf"),
}
DEFAULT_RESUME = str(BASE / "Rashed_Alneyadi_Resume.pdf")

def _pick_resume(angle: str) -> str:
    key = (angle or "").lower().split("/")[0].strip()
    path = RESUME_BY_ANGLE.get(key, DEFAULT_RESUME)
    return path if Path(path).exists() else DEFAULT_RESUME

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def fetch_pending_jobs() -> list[dict]:
    """Fetch all Notion rows with Decision='Auto Apply' and Applied checkbox=false."""
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Decision", "select": {"equals": "Auto Apply"}},
                {"property": "Applied",  "checkbox": {"equals": False}},
            ]
        },
        "page_size": 100,
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
                if p.get("type") == "number":
                    return p.get("number")
                if p.get("type") == "url":
                    return p.get("url", "")
                return ""

            jobs.append({
                "notion_page_id":    row["id"],
                "title":             txt("Role"),
                "company":           txt("Company"),
                "location":          txt("Location"),
                "job_url":           txt("Job URL"),
                "score":             props.get("Score", {}).get("number") or 0,
                "decision":          "auto_apply",
                "positioning_angle": txt("Positioning Angle") or "investments",
                "applied":           False,
                "apply_notes":       "",
            })
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return jobs


GCC_LOCATION_KEYWORDS = (
    "uae", "united arab emirates", "dubai", "abu dhabi", "difc",
    "qatar", "doha", "saudi", "riyadh", "bahrain", "kuwait", "oman",
    "gcc", "emirate", "sharjah", "ajman",
)


def dedupe_jobs(jobs: list[dict]) -> list[dict]:
    """One entry per Job URL (Notion often has duplicate rows)."""
    seen: set[str] = set()
    out = []
    for job in jobs:
        key = (job.get("job_url") or "").strip().lower()
        if not key:
            key = f"{job.get('title', '')}|{job.get('company', '')}".lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def filter_gcc_jobs(jobs: list[dict]) -> list[dict]:
    """Keep only jobs whose Location mentions GCC / UAE / Qatar."""
    kept = []
    for job in jobs:
        loc = (job.get("location") or "").lower()
        if any(kw in loc for kw in GCC_LOCATION_KEYWORDS):
            kept.append(job)
    return kept


def prompt_linkedin_credentials() -> tuple[str, str]:
    """Return configured credentials without blocking unattended runs."""
    email = LINKEDIN_EMAIL
    password = LINKEDIN_PASSWORD
    if email and password:
        print(f"LinkedIn: using credentials from profile settings ({email})")
        return email, password
    logger.warning("LinkedIn credentials missing - relying on saved session or skipping LinkedIn jobs")
    return email, password


def update_notion_row(page_id: str, applied: bool, notes: str, decision: str = ""):
    """Update the Applied checkbox, Decision, and Notes field on a Notion row."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    props = {
        "Applied": {"checkbox": applied},
        "Notes":   {"rich_text": [{"text": {"content": (notes or "")[:2000]}}]},
    }
    if applied:
        props["Decision"] = {"select": {"name": "Applied"}}
    elif decision == "skip":
        props["Decision"] = {"select": {"name": "Skipped"}}
    elif decision == "manual_review":
        props["Decision"] = {"select": {"name": "Manual Review"}}
    try:
        r = requests.patch(
            url,
            json={"properties": props},
            headers={"Authorization": f"Bearer {NOTION_TOKEN}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        logger.error(f"  Notion update failed: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Actually submit applications (default: dry run)")
    parser.add_argument("--gcc-only", action="store_true",
                        help="Only apply to jobs in UAE/GCC/Qatar (by Location field)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max jobs to process (0 = all)")
    parser.add_argument("--no-validate-fit", action="store_true",
                        help="Skip live fit validation before apply (not recommended)")
    args = parser.parse_args()
    dry_run = not args.live

    print("=" * 60)
    print(f"  Apply from Notion | Mode: {'LIVE' if args.live else 'DRY RUN'}")
    if args.gcc_only:
        print("  Filter: GCC / UAE / Qatar only")
    print("=" * 60)

    if not NOTION_TOKEN or not NOTION_DB_ID:
        print("ERROR: NOTION_TOKEN or NOTION_DATABASE_ID not set in Profile Settings")
        sys.exit(1)

    all_pending = fetch_pending_jobs()
    raw_count = len(all_pending)
    jobs = dedupe_jobs(all_pending)
    print(f"\nFound {raw_count} pending rows in Notion -> {len(jobs)} unique jobs")

    if args.gcc_only:
        before = len(jobs)
        jobs = filter_gcc_jobs(jobs)
        print(f"  GCC filter: {before} -> {len(jobs)} jobs\n")
    else:
        print()

    if not jobs:
        print("Nothing to apply to. Check Notion (Auto Apply + Applied=No) or filters.")
        return

    # Quick title/keyword prefilter before opening browser
    kept, skipped = [], []
    for job in jobs:
        blocked, reason = prefilter_job(job)
        if blocked:
            skipped.append((job, reason))
        else:
            kept.append(job)
    if skipped:
        print(f"  Prefilter removed {len(skipped)} job(s) (AI-agent / senior / blocklist):")
        for job, reason in skipped:
            print(f"    - {job['title']} @ {job['company']}: {reason}")
            if job.get("notion_page_id"):
                update_notion_row(
                    job["notion_page_id"], False,
                    f"Skipped (prefilter): {reason}",
                    decision="skip",
                )
    jobs = kept
    if not jobs:
        print("\nNo jobs left after prefilter. Run: python rescore_notion.py")
        return

    if args.limit > 0:
        jobs = jobs[: args.limit]
        print(f"  Processing first {len(jobs)} jobs after prefilter (--limit)\n")

    validate_fit = not args.no_validate_fit
    if validate_fit:
        print("  Live fit validation: ON (reads job page + re-scores with Ollama)\n")

    li_email, li_password = prompt_linkedin_credentials()

    # Attach per-job resume path before passing to batch filler
    for job in jobs:
        angle = job.get("positioning_angle", "investments")
        resume = _pick_resume(angle)
        job["_resume_path"] = resume
        print(f"  {job['title']} @ {job['company']}  |  angle={angle}  |  {Path(resume).name}")

    print(f"\nOpening ONE browser window for all {len(jobs)} jobs...\n")

    # Single-window batch apply — LinkedIn jobs share one persistent context,
    # all other jobs share one browser (new tab per job, no popup windows)
    qa = {**APPLICATION_QA}
    results = apply_jobs_batch(
        jobs=jobs,
        qa=qa,
        candidate_profile=CANDIDATE_PROFILE,
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        dry_run=dry_run,
        headless=False,
        linkedin_email=li_email,
        linkedin_password=li_password,
        vision_model=OLLAMA_VISION,
        validate_fit=validate_fit,
    )

    # Summarise + update Notion
    counts = {"applied": 0, "dry_run": 0, "failed": 0, "manual": 0}
    for job in results:
        title   = job.get("title",   "Unknown Role")
        company = job.get("company", "Unknown Company")
        notes   = job.get("apply_notes", "")
        applied = job.get("applied", False)

        if applied:
            status = "APPLIED"
            counts["applied"] += 1
        elif dry_run:
            status = "DRY-RUN"
            counts["dry_run"] += 1
        elif job.get("decision") == "manual_review":
            status = "MANUAL"
            counts["manual"] += 1
        else:
            status = "FAILED"
            counts["failed"] += 1

        print(f"  [{status}] {title} @ {company}: {notes}")

        if job.get("notion_page_id"):
            update_notion_row(job["notion_page_id"], applied, notes)

    print()
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    if dry_run:
        print(f"  Dry-run filled:  {counts['dry_run']}")
        print(f"  Failed:          {counts['failed']}")
        print(f"\n  Screenshots saved to logs/")
        print(f"  Re-run with --live to submit for real.")
    else:
        print(f"  Submitted:       {counts['applied']}")
        print(f"  Manual review:   {counts['manual']}")
        print(f"  Failed:          {counts['failed']}")

if __name__ == "__main__":
    main()

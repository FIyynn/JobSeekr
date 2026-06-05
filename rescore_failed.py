"""
Rescore all jobs that failed JSON parsing (score=50, fit_reason contains "Could not parse").
Also rescores jobs where decision doesn't match the score thresholds.

Run with:  python rescore_failed.py
(Close the GUI first so the DB isn't locked.)

Pass --all to rescore every non-applied job (useful after prompt changes).
"""
import sys, os, sqlite3, logging, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rescore_failed")

from config.config import OLLAMA_MODEL, OLLAMA_BASE_URL, CANDIDATE_PROFILE, SCORE_THRESHOLDS
from agents.scorer import score_job

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "jobs.db")


def fetch_jobs(conn, rescore_all: bool) -> list[dict]:
    cur = conn.cursor()
    if rescore_all:
        cur.execute("""
            SELECT id, company, title, location, source, date_posted,
                   job_url, description, score, decision, fit_reason
            FROM jobs
            WHERE applied IS NOT 1 AND decision != 'applied'
            ORDER BY id DESC
        """)
    else:
        cur.execute("""
            SELECT id, company, title, location, source, date_posted,
                   job_url, description, score, decision, fit_reason
            FROM jobs
            WHERE fit_reason LIKE '%Could not parse%'
               OR fit_reason LIKE '%Scoring error%'
            ORDER BY id DESC
        """)
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]


def write_result(conn, job_id: int, result: dict):
    now = datetime.now().isoformat()
    cur = conn.cursor()
    cur.execute("""
        UPDATE jobs SET
            score=?, decision=?, fit_reason=?, skip_reason=?,
            positioning_angle=?, outside_target_industry=?, outside_target_reason=?,
            matches_stated_targets=?, suggested_alternate=?, alternate_suggestion_reason=?,
            updated_at=?
        WHERE id=?
    """, (
        result["score"],
        result["decision"],
        result.get("fit_reason", ""),
        result.get("skip_reason", ""),
        result.get("positioning_angle", "investments"),
        1 if result.get("outside_target_industry") else 0,
        result.get("outside_target_reason", ""),
        1 if result.get("matches_stated_targets", True) else 0,
        1 if result.get("suggested_alternate") else 0,
        result.get("alternate_suggestion_reason", ""),
        now,
        job_id,
    ))
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Rescore all non-applied jobs")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    jobs = fetch_jobs(conn, rescore_all=args.all)
    log.info(f"Found {len(jobs)} job(s) to rescore (--all={args.all})")

    for i, job in enumerate(jobs, 1):
        title = job["title"]
        company = job["company"]
        log.info(f"\n[{i}/{len(jobs)}] {title} @ {company} (was {job['score']}/{job['decision']})")
        result = score_job(job, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS)
        write_result(conn, job["id"], result)
        log.info(f"  → {result['score']}/100 {result['decision'].upper()} | {result.get('fit_reason','')[:80]}")

    conn.close()
    log.info(f"\nDone. Rescored {len(jobs)} job(s).")


if __name__ == "__main__":
    main()

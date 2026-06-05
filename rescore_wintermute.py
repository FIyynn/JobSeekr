"""
One-shot: insert Graduate Algorithmic Trader @ Wintermute and score it.
Run with:  python rescore_wintermute.py
(Close the GUI first so the DB isn't locked.)
"""
import sys, os, sqlite3, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("rescore_wintermute")

from config.config import OLLAMA_MODEL, OLLAMA_BASE_URL, CANDIDATE_PROFILE, SCORE_THRESHOLDS
from agents.scorer import score_job
from storage.db import get_db_path  # adjust if your db helper differs

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "jobs.db")

JOB_URL = "https://jobs.lever.co/wintermute-trading/06eb85da-c5b3-4e49-a906-8ac0f3452517"

DESCRIPTION = """
Wintermute is one of the largest crypto-native algorithmic trading companies in digital assets.
We provide liquidity algorithmically across most cryptocurrency exchanges and trading platforms,
a broad range of OTC trading solutions as well as support high profile blockchain projects and
traditional financial institutions moving into crypto. Founded 2017.

We are looking for an Algorithmic Trader with strong coding skills (Python) and a curiosity
about HFT, liquidity provision and crypto trading. You will eventually be responsible for your
own desk, developing your own trading algorithms and strategies.

After a short training period, you'll be tasked with improving existing strategies, adding new
trading products, and improving the technology behind our trading systems. You'll need to analyze
large amounts of trading and transaction data, generate insights, prioritize them and build
solutions based on your findings.

Hard Skills Requirements:
- Strong Python skills – 1-3 years of experience coding in Python (work, study or personal projects)
- Excellent quantitative and analytical skills
- Trading knowledge isn't required but strong curiosity to learn algorithmic, HFT, quantitative
  and liquidity provision trading is crucial

Other Requirements:
- Owner mentality – focus on ultimate result (short and long-term P&L for the company)
- Love problem solving; do whatever it takes
- Entrepreneurial mindset vs 9-to-5 mentality
- Non-standard working hours (24/7 crypto world)
- Determined, ambitious yet humble

Compensation & Benefits:
- Performance-based compensation with significant earning potential
- Equity incentives aligned with company
- Pension and private health insurance
- UK work permits and relocation support
"""


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Check if already exists
    cur.execute("SELECT id, score, decision FROM jobs WHERE job_url = ?", (JOB_URL,))
    row = cur.fetchone()

    from datetime import datetime
    now = datetime.now().isoformat()

    job = {
        "title": "Graduate Algorithmic Trader 2026",
        "company": "Wintermute",
        "location": "London, UK",
        "date_posted": "2025-05-23",
        "source": "lever",
        "job_url": JOB_URL,
        "description": DESCRIPTION.strip(),
    }

    if row:
        job_id = row["id"]
        log.info(f"Job already in DB (id={job_id}), current score={row['score']} decision={row['decision']}. Re-scoring...")
    else:
        cur.execute(
            """INSERT INTO jobs (company, title, location, score, decision, source,
               date_posted, discovered_at, job_url, description, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job["company"], job["title"], job["location"],
             0, "pending", job["source"], job["date_posted"], now,
             JOB_URL, job["description"], now, now),
        )
        conn.commit()
        job_id = cur.lastrowid
        log.info(f"Inserted new job with id={job_id}")

    conn.close()

    # Score it
    log.info(f"Scoring with {OLLAMA_MODEL}...")
    result = score_job(job, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS)

    print(f"\n{'='*60}")
    print(f"SCORE:    {result['score']}/100")
    print(f"DECISION: {result['decision'].upper()}")
    print(f"FIT:      {result['fit_reason']}")
    print(f"ANGLE:    {result['positioning_angle']}")
    if result.get("skip_reason"):
        print(f"SKIP:     {result['skip_reason']}")
    print(f"OUTSIDE:  {result.get('outside_target_industry')}")
    print(f"BREAKDOWN:{result.get('score_breakdown')}")
    print(f"{'='*60}\n")

    # Write result back to DB
    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.cursor()
    cur2.execute(
        """UPDATE jobs SET score=?, decision=?, fit_reason=?, skip_reason=?,
           positioning_angle=?, outside_target_industry=?, outside_target_reason=?,
           matches_stated_targets=?, suggested_alternate=?, alternate_suggestion_reason=?,
           job_profile_json=?, updated_at=?
           WHERE id=?""",
        (
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
            str(result.get("score_breakdown", {})),
            datetime.now().isoformat(),
            job_id,
        ),
    )
    conn2.commit()
    conn2.close()
    log.info(f"DB updated for id={job_id}: {result['score']}/100 → {result['decision'].upper()}")


if __name__ == "__main__":
    main()

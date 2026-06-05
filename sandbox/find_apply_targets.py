"""Find pending jobs suitable for Easy Apply / Greenhouse / Workday tests."""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "data" / "jobs.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

# Already applied DRW?
applied = conn.execute(
    "SELECT id, title, company, applied, apply_notes FROM jobs WHERE applied = 1"
).fetchall()
print("=== APPLIED ===")
for r in applied:
    print(r["id"], r["title"][:40], r["apply_notes"][:60] if r["apply_notes"] else "")

# GCC linkedin pending
rows = conn.execute(
    """
    SELECT id, title, company, location, job_url, apply_notes, score
    FROM jobs
    WHERE job_url LIKE '%linkedin.com%'
      AND (applied IS NULL OR applied = 0)
      AND location LIKE '%UAE%' OR location LIKE '%Dubai%' OR location LIKE '%Abu Dhabi%'
    ORDER BY score DESC
    LIMIT 20
    """
).fetchall()
print("\n=== GCC LINKEDIN PENDING ===")
for r in rows:
    print(r["id"], r["score"], r["company"], r["title"][:40])
    print(" ", r["apply_notes"] or "")

# Notes mentioning greenhouse/workday
for kw in ("greenhouse", "workday", "oracle", "external"):
    hits = conn.execute(
        "SELECT id, title, company, apply_notes FROM jobs WHERE apply_notes LIKE ? LIMIT 5",
        (f"%{kw}%",),
    ).fetchall()
    if hits:
        print(f"\n=== notes contain '{kw}' ===")
        for r in hits:
            print(r["id"], r["title"][:35], (r["apply_notes"] or "")[:80])

conn.close()

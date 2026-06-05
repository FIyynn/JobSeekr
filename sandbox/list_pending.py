"""List pending apply jobs for test selection."""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "data" / "jobs.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT id, title, company, location, decision, applied, apply_notes, job_url, score
    FROM jobs
    WHERE decision IN ('auto_apply', 'manual_review')
      AND (applied IS NULL OR applied = 0)
    ORDER BY score DESC
    LIMIT 40
    """
).fetchall()
for r in rows:
    loc = (r["location"] or "")[:35]
    co = (r["company"] or "")[:22]
    ti = (r["title"] or "")[:45]
    url = (r["job_url"] or "")[:75]
    print(f"{r['id']}|{r['decision']}|{r['score']}|{loc}|{co}|{ti}")
    print(f"  {url}")
    if r["apply_notes"]:
        print(f"  notes: {(r['apply_notes'] or '')[:90]}")
conn.close()

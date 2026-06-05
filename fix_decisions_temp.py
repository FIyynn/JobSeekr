# -*- coding: utf-8 -*-
# Fix: upgrade manual_review -> auto_apply for jobs scoring >= 75
import sqlite3, pathlib, os

DB = pathlib.Path(os.path.dirname(os.path.abspath(__file__))) / "data" / "jobs.db"
print("DB path:", DB)

conn = sqlite3.connect(str(DB), timeout=15)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, title, company, score, decision FROM jobs"
    " WHERE score >= 75 AND decision = 'manual_review'"
    " AND (applied IS NULL OR applied = 0)"
    " ORDER BY score DESC"
).fetchall()

print("Jobs to upgrade to auto_apply:", len(rows))
for row in rows:
    print(" [%d] %s @ %s" % (row['score'], row['title'], row['company']))
    conn.execute(
        "UPDATE jobs SET decision='auto_apply', decision_display='Auto Apply' WHERE id=?",
        (row['id'],)
    )

conn.commit()
conn.close()
print("Done. %d jobs upgraded." % len(rows))
input("Press Enter to exit...")

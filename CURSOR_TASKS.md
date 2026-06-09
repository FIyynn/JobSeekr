# Cursor Tasks — Orchestrated by Claude

---

## Task 001 — DONE
Engine layer built. Truncated files fixed. All imports verified OK.

## Task 002/003 — SKIPPED
Migration requires shell execution which is blocked in non-interactive mode. User will run manually: `python engine/migrate_legacy.py`

---

## Task 004

**status: DONE**

### Report

**storage/opportunity_store.py**
- Line 110: added `warm_lead_score REAL DEFAULT 0.0` to `outreach_attempts` CREATE TABLE
- Lines 116–121: added migration guard (`ALTER TABLE outreach_attempts ADD COLUMN warm_lead_score ...`) in `_init_db`, wrapped in try/except

**engine/warm_lead.py**
- Lines 69–70: `enrich_contact_warmth` now assigns `warm_lead_score` as an explicit float clamped to 0–100

### Goal
Add a `warm_lead_score` field to the outreach table in `storage/opportunity_store.py` and expose it in `engine/warm_lead.py`.

### Details

1. In `storage/opportunity_store.py`, find the `outreach_attempts` table CREATE statement and add this column if it doesn't exist:
   ```sql
   warm_lead_score REAL DEFAULT 0.0
   ```
   Also add a migration guard using `ALTER TABLE ... ADD COLUMN` inside `_init_db` (wrapped in try/except so it doesn't fail if column already exists).

2. In `engine/warm_lead.py`, find the `enrich_contact_warmth` function (or equivalent) and make sure the returned dict includes `warm_lead_score` as a float 0-100.

3. Do NOT modify any other files.

### Report
List the exact lines changed in each file, then set status to DONE.

---

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
- `storage/opportunity_store.py` line 110: added `warm_lead_score REAL DEFAULT 0.0` to `outreach_attempts` CREATE TABLE
- `storage/opportunity_store.py` lines 116–121: migration guard `ALTER TABLE outreach_attempts ADD COLUMN warm_lead_score ...` in `_init_db`
- `engine/warm_lead.py` lines 69–70: `enrich_contact_warmth` sets `warm_lead_score` as explicit float clamped 0–100

---

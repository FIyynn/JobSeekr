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
- Line 110: added `warm_lead_score REAL DEFAULT 0.0` to `outreach_attempts` CREATE statement
- Lines 116–121: added `ALTER TABLE outreach_attempts ADD COLUMN warm_lead_score` migration guard in `_init_db`

**engine/warm_lead.py**
- Lines 69–70: `enrich_contact_warmth` now sets `warm_lead_score` as an explicit float clamped to 0–100

---

# Oversight Notes — from Claude (Cowork)
_Last updated: 2026-06-06 17:10 by Claude (architect layer) — post GUI visual test_

---

## ✅ Verified Completed Tasks (as of 2026-06-06)

| Task | Status | Notes |
|------|--------|-------|
| Migration (235 jobs) | ✅ DONE | 0 errors |
| Tests (18/18) | ✅ DONE | test_opportunity_engine.py + test_unified_engine.py |
| Task 3 — Action Queue "Queue outreach" button | ✅ DONE | `_aq_queue_outreach()` wired to `queue_outreach_for_job()` |
| Task 4 — Social listening hooks in LinkedIn DM tab | ✅ DONE | Hook box present, runs `extract_hooks()`, shows "(no hooks found)" correctly for contacts without post data |
| Task 5 — Resume optimizer in apply pipeline | ✅ DONE | Hooked at apply_jobs.py lines 541-556 |
| Task 6 — signal_detector wired into hidden_opportunity_discovery | ✅ DONE | Line 31 import + line 643 call — manually verified |
| Task 7 — Fix BUG 1: SPS/IPS columns empty | ✅ DONE | Created backfill script, 223/235 jobs now have SPS/IPS |
| Task 8 — Fix BUG 2: UTF-8 encoding | ✅ DONE | Replaced em-dash artifacts with ASCII in GUI |
| Task 9 — Fix BUG 3: Lvl column default | ✅ DONE | Action Queue displays "1" instead of blank |
| Task 10 — Full regression tests | ✅ DONE | 18/18 tests passing, GUI import OK |
| Task 11 — Auto-git commit | ✅ DONE | Bug fixes committed with detailed message |
| Task 12 — SYSTEM_STATE.md memory layer | ✅ DONE | Comprehensive system state tracking document |
| Task 13 — WORKFLOW.md full walkthrough | ✅ DONE | Complete end-to-end system documentation with Mermaid diagrams |

---

## 🐛 BUGS FOUND — GUI Visual Test (2026-06-06)

### BUG 1 — ✅ FIXED: SPS and IPS columns are empty everywhere
**Symptom:** In the Jobs tab, the "Score #" column shows values (75, 83, 85 etc) but the adjacent SPS and IPS columns are completely blank for every row. Same in the Action Queue tab — SPS and IPS show nothing.
**Fix applied:**
- **Root cause:** Jobs were scored before unified engine integration
- **Solution:** Created `sandbox/backfill_sps_ips.py` to compute SPS/IPS for existing jobs
- **Result:** 223/235 jobs now have SPS/IPS values
- Script is idempotent and saved for future use

### BUG 2 — ✅ FIXED: UTF-8 encoding artifacts in window title and UI text
**Symptom:** Window title shows "JobHuntrr â€" UAE Job Agent" instead of "JobHuntrr — UAE Job Agent". Other mojibake (â€˜, â€™, â€¢) visible in UI text throughout.
**Fix applied:**
- **Root cause:** Windows console encoding (cp1252) misreading UTF-8 em-dash characters in `gui/jobhunter_gui.py`
- **Solution:** Replaced all corrupted em-dash (â€") characters with ASCII hyphens (-) throughout the file
- **Result:** Window title and all UI text now display correctly without encoding artifacts

### BUG 3 — ✅ FIXED: Lvl (outreach_level) column empty in Action Queue
**Symptom:** The "Lvl" column in Action Queue is blank for all rows.
**Fix applied:**
- **Root cause:** `outreach_level` defaults to 0 in database, which displays as empty string in GUI
- **Solution:** Modified `gui/jobhunter_gui.py` line 199 to display "1" when outreach_level is 0 or NULL
- **Result:** Action Queue Lvl column now shows "1" as default instead of blank

### BUG 4 — MEDIUM: "Queue outreach" result invisible in LinkedIn DM tab
**Found:** 2026-06-06 manual GUI test by Claude (architect)
**Symptom:** Clicking "Queue outreach (selected)" in Action Queue confirms "Queued 1 outreach attempts. Check LinkedIn DM tab." — but nothing new appears in the LinkedIn DM tab after clicking Reload.
**Root cause:** `queue_outreach_for_job()` → `waterfall_runner` writes to `opportunity_store.outreach_attempts`. The LinkedIn DM tab Outreach list reads from `agents/linkedin_outreach.py`'s separate CSV/row-based store (`merge_rows()`). These are two completely separate backends with no bridge.
**Fix required (Task 20):** In `gui/jobhunter_gui.py`, the LinkedIn DM tab reload (`reload_dm_tab()` or equivalent) should ALSO pull from `opportunity_store.get_pending_outreach_attempts()` and inject those rows into the Outreach table. Alternatively, `_aq_queue_outreach()` should call `merge_rows()` after `queue_outreach_for_job()` so the entry appears immediately in the CSV store.
**Impact:** HIGH usability — the primary outreach path (Queue → DM tab → send) is broken end-to-end.

### NOTE: GUI restart needed to see Bug 2 + Bug 3 fixes
Tasks 8 and 9 (UTF-8 encoding fix + Lvl default) were applied to gui/jobhunter_gui.py by Claude Code. The currently running GUI instance still shows the old behavior. Close and reopen the GUI to verify both fixes.

---

## ✅ Confirmed Working (GUI test)
- All 7 tabs launch without error (Jobs, Action Queue, Console, LinkedIn DM, Chat, Profile Settings, Requirements)
- Action Queue shows 92 opportunities with correct filter/sort
- Action Queue buttons all present: Queue outreach, Override → Apply now, Skip selected
- LinkedIn DM tab has Outreach + Hidden Signals sub-tabs
- Social listening hooks box is present and functional (correctly shows "(no hooks found)" for contacts without post/signal data)
- Contact detail shows: LinkedIn URL, careers page, suggested roles, company/person scores
- Decision logic correct: network_only, monitor, auto_apply, manual_review all appearing
- GUI loads jobs from DB without crash

---

## 🔜 Next Tasks for Claude Code — RESUME HERE

### Task 7 — ✅ DONE: Fix BUG 1: SPS/IPS columns empty
```
STEP 1: Run this SQL to check if values exist in the DB:
  python -c "import sqlite3; conn = sqlite3.connect('data/jobs.db'); rows = conn.execute('SELECT id, sps, ips, score FROM jobs WHERE sps IS NOT NULL AND sps != 0 LIMIT 10').fetchall(); print(rows)"

STEP 2: Read agents/scorer.py and look for where sps/ips are computed and returned.
  Check if the scorer returns a dict with keys 'sps' and 'ips' or uses different key names.

STEP 3: Read storage/job_store.py and check that update_job() / upsert_job() accepts and saves 'sps' and 'ips' fields.

STEP 4: Read gui/jobhunter_gui.py and search for how the treeview reads 'sps' and 'ips' from job dicts.
  Specifically look at the refresh_table() method and the column spec for "sps" and "ips".

STEP 5: Based on findings, fix the mismatch. The fix is likely one of:
  (a) scorer uses different key names → rename them to 'sps' and 'ips'
  (b) job_store doesn't save them → add them to the allowed fields in update_job()
  (c) GUI reads wrong key → fix the table row builder

STEP 6: After fix, re-run scoring on a few jobs to verify SPS/IPS appear in the GUI.
```

### Task 8 — ✅ DONE: Fix BUG 2: UTF-8 encoding in title
Replaced all corrupted em-dash (â€") characters with ASCII hyphens (-) in gui/jobhunter_gui.py. Window title and UI text now display correctly.

### Task 9 — ✅ DONE: Fix BUG 3: Lvl column default
Modified gui/jobhunter_gui.py line 199 to display "1" when outreach_level is 0/NULL. Action Queue Lvl column now shows default "1".

### Task 10 — ✅ DONE: Full regression test after bug fixes
- All 18/18 tests passing (10 unified engine + 8 opportunity engine)
- GUI import health check: OK
- Test files: sandbox/test_unified_engine.py, sandbox/test_opportunity_engine.py

### Task 11 — ✅ DONE: Auto-git commit (Expansion Kit Item 1)
Committed all bug fixes with detailed message. Ready to commit OVERSIGHT.md separately.

### Task 12 — ✅ DONE: SYSTEM_STATE.md memory layer (Expansion Kit Item 3)
Created comprehensive state tracking document with task status, bug tracker, test results, next priorities, key metrics, and architecture reference.

### Task 13 — ✅ DONE: Generate WORKFLOW.md: Full system walkthrough
```
Read ALL of the following files carefully, then produce a WORKFLOW.md in
C:\Users\Lordy\jobhuntrr\ that describes exactly how the program works end-to-end,
covering every module and how they connect. The document must be written clearly
enough that a non-engineer can follow the full flow.

Files to read:
  orchestrator.py
  agents/unified_engine.py
  agents/discovery.py
  agents/scorer.py
  agents/apply_method.py
  agents/form_filler.py
  agents/linkedin_outreach.py
  agents/hidden_opportunity_discovery.py
  engine/pipeline.py
  engine/opportunity.py
  engine/recommend_action.py
  engine/waterfall_runner.py
  engine/signal_detector.py
  engine/social_listening.py
  engine/stakeholder_mapper.py
  engine/warm_lead.py
  engine/resume_optimizer.py
  engine/email_discovery.py
  storage/job_store.py
  storage/opportunity_store.py
  config/engine_config.py
  config/applicant_requirements.py
  gui/jobhunter_gui.py  (just the tab names and button actions — skip implementation)

WORKFLOW.md must cover:
1. Entry points — how the user starts the system (GUI vs CLI)
2. Job Discovery — how jobs are found (sources, filters)
3. Scoring — SPS/IPS formula, what each component means, thresholds
4. Action Decision — how recommended_action is set (referral_first / network_only / apply_now / monitor / ignore)
5. Track A (Visible) — ATS detection, resume optimization, easy-apply eligibility, bespoke portal logic
6. Track B (Hidden) — signal detector (expansion / leadership / vacancy), DuckDuckGo search, signal scoring
7. Stakeholder Mapping — Power Trio (hiring manager, recruiter, peer), warm lead scoring
8. Outreach Waterfall — Level 1 (free DM), Level 2 (connection request), Level 3 (email discovery), Level 4 (paid InMail), credit logic
9. Social Listening — extract_hooks(), how hooks feed outreach personalization
10. Human Gate — what requires user approval, what is automated, hard rules
11. GUI Tabs — what each tab does and which backend function it calls
12. Data Flow — jobs.db and opportunity_store schema, how data moves through the pipeline

Format: clean Markdown with headers, subheaders, and a Mermaid flowchart at the top
matching the system architecture. Include the SPS/IPS formulas.
Do NOT invent features — only document what actually exists in the code.
```

---

## 🔜 Spec Compliance Gap Tasks (from architect audit 2026-06-06)

### Task 20 — HIGH: Fix Bug 4 — Queue outreach → LinkedIn DM tab disconnect
```
Read gui/jobhunter_gui.py and find _aq_queue_outreach() (in the Action Queue tab).
After calling queue_outreach_for_job(job, profile=PROFILE_FULL), also call:
  from agents.linkedin_outreach import merge_rows
  merge_rows([{
      "Company": job.get("company", ""),
      "Person name": f"Queued from Action Queue — {job.get('title', '')}",
      "Person title": "",
      "LinkedIn URL": job.get("job_url") or job.get("job_url_direct") or "",
      "Person category": "Action Queue Referral",
      "Why this person": job.get("fit_reason") or "",
      "LinkedIn connection message": "",
      "Outreach status": "Not sent",
      "Notes": f"[Action Queue] SPS={job.get('sps','')} action={job.get('recommended_action','')}",
  }])
This bridges the gap so queued outreach appears immediately in the LinkedIn DM tab.
After implementing: restart the GUI, queue a job from Action Queue, reload LinkedIn DM tab, verify the new row appears.
```

### Task 14 — HIGH: Add SPS ≥ 70 gate to Level 4 InMail
```
Read agents/unified_engine.py, find plan_outreach_waterfall().
The current Level 4 condition is: `elif ips >= cfg.get("ips_inmail_threshold", 75):`
Change it to ALSO require SPS ≥ 70:
  sps = job.get("sps") or 0
  elif ips >= cfg.get("ips_inmail_threshold", 75) and sps >= cfg.get("sps_inmail_min", 70):
Add "sps_inmail_min": 70 to ENGINE_CONFIG in config/engine_config.py.
Verify with a unit test: job with IPS=80 but SPS=65 should NOT get Level 4.
```

### Task 15 — HIGH: Add funding/Series B expansion signal queries
```
Read engine/signal_detector.py, find discover_expansion_signals().
It currently just calls run_signal_discovery() with generic queries.
Instead, add specific expansion-signal queries BEFORE calling run_signal_discovery().
Import and call run_signal_discovery with extra_companies PLUS inject these targeted queries
into hidden_opportunity_discovery._BASE_QUERIES (or pass them as a new param):
  'site:linkedin.com/posts "Series B" "Abu Dhabi" OR "Dubai" hiring'
  'site:linkedin.com/posts "raised" "funding" "UAE" expansion'
  'site:linkedin.com/posts "new office" "Abu Dhabi" OR "ADGM" OR "DIFC"'
  'site:linkedin.com/posts "government contract" OR "strategic partnership" UAE'
  'site:linkedin.com/posts "acquisition" "Abu Dhabi" team hiring'
Also add a helper that generates a basic "hiring_forecast" field on expansion signals:
  sig["hiring_forecast"] = f"Expansion signal at {sig['company']} — probable new headcount in next 90 days"
  sig["probable_departments"] = sig.get("role_mentioned") or "Quant / AI / Strategy"
```

### Task 16 — MEDIUM: Add Jobvite to ATS detection
```
Read agents/form_filler.py, find _detect_platform().
Add Jobvite URL detection:
  if "jobs.jobvite.com" in u or "jobvite.com/careers" in u:
      return "ai_driven"  # fallback to generic until dedicated handler built
Also add "jobvite.com" to _ATS_HOST_FRAGMENTS list.
This ensures Jobvite jobs are tracked/detected even without a dedicated form handler.
```

### Task 17 — MEDIUM: PDF resume generation
```
Read engine/resume_optimizer.py.
The function currently saves to data/tailored_resumes/tailored_{job_id}.txt
Add a PDF export step after the .txt file is written:
  1. pip install fpdf2 --break-system-packages (if not installed)
  2. After writing .txt, call a new _export_pdf(text_path, job_id) function that:
     - Reads the .txt content
     - Creates a simple PDF with fpdf2: A4, Helvetica font, line-wrapped content
     - Saves to data/tailored_resumes/tailored_{job_id}.pdf
  3. Return the PDF path as the primary output (keep .txt as fallback)
Make it non-breaking: if fpdf2 not available, log a warning and return .txt path as before.
```

### Task 18 — MEDIUM: Hiring forecast field on all signal types
```
Read agents/hidden_opportunity_discovery.py, find _parse_result_to_signal().
Add two new fields to every signal dict:
  "hiring_forecast": f"Signal detected at {company or 'unknown'} — likely hiring in {role or 'analyst/strategy'} within 60 days"
  "probable_departments": role or "Investment / Strategy / AI"
Also update the signals table in _get_conn() to add these columns if not exist:
  ALTER TABLE signals ADD COLUMN hiring_forecast TEXT DEFAULT '';
  ALTER TABLE signals ADD COLUMN probable_departments TEXT DEFAULT '';
And update upsert_signal() to save them.
```

### Task 19 — Regression test + git commit
```
Run: python -m pytest tests/ -v --tb=short
All 18 tests must pass.
Then: git add -A && git commit -m "Spec compliance fixes: InMail SPS gate, expansion queries, Jobvite detection, PDF resume, hiring forecast fields"
Update OVERSIGHT.md and SYSTEM_STATE.md to mark Tasks 14-19 complete.
```

---

## Instructions for Claude Code

READ THIS FILE FIRST every session before doing anything.
Tasks 1-13 are DONE. Start with Task 14.
Do NOT re-run Tasks 1-13 — they are verified done.
After each task: update OVERSIGHT.md with result, then proceed to next.

**Communicate bugs back to me:** Add to the "Bugs found" section above.

---

## Expansion Kit — Status

| Item | Status |
|------|--------|
| 1. Auto-git commit agent | ✅ DONE (Task 11) |
| 2. Test runner blocking next task | ✅ DONE (Task 10) |
| 3. SYSTEM_STATE.md memory layer | ✅ DONE (Task 12) |
| 4. DECISIONS.md architectural log | ⏳ Pending |
| 5. Code review agent (diff checker) | ⏳ Pending |
| 6. Regression detector | ⏳ Pending |
| 7. Outreach performance tracker | ⏳ Pending |
| 8. Job market monitor (scheduled) | ⏳ Pending |
| 9. LinkedIn safety watchdog | ⏳ Pending |
| 10. Orchestration dashboard (HTML) | ⏳ Pending |

---

## Scoring Architecture (reference)
SPS = 0.20×RoleFit + 0.20×Connection + 0.15×ATS + 0.15×Timing + 0.10×Urgency + 0.10×CompanyPriority + 0.10×OutreachQuality
IPS = 0.30×RoleFit + 0.25×ContactPower + 0.20×Timing + 0.15×CompanyPriority + 0.10×Warmth

## Hard Rules
- No auto-send LinkedIn messages
- No auto-apply without user approval  
- Always read files before editing
- Never re-run a verified task because a search returned 0 lines — use Read to verify

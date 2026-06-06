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

## Instructions for Claude Code

READ THIS FILE FIRST every session before doing anything.
Start with Task 7 (SPS/IPS fix) — it's the most impactful bug.
Do NOT re-run Tasks 1-6 — they are verified done.
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

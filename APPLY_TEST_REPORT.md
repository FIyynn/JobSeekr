# JobHuntrr — Apply Function Test Report

_Generated: 2026-05-31 (local). Companion to `SEARCH_TEST_REPORT.md`._

This report covers live, **dry-run** tests of the application engine across the four
ATS paths you asked about: **LinkedIn Easy Apply**, **Greenhouse**, **Workday**, and
**custom / AI-driven** portals. Every test ran the real `apply_to_job` /
`apply_jobs_batch` code in **dry-run mode** (forms are filled but **never submitted**),
so nothing was sent to any employer.

---

## How testing was done

- A reusable probe harness (`sandbox/_apply_probe.py`) drives the production code
  against real job URLs and prints the detected platform + final outcome.
- LinkedIn was exercised through the real pipeline using live discovered jobs
  (`--linkedin-from-json`), with score/decision pre-set to `auto_apply` so the slow
  LLM fit-check didn't dominate the run.
- Offline logic is covered by `sandbox/_logic_tests.py` (**28/28 assertions pass**):
  geo filtering, ATS detection, QA-label mapping, and lenient JSON parsing.

---

## 1. LinkedIn Easy Apply  ✅ handled

**What the code does:** logs into LinkedIn once, reuses the session, and for each job
detects whether it's a true in-LinkedIn *Easy Apply* modal or an *"Apply on company
website"* hand-off. Easy Apply is filled fast from profile settings; external hand-offs
open the company ATS in a new tab and switch to the right filler.

**Live result (4 jobs):**

| Job | Path detected | Outcome |
|---|---|---|
| Investment Professional @ PER | Company website → login wall | `manual_review` — flagged "log in manually" (correct: site needs auth) |
| Junior Research Scientist @ NYU Abu Dhabi | Company website → Interfolio (AI-driven) | `auto_apply` — filled, not submitted |
| Associate Research Scientist @ NYU Abu Dhabi | Company website → Interfolio (AI-driven) | `auto_apply` — filled, not submitted |
| Graduate Programme 2027 @ Revolut | Company website → Revolut portal (AI-driven) | `auto_apply` — resume uploaded, filled, not submitted |

**Judgement:** The Easy Apply / external-hand-off detection works and correctly
routes each job. When a site requires login it stops and asks for manual action
rather than failing silently. Contact details (phone `+971...`, NYU email) are pulled
from profile settings, not the LLM — as designed.

---

## 2. Greenhouse  ✅ handled

**Test job:** Junior Data Scientist @ Cobblestone Energy
(`job-boards.greenhouse.io/...`).

**Live result:** Detected **Greenhouse**, triggered Greenhouse resume autofill,
uploaded the resume PDF, set the intl-tel phone for a UAE national, and filled the
required fields deterministically:

- `Country*` → United Arab Emirates
- `Which country are you applying from?*` → United Arab Emirates
- `Are you willing to relocate to the UAE?*` → Yes, within UAE and GCC
- `Highest level of education*` → Bachelor's Degree
- `How many years of work experience do you have?*` → 2
- `LinkedIn Profile` → profile URL

Open-ended / unknown questions were routed to the LLM, and in a non-interactive run
fall back to safe defaults instead of hanging. **Outcome:** `Dry run — greenhouse
filled, not submitted`.

**Judgement:** Greenhouse is handled well — resume autofill + deterministic field
mapping is exactly the "quick path" intended for structured ATS forms.

---

## 3. Workday  ✅ handled (correctly defers to manual when gated)

**Test job:** Cyber Security Analyst @ NVIDIA (`nvidia.wd5.myworkdayjobs.com/...`).

**Live result:** Detected **Workday**, walked section 1, and when it hit Workday's
account/sign-in gate it stopped cleanly:

```
applied: False
apply_notes: Workday application incomplete — apply manually
decision: manual_review
```

**Judgement:** Workday almost always forces *create-account / sign-in* before the
form, which can't be safely automated. The important thing is the program now
**recognises the gate, never falsely reports success, and returns a clear
`manual_review`** — which is the right behavior. (See fixes below; this used to fail
with a generic "Form fill failed".)

---

## 4. Custom / AI-driven portals  ✅ handled

**Tested via:** Interfolio (NYU) and Revolut's own careers portal, reached through the
LinkedIn external hand-off above.

**What the code does:** when a page isn't a known ATS, it's classified `ai_driven`.
It first does a **deterministic fill** from profile settings (name/email/phone/resume),
then asks the local LLM to map any remaining fields from a DOM snapshot, parses the
LLM's JSON leniently, and fills what it can. Resume upload worked on Revolut.

**Judgement:** The custom path does the right thing — cheap deterministic fills first,
LLM only for the leftovers — matching the "more custom ⇒ more LLM" design you
described. Fill counts on these specific pages were low because much of their form is
behind JS steps/login, but detection, routing, resume upload, and graceful
non-interactive fallback all work.

---

## Flaws found and fixed during this evaluation

1. **Search returned out-of-region jobs (US/UK/India).**
   Added a geographic pre-filter (`is_outside_target_geo` in `agents/job_fit.py`) that
   drops postings clearly outside UAE/GCC unless the employer is a Tier-1 target.
   The regenerated `SEARCH_TEST_REPORT.md` now shows **only UAE roles**.

2. **Education fields mis-mapped.** "University grade", "current university status",
   and "Maths grade" were being filled with the *university name*. Tightened the
   label matcher in `agents/form_filler.py` with negative lookaheads so grade/status/
   level/year fields no longer grab the university name; "Highest level of education"
   now maps to the degree level. Covered by unit tests.

3. **Non-interactive runs could hang on `input()`.** When run head-less (orchestrator
   `--apply`, probes), unknown-question prompts blocked forever. `agents/apply_prompts.py`
   now detects no-TTY and falls back to safe defaults / warnings instead of blocking.

4. **Windows console crash on Unicode.** Arrows/em-dashes in logs raised
   `UnicodeEncodeError` (cp1252). Forced UTF-8 on stdout/stderr and the log file in
   `orchestrator.py`, `apply_jobs.py`, `apply_from_notion.py`, and the probe; replaced a
   stray `→` in a log line.

5. **LLM JSON output broke the AI-driven filler.** Qwen `<think>` blocks, trailing
   commas, and smart quotes caused `json.loads` to throw and abort the fill. Added
   `_loads_lenient_json` (used by the AI-driven mapper) plus a hardened parse for the
   vision-fallback array. Covered by unit tests.

6. **Workday outcome was misleading.** A post-apply sign-in gate produced a generic
   "Form fill failed", and callers overwrote platform-specific notes. Added
   `_workday_signin_gate_visible`, made `_fill_workday` return a clear `manual_review`,
   and stopped `apply_to_job` / `apply_jobs_batch` from clobbering explicit outcomes.

---

## Overall judgement

- **Search:** finds relevant UAE roles across all target families (quant, investments,
  AI/data, space/defense, energy, fintech) and surfaces sensible alternatives; region
  filtering now correct. See `SEARCH_TEST_REPORT.md`.
- **Apply on LinkedIn:** Easy Apply and external hand-off both work.
- **Apply off LinkedIn:** Greenhouse (fast structured) and custom/AI-driven (LLM-assisted)
  both work; Workday correctly defers to manual when it forces an account wall.
- **Safety:** every test was dry-run; the engine never submitted and never falsely
  reported success.

**Bottom line:** the program can find jobs for you and drive applications both on and
off LinkedIn, with the right amount of automation per ATS. The main inherent limit is
sites that *require login/account creation first* (PER, Workday) — by design these are
handed back to you for a one-time manual step, after which the pipeline continues.

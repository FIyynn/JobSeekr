# JobHunter — UAE Autonomous Job Agent

**Zero cloud credits. Runs 24/7 on your machine with local Ollama (`qwen3:8b` by default).**

---

## Applicant config (markdown)

Edit before running:

- `data/applicant_profile.md` — who you are (skills, languages, experience)
- `data/applicant_requirements.md` — what you want (YAML thresholds + rules)

Templates: `data/applicant_profile.template.md`, `data/applicant_requirements.template.md`

See `APPLICANT.md` for details.

---

## What It Does

| Phase | What happens |
|---|---|
| **Discovery** | Searches LinkedIn, Indeed for fresh UAE jobs every 4 hours |
| **Scoring** | Local LLM scores each job 0–100 against your full target criteria |
| **Logging** | Every job saved to local SQLite (`data/jobs.db`) or optional Notion |
| **GUI** | `python gui/jobhunter_gui.py` — browse, filter, discover, apply |
| **Applying** | Playwright fills Greenhouse / Lever / Ashby forms + uploads your resume |

---

## Quick Setup (Windows)

### 1. Prerequisites

Make sure you have:
- **Python 3.11+** — https://python.org
- **Ollama** — https://ollama.com
- **Git** (optional)

Pull the default model and verify Ollama is running:
```
ollama pull qwen3:8b
ollama serve
ollama run qwen3:8b "test"
```

**Model choice:** set `"ollama_model"` in `data/profile_settings.json` (e.g. `qwen3:8b` for 8 GB RAM, `qwen2.5:14b` for 16 GB). No code changes needed to swap models.

---

### 2. Install dependencies

Open a terminal in this folder and run:

```cmd
pip install -r requirements.txt
playwright install chromium
```

---

### 3. Configure your .env file

Copy the template:
```cmd
copy .env.template .env
```

Edit `.env` and fill in:
```
NOTION_TOKEN=secret_YOUR_TOKEN
NOTION_DATABASE_ID=YOUR_DB_ID
APPLICANT_EMAIL=your@email.com
APPLICANT_PHONE=+971-XX-XXX-XXXX
```

**Getting your Notion token:**
1. Go to https://www.notion.so/my-integrations
2. Create a new integration → copy the "Internal Integration Token"
3. Go to the Notion page where you want the database
4. Click the `...` menu → Connections → add your integration

---

### 4. Create the Notion database

Find your Notion page ID from its URL:
```
https://notion.so/My-Workspace/My-Page-abc123def456...
                                         ^^^^^^^^^^^^^^^^ this is the page ID
```

Run:
```cmd
python setup_notion.py --page-id YOUR_PAGE_ID_HERE
```

Copy the printed database ID to your `.env` as `NOTION_DATABASE_ID`.

---

### 5. Test run (discovery + scoring only, no applications)

```cmd
python orchestrator.py --run-once
```

Check your Notion database — jobs should appear with scores and decisions.

---

### 6. Enable application filling (dry run)

Forms will be filled but NOT submitted:
```cmd
python orchestrator.py --run-once --apply
```

This opens a visible browser so you can watch it work. Check the
`logs/` folder for screenshots of filled forms.

---

### 7. Enable live submission

**⚠️ Only do this after you've reviewed dry-run screenshots.**

```cmd
python orchestrator.py --run-once --apply --live
```

Or for 24/7 scheduled operation:
```cmd
START_JOBHUNTER.bat
```

For AFK discovery, persisted queue retries, and live submissions:
```cmd
START_AUTONOMOUS.bat
```

CAPTCHA, MFA, and email-verification walls are deferred as `manual_review`
without blocking the remaining jobs.

Discovery uses LinkedIn Jobs through JobSpy plus public indexed hiring signals:
- LinkedIn employee / recruiter hiring posts are searched through the saved LinkedIn session and saved as `employee_post`.
- Direct ATS and company-careers openings found through Google Jobs or web search are saved as `web_indexed`.
- Set `GOOGLE_SEARCH_API_KEY` and `GOOGLE_SEARCH_CX` in Profile Settings to use Google Programmable Search.
- Without Google credentials, indexed discovery searches Google in a normal Chromium window, then falls back to Bing RSS and DuckDuckGo HTML.
- Employee posts without a direct application URL are held for manual review instead of entering the submit queue.

The same controls are available in the GUI Jobs tab:
- `Search + apply now (LIVE)` runs one discovery, scoring, retry, and submission cycle.
- `Start repeating search + apply (LIVE)` launches the scheduled autonomous worker.
- `Apply queued jobs now (LIVE)` drains the existing queue with live submissions.
- Buttons labeled `NO SUBMIT` are diagnostic form-fill tests.

---

## Running 24/7

Double-click `START_JOBHUNTER.bat` — the bot runs every 4 hours.

To change the interval, edit `config/config.py`:
```python
RUN_EVERY_HOURS = 4
```

To run headless (browser invisible):
```cmd
python orchestrator.py --schedule --apply --live --headless
```

---

## CLI Reference

```
python orchestrator.py [options]

  --run-once    Run once and exit (no scheduler)
  --schedule    Run on schedule every N hours (default)
  --apply       Enable form-filling
  --live        Disable dry-run (actually submit applications)
  --autonomous  Scheduled AFK discovery, queue retries, and live submission
  --headless    Run browser without visible window
```

---

## Portal Support

| ATS | Support |
|---|---|
| Greenhouse | ✅ Full |
| Lever | ✅ Full |
| Ashby | ✅ Full |
| Generic HTML forms | ✅ Best-effort |
| Workday | ⚠️ Manual review (flagged, not auto-submitted) |
| iCIMS / Taleo | ⚠️ Generic filler — may need manual review |
| LinkedIn Easy Apply | ❌ Too much bot detection — log for manual |

---

## Notion Database Columns

| Column | Description |
|---|---|
| Company | Employer name |
| Role | Job title |
| Location | City/country |
| Score | 0–100 (weighted by your criteria) |
| Decision | Auto Apply / Manual Review / Skipped / Applied |
| Positioning Angle | Which profile angle to lead with |
| Source | Where it was found |
| Date Posted | When the job was posted |
| Job URL | Link to apply |
| Fit Reason | Why it's a match |
| Skip Reason | Why it was skipped |
| Applied | Checkbox — was application submitted? |

---

## Resume Path

Your resume is configured at:
```
C:\Users\Lordy\OneDrive\Documents\Rashed_Alneyadi_4.5cx_Found.pdf
```

To change it, edit `config/config.py`:
```python
RESUME_PATH = r"C:\path\to\your\resume.pdf"
```

---

## Logs

All logs written to `logs/` folder:
- `run_YYYYMMDD.log` — full run log
- `*.png` — screenshots of dry-run form fills

---

## Troubleshooting

**"Cannot connect to Ollama"**
→ Run `ollama serve` in a separate terminal first

**"Resume not found"**
→ Check the path in `config/config.py` — use raw strings: `r"C:\path\..."`

**Notion errors**
→ Make sure your integration has access to the target page
→ Go to the page → `...` menu → Connections → add your integration

**Jobs not appearing**
→ Jobspy may hit rate limits. Try reducing `MAX_JOBS_PER_RUN` or
  increasing the sleep between searches in `discovery.py`

**Form not filling**
→ Set `headless=False` (default) and watch the browser
→ Some portals change their DOM — check the logs for selector errors

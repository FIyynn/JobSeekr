# Applicant config (JobHuntrr)

## Markdown files (edit these)

| File | Purpose |
|------|---------|
| **`data/applicant_profile.md`** | **Source** profile — your master “about me” (authoritative) |
| **`data/applicant_requirements.md`** | **Source** requirements — targets, skips, custom scoring/search |
| **`data/enhanced/applicant_profile_enhanced.md`** | **Enhanced** profile — auto from Enrich (optional edits) |
| **`data/enhanced/applicant_requirements_enhanced.md`** | **Enhanced** requirements — auto supplements |

With **`PROFILE_DUAL_LAYER=1`** (default), scoring and applications use **source + enhanced** merged. Enrich does not overwrite your source files.

**New user:** copy the `.template.md` files in the same folder, rename to `applicant_profile.md` and `applicant_requirements.md`, then fill in.

| Template | Copy to |
|----------|---------|
| `data/applicant_profile.template.md` | `data/applicant_profile.md` |
| `data/applicant_requirements.template.md` | `data/applicant_requirements.md` |

### Requirements YAML (top of `applicant_requirements.md`)

```yaml
---
auto_apply: 75
manual_review: 60
max_years_hard_skip: 7
linkedin_hours_fresh: 48
---
```

Change these numbers to adjust auto-apply behavior without editing Python.

## Rashed (current)

- **Emirati**, fluent **Arabic** and **English**
- **NYU New York** (not NYU Abu Dhabi for degree); NYUAD = research only
- **Excel** listed in profile and form Q&A
- Do not apply to **ADIA / ADIC** as employer

## Also configured in code

- **Search queries:** `config/config.py` → `SEARCH_QUERIES`
- **Account / credentials:** GUI Profile Settings → `data/profile_settings.json` (email, phone, LinkedIn, Notion, flags). No `.env` required.
- **Form fields:** `config/config.py` → `APPLICATION_QA` (filled from profile settings at runtime)
- **Blocklists / role families:** `config/applicant_requirements.py`

## Storage

Default: **local SQLite** at `data/jobs.db` (no Notion required).

```env
STORAGE_BACKEND=local
```

Set `STORAGE_BACKEND=notion` + Notion tokens to use cloud DB instead.

## GUI

```powershell
python gui/jobhunter_gui.py
# or double-click START_GUI.bat
```

Filter by decision, GCC, search; view fit/skip reasons; run discover / rescore / apply.

## Commands (CLI)

```powershell
python orchestrator.py --run-once
START_AUTONOMOUS.bat
python rescore_jobs.py --gcc-only
python apply_jobs.py --gcc-only
python check_gcc_queue.py
```

## After editing profile or requirements

1. Save the `.md` files (no restart needed — loaded each run)
2. Run `python rescore_notion.py --auto-only` to refresh Notion scores

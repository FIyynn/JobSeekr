# Enrich evaluation sandbox (isolated)

This folder tests **“Enrich from links + resume”** without touching production prompts or `data/`.

## What this does NOT touch

| Never modified by sandbox |
|---------------------------|
| `jobhuntr_prompt_pack_rashed.md` (your perfected prompts) |
| `data/applicant_profile.md` |
| `data/applicant_requirements.md` |
| `data/custom_scoring_prompt.md` |
| `data/enhanced/*` (production enhanced layers) |
| `data/profile_settings.json` (unless you explicitly copy a test copy here) |

## What it does

1. **`run_sandbox_enrich.py`** — Fetches resume + links and runs the same enrich logic into `sandbox/enrich_eval/output/` only.
2. **`compare_to_prompt_pack.py`** — Read-only report: sandbox (or optional `--production-output`) vs sections **#2** and **#4** in `jobhuntr_prompt_pack_rashed.md`.

## Setup (one time)

```powershell
cd C:\Users\Lordy\jobhuntrr
pip install -r requirements.txt
python -m playwright install chromium
```

Copy your real settings into the sandbox (optional — for live LinkedIn/resume fetch):

```powershell
copy data\profile_settings.json sandbox\enrich_eval\fixtures\profile_settings.json
```

Or edit `fixtures/profile_settings.template.json` and save as `fixtures/profile_settings.json`.

Ensure `fixtures/profile_settings.json` points `resume_path` at your PDF (absolute path is fine).

## Run enrich (sandbox only)

```powershell
cd C:\Users\Lordy\jobhuntrr
python sandbox/enrich_eval/run_sandbox_enrich.py
```

Options:

```text
--fetch-only     Only fetch resume/LinkedIn/GitHub/website; no Ollama, no writes except fetch log
--dry-run        Fetch + show what would be written; no enhanced files updated
```

Requires Ollama with the project model (`qwen3:8b` by default) for a full run. LinkedIn needs `data/linkedin_session` (from `python setup_linkedin.py` in the main project — session is read-only).

## Compare output to your prompt pack (read-only)

After a sandbox run:

```powershell
python sandbox/enrich_eval/compare_to_prompt_pack.py
```

Optional: also score the **production** enhanced file (read-only, no enrich):

```powershell
python sandbox/enrich_eval/compare_to_prompt_pack.py --production-output
```

Report is written to `sandbox/enrich_eval/reports/comparison_report.md`.

## Expected quality bar

Your target spec is **`jobhuntr_prompt_pack_rashed.md`** section **#2 Enhanced Profile Layer**:

- Structured sections (experience blocks, technical evidence, role-fit ranking)
- Verified facts: ADIA, ADIC, MIT/MBRSC, NYUAD, Polygon, RECtify, DIBA metrics
- Emirati / bilingual / NYU degree wording
- No link dumps or “here are the bullets” filler

Enrich today writes per-source sections (`## From Resume`, `## From LinkedIn`, …). The comparator checks **keyword coverage** and **overlap** with your pack — not a byte-for-byte match.

## Fixtures

| File | Role |
|------|------|
| `fixtures/source_profile_stub.md` | Minimal source profile for dedup (not your full master prompt) |
| `fixtures/source_requirements_stub.md` | Minimal requirements for requirements-enhanced dedup |
| `fixtures/profile_settings.template.json` | Template for links + resume path |

To test against richer source text, copy **only** section `#1` from the prompt pack into `fixtures/source_profile_stub.md` manually (sandbox copy — production files stay unchanged).

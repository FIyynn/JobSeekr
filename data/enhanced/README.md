# Enhanced layer (auto-generated)

JobHuntrr uses **two layers** when `PROFILE_DUAL_LAYER=1` (default):

| Layer | File | Who edits | Used for |
|-------|------|-----------|----------|
| **Source** | `../applicant_profile.md` | You — paste your master “about me” | Authoritative identity, rules, summaries |
| **Enhanced** | `applicant_profile_enhanced.md` | Auto (Enrich) or you | LinkedIn/resume/GitHub facts, deduped bullets |

| Layer | File | Who edits |
|-------|------|-----------|
| **Source** | `../applicant_requirements.md` | You — targets, skips, scoring prompt |
| **Enhanced** | `applicant_requirements_enhanced.md` | Auto (Enrich) | Supplemental skills/industries from links |

**Scoring and applications** receive both layers merged (source first, then enhanced).

- Turn off dual layer: set `PROFILE_DUAL_LAYER=0` in Profile Settings → Account (legacy: enrich appends into source file only).
- Inline `## Enrichment from links` on the source profile is migrated here on first reload/enrich.

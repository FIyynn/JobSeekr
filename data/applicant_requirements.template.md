---
auto_apply: 75
manual_review: 60
max_years_hard_skip: 7
min_requirements_match_pct: 50
linkedin_hours_fresh: 48
ats_days_fresh: 7
min_salary_aed_monthly: 12000
---

# Applicant Requirements

> **Instructions:** Duplicate as `applicant_requirements.md`.
> Edit the YAML block for score thresholds; edit the sections below for what jobs you want.

## Compensation

- Set `min_salary_aed_monthly` in the YAML block (default 12000 AED/month).
- Parsed salaries below this are skipped; unlisted salary is still scored.

## Geography

- **Priority locations:**
- **Relocate:**

## Score thresholds

| Decision | Score |
|----------|-------|
| Auto-apply | (match auto_apply above) |
| Manual review | (match manual_review above) |
| Skip | below manual review |

## Experience rules

- **Hard skip:** years required
- **Target seniority:** e.g. analyst, associate, graduate

## Never apply (employers)

- List companies you must not apply to

## Target role families

1. Role family one
2. Role family two

## Target companies

List priority employers.

## Compensation

Minimum or target compensation.

## Custom scoring prompt

Optional. Example: Prioritize sovereign-backed UAE employers. Downgrade pure sales roles.

## Search queries

Optional. One per line:
```
data scientist | Dubai
investment analyst | Abu Dhabi
```

## Custom search prompt

Optional natural language (used if Search queries is empty):
```
Focus on climate tech and energy transition roles in UAE only.
```

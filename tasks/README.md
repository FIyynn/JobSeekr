# Tasks

This folder holds shallow task runtime code and task assets.

Current task:

- `onboarding_task.py` - onboarding / profile digitization runtime loop
- `candidate_scoring_task.py` - stage-3 candidate listing scoring runtime

Runtime state file:

- `runtime/task_states/onboarding_task_state.json`
- `runtime/task_states/candidate_scoring_task_state.json`

The task runtime updates the state file while it runs so notebooks or future UI layers can poll progress.

The onboarding task now stays digitization-only. It returns a compact `digitized_user` handoff plus completeness metadata, not scorer prompts or follow-up questions.
Its constraint handoff is split into `constraints.hard_yes` and `constraints.hard_no`, with `must_have` kept only as a compatibility alias inside the task shape.

Downstream stages should treat `digitized_user` as the stable handoff key and read `hard_yes` / `hard_no` explicitly.

The `digitized_user` handoff should preserve the user's facts in clear buckets, including:

- `identity`
- `contact`
- `links`
- `summary`
- `education`
- `experience`
- `projects`
- `skills`
- `languages`
- `certifications`
- `eligibility` for right-to-work / work-permit, license, availability, and work-arrangement compatibility
- `seniority` for entry-level / recent-graduate signals
- `application_policy` for shortlist-vs-auto-apply intent
- `preferences` for roles, industries, work style, compensation, commute, company size, and trade-offs
- `constraints` for explicit hard yes / hard no rules
- `source_coverage` so downstream stages can see where each field came from
- `completeness` so downstream stages know whether the handoff is ready

Keep `preferences` descriptive and user-owned. Do not flatten eligibility or seniority back into role lists.

General onboarding extraction rules:

- Treat explicit resume bullets and profile notes as first-class facts when they describe eligibility, availability, mobility, or application policy.
- Normalize common aliases instead of copying one raw phrase only. For example, map right-to-work / work-permit terms to `eligibility.right_to_work`, work-mode phrases to `eligibility.work_arrangement`, and entry-level language to `seniority`.
- Keep profile-level instructions separate from role targets. A sentence like “treat this as an entry-level / recent-graduate profile” belongs in `seniority`, not in `preferences.roles`.
- Keep `seniority.evidence` for factual signals and `seniority.hints` for instruction-like profile cues.
- Keep hard requirements and hard exclusions separate. Only move a bullet into `constraints.hard_no` when the source uses explicit hard language or the item is objectively unsafe, invalid, illegal, unpaid, commission-only, or otherwise clearly unacceptable. Softer language like "poor fit" or "not worth moving forward" should stay as preference signal or note signal, not as a hard dealbreaker.
- If a hard constraint includes an explanation or rationale, keep the compact rule in `constraints.hard_yes` or `constraints.hard_no` and move only the explanatory fragment into `constraints.notes`.
- Normalize “without a clear X” style constraints into a compact rule like `no clear X` instead of keeping the full warning sentence in the main bucket.
- Keep policy decisions like “shortlist first” or “do not auto-apply” in `application_policy`, not in `preferences.roles` or `hard_constraints`.
- When a preference section contains both concrete categories and explanatory prose, keep the concrete categories in the primary list and move the prose into a sibling `notes` list for that section.
- When a preference bullet combines a numeric value or range with a reason clause, keep the numeric value/range in the main item and move the reason clause into `notes`.
- When a compensation trade-off like `lower_if` includes a rationale sentence, keep the actual trade-off phrase in the main list and move the rationale into `notes`.
- Only keep a `notes` entry when it adds information that is not already represented by the structured field. If the bucket already captures the meaning, leave `notes` empty instead of restating it.
- Treat formal graduate / talent-program / nationalization-friendly opportunities as potentially valid unless the source text clearly says to skip them. Do not collapse every “talent pool” mention into a hard no; preserve the exact condition that makes it acceptable or unacceptable.
- Keep right-to-work / work-permit language normalized to the generic `eligibility.right_to_work` bucket. Use whatever country-specific wording appears in the source as an alias only when it clearly expresses the same right-to-work idea.
- Keep salary and other numeric preferences structured when the text provides numeric ranges. Do not leave them as only free text if the document contains enough data to build a range.
- Keep profile-level seniority cues out of role target arrays. If the source says “entry-level”, “recent graduate”, or similar, put that in `seniority`, not `preferences.roles`.
- Avoid claiming the profile is complete unless the required identity fields are actually present. Missing required fields should lower confidence and mark the handoff as partial when needed.
- Prefer stable canonical values with short notes over noisy verbatim dumps.

The candidate scoring task consumes `digitized_user` plus candidate rows, batches them in groups of 30, and returns conservative keep/exclude decisions with stable reason codes for later detail-fetch and filtering stages. Hard constraints are necessary but not sufficient: the scorer also applies a role-relevance gate so clearly unrelated hospitality/admin/customer-service/reception/driver/real-estate jobs get excluded even if they pass the legal filters. Excluded rows should also carry a compact `exclude_reason` object with `current` and `target` so notebook output stays readable. It uses `hard_no` for exclusions and `hard_yes` for required-condition conflicts.

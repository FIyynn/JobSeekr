# Agent Review Rules

## Repo Target

- From now on, treat `https://github.com/brshood/offmarket/tree/offmarket-fresh` as the active target repo/branch for this work.
- When the user says "push" or "update the branch", push to `brshood/offmarket` on `offmarket-fresh` unless they explicitly say otherwise.
- Do not assume `Fyynn/JobSeekr` is the destination unless the user names it again.

- When the user asks to "discover and assess" problems, produce a real issue review, not a vague summary.
- Use a short table with clear full-word column names.
- Required issue review schema:
  - `Problem`
  - `Value`
  - `Difficulty`
  - `Should do`
  - `Best fix`
- Use icon-box ratings instead of numbers:
  - `🟥` = low / bad / hard
  - `🟧` = low-medium / somewhat hard
  - `🟨` = medium
  - `🟩` = good / easy
  - `🟩🟩` = very high / very easy
- Use `Value` for how worth fixing something is, and `Difficulty` for how hard it is to fix.
- Prefer concrete runtime or architecture problems over generic observations.
- Rate both value and difficulty so the user can prioritize fixes quickly.
- Explain what the code should do and the best fix in plain language.
- If a problem is only a future risk, say so clearly.
- If there are multiple issues, order them by importance.
- After finishing edits, always run a quick sanity check on the changed files before replying.
- For notebook edits, the fastest post-edit sanity check is one `shell_command` that parses each code cell with `ast` and flags missing names/imports before any runtime test.
- Use `scripts/preflight_check.py` as the standard reusable preflight command for `.py` and `.ipynb` files after edits.
- Preferred usage: `python scripts/preflight_check.py <path1> <path2> ...`
- For onboarding-to-scoring handoff, treat `digitized_user` as the only stable payload key the scorer should consume.
- For onboarding digitization, keep eligibility, seniority, application policy, and user preferences in separate fields; do not flatten profile-level instructions like “entry-level / recent-graduate profile” into `preferred_roles`.
- For onboarding digitization, normalize explicit eligibility language into canonical buckets. Map right-to-work / work-permit terms to `eligibility.right_to_work`, license terms to `eligibility.driving_license`, start-time terms to `eligibility.availability`, and work-mode terms to `eligibility.work_arrangement`. Avoid country-specific bucket names.
- When a preference section contains both concrete categories and explanatory prose, keep the concrete categories in the main bucket and move the prose into a sibling `notes` field instead of mixing it into the primary list.
- When a preference bullet combines a numeric range or amount with a reason clause, keep the numeric value in the main bucket and move the reason clause into a sibling `notes` field.
- When a compensation trade-off like `lower_if` contains both the trade-off and rationale sentence, keep the trade-off in the main bucket and move the rationale into `notes`.
- When a hard constraint includes a rationale, keep the concise rule in `constraints.hard_yes` or `constraints.hard_no` and move only the explanatory fragment into `constraints.notes`. Only treat it as `hard_no` when the source uses explicit dealbreaker language or the item is objectively unsafe, invalid, illegal, unpaid, commission-only, or otherwise clearly unacceptable; softer "poor fit" language should not become a hard no unless the source is explicit.
- Only keep a `notes` field when it adds information that is not already represented by the main bucket. If the bucket already captures the meaning, leave `notes` empty instead of repeating it.
- Treat formal graduate / nationalization-friendly programs as potentially valid unless the source clearly marks them as vague, unpaid, commission-only, or otherwise invalid. Do not collapse every talent-pool mention into a blanket hard no.
- Keep `seniority.evidence` factual and `seniority.hints` for instruction-like cues such as entry-level or recent-graduate profile language.
- Keep salary and compensation structured when the source contains numeric ranges so downstream scoring does not have to reparsed free text.
- Only mark onboarding as complete when the required identity/contact fields are present; otherwise keep the handoff partial and let the scorer consume the best-effort profile.
- For candidate-scoring exclusions, emit a compact `exclude_reason` object with `current` and `target`, and keep notebook-facing output readable instead of dumping only opaque reason codes.

## Current Issue Log

When recording issues, use this exact schema:

| Problem | Value | Difficulty | Should do | Best fix |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

Use the icon ratings already defined above for `Value` and `Difficulty`.

### Open Issues

| Problem | Value | Difficulty | Should do | Best fix |
|---|---|---|---|---|
| Onboarding and scoring both treat `hard_no` as the only constraint bucket, so the scorer can confuse “hard no” with user preferences. The onboarding output should split constraints into `hard_yes` and `hard_no`, and the scoring stage should consume that split explicitly. | 🟩🟩🟩🟩 | 🟨 | Split hard requirements from hard exclusions in onboarding, then update the scorer instructions and parsing so each bucket has a distinct meaning. | Add separate `constraints.hard_yes` and `constraints.hard_no` fields in onboarding, then update scorer prompts and adapters to use those fields instead of a single mixed constraint bucket. |
| Candidate-scoring exclusions are still too opaque when they only show a code like `constraint_conflict`; the user wants a short `current` vs `target` explanation. | ?????? | ?? | Make each excluded row explain what was detected and what user rule it violated, in a compact readable shape. | Add `exclude_reason.current` and `exclude_reason.target` to scorer output, and show those fields in the notebook preview. |
| `notebooks/candidate_pipeline_runtime.ipynb` prints too much dev chatter and nested debug payloads, which makes it hard to see what the agent actually did or whether something failed. The notebook output should be compact, readable, and non-duplicated. | 🟩🟩🟩 | 🟨🟨 | Keep only the information needed to understand the action taken, any warning/error, and the final result. | Trim notebook display helpers so they show one clean status line, one compact result summary, and one compact error block if needed. Remove repeated nested debug dumps. |

### Reminder

- Use [`notebooks/candidate_pipeline_runtime.ipynb`](notebooks/candidate_pipeline_runtime.ipynb) as the main notebook for tuning and testing agent behavior.
- Run the notebook against different cases while adjusting instructions so we can see how the candidate-scoring agent behaves under change.

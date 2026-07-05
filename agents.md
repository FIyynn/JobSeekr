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
| `notebooks/candidate_pipeline_runtime.ipynb` prints too much dev chatter and nested debug payloads, which makes it hard to see what the agent actually did or whether something failed. The notebook output should be compact, readable, and non-duplicated. | 🟩🟩🟩 | 🟨🟨 | Keep only the information needed to understand the action taken, any warning/error, and the final result. | Trim notebook display helpers so they show one clean status line, one compact result summary, and one compact error block if needed. Remove repeated nested debug dumps. |

### Reminder

- Use [`notebooks/candidate_pipeline_runtime.ipynb`](notebooks/candidate_pipeline_runtime.ipynb) as the main notebook for tuning and testing agent behavior.
- Run the notebook against different cases while adjusting instructions so we can see how the candidate-scoring agent behaves under change.

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

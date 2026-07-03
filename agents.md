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

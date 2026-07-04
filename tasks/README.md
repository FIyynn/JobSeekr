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

Downstream stages should treat `digitized_user` as the stable handoff key.

The candidate scoring task consumes `digitized_user` plus candidate rows, batches them in groups of 30, and returns conservative keep/exclude decisions with stable reason codes for later detail-fetch and filtering stages.

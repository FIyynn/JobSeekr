# Tasks

This folder holds shallow task runtime code and task assets.

Current task:

- `onboarding_task.py` - onboarding / profile digitization runtime loop

Runtime state file:

- `runtime/task_states/onboarding_task_state.json`

The task runtime updates the state file while it runs so notebooks or future UI layers can poll progress.

When profile information is missing, the runtime returns `questions` in the task result so a future UI or agent can ask the user for missing details.

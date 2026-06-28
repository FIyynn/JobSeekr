# WebAgent Reflection

Reflect on the current task state immediately after a tool call.

Rules:
- Start with exactly one line: `Decision: complete` or `Decision: continue`.
- Reuse the existing plan, do not rewrite it from scratch.
- Use the latest tool result, current page state, and runtime memory.
- Briefly say what changed, whether any plan step is now done, and what is still missing.
- If the task is not done, say the next action clearly and keep it to one tool-worthy step.
- Do not go back into planning mode here.

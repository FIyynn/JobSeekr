# Agent Reflection

Think privately. Do not expose chain-of-thought.

Rules:
- Use this phase after tool outputs.
- Readdress the current plan without repeating the initial planning block.
- Say what has already been done, what evidence you have, and whether the task is complete.
- Reuse the current search task id, query, filters, and fetched pages when deciding whether to continue.
- Remember which listing ids have already been inspected and avoid repeating them.
- Start with exactly one line: `Decision: complete` or `Decision: continue`.
- If continuing, explain the next action clearly and keep it concise.
- Do not call tools in this phase.
- Do not repeat the original planning instructions.

Output shape:
- decision line
- current state
- what is done
- what is missing
- next step if continuing

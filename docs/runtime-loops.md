# Runtime Loops

The repo currently has two agent runtime loops.

## Generic Webagent Runtime

Location:

- `tests/llm_test/llm_runtime.py`
- `tests/llm_test/llm_runtime_test.ipynb`

Purpose:

- Test general web navigation and interaction.
- Convert current page to markdown.
- Let the LLM call a small set of generic web tools.
- Return markdown and diffs instead of noisy JSON.

Current tools:

- `webagent_fetch_page`
- `webagent_click`
- `webagent_type`
- `webagent_clear_text`

Current behavior:

- Supports local llama.cpp and OpenAI-compatible APIs.
- Parses `<cmd>tool({...})</cmd>`.
- Parses `<final_response>...</final_response>`.
- Rejects unclosed protocol tags.
- Uses a sliding token budget.
- Refreshes markdown after interactions.
- Sends markdown/diffs to the LLM instead of the full dev payload.

## LinkedIn Runtime

Location:

- `tests/llm_test_linkedin/linkedin_runtime.py`
- `tests/llm_test_linkedin/llm_linkedin_runtime_test.ipynb`

Purpose:

- Test LinkedIn decision making with higher-level tools.
- Keep the LLM away from low-level browser details.
- Use instruction and state swapping by phase.

Phases:

| Phase | Instructions shown | Expected model output |
|---|---|---|
| Planning | `agent_plan.md` | Plain plan only, no tools |
| Tool | `agent_loop.md`, `tool_instructions_linkedin.md`, examples | Exactly one `<cmd>...</cmd>` |
| Reflection | `agent_reflection.md` | `Decision: continue` or `Decision: complete` |
| Finalize | `agent_finish.md` | Exactly one `<final_response>...</final_response>` |

Important behavior:

- Search results are summarized compactly.
- Candidate shortlist is kept visible.
- Already inspected listing IDs are kept visible.
- Search task state is kept visible.
- Page cache remains runtime/internal.
- `listing_id` is required for detail fetches.
- `listing_index` is intentionally not part of the LLM-facing contract.

Protocol tags:

- Commands must use `<cmd>...</cmd>`.
- Tool outputs use `<output>...</output>` when added to context.
- Final answers must use `<final_response>...</final_response>`.
- Any unclosed protocol tag should be treated as an error.

# Worker Tasks

Workers own business workflows and tool execution.

## MVP Stages

| Stage | Worker task | Helper services used |
|---:|---|---|
| 1 | Onboard create/edit | LLM Server, MongoDB |
| 2 | Accumulate candidate listings | LinkedIn Bot Server, LLM Server |
| 3 | Score candidate listings | LLM Server |
| 4 | Fetch detailed listing descriptions | LinkedIn Bot Server |
| 5 | Score detailed listings | LLM Server |
| 6 | Apply to jobs | Web Server, LinkedIn Bot Server, LLM Server |

## Worker Runtime Pattern

Each worker task should:

1. Load task payload from MongoDB.
2. Load user profile/config.
3. Build runtime state.
4. Choose the correct tool loop or deterministic workflow.
5. Execute one step at a time.
6. Persist intermediate state and snapshots.
7. Return partial/success/failure result.

## Task Statuses

Recommended statuses:

- `queued`
- `running`
- `waiting_for_user`
- `partial`
- `success`
- `failed`
- `cancelled`

## Error Handling

Workers should persist:

- error message
- failed tool name
- failed payload summary
- browser/session state
- latest screenshot or snapshot when possible
- retry count
- whether retry is safe

## Current Prototype Mapping

| Future worker task | Current prototype |
|---|---|
| Generic web use | `browser/webagent.py`, `tests/llm_test/llm_runtime.py` |
| LinkedIn listing search | `browser/linkedin.py`, `tests/llm_test_linkedin/linkedin_runtime.py` |
| Candidate listing scoring | `tasks/candidate_scoring_task.py` |
| LinkedIn detail fetch | `browser/linkedin.py`, `stages/listing_detail.py` |
| Markdown page snapshot | `browser/markdown.py`, `parsers/page_markdown.py` |
| Local persistence mock | `storage/embedded_mongo.py` |

Candidate listing scoring should treat hard constraints as necessary, not sufficient, and should also apply a role-relevance gate so clearly unrelated hospitality/admin/customer-service/reception/driver/real-estate jobs are excluded even when they pass the legal filters. Exclusion rows should stay compact with `current` and `target` fields for notebook readability.

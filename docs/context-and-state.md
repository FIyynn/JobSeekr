# Context And State

The runtime has two kinds of context:

1. LLM prompt context.
2. Python runtime state.

They are related, but they are not the same thing.

## LLM Prompt Context

Each model turn rebuilds messages from:

- current phase instructions
- user task
- current plan
- latest tool name
- latest tool result
- active search task summary
- candidate shortlist
- inspected listing IDs
- recent transcript snippets

Older context is not automatically wiped after each tool call. It can remain until sliding-window trimming drops it.

Current LinkedIn context budget:

- default context tokens: `20000`
- response reserve: `2048`

## Runtime State

The `runtime` dict persists across steps while the notebook/kernel is alive.

Important generic runtime keys:

| Key | Meaning |
|---|---|
| `driver` | Selenium/UC Chrome driver |
| `store` | mock embedded Mongo store |
| `markdown_text` | latest page markdown snapshot |
| `interactables` | latest dev interactables catalog |
| `session_outputs` | local transcript display/history |
| `stuck_counts` | repeated error/tool-state detection |
| `last_result` | final/error/halt result |

Important LinkedIn runtime keys:

| Key | Meaning |
|---|---|
| `search_task` | compact active search task summary |
| `search_tasks` | session-scoped cache of task payloads by id |
| `last_listing_payload` | latest accumulated listings payload |
| `listings_json` | alias for latest listing payload |
| `page_cache` | internal page/listing snapshot cache |
| `inspected_listing_ids` | listing IDs already opened for details |
| `last_detail_payload` | latest listing detail result |
| `current_linkedin_state` | latest LinkedIn browser/session metadata |
| `phase` | planning, tool, reflection, or finalize |

## What Gets Refreshed

Every step refreshes:

- the phase-specific instruction set
- the generated user prompt for that phase
- latest result summary
- active search task summary
- recent context snippet

Every tool call updates:

- `last_tool`
- `last_tool_result`
- relevant runtime payloads for that tool
- phase, usually to `reflection`

## What Does Not Get Cleared Automatically

The following can carry between `run_agent(...)` calls if the same `runtime` dict is reused:

- `search_task`
- `search_tasks`
- `inspected_listing_ids`
- `page_cache`
- `last_listing_payload`
- `last_detail_payload`
- `current_linkedin_state`
- `session_outputs`

For a clean new task, create a fresh runtime dict or explicitly reset these keys.

Suggested reset keys for a new LinkedIn task:

```text
search_task
search_tasks
inspected_listing_ids
page_cache
last_listing_payload
listings_json
last_detail_payload
current_linkedin_state
last_plan
last_tool
last_tool_result
last_result
phase
session_outputs
stuck_counts
```

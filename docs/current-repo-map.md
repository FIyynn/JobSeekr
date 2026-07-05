# Current Repo Map

The current repo is a prototype of the worker-side tooling and LLM runtimes. It is not yet the full distributed app.

Top-level areas in the current repo, ordered from highest-level ownership to lowest-level implementation detail:

| Order | Path | Purpose |
|---:|---|---|
| 01 | `frontend/` | Frontend app and UI entry points |
| 02 | `api/` | API service boundary and request intake |
| 03 | `worker/` | Worker orchestration, runtime loops, and task execution |
| 04 | `services/` | Helper services for `web`, `linkedin`, and `llm` |
| 05 | `shared/` | Common config, logging, pipeline, and storage helpers |
| 06 | `infra/` | Docker, Mongo, and queue wiring placeholders |
| 07 | `docs/` | Architecture, runtime, data-flow, and handoff documentation |
| 08 | `notebooks/` | Manual runtime notebooks and targeted debug harnesses |
| 09 | `tests/` | Unit tests plus parser/runtime coverage |
| 10 | `config/` | Profile and extraction config |
| 11 | `scripts/` | Local helper and startup scripts |
| 12 | `legacy/` | Old prototype code kept only as reference |
| 13 | `profiles/` | Reference profile material used by onboarding/digitization work |
| 14 | `runtime/` | Runtime state and local task artifacts |
| 15 | `tasks/` | Task runtime code and task-specific assets |
| 16 | `parsers/` | Shared parsers used by the current tool stack |
| 17 | `storage/` | Embedded storage and persistence helpers |
| 18 | `stages/` | Stage helpers for higher-level task workflows |
| 19 | `browser/` | Current browser-facing helpers and wrappers |
| 20 | `core/` | Core app configuration and glue |
| 21 | `agent-system-prompts/` | Prompt packs and agent instruction material |

Important current modules:

| Module | What it does |
|---|---|
| `browser/driver.py` | Browser driver helper |
| `browser/markdown.py` | HTML-to-markdown helper used by browser pages |
| `browser/interact.py` | Browser interaction engine |
| `browser/webagent.py` | Generic web wrapper |
| `browser/linkedin_jobs.py` | Low-level LinkedIn browser actions |
| `browser/linkedin.py` | High-level LinkedIn wrapper tools |
| `services/linkedin/listing_detail.py` | LinkedIn listing detail parser |
| `worker/webagent_runtime.py` | Generic webagent runtime loop utilities |
| `worker/linkedin_runtime.py` | LinkedIn-only phase-swapping runtime loop |
| `tests/llm_test/` | Generic webagent LLM runtime notebook harness |
| `tests/llm_test_linkedin/` | LinkedIn LLM runtime notebook harness |
| `notebooks/filter_sync_debug/` | Targeted filter-sync debug notebook harness |

Current important limitation:

The notebooks and local wrappers prove the agent/tool flow, but the production API, worker server process, queue, MongoDB server integration, and frontend are still future work.

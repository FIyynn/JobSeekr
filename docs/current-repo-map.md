# Current Repo Map

The current repo is a prototype of the worker-side tooling and LLM runtimes. It is not yet the full distributed app.

Top-level areas, ordered from highest-level ownership to lowest-level implementation detail:

| Order | Path | Purpose |
|---:|---|---|
| 01 | `01 - frontend/` | Placeholder for the future Next.js app |
| 02 | `02 - api/` | Placeholder for the future API service |
| 03 | `03 - worker/` | New worker orchestration area for runtime loops and task execution |
| 04 | `04 - services/` | New shallow service ownership area for `web`, `linkedin`, and `llm` |
| 05 | `05 - shared/` | New common code area for config, logging, driver, pipeline, and storage |
| 06 | `06 - infra/` | Placeholder for Docker/Mongo/queue wiring |
| 07 | `07 - docs/` | Architecture, runtime, data-flow, and handoff documentation |
| 08 | `08 - notebooks/` | Manual runtime notebooks copied into the new structure |
| 09 | `09 - tests/` | Unit tests plus parser/runtime coverage |
| 10 | `10 - config/` | Profile and extraction config |
| 11 | `11 - scripts/` | Local helper and startup scripts |
| 12 | `12 - legacy/` | Old prototype code moved out of the active tree |
| 13 | `12 - legacy/browser/` | Legacy prototype browser layer still used by moved notebooks and tests |
| 14 | `12 - legacy/core/` | Legacy prototype config/pipeline helpers |
| 15 | `12 - legacy/parsers/` | Legacy prototype HTML/detail parsers |
| 16 | `12 - legacy/stages/` | Legacy prototype stage helpers |
| 17 | `12 - legacy/storage/` | Legacy prototype embedded store |
| 18 | `12 - legacy/runtime/` | Legacy runtime helpers from the prototype |
| 19 | `12 - legacy/tests/` | Legacy copies of the notebook/test bundles |
| 20 | `13 - Profile/` | Legacy user profile reference docs |
| 21 | `dataflow.md` | Original task data-flow table |
| 22 | `tasks.md` | Current implementation TODO notes |

Important current modules:

| Module | What it does |
|---|---|
| `shared/driver.py` | Copied browser driver helper for the new shared layer |
| `services/web/markdown.py` | Copied HTML-to-markdown helper |
| `services/web/interact.py` | Copied interaction engine |
| `services/web/webagent.py` | Copied generic web wrapper |
| `services/web/page_markdown.py` | Copied DOM-to-markdown parser |
| `services/linkedin/linkedin_jobs.py` | Copied low-level LinkedIn browser actions |
| `services/linkedin/linkedin.py` | Copied high-level LinkedIn wrapper tools |
| `services/linkedin/listing_detail.py` | Copied LinkedIn listing detail parser |
| `services/linkedin/listing_detail_stage.py` | Copied stage helper for listing detail |
| `shared/config.py` | Copied config loader and request resolver |
| `shared/logging.py` | Copied structured logger |
| `shared/pipeline.py` | Copied pipeline glue |
| `shared/storage.py` | Copied embedded Mongo-style storage mock |
| `worker/webagent_runtime.py` | Copied generic webagent runtime loop utilities |
| `worker/linkedin_runtime.py` | Copied LinkedIn-only phase-swapping runtime loop |

Current important limitation:

The notebooks and local wrappers prove the agent/tool flow, but the production API, worker server process, queue, MongoDB server integration, and frontend are still future work.

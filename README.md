# JobSeekr Fresh

JobSeekr Fresh is a pipeline for turning raw user profile material into job search decisions.
It starts with onboarding, turns the profile into a stable `digitized_user`, plans LinkedIn searches, fetches candidates, scores them, fetches full job details, and then scores those details again.

## Table of Contents

- [System Overview](#system-overview)
- [Execution Architecture](#execution-architecture)
- [Processing Pipeline](#processing-pipeline)
- [Runtime Components](#runtime-components)
- [Configuration](#configuration)
- [Testing Entry Points](#testing-entry-points)
- [Module Taxonomy](#module-taxonomy)
- [Repository Layout](#repository-layout)
- [Reference Order](#reference-order)

## System Overview

This repo is a current prototype for a job-search automation pipeline.

At a high level:

- raw user documents are ingested
- onboarding produces a stable `digitized_user`
- query planning generates LinkedIn search clusters
- search execution returns candidate rows
- candidate scoring filters irrelevant rows
- detail fetch resolves full job descriptions
- detail scoring ranks the detailed listings

The goal is to keep each stage deterministic, modular, and easy to validate.

## Execution Architecture

```text
Users
  -> Frontend
  -> API
  -> Queue / Router
  -> Agents runtime
  -> Task runtime
  -> Helper services
       -> LLM server
       -> Web browser helper
       -> LinkedIn helper
       -> Storage
```

The core rule is:

- agents runtime owns orchestration and decisions
- service adapters expose narrow interfaces
- shared code keeps browser, parsing, configuration, and persistence behavior consistent

## Processing Pipeline

```text
Raw user input
  -> Onboarding
  -> digitized_user
  -> Query planner
  -> LinkedIn search queries
  -> Candidate rows
  -> Candidate scoring
  -> Kept candidate rows
  -> Detail fetch
  -> Full job details
  -> Detail scoring
  -> Final ranked output
```

The current prototype keeps local runtime state and notebook artifacts so the same search or listing can be reused while testing.

## Testing Entry Points

Use the notebook that matches the stage you want to validate:

- `notebooks/tasks/onboarding_task_runtime.ipynb` - onboarding digitization and `digitized_user` handoff
- `notebooks/candidate_pipeline_runtime.ipynb` - onboarding, LinkedIn query planning, candidate fetch, candidate scoring, detail fetch, and detail scoring
- `notebooks/linkedin/runtime_test.ipynb` - LinkedIn search execution and candidate collection
- `notebooks/linkedin/extract_demo.ipynb` - LinkedIn extraction and result parsing demo
- `notebooks/filter_sync_debug/runtime_test.ipynb` - LinkedIn filter state synchronization debugging
- `notebooks/browser_use_oss/browser_use_oss_test.ipynb` - isolated Browser Use OSS harness
- `notebooks/webagent_deprecated/runtime_test.ipynb` - legacy generic webagent harness kept for reference

When in doubt:

- use `notebooks/tasks/onboarding_task_runtime.ipynb` to test profile digitization
- use `notebooks/candidate_pipeline_runtime.ipynb` to test the staged LinkedIn pipeline end to end
- use the LinkedIn or browser-specific notebooks only for stage-local debugging

## Module Taxonomy

The current codebase is split into a few high-level module groups:

- `core/` - configuration loading and application glue
- `shared/` - common utilities for driver setup, config, logging, parsing, and storage
- `browser/` - browser automation wrappers, markdown rendering, and interaction logic
- `services/` - service adapters that expose higher-level web and LinkedIn operations
- `tasks/` - task runtimes such as onboarding and candidate scoring
- `agents_runtime/` - orchestration loops that coordinate staged workflows
- `worker/` - compatibility wrappers for the old import path
- `stages/` - stage helpers used by higher-level task flows
- `parsers/` - HTML and text extraction helpers
- `storage/` - embedded persistence helpers
- `notebooks/` - manual test harnesses for stage-by-stage validation

The simplest mental model is:

- notebooks exercise stages
- tasks implement stage runtimes
- services and browser modules implement narrow execution primitives
- shared and core modules provide common infrastructure

## Runtime Components

### Onboarding Digitization

Purpose:

- transform source documents into one clean `digitized_user`

Core interfaces:

- config loader
- document parser
- normalization and merge logic
- state writer

Stage output:

- one stable handoff object for later stages

### LinkedIn Query Planner

Purpose:

- turn `digitized_user` into a small set of useful LinkedIn search queries

Core interfaces:

- prompt bundle
- LLM call
- query-plan serializer

Stage output:

- clustered role-based search queries

### LinkedIn Search Executor

Purpose:

- run the planned search queries and collect candidate rows

Core interfaces:

- LinkedIn browser helper
- query execution helper
- result dedupe and pagination
- notebook cache

Stage output:

- candidate listings table

### Candidate Scoring

Purpose:

- remove obviously irrelevant jobs before opening details

Core interfaces:

- LLM batch judge
- compact exclusion reasons
- state writer

Stage output:

- kept rows
- excluded rows with short explanation

### Detail Fetch

Purpose:

- open the kept candidate listings and pull full descriptions

Core interfaces:

- LinkedIn listing opener
- detail parser
- notebook-local reuse cache

Stage output:

- full listing detail rows

### Detail Scoring

Purpose:

- score the richer job descriptions against the user profile

Core interfaces:

- LLM batch judge
- deterministic section computation
- state writer

Stage output:

- kept detail rows
- excluded detail rows
- section-level fit summary

### Generic Webagent

Purpose:

- test general browser use outside LinkedIn

Core interfaces:

- fetch page
- click
- type
- clear text
- markdown extraction
- diff generation

Stage output:

- readable markdown plus interaction diffs

## Configuration

The current shared config lives in `config/app_config.json`.

Main sections:

- `chrome`: browser behavior and profile directory
- `user`: the root directory for user-owned inputs and outputs
- `paths`: runtime roots and agent roots
- `llm`: default model settings
- `agent_onboarding`: onboarding runtime settings
- `agent_search_query`: query planning settings
- `agent_candidate_scoring`: candidate scoring settings
- `task_fetch_listing_details`: detail fetch settings
- `agent_score_detailed_listings`: detail scoring settings
- `dev`: debug and cache behavior

The loaders in `core/config.py` and `shared/config.py` now prefer the central app config file and still provide compatibility aliases for the existing notebooks.

## Repository Layout

- `agent-system-prompts/` - prompt bundles for each agent
- `api/` - future request intake and routing
- `browser/` - browser wrappers, markdown, and interaction helpers
- `config/` - shared configuration files
- `core/` - config loading and glue code
- `docs/` - design, flow, and runtime notes
- `legacy/` - old prototype material
- `notebooks/` - stage-specific test harnesses and debug notebooks
- `parsers/` - shared parsing code
- `profiles/` - reference profile data used for testing
- `runtime/` - local state and artifacts
- `scripts/` - helper scripts
- `services/` - service adapters such as web and LinkedIn
- `shared/` - common driver, config, logging, and storage helpers
- `stages/` - stage-level helpers and orchestration pieces
- `storage/` - embedded persistence helpers
- `tasks/` - task runtime code
- `tests/` - unit tests and notebook harness tests
- `agents_runtime/` - agent runtime loops and orchestration code
- `worker/` - compatibility wrappers for the old import path

## Reference Order

Recommended reading order:

1. `docs/architecture.md`
2. `docs/data-flow.md`
3. `docs/runtime-loops.md`
4. `docs/context-and-state.md`
5. `docs/tool-contracts.md`
6. `docs/worker-tasks.md`

If you want to understand how the current pipeline behaves, start with the notebook harnesses under `notebooks/`.

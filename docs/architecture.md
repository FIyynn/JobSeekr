# Architecture

This repo is a current prototype that is being shaped toward a full distributed app.

## Module Responsibilities

- `frontend/` is the UI layer placeholder.
- `api/` is the request intake and routing layer placeholder.
- `agents_runtime/` owns task orchestration and runtime loops.
- `services/` owns narrow helper interfaces such as `web` and `linkedin`.
- `browser/` holds browser-facing wrappers, markdown rendering, and interaction helpers.
- `core/` holds application configuration and glue code.
- `shared/` holds common driver, config, logging, pipeline, and storage utilities.
- `parsers/` holds shared HTML and extraction parsers.
- `storage/` holds embedded persistence helpers.
- `docs/` holds architecture and design documentation.
- `notebooks/` holds manual runtime notebooks and focused debug harnesses.
- `tests/` holds unit, parser, runtime, and notebook coverage.
- `config/` holds profile and extraction configuration.
- `scripts/` holds local helper scripts.
- `legacy/` holds old prototype code that is no longer the active source of truth.
- `profiles/` holds reference profile data used by digitization and scoring.
- `tasks/` holds task runtime code and task-specific assets.
- `runtime/` holds runtime state and local task artifacts.
- `agent-system-prompts/` holds prompt bundles and agent instructions.
- `infra/` holds deployment and wiring placeholders.

## Execution Model

```text
Users
  ->
Frontend
  ->
API
  ->
Queue / Load Balancer
  ->
Agents runtime
  -> task workflow
  -> tool calls
  -> state updates
  -> service adapters
       -> LLM server
       -> Web server
       -> LinkedIn bot helper
```

## Core Principle

Agents runtime owns decisions. Service adapters do one step at a time. Shared code keeps driver, logging, parsing, and storage behavior consistent across the repo.

## Notebook Use

The notebooks are not the production runtime. They are test harnesses for fast reruns, live debugging, and manual validation while the agent runtime code is still being hardened.

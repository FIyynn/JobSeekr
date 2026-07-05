# Architecture

This repo is a worker-side prototype that is being shaped toward a full distributed app.

## Current Shape

- `frontend/` is the UI layer placeholder.
- `api/` is the future request intake and routing layer.
- `worker/` owns task workflows and runtime loops.
- `services/` owns helper services such as `web` and `linkedin`.
- `browser/` holds browser-facing wrappers, markdown, and interaction helpers.
- `core/` holds the current app config and glue code.
- `shared/` holds common driver/config/logging/pipeline/storage code.
- `parsers/` holds shared HTML and extraction parsers.
- `storage/` holds embedded persistence helpers.
- `docs/` holds the handoff and design map.
- `notebooks/` holds manual runtime notebooks and targeted debug harnesses.
- `tests/` holds unit, parser, runtime, and notebook parser coverage.
- `config/` holds profile and extraction configuration.
- `scripts/` holds local helper scripts.
- `legacy/` holds old prototype code that is no longer the active source of truth.
- `profiles/` holds reference profile data used by digitization and scoring work.
- `tasks/` holds task runtime code and task-specific assets.
- `runtime/` holds runtime state and local task artifacts.
- `agent-system-prompts/` holds consolidated prompt packs and agent instructions.
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
Worker
  -> task workflow
  -> tool calls
  -> state updates
  -> helper services
       -> LLM server
       -> Web server
       -> LinkedIn bot helper
```

## Core Principle

Workers own decisions. Helper services do one step at a time. Shared code keeps driver, logging, parsing, and storage behavior consistent across the repo.

## Notebook Use

The notebooks are not the production runtime. They are targeted harnesses for fast reruns, live debugging, and manual validation while the worker-side code is still being hardened.

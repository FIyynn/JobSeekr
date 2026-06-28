# Open Questions

These are decisions to settle before production work gets large.

## Architecture

- Which queue/load balancer will be used first?
- Will helper services be HTTP APIs from day one, or local worker modules first?
- How many Chrome instances should one LinkedIn Bot Server process own?
- How should browser profiles be allocated and retired?

## Storage

- Exact MongoDB collections and indexes.
- Snapshot retention policy.
- Whether raw HTML should be compressed.
- How to version markdown/interactables schema.
- How to separate client cache data from server truth.

## Agent Runtime

- Whether generic webagent and LinkedIn runtimes should merge into one worker runtime.
- How strict final answer formatting should be.
- How much listing detail should stay in prompt vs Mongo references.
- How to reset runtime state between tasks safely.

## Product

- What fields are mandatory in onboarding?
- What scoring weights matter most?
- How much user confirmation is needed before applying?
- What application states should be visible in the frontend?

## LinkedIn

- Session/profile management strategy.
- Rate limits and delay presets.
- How to recover if LinkedIn changes DOM structure.
- How to validate filters are applied before scraping.
- How to persist exact page state for later detail fetches.

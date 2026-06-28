# Implementation Roadmap

This roadmap starts from the current repo and moves toward the full architecture.

## Phase 1 - Stabilize Prototype Contracts

- Freeze LLM-facing tool contracts.
- Keep `listing_id` only for LinkedIn details.
- Keep browser/profile/session inputs runtime-only.
- Add reset helper for clean runtime task starts.
- Keep markdown/dev split strict.
- Add more fixture tests for markdown and LinkedIn parsing.

## Phase 2 - Server Storage

- Add MongoDB Docker config.
- Replace mock log paths with real Mongo writes.
- Add collections for tasks, snapshots, search tasks, listings, details, scores, applications.
- Store page HTML, markdown, and interactables for every fetch.
- Add data migration/version field to documents.

## Phase 3 - Worker Process

- Move notebook runtime loops into worker modules.
- Add worker task runner.
- Add durable task status updates.
- Add retry/error policy.
- Add clean runtime reset per task.
- Add worker logs in MongoDB.

## Phase 4 - API Endpoint

- Add request validation.
- Add task creation endpoints.
- Add task status/result endpoints.
- Add cache sync endpoints for frontend.
- Add user/session boundaries.

## Phase 5 - Queue / Load Balancer

- Add task dispatch queue.
- Add worker availability tracking.
- Add task lease/heartbeat.
- Add retry/requeue behavior.
- Keep queue metadata small.

## Phase 6 - Helper Services

- Split generic web helper into a service.
- Split LinkedIn bot helper into a service.
- Add one Chrome profile per browser instance.
- Add LLM client abstraction for local/OpenAI-compatible backends.

## Phase 7 - Frontend

- Add tables for listings, descriptions, scores, applications.
- Add config panels.
- Add onboard document/profile management.
- Add local Mongita cache.
- Add status/progress views.
- Add snapshot/debug views for dev.

## Phase 8 - Full MVP Flow

- Onboard profile.
- Find LinkedIn listings.
- Score candidate listings.
- Fetch details for selected listing IDs.
- Score detailed listings.
- Confirm applications.
- Apply with snapshots and status tracking.

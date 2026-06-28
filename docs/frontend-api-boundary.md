# Frontend And API Boundary

The frontend should not own task workflows. It owns interaction, configuration, cached views, and user review.

## Frontend Responsibilities

- Show profile/onboarding documents.
- Let user edit profile/config values.
- Show listings tables.
- Show detail tables.
- Show scoring weights and filters.
- Let user select jobs to inspect, score, or apply.
- Keep a local Mongita cache for fast reads.
- Sync with server task state.

Frontend should not:

- run browser automation
- run LLM loops
- manage LinkedIn sessions
- decide worker steps
- write directly to server DB

## API Endpoint Responsibilities

API should:

- validate frontend requests
- decide task type
- create task payload
- persist task record
- dispatch task to queue/load balancer
- expose task status
- expose cached result views

API should not:

- run LinkedIn/browser workflows
- call the LLM for task work
- own detailed business workflow logic

## API Payload Pattern

Request:

```json
{
  "user_id": "string",
  "task_type": "find_linkedin_job_listings",
  "input": {},
  "client_cache_version": "string"
}
```

Response:

```json
{
  "task_id": "string",
  "status": "queued",
  "server_cache_version": "string",
  "view_hint": "listings"
}
```

Result fetch:

```json
{
  "task_id": "string",
  "status": "running|partial|success|failed",
  "summary": {},
  "rows": [],
  "config": {},
  "updated_at": "iso datetime"
}
```

Frontend tables should be driven from server-shaped rows, not raw tool output.

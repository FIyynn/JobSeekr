# Storage Model

## Current Storage

Current local mock:

- `storage/embedded_mongo.py`
- JSON file-backed store
- Mongo-style method names
- no MongoDB server required

Current supported operations:

- `insert_one`
- `find`
- `find_one`
- `replace_one`
- `update_one`
- `save_run`
- `save_stage_output`

Current collections used or expected:

| Collection | Current/future purpose |
|---|---|
| `runs` | Stage or agent run records |
| `stage_outputs` | Pipeline output by run/stage |
| `webagent_runs` | Generic page fetches |
| `webagent_actions` | Generic interactions |
| `linkedin_search_tasks` | Future durable search-task records |
| `linkedin_listing_details` | Future listing detail payloads |
| `web_snapshots` | Future exact webpage snapshots |
| `onboard_profiles` | Future parsed user profile and documents |
| `scores` | Future listing/detail scoring results |
| `applications` | Future apply attempts and statuses |

## Production Storage

Production should use MongoDB Docker / server DB as source of truth.

Recommended durable documents:

### Task

```json
{
  "task_id": "string",
  "user_id": "string",
  "type": "find_linkedin_job_listings",
  "status": "queued|running|partial|success|failed",
  "input": {},
  "created_at": "iso datetime",
  "updated_at": "iso datetime",
  "worker_id": "string",
  "result_summary": {}
}
```

### Search Task

```json
{
  "search_task_id": "string",
  "user_id": "string",
  "query": "software engineer",
  "location": "Dubai",
  "filters": [],
  "pages_requested": [1, 2, 3],
  "pages_fetched": [1, 2],
  "listing_count": 50,
  "visible_unfetched_pages": [3, 4],
  "snapshot_ids": [],
  "created_at": "iso datetime",
  "updated_at": "iso datetime"
}
```

### Listing

```json
{
  "listing_id": "linkedin job id",
  "search_task_id": "string",
  "title": "string",
  "company": "string",
  "location": "string",
  "listed_on": "date or text",
  "easy_apply": true,
  "raw_listing": {},
  "snapshot_id": "string"
}
```

### Listing Detail

```json
{
  "listing_id": "linkedin job id",
  "detail": {},
  "company_profile": {},
  "raw_ai_payload": {},
  "raw_dev_payload": {},
  "snapshot_id": "string",
  "fetched_at": "iso datetime"
}
```

### Web Snapshot

```json
{
  "snapshot_id": "string",
  "url": "string",
  "title": "string",
  "html": "string",
  "markdown": "string",
  "interactables": {},
  "created_at": "iso datetime",
  "source": "webagent|linkedin"
}
```

Snapshot rule:

Every fetched webpage result should produce a snapshot. Later scoring, comparison, or application workflows should refer to stored snapshots rather than trusting the live web page has not changed.

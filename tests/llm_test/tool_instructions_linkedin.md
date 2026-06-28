# LinkedIn Tools

These are specialized wrappers. Prefer `webagent_*` tools unless the task is clearly LinkedIn-specific.

## `linkedin.fetch_job_listings()`

LLM-facing inputs:
- `keyword`
- `location`
- optional `filters`
- optional `filter_by`

Runtime inputs:
- `driver`
- `delays`
- `log_path`
- `store`
- current session state

## `linkedin.fetch_listings_description()`

LLM-facing inputs:
- `listing_id`

Runtime inputs:
- `driver`
- `delays`
- `log_path`
- `store`
- current session state

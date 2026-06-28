# Tool Contracts

Tools are split into LLM-facing inputs and runtime-filled inputs. The LLM should only choose intent-level parameters. Runtime fills browser/session/logging/storage details.

## Generic Webagent Tools

### `webagent_fetch_page()`

LLM-facing inputs:

- `url`

Runtime inputs:

- `driver`
- browser profile / user-data-dir
- delays
- logging target
- storage/store
- current session state

Behavior:

- Navigate to URL.
- Convert page to clean markdown.
- Return markdown to the LLM.
- Keep dev interactables in runtime.

### `webagent_click()`

LLM-facing inputs:

- `target_id`

Runtime inputs:

- `driver`
- current markdown snapshot
- interactables catalog
- delay settings
- logging target
- storage/store
- current session state

Behavior:

- Resolve target ID from interactables.
- Click the live DOM element.
- Return diffs/changes.
- Refresh markdown snapshot in runtime.

### `webagent_type()`

LLM-facing inputs:

- `target_id`
- `text`

Runtime inputs:

- `driver`
- current markdown snapshot
- interactables catalog
- delay settings
- logging target
- storage/store
- current session state

Behavior:

- Resolve text-like target.
- Type text.
- Return diffs.
- Refresh markdown snapshot.

### `webagent_clear_text()`

LLM-facing inputs:

- `target_id`

Runtime inputs:

- `driver`
- current markdown snapshot
- interactables catalog
- delay settings
- logging target
- storage/store
- current session state

Behavior:

- Resolve text-like target.
- Clear value.
- Return diffs.
- Refresh markdown snapshot.

## LinkedIn Tools

### `linkedin.fetch_job_listings()`

LLM-facing inputs:

- `keyword`
- `location`
- `pages`
- optional `filter_by`
- optional `filters`

Runtime inputs:

- `driver`
- delay preset
- log path
- runtime search-task cache
- current LinkedIn session state

Behavior:

- Open LinkedIn jobs search.
- Set keyword.
- Set location.
- Click search.
- Open all filters.
- Sync requested filters.
- Show results.
- Fetch requested pages.
- Return compact listing results and search task summary.
- Store page cache internally for later detail fetches.

Supported `pages` forms:

- `1`
- `[1, 2, 5]`
- `"1-3"`
- `"1,3,5"`

Filter shorthand examples:

```json
{
  "experience_level": "Entry level",
  "date_posted": "Past month",
  "job_type": "Full-time",
  "remote": "On-site",
  "easy_apply": true
}
```

### `linkedin.resume_search_task()`

LLM-facing inputs:

- `search_task_id`
- `pages`

Runtime inputs:

- `driver`
- active runtime search-task cache
- delay preset
- log path
- current LinkedIn session state

Behavior:

- Reuse existing search task.
- Skip pages already fetched.
- Fetch missing requested pages.
- Keep accumulated listing state.

### `linkedin.fetch_listings_description()`

LLM-facing inputs:

- `listing_id`

Runtime inputs:

- `driver`
- accumulated listings payload
- internal page cache
- delay preset
- log path
- current LinkedIn session state

Behavior:

- Restore the cached search page for that listing if needed.
- Click the listing card in the visible search result page.
- Read detail panel.
- Parse detail and company profile.
- Return compact AI-facing details.

Important rule:

Use `listing_id` only. Do not use listing indexes in the LLM-facing contract.

# LinkedIn Search Tasks

LinkedIn search is task based, not one-off page scraping.

## Search Task Shape

The model-visible search-task summary should contain:

| Field | Meaning |
|---|---|
| `search_task_id` | Runtime ID for continuing the search |
| `search query` | Keyword/query used |
| `location` | Search location |
| `filter_by` | Usually `Jobs` |
| `filters` | Applied filters |
| `pages requested` | Pages requested this call |
| `pages fetched` | Pages successfully fetched |
| `listings accumulated` | Total current listing count |
| `visible unfetched pages` | Pages visible in pagination but not fetched |
| `inspected listing ids` | Listings already opened for detail |

Internal-only search-task data:

- page cache
- DOM/page restore metadata
- full listing payloads
- browser/session state

## Why Page-Based Fetching

The LLM should choose which pages to fetch because it can reason about scope:

- quick task: page 1
- compare task: pages 1-3
- broad search: pages 1-5, then resume if needed

The tool keeps all listings from fetched pages. If page 3 adds more listings than the user strictly needs, they stay in the accumulated result.

## Resume Behavior

Use `linkedin.resume_search_task()` when:

- the same search should continue
- the model already has a `search_task_id`
- new pages are needed

Do not start a fresh search if the same search task can be resumed.

## Detail Fetch Behavior

Use `linkedin.fetch_listings_description()` only after choosing a specific `listing_id`.

Selection should consider:

- title match
- entry-level fit
- company quality
- salary or compensation clarity
- Easy Apply or apply friction
- listing freshness
- applicant/apply activity
- role detail quality
- user profile fit

The runtime tracks inspected listing IDs so the model can avoid re-opening the same candidate when comparison needs distinct jobs.

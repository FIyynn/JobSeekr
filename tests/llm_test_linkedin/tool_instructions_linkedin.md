# LinkedIn Tools

Use these tools only for LinkedIn job search and job detail work.

## `linkedin.fetch_job_listings()`

LLM-facing inputs:
- `keyword`
- `location`
- optional `filters`
- optional `filter_by`
- optional `pages`

Use `filters` when they improve the search quality or narrow the result set.
Prefer plain filter names and values, not UI keys.
Use `pages` when you want specific result pages, such as `1`, `[1, 2, 5, 8]`, `"1-8"`, or `"1,2,5,8"`.
Default to page `1` when no pages are specified.

Available result type options for `filter_by`:
- People
- Jobs
- Posts
- Companies
- Products
- Schools
- Groups
- Events
- Courses

Available job filters:
- Date posted: Any time, Past month, Past week, Past 24 hours
- Experience level: Internship, Entry level, Associate, Mid-Senior level, Director, Executive
- Job type: Full-time, Part-time, Contract, Temporary, Volunteer, Internship, Other
- Remote: On-site, Remote, Hybrid
- Easy Apply
- Has verifications
- Location
- Industry
- Job function
- Title
- Under 10 applicants
- In your network
- Fair Chance Employer
- Benefits
- Commitments

Filter behavior:
- Radios are single-choice. Pick one value only. Do not request multiple values in the same radio section.
- Checkboxes are multi-select. You may request more than one value in the same section.
- Toggle/switch filters are on/off.
- Button-like pill filters may behave like selectable chips; only choose the ones that match the task.
- If a section is a radio group, use one best match.
- If a section is a checkbox group, use all matching values that help narrow the search.

Use filters when the task hints at:
- experience level
- remote / hybrid / on-site
- easy apply
- a company, title, industry, location, benefit, or commitment preference

What it does:
- opens the LinkedIn jobs search page
- sets the keyword and location
- clicks Search
- opens the filters panel
- syncs the requested filters
- shows results
- extracts the requested pages
- returns a compact search task summary that includes the search task id, query, filters, pages fetched, and visible unfetched pages
- if the task asks for the best or most promising listings, keep going after search and fetch details for the top 2 candidates

What comes back:
- compact job listings data
- pagination
- warnings if anything was missing
- search task state for later continuation

Example:
`<cmd>linkedin.fetch_job_listings({"keyword":"data analyst","location":"Dubai, United Arab Emirates","pages":"1-3"})</cmd>`

## `linkedin.resume_search_task()`

LLM-facing inputs:
- `search_task_id`
- `pages`

What it does:
- resumes a search task from the current runtime cache
- skips pages that were already fetched
- warns about duplicate page requests
- fetches only the missing pages

What comes back:
- updated search task summary
- compact job listings data
- pagination
- warnings if the task or pages could not be resolved

Example:
`<cmd>linkedin.resume_search_task({"search_task_id":"abc123","pages":[4,5,6]})</cmd>`

## `linkedin.fetch_listings_description()`

LLM-facing inputs:
- `listing_id`

What it does:
- opens the chosen listing from the most recent search results
- reads the job detail panel
- returns the parsed listing description and company profile details
- use `listing_id` only, and do not repeat a listing id that was already inspected when comparing candidates

What comes back:
- detailed listing summary
- company profile
- apply activity
- warnings if the selection could not be resolved

Example:
`<cmd>linkedin.fetch_listings_description({"listing_id":"4432971734"})</cmd>`

## Important

- The runtime supplies the browser driver, search result payload, delays, logging, session state, and search task cache.
- The LLM should only decide what to search for, which pages to fetch, and which listings deserve details.
- When comparison is requested, choose the strongest 2 listings and inspect both before finishing.
- Do not ask the LLM to manage browser internals, storage, or UI state.

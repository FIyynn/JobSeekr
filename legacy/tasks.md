# Tasks

- Mongo-backed logging for the LinkedIn wrapper flow is still mocked.
- `fetch_job_listings()` and `fetch_listings_description()` now expose `log_path` metadata, but no real Mongo persistence happens yet.
- Keep browser/session setup outside the LLM-facing wrapper API.
- `webagent_fetch_page()`, `webagent_click()`, `webagent_type()`, and `webagent_clear_text()` are the generic wrapper entry points; storage is still mock-only for now.

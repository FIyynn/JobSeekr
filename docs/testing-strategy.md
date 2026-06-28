# Testing Strategy

Current tests are mostly unit and parser tests. Browser/live tests are still notebook-driven.

## Current Test Areas

| Test file | Purpose |
|---|---|
| `tests/test_markdown.py` | HTML to markdown behavior |
| `tests/test_interact.py` | Interaction parser/diff behavior |
| `tests/test_linkedin_api.py` | High-level LinkedIn wrapper behavior |
| `tests/test_listing_detail.py` | Listing detail extraction/parsing |
| `tests/test_webagent_api.py` | Generic webagent wrapper behavior |
| `tests/test_storage.py` | Embedded Mongo-style store |
| `tests/llm_test/test_llm_runtime_parser.py` | Generic runtime command parsing |
| `tests/llm_test_linkedin/test_llm_linkedin_runtime_parser.py` | LinkedIn runtime command parsing |

## Recommended Test Layers

Unit tests:

- parser helpers
- tool argument validation
- runtime phase routing
- state formatting
- storage methods

Fixture tests:

- LinkedIn listing cards
- LinkedIn filter panels
- LinkedIn detail panels
- generic search result pages
- application forms

Integration tests:

- webagent fetch page and markdown output
- click/type/clear diffs
- LinkedIn search pages 1-3
- resume search task
- fetch listing details by listing_id

Notebook smoke tests:

- start browser
- fetch page
- call one tool
- run one short agent task
- verify final response closes tags correctly

## Commands

Run parser/runtime tests:

```powershell
python -m unittest tests.llm_test.test_llm_runtime_parser tests.llm_test_linkedin.test_llm_linkedin_runtime_parser
```

Run current unit tests:

```powershell
python -m unittest discover tests
```

Before productionizing any tool:

- add fixture coverage
- add mocked driver tests if possible
- keep live LinkedIn tests manual or gated because they depend on login/session state

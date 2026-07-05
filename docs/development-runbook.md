# Development Runbook

## Start Local Browser Tests

Use the repo virtual environment. The existing `.venv` should stay inside the repo.

Open notebooks:

- `extract_demo.ipynb`
- `tests/llm_test/llm_runtime_test.ipynb`
- `tests/llm_test_linkedin/llm_linkedin_runtime_test.ipynb`

## Start Local LLM Server

PowerShell script:

```powershell
cd "D:\_Desktop\Projects\Automations prj\Job_search\JobSeekr_fresh\tests\llm_test"
.\run_llama_server.ps1
```

Current expected local model path:

```text
E:\HF_cache\external_models\qewn3.5\Qwen3.5-9B.Q4_K_M.gguf
```

Current llama.cpp path:

```text
E:\llama.cpp
```

## Generic Webagent Loop

Use:

- `tests/llm_test/llm_runtime_test.ipynb`

Typical task:

```text
Go to DuckDuckGo and search for the cheapest VPS server available.
```

## LinkedIn Loop

Use:

- `tests/llm_test_linkedin/llm_linkedin_runtime_test.ipynb`

Typical task:

```text
Find entry-level software engineering jobs in Dubai. Use filters when they help and fetch pages 1-3, then open the 2 most promising listings and compare them.
```

## Common Debug Checks

- If model repeats old state, reset runtime state keys listed in `context-and-state.md`.
- If model emits malformed tags, parser should reject it.
- If LinkedIn detail fetch repeats a job, inspect `inspected_listing_ids`.
- If pagination is too fast, tune only `go_to_page` delay.
- If markdown shows hidden controls, check `parsers/page_markdown.py`.
- If click/type diffs are noisy, check `browser/interact.py`.
- If LinkedIn filter toggles behave oddly, rerun `notebooks/filter_sync_debug/runtime_test.ipynb` cell 2 only and inspect `notebooks/filter_sync_debug/filter_debug_tools.py`.

## Git Safety

The repo may have many uncommitted changes during experiments. Do not revert unrelated files. Keep changes scoped and commit logical batches.

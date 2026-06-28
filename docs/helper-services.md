# Helper Services

Helper services are interfaces used by workers. They do not own task workflow.

## LLM Server

Role:

- Provide OpenAI-compatible chat completion API.
- Support local llama.cpp and hosted OpenAI-style endpoints.

Current prototype:

- `tests/llm_test/run_llama_server.ps1`
- local API at `http://127.0.0.1:8080/v1/chat/completions`
- OpenAI backend support in `tests/llm_test/llm_runtime.py`

Production direction:

- worker calls LLM server through one stable client
- model provider is runtime config, not tool logic
- prompts/instructions should be versioned

## Web Server

Role:

- General browser navigation and interaction helper.
- Converts page to markdown and returns interactables.
- Executes generic actions by target ref.

Current prototype:

- `browser/webagent.py`
- `browser/markdown.py`
- `browser/interact.py`

Production direction:

- expose `fetch_page`, `click`, `type`, `clear_text`, later `attach`
- keep browser driver/session details hidden from LLM
- save snapshots for every fetched page

## LinkedIn Bot Server

Role:

- LinkedIn-specific browser automation helper.
- Manages Chrome instances and profiles.
- Performs search, filters, pagination, listing details.

Current prototype:

- `browser/linkedin.py`
- `browser/linkedin_jobs.py`
- `tests/llm_test_linkedin/linkedin_runtime.py`

Production direction:

- one server/process can manage many Chrome instances
- each Chrome instance gets a separate browser profile
- workers call LinkedIn Bot Server through high-level API
- login/session handling stays inside the LinkedIn Bot Server

## Browser Profile Rule

Each browser session needs isolated user data:

```text
LinkedIn Bot Server
|- Chrome instance / profile 1
|- Chrome instance / profile 2
|- Chrome instance / profile 3
`- Chrome instance / profile N
```

The LLM should never choose user-data-dir, profile paths, headless mode, or driver options.

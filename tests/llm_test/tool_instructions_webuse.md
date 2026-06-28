# WebUse Tools

Use these as the default tools for browser work.

## `webagent_fetch_page()`

LLM-facing inputs:
- `url`

Runtime inputs:
- `driver`
- `store`
- `log_path`
- `wait_seconds`
- current session state

Example:
`<cmd>webagent_fetch_page({"url":"https://example.com"})</cmd>`

## `webagent_click()`

LLM-facing inputs:
- `target_id`

Runtime inputs:
- `driver`
- current `markdown_text`
- current `interactables`
- `delays`
- `store`
- `log_path`

Example:
`<cmd>webagent_click({"target_id":"i4"})</cmd>`

## `webagent_type()`

LLM-facing inputs:
- `target_id`
- `text`
- optional `click_enter`

Runtime inputs:
- `driver`
- current `markdown_text`
- current `interactables`
- `delays`
- `store`
- `log_path`

Example:
`<cmd>webagent_type({"target_id":"t1","text":"engineer","click_enter":true})</cmd>`

## `webagent_clear_text()`

LLM-facing inputs:
- `target_id`

Runtime inputs:
- `driver`
- current `markdown_text`
- current `interactables`
- `delays`
- `store`
- `log_path`

Example:
`<cmd>webagent_clear_text({"target_id":"t1"})</cmd>`

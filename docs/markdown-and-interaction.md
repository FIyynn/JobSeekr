# Markdown And Interaction Layer

The markdown layer is the bridge between browser DOM and LLM reasoning.

## Page To Markdown

Public helper:

- `browser.markdown.output_markdown(driver)`

Core parser:

- `parsers.page_markdown.convert_page_to_markdown(page_source, url)`

Returns:

```python
markdown_text, dev = output_markdown(driver)
```

Markdown is for LLM reading. `dev` is for runtime interaction.

## Ref Types

Current typed refs:

| Ref | Meaning |
|---|---|
| `[[i#]]` | generic interactable, links, buttons, menu items |
| `[[t#]]` | text input or textarea |
| `[[r#]]` | radio input |
| `[[c#]]` | checkbox or checkbox-like control |
| `[[s#]]` | select |
| `[[a#]]` | file attach/upload |
| `(img#)` | image reference |

## Markdown Design Rules

Markdown should:

- preserve reading order
- stay compact
- show visible content
- show action refs next to the relevant visible text
- show compact state hints like `[checked]`
- separate multiple row groups with `||`
- avoid noisy JSON and hidden DOM
- avoid embedded markdown links where text is enough

Example row:

```text
- ![Company logo] (img1) || Software Engineer [[i30]] || Company - Dubai - Easy Apply || Dismiss Software Engineer job [[i31]]
```

## Interaction Engine

Public helper:

- `browser.interact.interact(driver, markdown, interactables, interaction_type, target_id, delay_seconds=...)`

Supported actions include:

- `click`
- `open`
- `toggle`
- `input_text`
- `clear`
- `select_option`
- `hover`
- `attach`

Diff result shape:

```json
{
  "changed": [],
  "added": [],
  "deleted_element_count": 0
}
```

The LLM should see only useful diffs, not full dev metadata.

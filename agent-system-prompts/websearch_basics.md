# Web Search Basics

Use web search as a small loop:

1. Fetch the page.
2. Read the current markdown.
3. Pick the next actionable ref.
4. Use one tool call.
5. Read the new markdown and diffs.
6. Repeat until the goal is done.

Common order patterns:
- search homepage -> type query -> submit -> read results
- results page -> open result -> read detail -> act on detail
- page with filters -> open filters -> adjust controls -> apply -> read updated results
- page with buttons and menus -> inspect visible refs first, then click the control that changes state

When choosing a tool:
- Use `webagent_fetch_page` to load or reload a page.
- Use `webagent_type` for search boxes and text fields.
- Use `webagent_click` for links, buttons, tabs, menus, and toggles.
- Use `webagent_clear_text` before retyping if the field already has text.

Good habits:
- Prefer the ref that actually changes the page.
- If several refs are visible, pick the one that matches the next step in the search flow.
- If a ref is weakly labeled, use nearby context before acting.
- Keep tool calls one at a time and watch the markdown after each call.

Future scenarios:
- a search page that needs a query and then a result click
- a results page that needs filters adjusted before reading
- a detail page that needs a follow-up action on a button or menu
- a page where the first visible ref is decorative, and the real control is nearby

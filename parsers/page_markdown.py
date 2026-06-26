from __future__ import annotations

import html
import json
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag


BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "div",
    "footer",
    "form",
    "header",
    "main",
    "nav",
    "p",
    "section",
}
HEADING_TAGS = {f"h{i}" for i in range(1, 7)}
INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "summary"}
NOISE_TAGS = {"script", "style", "noscript", "template", "svg", "path", "meta", "link"}
INTERACTABLE_REF_RE = re.compile(r"\[\[([a-z]\d+)\]\]", re.IGNORECASE)
IMAGE_REF_RE = re.compile(r"\((img\d+)\)")
ROLE_INTERACTABLES = {"button", "link", "tab", "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio"}


@dataclass
class RenderState:
    soup: BeautifulSoup
    url: str
    interactables: list[dict[str, Any]]
    images: list[dict[str, Any]]
    counter: int = 0
    image_counter: int = 0
    type_counters: dict[str, int] = field(default_factory=dict)
    handled_file_inputs: set[str] = field(default_factory=set)


def _clean(text: str | None) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ")
    text = text.replace("Â·", "·").replace("â€¢", "•").replace("â€”", "—").replace("â€™", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _stable_clean(text: str | None) -> str:
    text = _clean(text)
    text = re.sub(r"\[\[[a-z]\d+\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(img\d+\)", "", text)
    text = re.sub(r"!\[([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _stable_id(*parts: str) -> str:
    payload = "||".join(part for part in (_stable_clean(part) for part in parts) if part)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


def _row_item_signature(item: dict[str, Any]) -> str:
    bits = [
        item.get("type", ""),
        item.get("text", ""),
        item.get("anchor_text", ""),
        item.get("aria_label", ""),
        item.get("href", ""),
        item.get("value", ""),
        item.get("kind", ""),
        item.get("role", ""),
    ]
    return _stable_id(*[str(bit) for bit in bits if bit is not None])


def _row_signature(line: str, items: list[dict[str, Any]]) -> str:
    visible_line = _stable_clean(line)
    item_signatures = [_row_item_signature(item) for item in items]
    return _stable_id("row", visible_line, *item_signatures)


def _control_state_signature(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    bits: list[str] = []
    for key in ("selected", "checked", "pressed", "expanded", "current", "disabled", "value", "text"):
        if key in state and state.get(key) not in (None, ""):
            bits.append(f"{key}={state.get(key)}")
    return _stable_id(*bits)


def _state_hint(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    input_type = _clean(state.get("input_type", "")).lower()
    selected = state.get("selected")
    if isinstance(selected, str) and selected:
        return f" [selected: {selected}]"
    if input_type in {"checkbox", "radio"}:
        if state.get("checked") is True:
            return " [checked]"
        if state.get("checked") is False:
            return " [unchecked]"
    if input_type == "switch":
        if state.get("checked") is True:
            return " [on]"
        if state.get("checked") is False:
            return " [off]"
    if state.get("pressed") is True:
        return " [pressed]"
    if state.get("checked") is True:
        return " [checked]"
    if state.get("selected") is True:
        return " [selected]"
    if state.get("expanded") is True:
        return " [expanded]"
    current = state.get("current")
    if current:
        if current is True:
            return " [current]"
        return f" [{_clean(str(current))}]"
    if state.get("disabled") is True:
        return " [disabled]"
    return ""


def _truthy_aria(tag: Tag, attr: str) -> bool:
    value = _clean(tag.get(attr, "")).lower()
    return value in {"true", "1", "yes", "on", "checked", "selected", "expanded"}


def _control_state(tag: Tag, soup: BeautifulSoup) -> dict[str, Any]:
    state: dict[str, Any] = {}
    role = _clean(tag.get("role", "")).lower()
    tag_name = tag.name.lower() if tag.name else ""
    input_type = _clean(tag.get("type", "")).lower()
    snapshot_checked = _clean(tag.get("data-codex-checked", "")).lower()
    snapshot_pressed = _clean(tag.get("data-codex-pressed", "")).lower()
    snapshot_expanded = _clean(tag.get("data-codex-expanded", "")).lower()
    snapshot_selected = _clean(tag.get("data-codex-selected", "")).lower()
    snapshot_current = _clean(tag.get("data-codex-current", ""))
    snapshot_input_type = _clean(tag.get("data-codex-input-type", "")).lower()
    if snapshot_input_type and not input_type:
        input_type = snapshot_input_type

    if tag.has_attr("disabled"):
        state["disabled"] = True

    if tag_name == "input":
        if input_type:
            state["input_type"] = input_type
        if input_type in {"checkbox", "radio"}:
            state["checked"] = (
                snapshot_checked == "true"
                or tag.has_attr("checked")
                or _truthy_aria(tag, "aria-checked")
            )
            if input_type == "radio":
                state["selected"] = bool(state["checked"])
        elif input_type in {"button", "submit", "reset"}:
            if tag.has_attr("aria-pressed"):
                state["pressed"] = _truthy_aria(tag, "aria-pressed")
        if tag.has_attr("aria-checked"):
            state["checked"] = _truthy_aria(tag, "aria-checked")

    if tag_name == "button" or role in {"button", "menuitem", "menuitemcheckbox", "menuitemradio"}:
        if snapshot_pressed:
            state["pressed"] = snapshot_pressed == "true"
        elif tag.has_attr("aria-pressed"):
            state["pressed"] = _truthy_aria(tag, "aria-pressed")
        if snapshot_expanded:
            state["expanded"] = snapshot_expanded == "true"
        elif tag.has_attr("aria-expanded"):
            state["expanded"] = _truthy_aria(tag, "aria-expanded")
        if snapshot_checked:
            state["checked"] = snapshot_checked == "true"
        elif tag.has_attr("aria-checked"):
            state["checked"] = _truthy_aria(tag, "aria-checked")

    if tag_name in {"a", "button"} or role == "link":
        aria_current = snapshot_current or _clean(tag.get("aria-current", ""))
        if aria_current:
            state["current"] = aria_current if aria_current not in {"true", "1"} else True

    if tag_name == "select":
        selected = tag.find("option", selected=True)
        if selected:
            selected_text = _visible_text(selected)
            if selected_text:
                state["selected"] = selected_text
                state["value"] = selected_text
        if not state.get("selected"):
            selected_text = _clean(tag.get("data-codex-selected-text", ""))
            if selected_text:
                state["selected"] = selected_text
                state["value"] = selected_text
        if "selected" not in state:
            first_option = tag.find("option")
            if first_option:
                first_text = _visible_text(first_option)
                if first_text:
                    state["value"] = first_text

    if tag_name == "textarea":
        value = _clean(tag.get("value", "")) or _clean(tag.text)
        if value:
            state["value"] = value

    if tag_name == "input" and input_type not in {"checkbox", "radio"}:
        value = _clean(tag.get("value", ""))
        if value:
            state["value"] = value

    state_text = _state_hint(state)
    if state_text:
        state["state_text"] = state_text
    state["signature"] = _control_state_signature(state)
    return state


def _host(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower()
    except Exception:
        return ""


def _absolute_url(base_url: str, href: str) -> str:
    href = _clean(href)
    if not href:
        return ""
    return urljoin(base_url or "", href)


def _is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden"):
        return True
    if str(tag.get("aria-hidden", "")).lower() == "true":
        return True
    style = _clean(tag.get("style", "")).lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    return False


def _visible_text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    return _clean(tag.get_text(" ", strip=True))


def _ancestor_visible_text(tag: Tag, limit: int = 4) -> str:
    current = tag.parent
    depth = 0
    while isinstance(current, Tag) and depth < limit:
        text = _visible_text(current)
        if text:
            return text
        current = current.parent
        depth += 1
    return ""


def _label_from_input(tag: Tag, soup: BeautifulSoup) -> str:
    element_id = _clean(tag.get("id", ""))
    if element_id:
        associated = soup.find("label", attrs={"for": element_id})
        if associated:
            label_text = _visible_text(associated)
            if label_text:
                return label_text

    parent_label = tag.find_parent("label")
    if parent_label is not None:
        label_text = _visible_text(parent_label)
        if label_text:
            return label_text

    for candidate in (
        tag.get("aria-label", ""),
        tag.get("title", ""),
        tag.get("placeholder", ""),
        tag.get("value", ""),
    ):
        cleaned = _clean(candidate)
        if cleaned:
            return cleaned

    return ""


def _is_file_input(tag: Tag) -> bool:
    return tag.name == "input" and _clean(tag.get("type", "")).lower() == "file"


def _is_attach_trigger(tag: Tag, soup: BeautifulSoup) -> bool:
    if tag.name not in {"button", "label"}:
        return False
    text = _label_for(tag, soup) or _visible_text(tag) or _clean(tag.get("aria-label", ""))
    lowered = text.casefold()
    if not lowered:
        return False
    attach_terms = (
        "attach",
        "upload",
        "choose file",
        "browse",
        "select file",
        "resume",
        "cv",
    )
    return any(term in lowered for term in attach_terms)


def _nearest_file_input(tag: Tag, soup: BeautifulSoup) -> Tag | None:
    if _is_file_input(tag):
        return tag
    if tag.name not in {"button", "label"}:
        return None

    element_id = _clean(tag.get("for", ""))
    if element_id:
        direct = soup.find("input", attrs={"id": element_id, "type": "file"})
        if isinstance(direct, Tag):
            return direct

    current = tag.parent
    depth = 0
    while isinstance(current, Tag) and depth < 4:
        direct = current.find("input", attrs={"type": "file"})
        if isinstance(direct, Tag):
            return direct
        current = current.parent
        depth += 1
    return None


def _ref_prefix(tag: Tag, soup: BeautifulSoup) -> str:
    if _is_file_input(tag) or _is_attach_trigger(tag, soup):
        return "a"
    if tag.name == "input":
        input_type = _clean(tag.get("type", "")).lower()
        if input_type == "radio":
            return "r"
        if input_type == "checkbox":
            return "c"
        if input_type in {"text", "search", "email", "tel", "url", "password", "number", "hidden"}:
            return "t"
        if input_type:
            return "t"
        return "t"
    if tag.name == "textarea":
        return "t"
    if tag.name == "select":
        return "s"
    return "i"


def _next_ref_id(state: RenderState, prefix: str) -> str:
    next_index = state.type_counters.get(prefix, 0) + 1
    state.type_counters[prefix] = next_index
    state.counter += 1
    return f"{prefix}{next_index}"


def _label_for(tag: Tag, soup: BeautifulSoup) -> str:
    role = _clean(tag.get("role", "")).lower()
    if role in {"button", "menuitem", "menuitemcheckbox", "menuitemradio"}:
        text = _visible_text(tag)
        aria = _clean(tag.get("aria-label", "") or tag.get("title", ""))
        if text and text.lower() not in {"dismiss", "close", "remove", "x"} and len(text) > 1:
            return text
        if aria:
            return aria
        if tag.has_attr("aria-haspopup") or tag.has_attr("aria-controls") or tag.has_attr("aria-expanded"):
            return "More actions"
        return text or "button"

    if role == "link":
        text = _visible_text(tag)
        if text:
            return text
        return _clean(tag.get("aria-label", "") or tag.get("title", ""))

    if tag.name == "a":
        text = _visible_text(tag)
        if text:
            return text
        return _clean(tag.get("aria-label", "") or tag.get("title", ""))

    if tag.name == "button":
        text = _visible_text(tag)
        aria = _clean(tag.get("aria-label", "") or tag.get("title", ""))
        if text and text.lower() not in {"dismiss", "close", "remove", "x"} and len(text) > 1:
            return text
        if aria:
            return aria
        if tag.has_attr("aria-haspopup") or tag.has_attr("aria-controls") or tag.has_attr("aria-expanded"):
            return "More actions"
        return text or "button"

    if tag.name == "input":
        return _label_from_input(tag, soup)

    if tag.name == "select":
        selected = tag.find("option", selected=True)
        if selected:
            selected_text = _visible_text(selected)
            if selected_text:
                return selected_text
        first_option = tag.find("option")
        if first_option:
            first_text = _visible_text(first_option)
            if first_text:
                return first_text
        return _clean(tag.get("aria-label", "") or tag.get("title", ""))

    if tag.name == "textarea":
        return _clean(tag.get("aria-label", "") or tag.get("placeholder", "") or tag.get("title", ""))

    if tag.name == "summary":
        text = _visible_text(tag)
        if text:
            return text
        return _clean(tag.get("aria-label", "") or tag.get("title", ""))

    return ""


def _interactable_type(tag: Tag) -> str:
    role = _clean(tag.get("role", "")).lower()
    if role in {"button", "menuitem", "menuitemcheckbox", "menuitemradio"}:
        return "button"
    if role == "link":
        return "link"
    if tag.name == "a":
        return "link"
    if tag.name == "button":
        return "button"
    if _is_file_input(tag):
        return "attach"
    if tag.name == "select":
        return "select"
    if tag.name == "textarea":
        return "textarea"
    if tag.name == "summary":
        return "button"
    if tag.name == "input":
        return "input"
    return ""


def _best_css_selector(tag: Tag) -> str:
    tag_id = _clean(tag.get("id", ""))
    if tag_id:
        return f"#{tag_id}"

    for attr in ("data-testid", "data-test-id", "data-test", "aria-controls", "aria-labelledby", "name", "aria-label", "title"):
        value = _clean(tag.get(attr, ""))
        if value:
            safe_value = value.replace('"', '\\"')
            return f'{tag.name}[{attr}="{safe_value}"]'

    href = _clean(tag.get("href", ""))
    if tag.name == "a" and href:
        safe_href = href.replace('"', '\\"')
        return f'a[href="{safe_href}"]'

    classes_value = tag.get("class", [])
    if isinstance(classes_value, str):
        classes = [part for part in classes_value.split() if part]
    else:
        classes = [part for part in classes_value if part]
    if classes:
        class_chain = ".".join(re.sub(r"[^a-zA-Z0-9_-]", "-", cls) for cls in classes[:3])
        return f"{tag.name}.{class_chain}"

    parent = tag.parent
    if isinstance(parent, Tag):
        siblings = [child for child in parent.find_all(tag.name, recursive=False)]
        if len(siblings) > 1:
            index = siblings.index(tag) + 1
            return f"{_best_css_selector(parent)} > {tag.name}:nth-of-type({index})"

    return tag.name


def _is_interactable(tag: Tag) -> bool:
    if tag.name in INTERACTIVE_TAGS:
        return True
    role = _clean(tag.get("role", "")).lower()
    if role in ROLE_INTERACTABLES:
        return True
    if tag.has_attr("onclick"):
        return True
    return False


def _looks_utility_text(text: str) -> bool:
    cleaned = _clean(text).casefold()
    if not cleaned:
        return True
    utility_phrases = (
        "search domain",
        "only include results",
        "redo search",
        "block this site",
        "share feedback",
        "dismiss",
        "remove",
        "close",
        "more actions",
        "menu",
        "button",
    )
    if cleaned in {"http", "https", "link"}:
        return True
    if cleaned.startswith("http://") or cleaned.startswith("https://") or cleaned.startswith("/?"):
        return True
    return any(phrase in cleaned for phrase in utility_phrases)


def _primary_row_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {}

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        item_type = str(item.get("type", "")).lower()
        text = item.get("anchor_text") or item.get("text") or item.get("aria_label") or ""
        href = _clean(item.get("href", ""))
        base = 0
        if item_type == "link":
            base += 300
        elif item_type in {"button", "select", "input", "textarea"}:
            base += 200
        else:
            base += 50
        if text and not _looks_utility_text(text):
            base += 120
        if href and not _looks_utility_text(href):
            base += 20
        if _looks_utility_text(text):
            base -= 200
        if item_type == "image":
            base -= 500
        return base, len(_clean(text)), -int(item.get("position", 10**9))

    return max(items, key=score)


def _record_interactable(tag: Tag, state: RenderState) -> str:
    attach_input = _nearest_file_input(tag, state.soup) if _is_attach_trigger(tag, state.soup) else None
    record_tag = attach_input if attach_input is not None else tag
    label = _label_for(tag, state.soup)
    if attach_input is not None:
        state.handled_file_inputs.add(_clean(attach_input.get("id", "")))
        if not label:
            label = _label_for(attach_input, state.soup)
    if not label and record_tag.name == "a":
        label = _clean(tag.get("href", ""))
    if not label and record_tag.name == "input":
        input_type = _clean(record_tag.get("type", ""))
        label = input_type or "input"
    if not label:
        label = tag.name

    interactable_type = _interactable_type(record_tag) or _clean(record_tag.get("role", "")).lower() or "button"
    ref_id = _next_ref_id(state, _ref_prefix(record_tag, state.soup))
    ref_token = f"[[{ref_id}]]"
    control_state = _control_state(record_tag, state.soup)

    record: dict[str, Any] = {
        "id": ref_id,
        "order": state.counter,
        "type": interactable_type,
        "text": label,
        "anchor_text": label,
        "aria_label": _clean(tag.get("aria-label", "")),
        "role": _clean(record_tag.get("role", "")).lower(),
        "href": "",
        "value": "",
        "state": {
            key: value
            for key, value in control_state.items()
            if key not in {"signature", "state_text"} and value not in ("", None)
        },
        "state_text": control_state.get("state_text", ""),
        "state_signature": control_state.get("signature", ""),
        "locator": {
            "kind": "css",
            "value": _best_css_selector(record_tag),
        },
        "ref": ref_token,
    }

    if record_tag.name == "a":
        record["href"] = _absolute_url(state.url, record_tag.get("href", ""))
    elif record_tag.name == "input":
        record["value"] = _clean(record_tag.get("value", ""))
        if "input_type" not in record["state"]:
            record["state"]["input_type"] = _clean(record_tag.get("type", ""))
        if _is_file_input(record_tag):
            record["value"] = _clean(record_tag.get("value", ""))
            record["accept"] = _clean(record_tag.get("accept", ""))
            if attach_input is not None:
                record["trigger_locator"] = {
                    "kind": "css",
                    "value": _best_css_selector(tag),
                }
    elif record_tag.name == "select":
        record["value"] = _label_for(record_tag, state.soup)
    elif record_tag.name == "textarea":
        record["value"] = _clean(record_tag.get("value", "")) or _clean(record_tag.text)
    elif record_tag.name == "button":
        record["value"] = _clean(record_tag.get("value", ""))

    state.interactables.append(record)
    state_suffix = record.get("state_text", "")
    if record_tag.name == "a" and record["href"]:
        return f"[{label}]({record['href']}){state_suffix} {ref_token}".strip()
    return f"{label}{state_suffix} {ref_token}".strip()


def _record_image(tag: Tag, state: RenderState) -> str:
    alt = _clean(tag.get("alt", "") or tag.get("title", "")) or "image"
    src = _clean(tag.get("src", ""))
    state.image_counter += 1
    ref_id = f"img{state.image_counter}"
    state.images.append(
        {
            "id": ref_id,
            "order": state.image_counter,
            "alt": alt,
            "src": _absolute_url(state.url, src) if src else "",
        }
    )
    return f"![{alt}] ({ref_id})".strip()


def _render_nested_images(tag: Tag, state: RenderState) -> str:
    images: list[str] = []
    for image_tag in tag.find_all("img", recursive=True):
        rendered = _record_image(image_tag, state)
        if rendered:
            images.append(rendered)
    return " ".join(images).strip()


def _render_inline(tag: Tag, state: RenderState) -> str:
    if _is_file_input(tag):
        input_id = _clean(tag.get("id", ""))
        if input_id and input_id in state.handled_file_inputs:
            return ""
        return _record_interactable(tag, state)
    if _is_hidden(tag):
        return ""
    if tag.name == "label" and _clean(tag.get("for", "")):
        return ""
    if _is_interactable(tag):
        if tag.name == "a":
            nested_images = _render_nested_images(tag, state)
            record = _record_interactable(tag, state)
            if nested_images:
                return f"{nested_images} {record}".strip()
            return record
        return _record_interactable(tag, state)

    if tag.name == "img":
        return _record_image(tag, state)

    parts: list[str] = []
    for child in tag.children:
        rendered = _render_node(child, state)
        if rendered:
            parts.append(rendered)
    return _join_inline(parts)


def _join_inline(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if _clean(part)]
    if not cleaned:
        return ""
    text = " ".join(cleaned)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return _clean(text)


def _render_list(tag: Tag, state: RenderState, ordered: bool = False) -> str:
    lines: list[str] = []
    items = [child for child in tag.children if isinstance(child, Tag) and child.name == "li"]
    for idx, item in enumerate(items, start=1):
        item_text = _render_block(item, state)
        if not item_text:
            continue
        prefix = f"{idx}." if ordered else "-"
        for line_idx, line in enumerate(item_text.splitlines()):
            if not line.strip():
                continue
            if line_idx == 0:
                lines.append(f"{prefix} {line}".rstrip())
            else:
                lines.append(f"  {line}".rstrip())
    return "\n".join(lines).strip()


def _render_table(tag: Tag, state: RenderState) -> str:
    rows: list[list[str]] = []
    for tr in tag.find_all("tr", recursive=True):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        row = []
        for cell in cells:
            value = _render_block(cell, state)
            if not value:
                value = _visible_text(cell)
            row.append(re.sub(r"\s+", " ", value).strip())
        if row:
            rows.append(row)

    if not rows:
        return _render_block(tag, state)

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:]

    def _line(cells: list[str]) -> str:
        return "| " + " | ".join(cell or "" for cell in cells) + " |"

    output = [_line(header), "| " + " | ".join("---" for _ in header) + " |"]
    for row in body:
        output.append(_line(row))
    return "\n".join(output).strip()


def _render_block(tag: Tag, state: RenderState) -> str:
    if _is_file_input(tag):
        input_id = _clean(tag.get("id", ""))
        if input_id and input_id in state.handled_file_inputs:
            return ""
        return _record_interactable(tag, state)
    if _is_hidden(tag):
        return ""

    if tag.name == "label" and _clean(tag.get("for", "")):
        return ""

    if tag.name in HEADING_TAGS:
        level = int(tag.name[1])
        text = _render_inline(tag, state)
        return f"{'#' * level} {text}".strip()

    if tag.name == "br":
        return "\n"

    if tag.name == "hr":
        return "---"

    if tag.name == "img":
        return _render_inline(tag, state)

    if tag.name == "li":
        return _render_inline(tag, state)

    if tag.name == "ul":
        return _render_list(tag, state, ordered=False)

    if tag.name == "ol":
        return _render_list(tag, state, ordered=True)

    if tag.name == "table":
        return _render_table(tag, state)

    if tag.name in {"tr", "thead", "tbody", "tfoot"}:
        return "\n".join(
            line
            for child in tag.children
            for line in [_render_node(child, state)]
            if line
        ).strip()

    if _is_interactable(tag):
        return _record_interactable(tag, state)

    if tag.name in {"div", "section", "article", "aside", "main", "header", "footer", "nav", "form", "blockquote"}:
        parts: list[str] = []
        for child in tag.children:
            rendered = _render_node(child, state)
            if rendered:
                parts.append(rendered.strip())
        return "\n\n".join(part for part in parts if part).strip()

    parts = []
    for child in tag.children:
        rendered = _render_node(child, state)
        if rendered:
            parts.append(rendered)
    text = _join_inline(parts)
    return text


def _render_node(node: Any, state: RenderState) -> str:
    if isinstance(node, NavigableString):
        return _clean(str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name in NOISE_TAGS:
        return ""
    if _is_hidden(node):
        return ""

    if node.name in BLOCK_TAGS or node.name in HEADING_TAGS or node.name in {"ul", "ol", "table", "li", "tr", "thead", "tbody", "tfoot"}:
        return _render_block(node, state)

    return _render_inline(node, state)


def _cleanup_markdown(markdown: str) -> str:
    lines = []
    previous_nonempty = None
    previous_blank = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if previous_blank:
                continue
            lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        normalized = line.strip()
        if previous_nonempty == normalized:
            continue
        lines.append(line)
        previous_nonempty = normalized

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def _build_row_items(markdown: str, interactables: list[dict[str, Any]], images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    interactable_by_id = {str(item.get("id", "")): item for item in interactables if item.get("id")}
    image_by_id = {str(item.get("id", "")): item for item in images if item.get("id")}
    rows: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate((markdown or "").splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip():
            continue

        interactable_ids = INTERACTABLE_REF_RE.findall(line)
        image_ids = IMAGE_REF_RE.findall(line)
        if not interactable_ids and not image_ids:
            continue

        ordered_items: list[tuple[int, int, dict[str, Any]]] = []
        for item_id in interactable_ids:
            item = interactable_by_id.get(item_id)
            if not item:
                continue
            token = f"[[{item_id}]]"
            position = line.find(token)
            ordered_items.append(
                (
                    position if position >= 0 else 10**9,
                    1,
                    {
                        "id": item_id,
                        "position": position if position >= 0 else 10**9,
                        "type": item.get("type", ""),
                        "text": item.get("text", ""),
                        "anchor_text": item.get("anchor_text", item.get("text", "")),
                        "aria_label": item.get("aria_label", ""),
                        "role": item.get("role", ""),
                        "state": item.get("state", {}),
                        "state_text": item.get("state_text", ""),
                        "state_signature": item.get("state_signature", ""),
                    },
                )
            )
        for image_id in image_ids:
            image = image_by_id.get(image_id)
            if not image:
                continue
            token = f"({image_id})"
            position = line.find(token)
            ordered_items.append(
                (
                    position if position >= 0 else 10**9,
                    0,
                    {
                        "id": image_id,
                        "position": position if position >= 0 else 10**9,
                        "type": "image",
                        "text": image.get("alt", ""),
                        "anchor_text": image.get("alt", ""),
                        "aria_label": image.get("alt", ""),
                        "role": "",
                        "state": {},
                        "state_text": "",
                        "state_signature": "",
                    },
                )
            )

        ordered_entries = sorted(ordered_items, key=lambda entry: (entry[0], entry[1]))
        items = [entry[2] for entry in ordered_entries]
        ref_tokens = [f"[[{entry[2]['id']}]]" if entry[2]["type"] != "image" else f"({entry[2]['id']})" for entry in ordered_entries]

        stable_item_ids = [_row_item_signature(item) for item in items]
        stable_row_signature = _row_signature(line, items)
        primary_item = _primary_row_item(items)
        primary_item_id = str(primary_item.get("id", ""))
        secondary_items = [item for item in items if item.get("id") and item.get("id") != primary_item_id and item.get("type") != "image"]

        enriched_items: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            enriched = dict(item)
            enriched["stable_id"] = stable_item_ids[index] if index < len(stable_item_ids) else _row_item_signature(item)
            enriched["state_signature"] = item.get("state_signature", "") or _control_state_signature(item.get("state", {}))
            enriched_items.append(enriched)

        rows.append(
            {
                "index": len(rows) + 1,
                "line": line_number,
                "text": line.strip(),
                "ref_tokens": ref_tokens,
                "interactable_ids": interactable_ids,
                "image_ids": image_ids,
                "items": enriched_items,
                "primary_interactable_id": primary_item_id,
                "primary_interactable_stable_id": _row_item_signature(primary_item) if primary_item else "",
                "secondary_interactable_stable_ids": [_row_item_signature(item) for item in secondary_items],
                "stable_id": stable_row_signature,
                "state_signature": _stable_id(
                    "row-state",
                    line,
                    *[str(item.get("state_signature", "") or _control_state_signature(item.get("state", {}))) for item in items if item.get("id")],
                ),
                "has_multiple_actions": len(items) > 1,
                "groups": [],
                "has_multiple_groups": False,
            }
        )

    return rows


def _find_token_span(line: str, token: str, start: int = 0) -> tuple[int, int]:
    if not token:
        return -1, -1
    index = line.find(token, start)
    if index < 0:
        return -1, -1
    return index, index + len(token)


def _find_text_span(line: str, text: str, start: int = 0) -> tuple[int, int]:
    cleaned = _clean(text)
    if not cleaned:
        return -1, -1
    haystack = line.casefold()
    needle = cleaned.casefold()
    index = haystack.find(needle, start)
    if index < 0:
        return -1, -1
    return index, index + len(cleaned)


def _is_group_noise_text(text: str) -> bool:
    cleaned = _clean(text)
    if not cleaned:
        return True
    if cleaned in {"[", "]", "(", ")", "##", "###", "####", "#####", "######", "-", "•"}:
        return True
    if re.fullmatch(r"[\[\]\(\)#\-\s]+", cleaned):
        return True
    return False


def _render_row_groups(line: str, row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    items = row.get("items") or []
    if len(items) < 3:
        return line, []

    ordered_items = sorted(
        [item for item in items if item.get("id")],
        key=lambda item: int(item.get("position", 10**9)),
    )
    if len(ordered_items) < 3:
        return line, []

    groups: list[dict[str, Any]] = []
    parts: list[str] = []
    cursor = 0
    seen_link = False

    for index, item in enumerate(ordered_items):
        item_id = str(item.get("id", ""))
        token = f"({item_id})" if item.get("type") == "image" else f"[[{item_id}]]"
        token_start, token_end = _find_token_span(line, token, cursor)
        if token_start < 0:
            token_start, token_end = _find_token_span(line, token)
        if token_start < 0:
            continue

        label = item.get("anchor_text") or item.get("text") or item.get("aria_label") or ""
        item_type = str(item.get("type", "")).lower()
        label_start = -1
        if item_type == "image":
            label_start = cursor
        elif label:
            label_start, _ = _find_text_span(line, label, cursor)
        if label_start < 0:
            label_start = token_start
        if item_type == "link" and label_start > 0 and line[label_start - 1] == "[":
            label_start -= 1

        if item_type != "image" and label_start > cursor:
            before_text = line[cursor:label_start].strip()
            if not _is_group_noise_text(before_text):
                groups.append(
                    {
                        "kind": "metadata",
                        "text": before_text,
                        "refs": [],
                        "item_ids": [],
                    }
                )
                parts.append(before_text)

        segment = line[label_start:token_end].strip()
        if not segment:
            segment = line[cursor:token_end].strip()
        if not segment:
            segment = line[token_start:token_end].strip()
        if not segment:
            cursor = token_end
            continue

        if item_type == "image":
            kind = "media"
        elif item_type == "link":
            if not seen_link:
                kind = "primary"
                seen_link = True
            else:
                kind = "secondary"
        elif index == 0:
            kind = "control"
        else:
            kind = "action"

        groups.append(
            {
                "kind": kind,
                "text": segment,
                "refs": [item_id],
                "item_ids": [item_id],
            }
        )
        parts.append(segment)
        cursor = token_end

    tail = line[cursor:].strip()
    if tail and not _is_group_noise_text(tail):
        if groups:
            groups[-1]["text"] = f"{groups[-1]['text']} {tail}".strip()
            parts[-1] = f"{parts[-1]} {tail}".strip()
        else:
            return line, []

    if len(groups) < 2:
        return line, []

    grouped_line = " || ".join(part for part in parts if part)
    return grouped_line, groups


def _apply_row_group_separators(markdown: str, rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    lines = markdown.splitlines()
    updated_rows = []
    for row in rows:
        line_index = int(row.get("line", 0)) - 1
        if line_index < 0 or line_index >= len(lines):
            updated_rows.append(row)
            continue
        grouped_line, groups = _render_row_groups(lines[line_index], row)
        if groups:
            lines[line_index] = grouped_line
            stable_row_id = row.get("stable_id", "")
            enriched_groups: list[dict[str, Any]] = []
            for group in groups:
                enriched_group = dict(group)
                enriched_group["stable_id"] = _stable_id(
                    stable_row_id,
                    enriched_group.get("kind", ""),
                    enriched_group.get("text", ""),
                    *[str(item_id) for item_id in enriched_group.get("item_ids", []) if item_id],
                )
                enriched_groups.append(enriched_group)
            updated = dict(row)
            updated["text"] = grouped_line
            updated["groups"] = enriched_groups
            updated["row_text"] = row.get("text", "")
            updated["has_multiple_groups"] = True
            updated_rows.append(updated)
        else:
            updated = dict(row)
            updated.setdefault("groups", [])
            updated["row_text"] = row.get("text", "")
            updated["has_multiple_groups"] = False
            updated_rows.append(updated)
    return "\n".join(lines), updated_rows


def _is_json_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped[0] not in "{[" or stripped[-1] not in "}]":
        return False
    try:
        json.loads(stripped)
    except Exception:
        return False
    return True


def _remove_json_lines(markdown: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in markdown.splitlines():
        if _is_json_line(raw_line):
            continue
        line = raw_line.rstrip()
        if not line.strip():
            if previous_blank:
                continue
            lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _page_metadata(soup: BeautifulSoup, url: str) -> dict[str, Any]:
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = _visible_text(title_tag)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = _visible_text(h1)
    return {
        "url": url,
        "title": title,
        "host": _host(url),
    }


def convert_page_to_markdown(page_source: str, url: str) -> tuple[str, dict[str, Any]]:
    soup = BeautifulSoup(page_source or "", "lxml")
    for tag in soup.find_all(list(NOISE_TAGS)):
        tag.decompose()

    root = soup.body or soup
    state = RenderState(soup=soup, url=url, interactables=[], images=[])
    rendered_parts: list[str] = []
    for child in root.children:
        rendered = _render_node(child, state)
        if rendered:
            rendered_parts.append(rendered)

    markdown = _cleanup_markdown("\n\n".join(rendered_parts))
    rows = _build_row_items(markdown, state.interactables, state.images)
    markdown, rows = _apply_row_group_separators(markdown, rows)
    dev = {
        "page": _page_metadata(soup, url),
        "counts": {
            "interactables": len(state.interactables),
            "images": len(state.images),
            "rows": len(rows),
        },
        "interactables": state.interactables,
        "images": state.images,
        "rows": rows,
    }
    return markdown, dev


def filter_json_lines(markdown: str) -> str:
    return _remove_json_lines(markdown)

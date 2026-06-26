from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
import time
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select

from browser.markdown import output_markdown, _remove_markdown_links


SUPPORTED_ACTIONS = {
    "click",
    "open",
    "toggle",
    "input_text",
    "clear",
    "select_option",
    "hover",
    "attach",
}

ACTION_ALIASES = {
    "type": "input_text",
    "input": "input_text",
    "fill": "input_text",
    "write": "input_text",
    "choose": "select_option",
    "select": "select_option",
    "press": "click",
    "upload": "attach",
    "file": "attach",
}


@dataclass(frozen=True)
class TargetRecord:
    kind: str
    data: dict[str, Any]


def _normalize_action(action: str) -> str:
    normalized = (action or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ACTION_ALIASES.get(normalized, normalized)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _locator_parts(locator: Any) -> tuple[str, str] | None:
    if not isinstance(locator, dict):
        return None
    kind = str(locator.get("kind", "")).strip().lower()
    value = str(locator.get("value", "")).strip()
    if not kind or not value:
        return None
    if kind in {"css", "css_selector"}:
        return By.CSS_SELECTOR, value
    if kind == "xpath":
        return By.XPATH, value
    if kind == "id":
        return By.ID, value
    if kind == "name":
        return By.NAME, value
    if kind == "tag_name":
        return By.TAG_NAME, value
    if kind == "link_text":
        return By.LINK_TEXT, value
    if kind == "partial_link_text":
        return By.PARTIAL_LINK_TEXT, value
    return None


def _find_live_element(driver, target: dict[str, Any]):
    locator = _locator_parts(target.get("locator"))
    if not locator:
        return None
    by, value = locator
    try:
        return driver.find_element(by, value)
    except Exception:
        return None


def _live_value(element: Any) -> str:
    for attr in ("value", "text"):
        try:
            value = getattr(element, attr, "")
            if callable(value):
                value = value()
            if value:
                return str(value)
        except Exception:
            pass
    try:
        return str(element.get_attribute("value") or "")
    except Exception:
        return ""


def _file_path_from_payload(payload: dict[str, str]) -> str:
    for key in ("path", "file", "value", "text"):
        value = (payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _stateful_target_type(target: dict[str, Any]) -> str:
    return _text_clean(target.get("type", "")).lower()


def _stateful_target_label(target: dict[str, Any]) -> str:
    for key in ("anchor_text", "text", "aria_label", "value", "id"):
        value = _text_clean(target.get(key, ""))
        if value:
            return value
    return ""


def _click_stateful_live_target(driver: Any, target: dict[str, Any]) -> bool:
    element = _find_live_element(driver, target)
    if element is None:
        return False

    target_label = _stateful_target_label(target).casefold()
    target_type = _stateful_target_type(target)
    selectors = []
    if target_type in {"radio", "checkbox"}:
        selectors.extend([
            "input[type='radio']",
            "input[type='checkbox']",
            "label",
        ])
    if target_type in {"switch", "toggle"}:
        selectors.extend([
            "input[type='checkbox']",
            "[role='switch']",
            "button[aria-pressed]",
            "label",
        ])
    if target_type == "multiselect_pill":
        selectors.extend([
            "button[aria-pressed]",
            "button",
        ])

    roots = [element]
    try:
        parent = getattr(element, "parent", None) or getattr(element, "find_element", None)
    except Exception:
        parent = None
    try:
        current = element
        for _ in range(3):
            current = current.find_element(By.XPATH, "./parent::*")
            if current is not None:
                roots.append(current)
    except Exception:
        pass

    def _matches(node: Any) -> bool:
        try:
            text = _text_clean(getattr(node, "text", "") or getattr(node, "get_attribute", lambda *_: "")("aria-label") or "")
        except Exception:
            text = ""
        return bool(target_label and target_label in text.casefold()) or bool(text and text.casefold() in target_label)

    for root in roots:
        for selector in selectors:
            try:
                for candidate in root.find_elements(By.CSS_SELECTOR, selector):
                    if selector == "label":
                        if _matches(candidate):
                            candidate.click()
                            return True
                        continue
                    aria = ""
                    try:
                        aria = _text_clean(candidate.get_attribute("aria-label") or "")
                    except Exception:
                        aria = ""
                    text = _text_clean(getattr(candidate, "text", "") or "")
                    if _matches(candidate) or (aria and target_label in aria.casefold()) or (text and target_label in text.casefold()):
                        candidate.click()
                        return True
            except Exception:
                continue

    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def _perform_live_action(driver, target: dict[str, Any], action: str, payload: dict[str, str]) -> tuple[bool, str]:
    element = _find_live_element(driver, target)
    if element is None:
        return False, "Live element could not be found."

    def _click_with_fallback() -> bool:
        try:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    element,
                )
            except Exception:
                pass
            element.click()
            return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                return False

    if action in {"click", "open"}:
        if _stateful_target_type(target) in {"radio", "checkbox", "switch", "toggle", "multiselect_pill"}:
            if _click_stateful_live_target(driver, target):
                return True, f"Clicked {target.get('id', 'target')}."
        if not _click_with_fallback():
            return False, f"Could not click live element: {target.get('id', 'target')}."
        return True, f"Clicked {target.get('id', 'target')}."

    if action == "hover":
        try:
            ActionChains(driver).move_to_element(element).perform()
        except Exception:
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));",
                    element,
                )
            except Exception:
                return False, "Could not hover live element."
        return True, f"Hovered {target.get('id', 'target')}."

    if action == "toggle":
        if not _click_with_fallback():
            return False, f"Could not toggle live element: {target.get('id', 'target')}."
        return True, f"Toggled {target.get('id', 'target')}."

    if action == "clear":
        try:
            element.clear()
        except Exception:
            element.click()
            element.send_keys("")
        return True, f"Cleared {target.get('id', 'target')}."

    if action == "input_text":
        value = payload.get("value", "")
        if not value:
            return False, "Missing value for input_text."
        try:
            element.clear()
        except Exception:
            pass
        element.send_keys(value)
        return True, f"Entered text into {target.get('id', 'target')}."

    if action == "select_option":
        value = payload.get("value", "")
        if not value:
            return False, "Missing option for select_option."
        try:
            selector = getattr(element, "select_by_visible_text", None)
            if callable(selector):
                selector(value)
            else:
                Select(element).select_by_visible_text(value)
            return True, f"Selected option on {target.get('id', 'target')}."
        except Exception:
            try:
                element.click()
                return True, f"Selected option on {target.get('id', 'target')}."
            except Exception as exc:
                return False, f"Could not select option: {exc}"

    if action == "attach":
        path = _file_path_from_payload(payload)
        if not path:
            return False, "Missing path for attach."
        try:
            element.send_keys(path)
            return True, f"Attached file for {target.get('id', 'target')}."
        except Exception:
            try:
                driver.execute_script(
                    """
                    arguments[0].removeAttribute('hidden');
                    arguments[0].style.display = 'block';
                    arguments[0].style.visibility = 'visible';
                    arguments[0].style.opacity = '1';
                    """,
                    element,
                )
            except Exception:
                pass
            try:
                element.send_keys(path)
                return True, f"Attached file for {target.get('id', 'target')}."
            except Exception as exc:
                return False, f"Could not attach file: {exc}"

    return False, f"Unsupported live action: {action}"


def _catalog(interactables: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(interactables, list):
        return [dict(item) for item in interactables if isinstance(item, dict)], []
    if isinstance(interactables, dict):
        items = interactables.get("interactables") or []
        images = interactables.get("images") or []
        return (
            [dict(item) for item in items if isinstance(item, dict)],
            [dict(item) for item in images if isinstance(item, dict)],
        )
    return [], []


def _split_target_id(target_id: str) -> tuple[str, dict[str, str]]:
    raw = (target_id or "").strip()
    if not raw:
        return "", {}
    base, sep, query = raw.partition("?")
    if not sep:
        return base.strip(), {}
    payload: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        payload[key] = value
    return base.strip(), payload


def _display_name(target: dict[str, Any]) -> str:
    if target.get("kind") == "image":
        return target.get("alt") or "image"
    if target.get("type") == "link":
        return target.get("text") or target.get("href") or target.get("id") or ""
    return target.get("text") or target.get("value") or target.get("id") or ""


def _target_ref(target: dict[str, Any]) -> str:
    if target.get("kind") == "image":
        return f"({target.get('id', '')})"
    return f"[[{target.get('id', '')}]]"


def _display_line(target: dict[str, Any]) -> str:
    name = _display_name(target)
    ref = _target_ref(target)
    if target.get("kind") == "image":
        return f"![{name}] {ref}".strip()
    if target.get("type") == "link" and target.get("href"):
        return f"[{name}]({target['href']}) {ref}".strip()
    return f"{name} {ref}".strip()


def _find_target_markdown_line(markdown: str, target: dict[str, Any]) -> str:
    ref = _target_ref(target)
    name = _display_name(target).strip()
    lines = [line.rstrip() for line in (markdown or "").splitlines() if line.strip()]
    for line in lines:
        if ref and ref in line:
            return line.strip()
    if name:
        lowered = name.casefold()
        for line in lines:
            if lowered in line.casefold():
                return line.strip()
    return _display_line(target)


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len([line for line in text.splitlines() if line.strip()]) or 1


def _changed(old_line: str, new_line: str) -> str:
    return f"{old_line} >>> {new_line}"


def _added(block: str) -> str:
    return block.rstrip()


def _deleted(count: int) -> str:
    return f"---\n{max(0, int(count))}\n---"


def _line_list(markdown: str) -> list[str]:
    return [line.rstrip() for line in (markdown or "").splitlines() if line.strip()]


def _text_clean(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _compact_state(state: Any) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    selected = state.get("selected")
    if isinstance(selected, str) and selected:
        return f"selected: {selected}"
    if state.get("selected") is True:
        return "selected"
    if state.get("checked") is True:
        return "checked"
    if "checked" in state and state.get("checked") is False:
        return "unchecked"
    if state.get("pressed") is True:
        return "pressed"
    if "pressed" in state and state.get("pressed") is False:
        return "off"
    if state.get("expanded") is True:
        return "expanded"
    if "expanded" in state and state.get("expanded") is False:
        return "collapsed"
    current = state.get("current")
    if current is True:
        return "current"
    if isinstance(current, str) and current:
        return _text_clean(current)
    if state.get("disabled") is True:
        return "disabled"
    return ""


def _state_transition(before: Any, after: Any) -> str:
    before_state = _compact_state(before)
    after_state = _compact_state(after)
    if before_state == after_state:
        return ""
    if not before_state and not after_state:
        return ""
    return f"{before_state or 'none'} -> {after_state or 'none'}"


def _item_state_line(item: dict[str, Any]) -> str:
    text = _text_clean(item.get("anchor_text") or item.get("text") or item.get("aria_label") or item.get("value") or item.get("id") or "")
    state = _compact_state(item.get("state"))
    if state:
        return f"{text} [{state}]".strip()
    return text


def _diff_text(value: Any) -> str:
    text = str(value or "")
    text = _remove_markdown_links(text)
    text = re.sub(r"([^\[\]\n]+?)\]\((https?://[^)]+)\)", r"\1", text)
    text = re.sub(r"([^\[\]\n]+?)\]\((/[^)]+)\)", r"\1", text)
    text = re.sub(r"\(\s*(https?://[^)]+)\s*\)", "", text)
    return _text_clean(text)


def _can_execute_script(driver: Any) -> bool:
    return callable(getattr(driver, "execute_script", None))


def _mutation_start_script() -> str:
    return """
    (function (element) {
      try {
        if (!window.__codexInteractMutationLog) {
          window.__codexInteractMutationLog = [];
        } else {
          window.__codexInteractMutationLog.length = 0;
        }
        window.__codexInteractMutationLastAt = Date.now();
        if (window.__codexInteractMutationObserver) {
          window.__codexInteractMutationObserver.disconnect();
        }
        window.__codexInteractMutationObserver = new MutationObserver(function (records) {
          for (const record of records) {
            const target = record.target && record.target.nodeType === Node.ELEMENT_NODE
              ? record.target
              : (record.target && record.target.parentElement ? record.target.parentElement : document.body);
            const snapshot = {
              type: record.type,
              tag: target && target.tagName ? target.tagName.toLowerCase() : "",
              text: target ? (target.innerText || target.textContent || "").trim() : "",
              html: target && target.outerHTML ? target.outerHTML : "",
              attribute: record.attributeName || "",
              oldValue: record.oldValue || "",
              added: Array.from(record.addedNodes || []).map(function (node) {
                const element = node && node.nodeType === Node.ELEMENT_NODE ? node : null;
                const text = node ? ((node.innerText || node.textContent || "").trim()) : "";
                return {
                  tag: element && element.tagName ? element.tagName.toLowerCase() : "#text",
                  text: text,
                  html: element && element.outerHTML ? element.outerHTML : (node && node.textContent ? node.textContent : ""),
                };
              }),
              removed: Array.from(record.removedNodes || []).map(function (node) {
                const element = node && node.nodeType === Node.ELEMENT_NODE ? node : null;
                const text = node ? ((node.innerText || node.textContent || "").trim()) : "";
                return {
                  tag: element && element.tagName ? element.tagName.toLowerCase() : "#text",
                  text: text,
                  html: element && element.outerHTML ? element.outerHTML : (node && node.textContent ? node.textContent : ""),
                };
              }),
            };
            window.__codexInteractMutationLog.push(snapshot);
            window.__codexInteractMutationLastAt = Date.now();
          }
        });
        const root = (function () {
          const selector = [
            "li",
            "article",
            "section",
            "main",
            "nav",
            "form",
            "tr",
            "[role='row']",
            "[role='listitem']",
            "[data-occludable-job-id]",
            "[data-job-id]",
            "[data-test]",
          ].join(",");
          if (element && element.closest) {
            const scoped = element.closest(selector);
            if (scoped) {
              return scoped;
            }
          }
          if (element && element.parentElement) {
            return element.parentElement;
          }
          return document.body || document.documentElement;
        })();
        if (root) {
          window.__codexInteractMutationObserver.observe(root, {
            subtree: true,
            childList: true,
            characterData: true,
            attributes: true,
            attributeOldValue: true,
            characterDataOldValue: true,
          });
          window.__codexInteractMutationRoot = root;
        }
      } catch (error) {
        window.__codexInteractMutationLog = [];
        window.__codexInteractMutationLastAt = Date.now();
      }
      return true;
    })(arguments[0] || null);
    """


def _mutation_stop_script() -> str:
    return """
    (function () {
      try {
        const records = Array.isArray(window.__codexInteractMutationLog) ? window.__codexInteractMutationLog.slice() : [];
        if (window.__codexInteractMutationObserver) {
          window.__codexInteractMutationObserver.disconnect();
        }
        window.__codexInteractMutationObserver = null;
        window.__codexInteractMutationRoot = null;
        window.__codexInteractMutationLog = [];
        return records;
      } catch (error) {
        return [];
      }
    })();
    """


def _mutation_status_script() -> str:
    return """
    (function () {
      try {
        return {
          count: Array.isArray(window.__codexInteractMutationLog) ? window.__codexInteractMutationLog.length : 0,
          lastAt: Number(window.__codexInteractMutationLastAt || 0),
        };
      } catch (error) {
        return { count: 0, lastAt: 0 };
      }
    })();
    """


def _scope_snapshot_script() -> str:
    return """
    (function (element) {
      try {
        const selector = [
          "li",
          "article",
          "section",
          "main",
          "nav",
          "form",
          "tr",
          "[role='row']",
          "[role='listitem']",
          "[data-occludable-job-id]",
          "[data-job-id]",
          "[data-test]",
        ].join(",");
        const root = (function () {
          if (element && element.closest) {
            const scoped = element.closest(selector);
            if (scoped) {
              return scoped;
            }
          }
          if (element && element.parentElement) {
            return element.parentElement;
          }
          return document.body || document.documentElement;
        })();
        return root && root.outerHTML ? root.outerHTML : "";
      } catch (error) {
        return "";
      }
    })(arguments[0] || null);
    """


def _start_dom_mutation_tracking(driver: Any, target: dict[str, Any]) -> bool:
    if not _can_execute_script(driver):
        return False
    try:
        element = _find_live_element(driver, target)
        driver.execute_script(_mutation_start_script(), element)
        return True
    except Exception:
        return False


def _capture_scoped_html(driver: Any, target: dict[str, Any]) -> str:
    if not _can_execute_script(driver):
        return ""
    try:
        element = _find_live_element(driver, target)
        html = driver.execute_script(_scope_snapshot_script(), element)
        return str(html or "")
    except Exception:
        return ""


def _stop_dom_mutation_tracking(driver: Any) -> list[dict[str, Any]]:
    if not _can_execute_script(driver):
        return []
    try:
        records = driver.execute_script(_mutation_stop_script())
        if isinstance(records, list):
            return [dict(record) for record in records if isinstance(record, dict)]
    except Exception:
        pass
    return []


def _wait_for_dom_quiet(driver: Any, timeout_seconds: float = 3.0, quiet_ms: int = 350, poll_seconds: float = 0.1) -> None:
    if not _can_execute_script(driver):
        time.sleep(min(timeout_seconds, quiet_ms / 1000.0))
        return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            status = driver.execute_script(_mutation_status_script())
        except Exception:
            break
        if not isinstance(status, dict):
            break
        last_at = float(status.get("lastAt") or 0)
        if last_at and (time.time() * 1000.0 - last_at) >= quiet_ms:
            return
        time.sleep(poll_seconds)


def _markdown_from_html_fragment(html_fragment: str, url: str) -> str:
    fragment = (html_fragment or "").strip()
    if not fragment:
        return ""
    if "<html" not in fragment.lower():
        fragment = f"<html><body>{fragment}</body></html>"

    class _SnapshotDriver:
        def __init__(self, current_url: str, page_source: str):
            self.current_url = current_url
            self.page_source = page_source

    markdown, _ = output_markdown(_SnapshotDriver(url, fragment))
    return markdown


def _scoped_markdown_diffs(driver: Any, target: dict[str, Any], before_scope_html: str) -> dict[str, Any]:
    after_scope_html = _capture_scoped_html(driver, target)
    before_markdown = _markdown_from_html_fragment(before_scope_html, getattr(driver, "current_url", "") or "")
    after_markdown = _markdown_from_html_fragment(after_scope_html, getattr(driver, "current_url", "") or "")
    if before_markdown or after_markdown:
        return _markdown_diffs(before_markdown, after_markdown)
    return {"changed": [], "added": [], "deleted_element_count": 0}


def _summarize_dom_mutations(records: list[dict[str, Any]]) -> dict[str, Any]:
    changed: list[str] = []
    added: list[str] = []
    deleted_count = 0

    for record in records:
        record_type = str(record.get("type", "")).lower()
        tag = str(record.get("tag", "")).strip()
        text = _text_clean(record.get("text", ""))
        html = _text_clean(record.get("html", ""))
        attribute = _text_clean(record.get("attribute", ""))
        old_value = _text_clean(record.get("oldValue", ""))

        if record_type == "attributes":
            summary = f"{tag or 'element'}"
            if attribute:
                summary = f"{summary}[{attribute}]"
            if old_value or text:
                summary = f"{summary}: {old_value} -> {text}".strip()
            changed.append(summary)
            continue

        if record_type == "characterdata":
            summary = f"{tag or 'text'}: {old_value} -> {text}".strip()
            changed.append(summary)
            continue

        if record_type == "childlist":
            for item in record.get("added", []) or []:
                item_text = _text_clean(item.get("text", ""))
                item_html = _text_clean(item.get("html", ""))
                added.append(_added(item_text or item_html or ""))
            removed_items = record.get("removed", []) or []
            deleted_count += len([item for item in removed_items if _text_clean(item.get("text", "")) or _text_clean(item.get("html", ""))])
            continue

        if html or text:
            changed.append(text or html)

    return {"changed": changed, "added": added, "deleted_element_count": deleted_count}


def _diff_score(diffs: dict[str, Any]) -> tuple[int, int, int, int]:
    changed = [str(item) for item in diffs.get("changed", []) or []]
    added = [str(item) for item in diffs.get("added", []) or []]
    deleted = int(diffs.get("deleted_element_count", 0) or 0)
    total_chars = sum(len(item) for item in changed + added)
    return (
        deleted * 50 + len(changed) * 30 + len(added) * 20 + total_chars,
        len(added),
        len(changed),
        deleted,
    )


def _has_meaningful_diff(diffs: dict[str, Any]) -> bool:
    return bool((diffs.get("changed") or []) or (diffs.get("added") or []) or int(diffs.get("deleted_element_count", 0) or 0))


def _choose_diff_summary(*candidates: dict[str, Any]) -> dict[str, Any]:
    usable = [candidate for candidate in candidates if isinstance(candidate, dict) and _has_meaningful_diff(candidate)]
    if not usable:
        return {"changed": [], "added": [], "deleted_element_count": 0}
    scored = sorted((( _diff_score(candidate), index, candidate) for index, candidate in enumerate(usable)), key=lambda item: (item[0], item[1]))
    return scored[0][2]


def _merge_diff_summaries(*diffs: dict[str, Any]) -> dict[str, Any]:
    changed: list[str] = []
    added: list[str] = []
    deleted_count = 0
    seen_changed: set[str] = set()
    seen_added: set[str] = set()

    for diff in diffs:
        if not isinstance(diff, dict):
            continue
        for item in diff.get("changed", []) or []:
            text = str(item)
            if text and text not in seen_changed:
                seen_changed.add(text)
                changed.append(text)
        for item in diff.get("added", []) or []:
            text = str(item)
            if text and text not in seen_added:
                seen_added.add(text)
                added.append(text)
        deleted_count += int(diff.get("deleted_element_count", 0) or 0)

    return {"changed": changed, "added": added, "deleted_element_count": deleted_count}


def _snapshot_page(driver: Any) -> tuple[str, dict[str, Any]]:
    markdown, dev = output_markdown(driver)
    if not isinstance(dev, dict):
        dev = {}
    if not isinstance(dev.get("rows"), list):
        dev["rows"] = []
    return markdown, dev


def _row_ids(row: dict[str, Any]) -> set[str]:
    ids = set()
    for key in ("interactable_ids", "image_ids"):
        for item_id in row.get(key, []) or []:
            if item_id:
                ids.add(str(item_id))
    for item in row.get("items", []) or []:
        item_id = str(item.get("id", "")).strip()
        if item_id:
            ids.add(item_id)
    return ids


def _row_item_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in row.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("stable_id") or item.get("id") or "").strip()
        if item_id:
            mapping[item_id] = item
    return mapping


def _row_for_target(dev: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    base_id, _ = _split_target_id(target_id)
    if not base_id:
        return None
    for row in dev.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        if base_id in _row_ids(row):
            return row
    return None


def _row_state_diffs(before_dev: dict[str, Any], after_dev: dict[str, Any], target_id: str) -> dict[str, Any]:
    before_rows = {str(row.get("stable_id", "")): row for row in before_dev.get("rows", []) or [] if isinstance(row, dict) and row.get("stable_id")}
    after_rows = {str(row.get("stable_id", "")): row for row in after_dev.get("rows", []) or [] if isinstance(row, dict) and row.get("stable_id")}
    before_target = _row_for_target(before_dev, target_id)
    after_target = _row_for_target(after_dev, target_id)
    before_target_id = str(before_target.get("stable_id", "")) if before_target else ""
    after_target_id = str(after_target.get("stable_id", "")) if after_target else ""

    changed: list[str] = []
    added: list[str] = []
    deleted_count = 0

    if before_target and after_target:
        before_text = _diff_text(before_target.get("text", ""))
        after_text = _diff_text(after_target.get("text", ""))
        before_items = _row_item_map(before_target)
        after_items = _row_item_map(after_target)
        shared_item_ids = [item_id for item_id in before_items.keys() if item_id in after_items]
        for item_id in shared_item_ids:
            before_item = before_items.get(item_id, {})
            after_item = after_items.get(item_id, {})
            transition = _state_transition(before_item.get("state"), after_item.get("state"))
            if transition:
                before_line = _item_state_line(before_item)
                after_line = _item_state_line(after_item)
                changed.append(_changed(before_line, after_line))
        if before_text != after_text:
            changed.append(_changed(before_text, after_text))
    elif before_target and not after_target:
        deleted_count += _line_count(_diff_text(before_target.get("text", "")))
    elif after_target and not before_target:
        after_text = _diff_text(after_target.get("text", ""))
        if after_text:
            added.append(after_text)

    protected_ids = {item_id for item_id in (before_target_id, after_target_id) if item_id}

    for stable_id, row in after_rows.items():
        if stable_id in before_rows or stable_id in protected_ids:
            continue
        text = _diff_text(row.get("text", ""))
        if text:
            added.append(text)

    for stable_id, row in before_rows.items():
        if stable_id in after_rows or stable_id in protected_ids:
            continue
        deleted_count += _line_count(_diff_text(row.get("text", "")))

    return {"changed": changed, "added": added, "deleted_element_count": deleted_count}


def _markdown_diffs(before: str, after: str) -> dict[str, Any]:
    before_lines = [_diff_text(line) for line in _line_list(before)]
    after_lines = [_diff_text(line) for line in _line_list(after)]
    changed: list[str] = []
    added: list[str] = []
    deleted_count = 0

    matcher = SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for line in before_lines[i1:i2]:
                deleted_count += _line_count(line)
            continue
        if tag == "insert":
            for line in after_lines[j1:j2]:
                added.append(_added(line))
            continue
        if tag == "replace":
            for line in before_lines[i1:i2]:
                deleted_count += _line_count(line)
            for line in after_lines[j1:j2]:
                added.append(_added(line))

    return {"changed": changed, "added": added, "deleted_element_count": deleted_count}


def _preview_diffs(target: dict[str, Any], action: str, value: str, markdown: str) -> dict[str, Any]:
    if action == "toggle":
        old_line, _ = _toggle_preview(target)
        return {"changed": [], "added": [], "deleted_element_count": _line_count(old_line)}
    if action == "input_text":
        changed, added, deleted_count = _input_preview(target, value, markdown)
        if changed:
            deleted_count = sum(_line_count(entry.split(" >>> ", 1)[0]) for entry in changed)
        return {"changed": [], "added": added, "deleted_element_count": deleted_count}
    if action == "clear":
        changed, added, deleted_count = _clear_preview(target, markdown)
        return {"changed": [], "added": added, "deleted_element_count": deleted_count}
    if action == "select_option":
        changed, added, deleted_count = _select_preview(target, value, markdown)
        if changed:
            deleted_count = sum(_line_count(entry.split(" >>> ", 1)[0]) for entry in changed)
        return {"changed": [], "added": added, "deleted_element_count": deleted_count}
    return {"changed": [], "added": [], "deleted_element_count": 0}


def _wait_for_markdown_change(driver, before_markdown: str, timeout_seconds: float = 3.0, poll_seconds: float = 0.25) -> str:
    deadline = time.time() + timeout_seconds
    current = before_markdown
    while time.time() < deadline:
        current, _ = output_markdown(driver)
        if current != before_markdown:
            return current
        time.sleep(poll_seconds)
    return current


def _resolve_target(interactables: Any, target_id: str) -> tuple[TargetRecord | None, dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    base_id, payload = _split_target_id(target_id)
    items, images = _catalog(interactables)
    if not base_id:
        return None, payload, items, images

    for item in items:
        if str(item.get("id", "")) == base_id:
            return TargetRecord(kind="interactable", data=deepcopy(item)), payload, items, images
    for image in images:
        if str(image.get("id", "")) == base_id:
            return TargetRecord(kind="image", data=deepcopy(image)), payload, items, images
    return None, payload, items, images


def _action_from_payload(action: str, payload: dict[str, str]) -> dict[str, str]:
    if not payload:
        return {}
    if action == "select_option":
        value = payload.get("option") or payload.get("value") or payload.get("selected") or ""
        return {"value": value}
    if action == "input_text":
        value = payload.get("value") or payload.get("text") or ""
        return {"value": value}
    if action == "attach":
        value = _file_path_from_payload(payload)
        return {"path": value}
    return payload


def _is_toggleable(target: dict[str, Any]) -> bool:
    state = target.get("state")
    if _is_mapping(state) and "checked" in state:
        return True
    target_type = str(target.get("type", "")).lower()
    return target_type in {"checkbox", "radio", "switch", "toggle"}


def _toggle_preview(target: dict[str, Any]) -> tuple[str, str]:
    current = target.get("state", {}).get("checked")
    if current is None:
        current = False
    current_bool = bool(current)
    next_bool = not current_bool
    old_line = _find_target_markdown_line("", target)
    old_state = "checked" if current_bool else "unchecked"
    new_state = "checked" if next_bool else "unchecked"
    if old_state in old_line:
        new_line = old_line.replace(old_state, new_state, 1)
    else:
        new_line = f"{old_line} ({new_state})"
    return old_line, new_line


def _input_preview(target: dict[str, Any], value: str, markdown: str) -> tuple[list[str], list[str], int]:
    value = value or ""
    current_value = str(target.get("value", "") or "")
    base_line = _find_target_markdown_line(markdown, target)
    if not value:
        return [], [], 0
    if "\n" in value and not current_value.strip():
        return [], [_added(value)], 0
    new_line = _changed(
        base_line if current_value == "" else f"{base_line}",
        f"{_display_name(target)}: {value} {_target_ref(target)}".strip(),
    )
    return [new_line], [], 1


def _clear_preview(target: dict[str, Any], markdown: str) -> tuple[list[str], list[str], int]:
    current_value = str(target.get("value", "") or "")
    if not current_value.strip():
        return [], [], 0
    count = _line_count(current_value)
    return [], [], count


def _select_preview(target: dict[str, Any], value: str, markdown: str) -> tuple[list[str], list[str], int]:
    value = value or ""
    if not value:
        return [], [], 0
    base_line = _find_target_markdown_line(markdown, target)
    new_line = f"{_display_name(target)}: {value} {_target_ref(target)}".strip()
    return [_changed(base_line, new_line)], [], 1


def _noop_result(target: dict[str, Any], interaction_type: str, target_id: str, payload: dict[str, str]) -> dict[str, Any]:
    return {
        "status": "noop",
        "interaction_type": interaction_type,
        "requested_target_id": target_id,
        "target_id": target.get("id", target_id),
        "target": target,
        "payload": payload,
        "diffs": {
            "changed": [],
            "added": [],
            "deleted_element_count": 0,
        },
        "message": "Resolved, but no markdown mutation was needed.",
    }


def _error_result(interaction_type: str, target_id: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "interaction_type": interaction_type,
        "requested_target_id": target_id,
        "target_id": "",
        "target": None,
        "payload": {},
        "diffs": {
            "changed": [],
            "added": [],
            "deleted_element_count": 0,
        },
        "message": message,
    }


def interact(
    driver: Any,
    markdown: str,
    interactables: Any,
    interaction_type: str,
    target_id: str,
    delay_seconds: float | int = 0,
) -> dict[str, Any]:
    normalized_action = _normalize_action(interaction_type)
    if normalized_action not in SUPPORTED_ACTIONS:
        return _error_result(
            normalized_action or interaction_type,
            target_id,
            f"Unsupported interaction type: {interaction_type}",
        )

    target_record, payload, _, _ = _resolve_target(interactables, target_id)
    if not target_record:
        return _error_result(
            normalized_action,
            target_id,
            f"Target not found: {target_id}",
        )

    target = target_record.data
    target = {"kind": target_record.kind, **target}
    resolved_payload = _action_from_payload(normalized_action, payload)
    target_kind = target_record.kind
    before_markdown = markdown
    can_scope = _can_execute_script(driver)
    before_scope_html = _capture_scoped_html(driver, target)
    before_snapshot_markdown, before_snapshot_dev = _snapshot_page(driver)
    if normalized_action in {"click", "open", "hover"}:
        ok, message = _perform_live_action(driver, target, normalized_action, resolved_payload)
        if not ok:
            return _error_result(normalized_action, target_id, message)
        if delay_seconds and float(delay_seconds) > 0:
            time.sleep(float(delay_seconds))
        after_snapshot_markdown, after_snapshot_dev = _snapshot_page(driver)
        diffs = _row_state_diffs(before_snapshot_dev, after_snapshot_dev, target_id)
        if not _has_meaningful_diff(diffs) and before_snapshot_markdown != after_snapshot_markdown:
            diffs = _markdown_diffs(before_snapshot_markdown, after_snapshot_markdown)
        return {
            "status": "success",
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message,
        }

    if normalized_action == "toggle":
        if not _is_toggleable(target):
            return _error_result(
                normalized_action,
                target_id,
                f"Target is not toggleable: {target_id}",
            )
        _start_dom_mutation_tracking(driver, target)
        ok, message = _perform_live_action(driver, target, normalized_action, resolved_payload)
        if not ok:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, message)
        _wait_for_dom_quiet(driver)
        if delay_seconds and float(delay_seconds) > 0:
            time.sleep(float(delay_seconds))
        records = _stop_dom_mutation_tracking(driver)
        after_snapshot_markdown, after_snapshot_dev = _snapshot_page(driver)
        preview_diffs = _preview_diffs(target, normalized_action, "", markdown)
        record_diffs = _summarize_dom_mutations(records) if records else {"changed": [], "added": [], "deleted_element_count": 0}
        scoped_diffs = _scoped_markdown_diffs(driver, target, before_scope_html) if can_scope and before_scope_html else {"changed": [], "added": [], "deleted_element_count": 0}
        snapshot_diffs = _row_state_diffs(before_snapshot_dev, after_snapshot_dev, target_id)
        if not _has_meaningful_diff(snapshot_diffs) and before_snapshot_markdown != after_snapshot_markdown:
            snapshot_diffs = _markdown_diffs(before_snapshot_markdown, after_snapshot_markdown)
        diffs = _choose_diff_summary(snapshot_diffs, record_diffs, scoped_diffs, preview_diffs)
        return {
            "status": "success",
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message or f"Toggled {target_kind} target.",
        }

    if normalized_action == "attach":
        target_type = str(target.get("type", "")).lower()
        input_type = str((target.get("state") or {}).get("input_type", "")).lower()
        if target_type != "attach" and input_type != "file":
            return _error_result(
                normalized_action,
                target_id,
                f"Target is not attachable: {target_id}",
            )
        path = _file_path_from_payload(resolved_payload)
        if not path:
            return _error_result(
                normalized_action,
                target_id,
                "Missing path for attach.",
            )
        _start_dom_mutation_tracking(driver, target)
        ok, message = _perform_live_action(driver, target, normalized_action, resolved_payload)
        if not ok:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, message)
        _wait_for_dom_quiet(driver)
        if delay_seconds and float(delay_seconds) > 0:
            time.sleep(float(delay_seconds))
        records = _stop_dom_mutation_tracking(driver)
        after_snapshot_markdown, after_snapshot_dev = _snapshot_page(driver)
        record_diffs = _summarize_dom_mutations(records) if records else {"changed": [], "added": [], "deleted_element_count": 0}
        scoped_diffs = _scoped_markdown_diffs(driver, target, before_scope_html) if can_scope and before_scope_html else {"changed": [], "added": [], "deleted_element_count": 0}
        snapshot_diffs = _row_state_diffs(before_snapshot_dev, after_snapshot_dev, target_id)
        if not _has_meaningful_diff(snapshot_diffs) and before_snapshot_markdown != after_snapshot_markdown:
            snapshot_diffs = _markdown_diffs(before_snapshot_markdown, after_snapshot_markdown)
        diffs = _choose_diff_summary(snapshot_diffs, record_diffs, scoped_diffs)
        return {
            "status": "success",
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message or f"Attached file to {target.get('id', target_id)}.",
        }

    if normalized_action == "input_text":
        value = resolved_payload.get("value", "")
        if not value:
            return _error_result(
                normalized_action,
                target_id,
                "Missing value for input_text.",
            )
        _start_dom_mutation_tracking(driver, target)
        element = _find_live_element(driver, target)
        if element is None:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, "Live element could not be found.")
        half_delay = float(delay_seconds) / 2.0 if delay_seconds and float(delay_seconds) > 0 else 0.0
        split_index = max(1, len(value) // 2) if len(value) > 1 else len(value)
        first_value = value[:split_index]
        second_value = value[split_index:]
        message = f"Entered text into {target.get('id', target_id)}."
        try:
            element.clear()
        except Exception:
            pass
        try:
            if first_value:
                element.send_keys(first_value)
        except Exception as exc:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, f"Could not type first half: {exc}")
        if half_delay > 0:
            time.sleep(half_delay)
        after_first_markdown, after_first_dev = _snapshot_page(driver)
        if second_value:
            try:
                element.send_keys(second_value)
            except Exception as exc:
                _stop_dom_mutation_tracking(driver)
                return _error_result(normalized_action, target_id, f"Could not type second half: {exc}")
        if half_delay > 0:
            time.sleep(half_delay)
        _wait_for_dom_quiet(driver)
        records = _stop_dom_mutation_tracking(driver)
        after_second_markdown, after_second_dev = _snapshot_page(driver)
        preview_diffs = _preview_diffs(target, normalized_action, value, markdown)
        record_diffs = _summarize_dom_mutations(records) if records else {"changed": [], "added": [], "deleted_element_count": 0}
        scoped_diffs = _scoped_markdown_diffs(driver, target, before_scope_html) if can_scope and before_scope_html else {"changed": [], "added": [], "deleted_element_count": 0}
        first_snapshot_diffs = _row_state_diffs(before_snapshot_dev, after_first_dev, target_id)
        if not _has_meaningful_diff(first_snapshot_diffs) and before_snapshot_markdown != after_first_markdown:
            first_snapshot_diffs = _markdown_diffs(before_snapshot_markdown, after_first_markdown)
        second_snapshot_diffs = _row_state_diffs(after_first_dev, after_second_dev, target_id)
        if not _has_meaningful_diff(second_snapshot_diffs) and after_first_markdown != after_second_markdown:
            second_snapshot_diffs = _markdown_diffs(after_first_markdown, after_second_markdown)
        diffs = _merge_diff_summaries(first_snapshot_diffs, second_snapshot_diffs, record_diffs, scoped_diffs, preview_diffs)
        status = "success" if (diffs["added"] or diffs["deleted_element_count"]) else "noop"
        return {
            "status": status,
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message,
        }

    if normalized_action == "clear":
        _start_dom_mutation_tracking(driver, target)
        ok, message = _perform_live_action(driver, target, normalized_action, resolved_payload)
        if not ok:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, message)
        _wait_for_dom_quiet(driver)
        if delay_seconds and float(delay_seconds) > 0:
            time.sleep(float(delay_seconds))
        records = _stop_dom_mutation_tracking(driver)
        after_snapshot_markdown, after_snapshot_dev = _snapshot_page(driver)
        preview_diffs = _preview_diffs(target, normalized_action, "", markdown)
        record_diffs = _summarize_dom_mutations(records) if records else {"changed": [], "added": [], "deleted_element_count": 0}
        scoped_diffs = _scoped_markdown_diffs(driver, target, before_scope_html) if can_scope and before_scope_html else {"changed": [], "added": [], "deleted_element_count": 0}
        snapshot_diffs = _row_state_diffs(before_snapshot_dev, after_snapshot_dev, target_id)
        if not _has_meaningful_diff(snapshot_diffs) and before_snapshot_markdown != after_snapshot_markdown:
            snapshot_diffs = _markdown_diffs(before_snapshot_markdown, after_snapshot_markdown)
        diffs = _choose_diff_summary(snapshot_diffs, record_diffs, scoped_diffs, preview_diffs)
        return {
            "status": "success",
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message,
        }

    if normalized_action == "select_option":
        value = resolved_payload.get("value", "")
        if not value:
            return _error_result(
                normalized_action,
                target_id,
                "Missing option for select_option.",
            )
        _start_dom_mutation_tracking(driver, target)
        ok, message = _perform_live_action(driver, target, normalized_action, resolved_payload)
        if not ok:
            _stop_dom_mutation_tracking(driver)
            return _error_result(normalized_action, target_id, message)
        _wait_for_dom_quiet(driver)
        if delay_seconds and float(delay_seconds) > 0:
            time.sleep(float(delay_seconds))
        records = _stop_dom_mutation_tracking(driver)
        after_snapshot_markdown, after_snapshot_dev = _snapshot_page(driver)
        preview_diffs = _preview_diffs(target, normalized_action, value, markdown)
        record_diffs = _summarize_dom_mutations(records) if records else {"changed": [], "added": [], "deleted_element_count": 0}
        scoped_diffs = _scoped_markdown_diffs(driver, target, before_scope_html) if can_scope and before_scope_html else {"changed": [], "added": [], "deleted_element_count": 0}
        snapshot_diffs = _row_state_diffs(before_snapshot_dev, after_snapshot_dev, target_id)
        if not _has_meaningful_diff(snapshot_diffs) and before_snapshot_markdown != after_snapshot_markdown:
            snapshot_diffs = _markdown_diffs(before_snapshot_markdown, after_snapshot_markdown)
        diffs = _choose_diff_summary(snapshot_diffs, record_diffs, scoped_diffs, preview_diffs)
        return {
            "status": "success",
            "interaction_type": normalized_action,
            "requested_target_id": target_id,
            "target_id": target.get("id", target_id),
            "target": target,
            "payload": resolved_payload,
            "diffs": diffs,
            "message": message,
        }

    return _error_result(
        normalized_action,
        target_id,
        f"Unsupported interaction type: {interaction_type}",
    )

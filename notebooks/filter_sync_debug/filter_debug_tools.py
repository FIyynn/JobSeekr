from __future__ import annotations

import importlib
import json
from html.parser import HTMLParser
from typing import Any

from selenium.webdriver.common.by import By


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def _strip_filter_prefix(text: str) -> str:
    normalized = _normalize(text)
    if normalized.casefold().startswith("filter by "):
        return normalized[10:].strip()
    return normalized


class _JobTypeHtmlProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_li = False
        self.in_hidden = False
        self.in_visible = False
        self.current: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        classes = attrs.get("class", "")
        if tag == "li" and "search-reusables__filter-value-item" in classes:
            self.in_li = True
            self.current = {"hidden": "", "visible": ""}
        elif self.in_li and tag == "span" and "visually-hidden" in classes:
            self.in_hidden = True
        elif self.in_li and tag == "span":
            self.in_visible = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self.in_li:
            row = {
                "hidden_text": _strip_filter_prefix(self.current.get("hidden", "")),
                "visible_text": _normalize(self.current.get("visible", "")),
            }
            self.rows.append(row)
            self.in_li = False
            self.in_hidden = False
            self.in_visible = False
            self.current = {}
        elif tag == "span":
            self.in_hidden = False
            self.in_visible = False

    def handle_data(self, data: str) -> None:
        if not self.in_li:
            return
        if self.in_hidden:
            self.current["hidden"] = f"{self.current.get('hidden', '')} {data}"
        elif self.in_visible:
            self.current["visible"] = f"{self.current.get('visible', '')} {data}"


def probe_job_type_html(html: str) -> dict[str, Any]:
    parser = _JobTypeHtmlProbe()
    parser.feed(html)
    rows = []
    for row in parser.rows:
        hidden = _normalize(row.get("hidden_text", ""))
        visible = _normalize(row.get("visible_text", ""))
        rows.append(
            {
                "hidden_text": hidden,
                "visible_text": visible,
                "root_text": " || ".join(part for part in [hidden, visible] if part),
                "matches_full_time": hidden.casefold() == "full-time",
                "matches_internship": hidden.casefold() == "internship",
                "matches_temporary": hidden.casefold() == "temporary",
            }
        )
    return {"rows": rows, "row_count": len(rows)}


def _checkbox_row_state(item) -> dict[str, Any]:
    checkbox = item.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
    label = None
    try:
        label = item.find_element(By.CSS_SELECTOR, "label")
    except Exception:
        label = None
    hidden_text = ""
    try:
        hidden = item.find_element(By.CSS_SELECTOR, ".visually-hidden")
        hidden_text = _strip_filter_prefix(hidden.text)
    except Exception:
        hidden_text = ""
    visible_text = ""
    if label is not None:
        try:
            visible_text = _normalize(label.text)
        except Exception:
            visible_text = ""
    try:
        root_text = _normalize(item.text)
    except Exception:
        root_text = ""
    return {
        "text": hidden_text or visible_text,
        "root_text": root_text,
        "hidden_text": hidden_text,
        "visible_text": visible_text,
        "checked": checkbox.is_selected(),
    }


def _match_choice(targets: dict[str, bool], text: str) -> tuple[str, bool | None]:
    normalized = _normalize(text)
    for target_name, target_state in targets.items():
        if normalized == _normalize(target_name):
            return target_name, target_state
    return "", None


def trace_filter_decisions(driver, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_payload(payload)
    desired_by_section: dict[str, dict[str, bool]] = {}
    for item in normalized.get("filters", []):
        section = _normalize(item.get("section", ""))
        if not section:
            continue
        inputs = item.get("inputs", []) if isinstance(item.get("inputs", []), list) else []
        desired_by_section[section] = {_normalize(choice.get("name", "")): bool(choice.get("state")) for choice in inputs if _normalize(choice.get("name", ""))}

    snapshot = collect_filters_state(driver)
    trace: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for section in snapshot.get("sections", []):
        section_name = _normalize(section.get("section", ""))
        desired_targets = desired_by_section.get(section_name, {})
        for item in section.get("items", []):
            hidden_text = _normalize(item.get("hidden_text", ""))
            root_text = _normalize(item.get("root_text", ""))
            matched_target, desired_state = _match_choice(desired_targets, hidden_text)
            if matched_target:
                action = "select" if desired_state else "clear"
                reason = f"matched text hint {matched_target!r}"
            else:
                action = "clear" if section_name.casefold() == "job type" else "ignore"
                reason = "no exact hidden text match"
            if section_name.casefold() == "job type" and not matched_target:
                unmatched.append(
                    {
                        "section": section_name,
                        "row_text": item.get("text", ""),
                        "root_text": root_text,
                        "hint_text": hidden_text,
                        "checked": item.get("checked", False),
                    }
                )
            trace.append(
                {
                    "section": section_name,
                    "row_text": item.get("text", ""),
                    "root_text": root_text,
                    "hint_text": hidden_text,
                    "checked": item.get("checked", False),
                    "matched_hint": matched_target,
                    "expected_checked": desired_state,
                    "decision": action,
                    "reason": reason,
                    "final_checked": desired_state if desired_state is not None else False,
                }
            )
    return {
        "payload": normalized,
        "snapshot": snapshot,
        "trace": trace,
        "section_targets": desired_by_section,
        "unmatched_rows": unmatched,
    }


def trace_section(driver, payload: dict[str, Any], section_name: str) -> dict[str, Any]:
    trace = trace_filter_decisions(driver, payload)
    section_name_norm = _normalize(section_name)
    section_rows = [row for row in trace["trace"] if _normalize(row.get("section", "")) == section_name_norm]
    unmatched_rows = [row for row in trace.get("unmatched_rows", []) if _normalize(row.get("section", "")) == section_name_norm]
    return {
        "section": section_name_norm,
        "targets": trace.get("section_targets", {}).get(section_name_norm, {}),
        "rows": section_rows,
        "unmatched_rows": unmatched_rows,
    }


def collect_filters_state(driver) -> dict[str, Any]:
    linkedin_jobs = importlib.import_module("browser.linkedin_jobs")
    snapshot = linkedin_jobs._fast_filters_snapshot(driver, verbose=False)
    sections: list[dict[str, Any]] = []
    for section in snapshot.get("filters", []):
        section_name = _normalize(section.get("section", "")) or "unknown"
        section_type = _normalize(section.get("type", ""))
        items: list[dict[str, Any]] = []
        for item in section.get("inputs", []):
            name = _normalize(item.get("name", ""))
            state = bool(item.get("state"))
            items.append(
                {
                    "text": name,
                    "root_text": name,
                    "hidden_text": name,
                    "visible_text": name,
                    "checked": state,
                }
            )
        sections.append(
            {
                "section": section_name,
                "type": section_type,
                "count": len(items),
                "selected_count": sum(1 for item in items if item.get("checked")),
                "items": items,
            }
        )
    return {
        "url": getattr(driver, "current_url", ""),
        "title": getattr(driver, "title", ""),
        "sections": sections,
    }


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    linkedin = importlib.import_module("browser.linkedin")
    if "filters" in payload and isinstance(payload["filters"], list):
        return {"filter_by": payload.get("filter_by", "Jobs"), "filters": payload["filters"]}
    if "filters" in payload and isinstance(payload["filters"], dict):
        return {
            "filter_by": payload.get("filter_by", "Jobs"),
            "filters": linkedin._normalize_filters_payload(payload["filters"]),
        }
    return {
        "filter_by": payload.get("filter_by", "Jobs"),
        "filters": linkedin._normalize_filters_payload(payload),
    }


def run_filter_sync_trace(driver, payload: dict[str, Any], delay_seconds: float = 0.2, verbose: bool = True) -> dict[str, Any]:
    linkedin_jobs = importlib.import_module("browser.linkedin_jobs")
    linkedin_jobs = importlib.reload(linkedin_jobs)
    normalized = _normalize_payload(payload)
    before = collect_filters_state(driver)
    job_type_trace = trace_section(driver, payload, "Job type")
    print("=== BEFORE ===")
    print(json.dumps(before, indent=2, ensure_ascii=False))
    print("=== PAYLOAD ===")
    print(json.dumps(normalized, indent=2, ensure_ascii=False))
    print("=== JOB TYPE TRACE ===")
    print(json.dumps(job_type_trace, indent=2, ensure_ascii=False))
    result = linkedin_jobs.sync_filters_state(driver, normalized, delay_seconds=delay_seconds, verbose=verbose)
    after = collect_filters_state(driver)
    after_job_type_trace = trace_section(driver, payload, "Job type")
    print("=== RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=== JOB TYPE TRACE AFTER ===")
    print(json.dumps(after_job_type_trace, indent=2, ensure_ascii=False))
    print("=== AFTER ===")
    print(json.dumps(after, indent=2, ensure_ascii=False))
    return {"before": before, "payload": normalized, "result": result, "after": after, "job_type_before": job_type_trace, "job_type_after": after_job_type_trace}

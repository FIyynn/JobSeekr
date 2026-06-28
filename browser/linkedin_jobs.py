from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://www.linkedin.com"
JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search"
GLOBAL_NAV_SELECTOR = "header#global-nav"
DEFAULT_WAIT_SECONDS = 15
KEYWORD_INPUT_SELECTOR = "input[aria-label='Search by title, skill, or company']"
LOCATION_INPUT_SELECTOR = "input[aria-label='City, state, or zip code']"
SEARCH_BUTTON_SELECTOR = "button.jobs-search-box__submit-button"
ALL_FILTERS_SELECTOR = "button[aria-label^='Show all filters']"
FILTER_MODAL_SELECTOR = "div[data-test-modal-container]"
RESULT_TYPE_TRIGGER_SELECTOR = "button.search-reusables__vertical-select-trigger"
FILTER_SECTION_SELECTOR = "li.search-reusables__secondary-filters-filter"
SHOW_RESULTS_SELECTOR = "button[data-test-reusables-filters-modal-show-results-button='true']"
SHOW_RESULTS_TEXT = "results"
LISTING_SELECTOR = "li[data-occludable-job-id]"
JOB_DETAIL_WRAPPER_SELECTOR = "div.jobs-search__job-details--wrapper"
PAGE_BUTTON_SELECTOR = "button.jobs-search-pagination__indicator-button"


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sync_log(verbose: bool, message: str, started_at: float | None = None) -> None:
    if not verbose:
        return
    stamp = _stamp()
    if started_at is None:
        print(f"[{stamp}] {message}", flush=True)
    else:
        elapsed = time.perf_counter() - started_at
        print(f"[{stamp}] {message} (+{elapsed:.2f}s)", flush=True)


def _wait(driver, selector: str, timeout: int = DEFAULT_WAIT_SECONDS, verbose: bool = True):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
    )


def _click_by_text(driver, tag_name: str, text: str, timeout: int = DEFAULT_WAIT_SECONDS, verbose: bool = True):
    WebDriverWait(driver, timeout).until(
        lambda d: any(
            text.lower() in element.text.strip().lower()
            for element in d.find_elements(By.TAG_NAME, tag_name)
            if element.is_displayed()
        )
    )
    for element in driver.find_elements(By.TAG_NAME, tag_name):
        if element.is_displayed() and text.lower() in element.text.strip().lower():
            element.click()
            return element
    raise ValueError(f"Could not find {tag_name} containing text: {text}")


def _normalize(text: str, verbose: bool = True) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _pause(delay_seconds: float | int = 0, verbose: bool = True) -> None:
    if delay_seconds and float(delay_seconds) > 0:
        time.sleep(float(delay_seconds))


def _listed_on_from_text(text: str, now: datetime | None = None) -> str | None:
    cleaned = _normalize(text)
    if not cleaned:
        return None
    current = now or datetime.now().astimezone()
    if cleaned == "today":
        return current.isoformat(timespec="seconds")
    if cleaned == "yesterday":
        return (current - timedelta(days=1)).isoformat(timespec="seconds")
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", cleaned, flags=re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "minute":
        delta = timedelta(minutes=amount)
    elif unit == "hour":
        delta = timedelta(hours=amount)
    elif unit == "day":
        delta = timedelta(days=amount)
    elif unit == "week":
        delta = timedelta(weeks=amount)
    elif unit == "month":
        delta = timedelta(days=amount * 30)
    else:
        delta = timedelta(days=amount * 365)
    return (current - delta).isoformat(timespec="seconds")


def _click_and_pause(element, delay_seconds: float | int = 0, verbose: bool = True) -> None:
    element.click()
    _pause(delay_seconds, verbose=verbose)


def _click_safely(driver, element, delay_seconds: float | int = 0, verbose: bool = True) -> None:
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)
    _pause(delay_seconds, verbose=verbose)


def _click_fast(driver, element) -> None:
    driver.execute_script("arguments[0].click();", element)


def open_jobs_search_page(
    driver,
    url: str = JOBS_SEARCH_URL,
    delay_seconds: float | int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    _vlog(verbose, f"page: open {url}")
    driver.get(url)
    _wait(driver, GLOBAL_NAV_SELECTOR, timeout=DEFAULT_WAIT_SECONDS, verbose=verbose)
    _pause(delay_seconds, verbose=verbose)
    _vlog(verbose, "page: ready")
    return {"page_ready": True}


def set_keyword_input(
    driver,
    keyword: str,
    delay_seconds: float | int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    _vlog(verbose, f"keyword: {keyword}")
    element = _wait(driver, KEYWORD_INPUT_SELECTOR, verbose=verbose)
    element.clear()
    element.send_keys(keyword)
    _pause(delay_seconds, verbose=verbose)
    return {"keyword_value": keyword}


def set_location_input(
    driver,
    location: str,
    delay_seconds: float | int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    _vlog(verbose, f"location: {location}")
    element = _wait(driver, LOCATION_INPUT_SELECTOR, verbose=verbose)
    element.clear()
    element.send_keys(location)
    element.send_keys(Keys.TAB)
    _pause(delay_seconds, verbose=verbose)
    return {"location_value": location}


def click_search_button(driver, delay_seconds: float | int = 1, verbose: bool = True) -> dict[str, Any]:
    _vlog(verbose, "search: click button start")
    try:
        button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SEARCH_BUTTON_SELECTOR))
        )
    except Exception:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        button = None
        for candidate in buttons:
            if not candidate.is_displayed():
                continue
            label = _normalize(candidate.get_attribute("aria-label") or candidate.text)
            if label == "search" or label.endswith(" search") or label == "Search":
                button = candidate
                break
        if button is None:
            raise ValueError("Could not find the search button")
    button.click()
    _pause(delay_seconds, verbose=verbose)
    _vlog(verbose, "search: clicked")
    return {"search_clicked": True}


def open_all_filters_menu(driver, delay_seconds: float | int = 1, verbose: bool = True) -> dict[str, Any]:
    started_at = time.perf_counter()
    _sync_log(verbose, "filters: open menu start")
    modal_elements = driver.find_elements(By.CSS_SELECTOR, FILTER_MODAL_SELECTOR)
    if any(element.is_displayed() for element in modal_elements):
        _sync_log(verbose, "filters: open menu already open", started_at)
        return {"modal_open": True}
    try:
        wait_button_at = time.perf_counter()
        _sync_log(verbose, "filters: open menu wait button")
        button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ALL_FILTERS_SELECTOR))
        )
        _sync_log(verbose, "filters: open menu button ready", wait_button_at)
        click_at = time.perf_counter()
        _sync_log(verbose, "filters: open menu click")
        button.click()
        _sync_log(verbose, "filters: open menu clicked", click_at)
        wait_modal_at = time.perf_counter()
        _sync_log(verbose, "filters: open menu wait modal")
        WebDriverWait(driver, 10).until(
            lambda d: any(
                element.is_displayed()
                for element in d.find_elements(By.CSS_SELECTOR, FILTER_MODAL_SELECTOR)
            )
        )
        _sync_log(verbose, "filters: open menu modal ready", wait_modal_at)
        _vlog(verbose, "filters: open ok")
        _sync_log(verbose, "filters: open menu done", started_at)
        return {"modal_open": True}
    except Exception as exc:
        _sync_log(verbose, "filters: open menu failed", started_at)
        raise RuntimeError("Could not open the all filters panel") from exc


def _ensure_all_filters_menu_open(driver, delay_seconds: float | int = 1, verbose: bool = True) -> dict[str, Any]:
    modal_elements = driver.find_elements(By.CSS_SELECTOR, FILTER_MODAL_SELECTOR)
    if any(element.is_displayed() for element in modal_elements):
        return {"modal_open": True}
    return open_all_filters_menu(driver, delay_seconds=delay_seconds, verbose=verbose)


def _fast_filters_snapshot(driver, verbose: bool = True) -> dict[str, Any]:
    return read_filters_state(driver, verbose=verbose, ensure_open=False, include_filter_by_options=False)


def _filters_scope(driver, verbose: bool = True):
    modal_elements = driver.find_elements(By.CSS_SELECTOR, FILTER_MODAL_SELECTOR)
    for element in modal_elements:
        try:
            if element.is_displayed():
                return element
        except Exception:
            continue
    return driver


def _section_block_in_scope(scope, section_name: str, verbose: bool = True):
    target = _normalize(section_name)
    for block in _section_blocks(scope, verbose=verbose):
        if _normalize(_section_title(block, verbose=verbose)) == target or _normalize(_section_legend(block, verbose=verbose)) == target:
            return block
    return None


def _parse_result_type_from_trigger_text(text: str, verbose: bool = True) -> str | None:
    match = re.search(r"type:\s*([^\.]+)", text or "", flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def read_result_type(driver, verbose: bool = True, ensure_open: bool = True) -> dict[str, Any]:
    if ensure_open:
        _ensure_all_filters_menu_open(driver, verbose=verbose)
    trigger = _wait(driver, RESULT_TYPE_TRIGGER_SELECTOR, verbose=verbose)
    selected = _parse_result_type_from_trigger_text(trigger.get_attribute("aria-label") or trigger.text)
    options: list[str] = []
    try:
        trigger.click()
        menu = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.search-advanced-filter__navigation-container"))
        )
        soup = BeautifulSoup(menu.get_attribute("outerHTML"), "html.parser")
        for item in soup.select("[role='button']"):
            label = item.get("aria-label") or item.get_text(" ", strip=True)
            label = re.sub(r"^Show only results of type:\s*", "", label, flags=re.IGNORECASE)
            label = label.replace(" selected", "").strip()
            if label:
                options.append(label)
    finally:
        try:
            if trigger.get_attribute("aria-expanded") == "true":
                trigger.click()
        except Exception:
            pass
    return {"filter_by": {"selected": selected or "Jobs", "options": options}}


def set_result_type(
    driver,
    filter_by: str,
    delay_seconds: float | int = 1,
    verbose: bool = True,
    ensure_open: bool = True,
) -> dict[str, Any]:
    _vlog(verbose, f"filters: type {filter_by}")
    if ensure_open:
        _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
    trigger = _wait(driver, RESULT_TYPE_TRIGGER_SELECTOR, verbose=verbose)
    trigger.click()
    options = WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.search-advanced-filter__navigation-container [role='button']"))
    )
    for option in options:
        label = option.get_attribute("aria-label") or option.text
        label = re.sub(r"^Show only results of type:\s*", "", label, flags=re.IGNORECASE)
        label = label.replace(" selected", "").strip()
        if _normalize(label) == _normalize(filter_by):
            _click_and_pause(option, delay_seconds, verbose=verbose)
            return {"filter_by": {"selected": filter_by}}
    raise ValueError(f"Result type not found: {filter_by}")


def _section_blocks(driver, verbose: bool = True):
    return driver.find_elements(By.CSS_SELECTOR, FILTER_SECTION_SELECTOR)


def _section_title(block, verbose: bool = True) -> str:
    try:
        return block.find_element(By.CSS_SELECTOR, "h3").text.strip()
    except Exception:
        return ""


def _section_legend(block, verbose: bool = True) -> str:
    try:
        return block.find_element(By.CSS_SELECTOR, "legend").text.strip()
    except Exception:
        return ""


def _section_kind_from_labels(
    section_name: str,
    label_name: str,
    input_count: int,
    input_type: str,
    verbose: bool = True,
) -> str:
    if input_type == "radio":
        return "radio"
    if input_type == "checkbox":
        if input_count == 1 and "easy apply" in _normalize(section_name + " " + label_name):
            return "switch"
        return "checkbox"
    if input_type == "button":
        return "multiselect_pill"
    return "unknown"


def _label_text(label, verbose: bool = True) -> str:
    if label is None:
        return ""
    if callable(getattr(label, "find_elements", None)):
        visible = label.find_elements(By.CSS_SELECTOR, "[aria-hidden='true']")
        if visible:
            text = visible[0].text.strip()
            if text:
                return text
        text = label.text.strip()
    else:
        visible = label.select_one("[aria-hidden='true']") if hasattr(label, "select_one") else None
        if visible:
            text = visible.get_text(" ", strip=True)
            if text:
                return text
        text = label.get_text(" ", strip=True) if hasattr(label, "get_text") else str(label).strip()
    text = re.sub(r"^Filter by\s*", "", text, flags=re.IGNORECASE)
    return text


def parse_filters_state(html: str, verbose: bool = True) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    result_type = {"selected": "Jobs", "options": []}
    trigger = soup.select_one("button.search-reusables__vertical-select-trigger")
    if trigger:
        selected = _parse_result_type_from_trigger_text(trigger.get("aria-label") or trigger.get_text(" ", strip=True))
        if selected:
            result_type["selected"] = selected
    for item in soup.select("ul.search-advanced-filter__navigation-container [role='button']"):
        label = item.get("aria-label") or item.get_text(" ", strip=True)
        label = re.sub(r"^Show only results of type:\s*", "", label, flags=re.IGNORECASE)
        label = label.replace(" selected", "").strip()
        if label:
            result_type["options"].append(label)

    filters: list[dict[str, Any]] = []
    for block in soup.select("li.search-reusables__secondary-filters-filter"):
        section = block.select_one("h3")
        legend = block.select_one("legend")
        section_name = section.get_text(" ", strip=True) if section else ""
        legend_name = legend.get_text(" ", strip=True) if legend else ""
        input_nodes = block.select("input")
        button_nodes = block.select("button[aria-pressed]")
        if button_nodes:
            items = []
            for button in button_nodes:
                items.append(
                    {
                        "name": button.get("aria-label") or button.get_text(" ", strip=True),
                        "state": button.get("aria-pressed") == "true",
                    }
                )
            filters.append(
                {
                    "section": section_name,
                    "type": "multiselect_pill",
                    "inputs": items,
                }
            )
            continue
        if input_nodes:
            input_type = input_nodes[0].get("type", "").lower()
            inferred = "unknown"
            if input_type == "radio":
                inferred = "radio"
            elif input_type == "checkbox":
                inferred = "switch" if len(input_nodes) == 1 and "easy apply" in _normalize(section_name + " " + legend_name) else "checkbox"
            items = []
            for input_node in input_nodes:
                input_id = input_node.get("id", "")
                label = None
                if input_id:
                    label = block.select_one(f"label[for='{input_id}']")
                if label is None:
                    label = input_node.find_parent("label")
                items.append(
                    {
                        "name": _label_text(label) if label else input_node.get("value") or "",
                        "state": input_node.has_attr("checked"),
                    }
                )
            filters.append(
                {
                    "section": section_name,
                    "type": inferred,
                    "inputs": items,
                }
            )
            continue
        filters.append(
            {
                "section": section_name,
                "type": "unknown",
                "inputs": [],
            }
        )

    return {"filter_by": result_type, "filters": filters}


def read_filters_state(
    driver,
    verbose: bool = True,
    ensure_open: bool = True,
    include_filter_by_options: bool = True,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    _sync_log(verbose, "filters: read start")
    if ensure_open:
        ensure_at = time.perf_counter()
        _sync_log(verbose, "filters: read ensure open")
        _ensure_all_filters_menu_open(driver, verbose=verbose)
        _sync_log(verbose, "filters: read ensure open done", ensure_at)
    trigger_at = time.perf_counter()
    _sync_log(verbose, "filters: read trigger wait")
    trigger = _wait(driver, RESULT_TYPE_TRIGGER_SELECTOR, verbose=verbose)
    _sync_log(verbose, "filters: read trigger ready", trigger_at)
    parse_type_at = time.perf_counter()
    _sync_log(verbose, "filters: read type parse")
    selected = _parse_result_type_from_trigger_text(trigger.get_attribute("aria-label") or trigger.text)
    _sync_log(verbose, "filters: read type parsed", parse_type_at)
    options: list[str] = []
    if include_filter_by_options:
        try:
            options_at = time.perf_counter()
            _sync_log(verbose, "filters: read type options open")
            trigger.click()
            menu = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.search-advanced-filter__navigation-container"))
            )
            _sync_log(verbose, "filters: read type options ready", options_at)
            for item in menu.find_elements(By.CSS_SELECTOR, "[role='button']"):
                label = item.get_attribute("aria-label") or item.text
                label = re.sub(r"^Show only results of type:\s*", "", label, flags=re.IGNORECASE)
                label = label.replace(" selected", "").strip()
                if label:
                    options.append(label)
        finally:
            try:
                if trigger.get_attribute("aria-expanded") == "true":
                    trigger.click()
            except Exception:
                pass
    else:
        _vlog(verbose, "filters: read type options skip")

    filters: list[dict[str, Any]] = []
    sections_at = time.perf_counter()
    _sync_log(verbose, "filters: read sections start")
    scope = _filters_scope(driver, verbose=verbose)
    _vlog(verbose, f"filters: read scope {'modal' if scope is not driver else 'driver'}")
    for block in _section_blocks(scope, verbose=verbose):
        section_started_at = time.perf_counter()
        section_name = _section_title(block, verbose=verbose)
        label_name = _section_legend(block, verbose=verbose)
        input_nodes = block.find_elements(By.CSS_SELECTOR, "input")
        button_nodes = block.find_elements(By.CSS_SELECTOR, "button[aria-pressed]")
        _sync_log(verbose, f"filters: read section start {section_name or 'unknown'}")

        if button_nodes:
            items = []
            for button in button_nodes:
                items.append(
                    {
                        "name": button.get_attribute("aria-label") or button.text.strip(),
                        "state": button.get_attribute("aria-pressed") == "true",
                    }
                )
            filters.append(
                {
                    "section": section_name,
                    "type": "multiselect_pill",
                    "inputs": items,
                }
            )
            _sync_log(verbose, f"filters: read section done {section_name or 'unknown'}", section_started_at)
            continue

        if input_nodes:
            input_type = (input_nodes[0].get_attribute("type") or "").lower()
            kind = _section_kind_from_labels(section_name, label_name, len(input_nodes), input_type, verbose=verbose)
            items = []
            for input_node in input_nodes:
                input_id = input_node.get_attribute("id") or ""
                label = None
                if input_id:
                    try:
                        label = block.find_element(By.CSS_SELECTOR, f"label[for='{input_id}']")
                    except Exception:
                        label = None
                if label is None:
                    try:
                        label = input_node.find_element(By.XPATH, "./following-sibling::label[1]")
                    except Exception:
                        label = None
                items.append(
                    {
                        "name": _label_text(label, verbose=verbose) if label else (input_node.get_attribute("value") or ""),
                        "state": input_node.is_selected(),
                    }
                )
            filters.append(
                {
                    "section": section_name,
                    "type": kind,
                    "inputs": items,
                }
            )
            _sync_log(verbose, f"filters: read section done {section_name or 'unknown'}", section_started_at)
            continue

        filters.append(
            {
                "section": section_name,
                "type": "unknown",
                "inputs": [],
            }
        )
        _sync_log(verbose, f"filters: read section done {section_name or 'unknown'}", section_started_at)

    _sync_log(verbose, "filters: read sections done", sections_at)
    _sync_log(verbose, "filters: read done", started_at)
    return {"filter_by": {"selected": selected or "Jobs", "options": options}, "filters": filters}


def _find_section_block(driver, section_name: str, verbose: bool = True):
    scope = _filters_scope(driver, verbose=verbose)
    return _section_block_in_scope(scope, section_name, verbose=verbose)


def _click_label_in_block(block, label_text: str, verbose: bool = True) -> bool:
    target = _normalize(label_text)
    for label in block.find_elements(By.CSS_SELECTOR, "label"):
        if _normalize(_label_text(label, verbose=verbose)) == target:
            label.click()
            return True
    return False


def _matches_choice_name(candidate: str, target: str, verbose: bool = True) -> bool:
    candidate = _normalize(candidate)
    target = _normalize(target)
    if not candidate or not target:
        return False
    if candidate == target:
        return True
    return candidate in target or target in candidate


def _checkbox_names(checkbox, label, verbose: bool = True) -> list[str]:
    names = []
    if label is not None:
        names.append(_label_text(label))
        names.append(label.get_attribute("aria-label") or "")
    names.append(checkbox.get_attribute("aria-label") or "")
    names.append(checkbox.get_attribute("value") or "")
    names.append(checkbox.get_attribute("name") or "")
    names.append(checkbox.get_attribute("id") or "")
    return [name for name in names if _normalize(name)]


def _set_checkbox_group(
    block,
    inputs: list[dict[str, Any]],
    driver=None,
    delay_seconds: float | int = 0,
    verbose: bool = True,
) -> None:
    started_at = time.perf_counter()
    _sync_log(verbose, "filters: checkbox scan start")
    desired = {}
    for item in inputs:
        name = _normalize(item.get("name", ""))
        if name:
            desired[name] = bool(item.get("state"))

    records = []
    for checkbox in block.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
        input_id = checkbox.get_attribute("id") or ""
        label = None
        if input_id:
            try:
                label = block.find_element(By.CSS_SELECTOR, f"label[for='{input_id}']")
            except Exception:
                label = None
        if label is None:
            try:
                label = checkbox.find_element(By.XPATH, "./following-sibling::label[1]")
            except Exception:
                label = None
        names = _checkbox_names(checkbox, label, verbose=verbose)
        records.append((checkbox, label, names))
    _sync_log(verbose, "filters: checkbox scan done", started_at)

    def _matched_state(names: list[str]) -> bool:
        for name in names:
            for target_name, target_state in desired.items():
                if _matches_choice_name(name, target_name, verbose=verbose):
                    return target_state
        return False

    to_fix = []
    for checkbox, label, names in records:
        desired_state = _matched_state(names)
        if checkbox.is_selected() != desired_state:
            to_fix.append((checkbox, label, names, desired_state))

    if not to_fix:
        return

    pass_started_at = time.perf_counter()
    _sync_log(verbose, f"filters: checkbox apply start ({len(to_fix)})")
    for index, (checkbox, label, names, desired_state) in enumerate(to_fix):
        try:
            if driver is not None:
                _click_fast(driver, checkbox)
            else:
                checkbox.click()
        except Exception:
            if label is not None:
                if driver is not None:
                    _click_fast(driver, label)
                else:
                    label.click()
            else:
                raise
    _pause(delay_seconds, verbose=verbose)
    _sync_log(verbose, "filters: checkbox apply done", pass_started_at)

    verify_started_at = time.perf_counter()
    _sync_log(verbose, "filters: checkbox verify start")
    remaining = []
    for checkbox, label, names, desired_state in to_fix:
        try:
            current_state = checkbox.is_selected()
        except Exception:
            current_state = None
        if current_state != desired_state:
            remaining.append((checkbox, label, names, desired_state))

    if not remaining:
        _sync_log(verbose, "filters: checkbox verify done", verify_started_at)
        return

    retry_started_at = time.perf_counter()
    _sync_log(verbose, f"filters: checkbox retry start ({len(remaining)})")
    for index, (checkbox, label, names, desired_state) in enumerate(remaining):
        if label is not None:
            if driver is not None:
                _click_fast(driver, label)
            else:
                label.click()
        else:
            if driver is not None:
                _click_fast(driver, checkbox)
            else:
                checkbox.click()
    _pause(delay_seconds, verbose=verbose)
    _sync_log(verbose, "filters: checkbox retry done", retry_started_at)

    final_started_at = time.perf_counter()
    _sync_log(verbose, "filters: checkbox final verify start")
    for checkbox, label, names, desired_state in remaining:
        try:
            if checkbox.is_selected() != desired_state:
                raise RuntimeError("Could not sync checkbox group to the requested state")
        except Exception as exc:
            raise RuntimeError("Could not sync checkbox group to the requested state") from exc
    _sync_log(verbose, "filters: checkbox final verify done", final_started_at)


def _set_radio_group(block, input_name: str, delay_seconds: float | int = 0, verbose: bool = True) -> None:
    if not _click_label_in_block(block, input_name, verbose=verbose):
        raise ValueError(f"Radio choice not found: {input_name}")
    _pause(delay_seconds, verbose=verbose)


def _set_switch(block, state: bool, driver=None, delay_seconds: float | int = 0, verbose: bool = True) -> None:
    checkbox = block.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
    current_state = checkbox.is_selected()
    if current_state != state:
        if driver is not None:
            _click_safely(driver, checkbox, delay_seconds, verbose=verbose)
        else:
            _click_and_pause(checkbox, delay_seconds, verbose=verbose)


def _set_pills(
    block,
    inputs: list[dict[str, Any]],
    delay_seconds: float | int = 0,
    verbose: bool = True,
) -> None:
    desired = {_normalize(item["name"]): bool(item["state"]) for item in inputs}
    for button in block.find_elements(By.CSS_SELECTOR, "button[aria-pressed]"):
        name = _normalize(button.get_attribute("aria-label") or button.text)
        if name in desired and (button.get_attribute("aria-pressed") == "true") != desired[name]:
            _click_and_pause(button, delay_seconds, verbose=verbose)


def _payload_section_lookup(payload: dict[str, Any], verbose: bool = True) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for item in payload.get("filters", []):
        section_name = _normalize(item.get("section", ""))
        if section_name:
            sections[section_name] = item
    return sections


def _current_radio_choice(current_filter: dict[str, Any]) -> str:
    for item in current_filter.get("inputs", []):
        if item.get("state"):
            return item.get("name", "")
    return ""


def _current_checkbox_map(current_filter: dict[str, Any]) -> dict[str, bool]:
    return {_normalize(item.get("name", "")): bool(item.get("state")) for item in current_filter.get("inputs", []) if _normalize(item.get("name", ""))}


def _desired_checkbox_map(target: dict[str, Any] | None) -> dict[str, bool]:
    desired: dict[str, bool] = {}
    for item in (target.get("inputs", []) if target else []):
        name = _normalize(item.get("name", ""))
        if name:
            desired[name] = bool(item.get("state"))
    return desired


def _current_pill_map(current_filter: dict[str, Any]) -> dict[str, bool]:
    return {_normalize(item.get("name", "")): bool(item.get("state")) for item in current_filter.get("inputs", []) if _normalize(item.get("name", ""))}


def _desired_pill_map(target: dict[str, Any] | None) -> dict[str, bool]:
    desired: dict[str, bool] = {}
    for item in (target.get("inputs", []) if target else []):
        name = _normalize(item.get("name", ""))
        if name:
            desired[name] = bool(item.get("state"))
    return desired


def _current_checkbox_selected_count(current_filter: dict[str, Any]) -> int:
    return sum(1 for item in current_filter.get("inputs", []) if bool(item.get("state")))


def sync_filters_state(
    driver,
    payload: dict[str, Any],
    delay_seconds: float | int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    _sync_log(verbose, "filters: sync start")
    phase_at = time.perf_counter()
    _sync_log(verbose, "filters: open panel start")
    _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
    _sync_log(verbose, "filters: open panel done", phase_at)
    phase_at = time.perf_counter()
    _sync_log(verbose, "filters: snapshot start")
    try:
        current_snapshot = _fast_filters_snapshot(driver, verbose=verbose)
    except Exception:
        _sync_log(verbose, "filters: snapshot retry")
        retry_at = time.perf_counter()
        _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
        current_snapshot = _fast_filters_snapshot(driver, verbose=verbose)
        _sync_log(verbose, "filters: snapshot retry done", retry_at)
    _sync_log(verbose, "filters: snapshot done", phase_at)

    filter_by = payload.get("filter_by")
    changed_any = False
    if filter_by:
        current = current_snapshot["filter_by"]["selected"]
        if _normalize(current) != _normalize(filter_by):
            phase_at = time.perf_counter()
            _sync_log(verbose, f"filters: type start {current or 'none'} -> {filter_by}")
            try:
                set_result_type(driver, filter_by, delay_seconds=delay_seconds, verbose=verbose, ensure_open=False)
            except Exception:
                _sync_log(verbose, "filters: type retry")
                retry_at = time.perf_counter()
                _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
                set_result_type(driver, filter_by, delay_seconds=delay_seconds, verbose=verbose, ensure_open=False)
                _sync_log(verbose, "filters: type retry done", retry_at)
            _sync_log(verbose, f"filters: type done {current or 'none'} -> {filter_by}", phase_at)
            changed_any = True

    desired_sections = _payload_section_lookup(payload, verbose=verbose)
    _sync_log(verbose, f"filters: sections start ({len(current_snapshot.get('filters', []))})")
    section_scope = _filters_scope(driver, verbose=verbose)

    for current_filter in current_snapshot.get("filters", []):
        section = current_filter.get("section", "")
        target = desired_sections.get(_normalize(section))
        section_type = current_filter.get("type", "")
        _vlog(verbose, f"{section}: {section_type} scan")
        if target is None and section_type != "checkbox":
            _vlog(verbose, f"{section}: no target skip")
            continue
        if target is None and section_type == "checkbox":
            selected_count = _current_checkbox_selected_count(current_filter)
            if selected_count == 0:
                _vlog(verbose, f"{section}: checkbox clean skip")
                continue
            _vlog(verbose, f"{section}: checkbox missing target clear {selected_count}")
        block = _section_block_in_scope(section_scope, section, verbose=verbose)
        if block is None:
            _sync_log(verbose, f"filters: section retry {section or 'unknown'}")
            retry_at = time.perf_counter()
            _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
            section_scope = _filters_scope(driver, verbose=verbose)
            block = _section_block_in_scope(section_scope, section, verbose=verbose)
            _sync_log(verbose, f"filters: section retry done {section or 'unknown'}", retry_at)
        if block is None:
            continue

        try:
            target_type = target.get("type") if target else None
            if section_type == "radio":
                current_choice = _current_radio_choice(current_filter)
                desired_choice = target.get("input", "") if target and target_type == "radio" else current_choice
                if _normalize(current_choice) != _normalize(desired_choice):
                    phase_at = time.perf_counter()
                    _sync_log(verbose, f"filters: {section} radio start")
                    _set_radio_group(block, target.get("input", ""), delay_seconds=delay_seconds, verbose=verbose)
                    _sync_log(verbose, f"filters: {section} radio done {current_choice or 'none'} -> {desired_choice or 'none'}", phase_at)
                    changed_any = True
                else:
                    _vlog(verbose, f"{section}: radio unchanged")
                    continue
            elif section_type == "checkbox":
                current_map = _current_checkbox_map(current_filter)
                desired_map = _desired_checkbox_map(target)
                _vlog(verbose, f"{section}: checkbox state {sum(1 for state in current_map.values() if state)}/{len(current_map)} -> {sum(1 for state in desired_map.values() if state)}/{len(desired_map)}")
                if current_map != desired_map:
                    phase_at = time.perf_counter()
                    _sync_log(verbose, f"filters: {section} checkbox start")
                    _set_checkbox_group(block, target.get("inputs", []) if target else [], driver=driver, delay_seconds=delay_seconds, verbose=verbose)
                    current_count = sum(1 for state in current_map.values() if state)
                    desired_count = sum(1 for state in desired_map.values() if state)
                    _sync_log(verbose, f"filters: {section} checkbox done {current_count}/{len(current_map)} -> {desired_count}/{len(desired_map)}", phase_at)
                    changed_any = True
                else:
                    _vlog(verbose, f"{section}: checkbox unchanged")
                    continue
            elif section_type == "switch":
                before_state = bool(current_filter.get("inputs", [{}])[0].get("state")) if current_filter.get("inputs") else False
                if target is None:
                    desired_state = False
                elif "state" in target:
                    desired_state = bool(target.get("state"))
                else:
                    desired_inputs = target.get("inputs", [])
                    desired_state = bool(desired_inputs[0].get("state")) if desired_inputs else False
                if before_state != desired_state:
                    phase_at = time.perf_counter()
                    _sync_log(verbose, f"filters: {section} switch start")
                    _set_switch(block, desired_state, driver=driver, delay_seconds=delay_seconds, verbose=verbose)
                    _sync_log(verbose, f"filters: {section} switch done {'on' if before_state else 'off'} -> {'on' if desired_state else 'off'}", phase_at)
                    changed_any = True
                else:
                    _vlog(verbose, f"{section}: switch unchanged")
                    continue
            elif section_type == "multiselect_pill":
                current_map = _current_pill_map(current_filter)
                desired_map = _desired_pill_map(target)
                if current_map != desired_map:
                    phase_at = time.perf_counter()
                    _sync_log(verbose, f"filters: {section} pill start")
                    _set_pills(block, target.get("inputs", []) if target else [], delay_seconds=delay_seconds, verbose=verbose)
                    current_count = sum(1 for state in current_map.values() if state)
                    desired_count = sum(1 for state in desired_map.values() if state)
                    _sync_log(verbose, f"filters: {section} pill done {current_count}/{len(current_map)} -> {desired_count}/{len(desired_map)}", phase_at)
                    changed_any = True
                else:
                    _vlog(verbose, f"{section}: pill unchanged")
                    continue
        except Exception:
            _sync_log(verbose, f"filters: {section} retry")
            retry_at = time.perf_counter()
            _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
            section_scope = _filters_scope(driver, verbose=verbose)
            block = _section_block_in_scope(section_scope, section, verbose=verbose)
            if block is None:
                continue
            target = desired_sections.get(_normalize(section))
            if section_type == "radio":
                if target and target.get("type") == "radio":
                    _set_radio_group(block, target.get("input", ""), delay_seconds=delay_seconds, verbose=verbose)
            elif section_type == "checkbox":
                _set_checkbox_group(block, target.get("inputs", []) if target else [], driver=driver, delay_seconds=delay_seconds, verbose=verbose)
            elif section_type == "switch":
                if target is None:
                    _set_switch(block, False, driver=driver, delay_seconds=delay_seconds, verbose=verbose)
                elif "state" in target:
                    _set_switch(block, bool(target.get("state")), driver=driver, delay_seconds=delay_seconds, verbose=verbose)
                else:
                    desired_inputs = target.get("inputs", [])
                    desired_state = bool(desired_inputs[0].get("state")) if desired_inputs else False
                    _set_switch(block, desired_state, driver=driver, delay_seconds=delay_seconds, verbose=verbose)
            elif section_type == "multiselect_pill":
                _set_pills(block, target.get("inputs", []) if target else [], delay_seconds=delay_seconds, verbose=verbose)
            changed_any = True
            _sync_log(verbose, f"filters: {section} retry done", retry_at)

    if not changed_any:
        _sync_log(verbose, "filters: sync done (unchanged)", started_at)
        return current_snapshot

    try:
        phase_at = time.perf_counter()
        _sync_log(verbose, "filters: final snapshot start")
        result = _fast_filters_snapshot(driver, verbose=verbose)
        _sync_log(verbose, "filters: final snapshot done", phase_at)
        _sync_log(verbose, "filters: sync done", started_at)
        return result
    except Exception:
        _sync_log(verbose, "filters: final snapshot retry")
        retry_at = time.perf_counter()
        _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
        result = _fast_filters_snapshot(driver, verbose=verbose)
        _sync_log(verbose, "filters: final snapshot retry done", retry_at)
        _sync_log(verbose, "filters: sync done", started_at)
        return result


def show_results(driver, delay_seconds: float | int = 1, verbose: bool = True) -> dict[str, Any]:
    _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
    try:
        button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SHOW_RESULTS_SELECTOR))
        )
        button.click()
    except Exception:
        try:
            _ensure_all_filters_menu_open(driver, delay_seconds=delay_seconds, verbose=verbose)
        except Exception:
            pass
        buttons = driver.find_elements(By.TAG_NAME, "button")
        match = None
        for button in buttons:
            if not button.is_displayed():
                continue
            label = " ".join(
                part
                for part in [
                    button.get_attribute("aria-label") or "",
                    button.text or "",
                ]
                if part
            )
            if "apply current filters" in _normalize(label) or SHOW_RESULTS_TEXT in _normalize(label):
                match = button
                break
        if match is None:
            raise ValueError("Could not find the show results button")
        match.click()
    _pause(delay_seconds, verbose=verbose)
    _vlog(verbose, "filters: applied")
    return {"filters_applied": True, "results_shown": True}


def parse_listings(html: str, verbose: bool = True, now: datetime | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict[str, Any]] = []
    for card in soup.select(LISTING_SELECTOR):
        job_id = card.get("data-occludable-job-id") or card.get("data-job-id")
        title_link = card.select_one("a.job-card-container__link") or card.select_one("a[aria-label]")
        title = ""
        link = None
        if title_link:
            title = (title_link.get("aria-label") or title_link.get_text(" ", strip=True)).strip()
            href = title_link.get("href")
            if href:
                link = urljoin(BASE_URL, href)
        company = ""
        company_node = card.select_one(".artdeco-entity-lockup__subtitle span") or card.select_one(
            ".job-card-container__company-name"
        )
        if company_node:
            company = company_node.get_text(" ", strip=True)
        location = ""
        location_node = card.select_one(".artdeco-entity-lockup__caption span[dir='ltr']") or card.select_one(
            ".job-card-container__metadata-wrapper li span"
        )
        if location_node:
            location = location_node.get_text(" ", strip=True)
        footer_text = " ".join(node.get_text(" ", strip=True) for node in card.select("ul li"))
        easy_apply = "easy apply" in _normalize(card.get_text(" ", strip=True))
        promoted = "promoted" in _normalize(card.get_text(" ", strip=True))
        listed_on = None
        for candidate in card.select("ul.job-card-list__footer-wrapper li, .job-card-container__footer-item"):
            text = candidate.get_text(" ", strip=True)
            norm = _normalize(text)
            if text and norm not in {"viewed", "applied", "easy apply"} and "job state" not in norm and "dismiss" not in norm:
                listed_on = _listed_on_from_text(text, now=now)
                break
        listings.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "link": link,
                "job_id": job_id,
                "promoted": promoted,
                "easy_apply": easy_apply,
                "listed_on": listed_on,
            }
        )
    return {"listings": listings}


def extract_listings(driver, verbose: bool = True, now: datetime | None = None) -> dict[str, Any]:
    return parse_listings(driver.page_source, verbose=verbose, now=now)


def click_listing_card(
    driver,
    index: int,
    delay_seconds: float | int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    cards = [card for card in driver.find_elements(By.CSS_SELECTOR, LISTING_SELECTOR) if card.is_displayed()]
    if not cards:
        raise RuntimeError("No visible job listings found")
    target_index = max(0, min(int(index), len(cards) - 1))
    card = cards[target_index]
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
    except Exception:
        pass
    _click_safely(driver, card, delay_seconds=delay_seconds, verbose=verbose)
    return {
        "index": target_index,
        "job_id": card.get_attribute("data-occludable-job-id") or card.get_attribute("data-job-id") or "",
    }


def read_job_detail_panel(driver, verbose: bool = True, timeout: int = 20) -> dict[str, Any]:
    panel = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, JOB_DETAIL_WRAPPER_SELECTOR))
    )
    html = panel.get_attribute("outerHTML") or ""
    text = panel.text.strip()
    return {"html": html, "text": text}


def _is_pagination_gap(text: str) -> bool:
    cleaned = _normalize(text)
    return bool(cleaned) and not cleaned.isdigit()


def _expand_page_entries(raw_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    last_number: int | None = None
    for index, page in enumerate(raw_pages):
        text = page["text"]
        if _is_pagination_gap(text):
            next_number = None
            for future in raw_pages[index + 1 :]:
                future_text = future["text"]
                if future_text.isdigit():
                    next_number = int(future_text)
                    break
            inferred = None
            if next_number is not None:
                inferred = max(1, next_number - 1)
            elif last_number is not None:
                inferred = last_number + 1
            if inferred is not None:
                pages.append({"text": str(inferred), "current": False})
                last_number = inferred
            continue

        pages.append({"text": text, "current": page["current"]})
        if text.isdigit():
            last_number = int(text)
    return pages


def parse_pages(html: str, verbose: bool = True) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    raw_pages = []
    current_page = None
    for button in soup.select(PAGE_BUTTON_SELECTOR):
        text = button.get_text(" ", strip=True)
        current = button.get("aria-current") == "page"
        raw_pages.append({"text": text, "current": current})
        if current:
            current_page = text
    return {"pages": _expand_page_entries(raw_pages), "current_page": current_page}


def get_visible_pages(driver, verbose: bool = True) -> dict[str, Any]:
    raw_pages: list[dict[str, Any]] = []
    current_page = None
    for button in driver.find_elements(By.CSS_SELECTOR, PAGE_BUTTON_SELECTOR):
        if not button.is_displayed():
            continue
        text = button.text.strip() or button.get_attribute("aria-label") or ""
        current = button.get_attribute("aria-current") == "page"
        raw_pages.append({"text": text, "current": current})
        if current:
            current_page = text
    return {"pages": _expand_page_entries(raw_pages), "current_page": current_page}


def get_current_page(driver, verbose: bool = True) -> dict[str, Any]:
    pages = get_visible_pages(driver, verbose=verbose)["pages"]
    current = next((page for page in pages if page["current"]), None)
    return {"current_page": current["text"] if current else None}


def go_to_page(driver, page: int, delay_seconds: float | int = 1, verbose: bool = True) -> dict[str, Any]:
    selector = f"{PAGE_BUTTON_SELECTOR}[aria-label='Page {page}']"
    try:
        button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
        )
        button.click()
        _pause(delay_seconds, verbose=verbose)
        _vlog(verbose, f"page: {page} ready")
        return {"navigated": True, "current_page": page}
    except Exception as exc:
        raise RuntimeError(f"Page {page} is not visible yet") from exc

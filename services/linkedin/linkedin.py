from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any
from uuid import uuid4

from core.logging import TreeLogger
from stages.listing_detail import _resolve_indexes, extract_listing_detail as _extract_listing_detail

from browser.linkedin_jobs import (
    extract_listings,
    get_current_page,
    get_visible_pages,
    click_search_button,
    open_all_filters_menu,
    open_jobs_search_page,
    go_to_page,
    parse_filters_state,
    set_keyword_input,
    set_location_input,
    show_results,
    sync_filters_state,
)


DEFAULT_DELAYS: dict[str, float | int] = {
    "open_jobs_search_page": 1,
    "set_keyword_input": 0,
    "set_location_input": 0,
    "click_search_button": 0,
    "open_all_filters_menu": 1,
    "sync_filters_state": 0,
    "show_results": 1,
    "click_listing_card": 1,
    "click_listing_card_jitter": 0,
}

DEFAULT_PAGE_SELECTION: tuple[int, ...] = (1,)


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _merge_delays(delays: dict[str, Any] | None) -> dict[str, float | int]:
    merged = deepcopy(DEFAULT_DELAYS)
    if isinstance(delays, dict):
        for key, value in delays.items():
            merged[str(key)] = value
    return merged


def _delay(delays: dict[str, float | int], key: str, fallback: float | int = 0) -> float | int:
    value = delays.get(key, fallback)
    try:
        return float(value)
    except Exception:
        return fallback


def _page_number(value: Any) -> int | None:
    try:
        numeric = int(str(value).strip())
    except Exception:
        return None
    return numeric if numeric > 0 else None


def _unique_pages(values: list[int]) -> list[int]:
    pages: list[int] = []
    for value in values:
        if value > 0 and value not in pages:
            pages.append(value)
    return pages


def _parse_pages_spec(pages: Any) -> list[int]:
    if pages is None or pages == "":
        return list(DEFAULT_PAGE_SELECTION)
    if isinstance(pages, int):
        return [pages] if pages > 0 else list(DEFAULT_PAGE_SELECTION)
    if isinstance(pages, str):
        text = pages.strip()
        if not text:
            return list(DEFAULT_PAGE_SELECTION)
        collected: list[int] = []
        for chunk in re.split(r"[,\s]+", text):
            piece = chunk.strip()
            if not piece:
                continue
            if "-" in piece:
                parts = [part.strip() for part in piece.split("-", 1)]
                if len(parts) == 2:
                    start = _page_number(parts[0])
                    end = _page_number(parts[1])
                    if start is not None and end is not None:
                        lo, hi = sorted((start, end))
                        collected.extend(list(range(lo, hi + 1)))
                        continue
            page = _page_number(piece)
            if page is not None:
                collected.append(page)
        return _unique_pages(collected) or list(DEFAULT_PAGE_SELECTION)
    if isinstance(pages, list):
        collected: list[int] = []
        for item in pages:
            if isinstance(item, int):
                collected.append(item)
            elif isinstance(item, str):
                collected.extend(_parse_pages_spec(item))
        return _unique_pages(collected) or list(DEFAULT_PAGE_SELECTION)
    page = _page_number(pages)
    return [page] if page is not None else list(DEFAULT_PAGE_SELECTION)


def _page_selection_text(pages: list[int]) -> str:
    if not pages:
        return "1"
    sorted_pages = _unique_pages(pages)
    if len(sorted_pages) == 1:
        return str(sorted_pages[0])
    return ", ".join(str(page) for page in sorted_pages)


def _search_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": task.get("id", ""),
        "query": task.get("keyword", ""),
        "location": task.get("location", ""),
        "filter_by": task.get("filter_by", ""),
        "filters": task.get("filters", []),
        "pages_requested": task.get("pages_requested", []),
        "pages_fetched": task.get("pages_fetched", []),
        "listing_count": task.get("listing_count", 0),
        "visible_unfetched_pages": task.get("visible_unfetched_pages", []),
        "warnings": task.get("warnings", []),
    }
    return summary


def _make_search_task(
    *,
    keyword: str,
    location: str,
    filter_by: str,
    filters: list[dict[str, Any]] | None,
    pages_requested: list[int],
    listings: list[dict[str, Any]],
    pages_fetched: list[int],
    visible_unfetched_pages: list[int],
    warnings: list[str],
    page_cache: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = uuid4().hex[:10]
    return {
        "id": task_id,
        "keyword": keyword,
        "location": location,
        "filter_by": filter_by,
        "filters": filters or [],
        "pages_requested": pages_requested,
        "pages_fetched": pages_fetched,
        "listing_count": len(listings),
        "visible_unfetched_pages": visible_unfetched_pages,
        "warnings": warnings,
        "page_cache": page_cache,
    }


def _format_page_list(pages: list[int]) -> str:
    unique = _unique_pages(pages)
    return ", ".join(str(page) for page in unique) if unique else "none"


def _visible_page_numbers(driver) -> list[int]:
    pages = get_visible_pages(driver, verbose=False).get("pages", [])
    numbers: list[int] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = _page_number(page.get("text"))
        if number is not None and number not in numbers:
            numbers.append(number)
    return numbers


def _next_visible_page_number(driver, current_page: int | None) -> int | None:
    numbers = _visible_page_numbers(driver)
    if not numbers:
        return None
    if current_page is None:
        return numbers[0]
    higher = [number for number in numbers if number > current_page]
    if higher:
        return min(higher)
    return None


def _restore_page_from_cache(driver, target_page: int | None, delays: dict[str, Any], verbose: bool = False) -> bool:
    if target_page is None:
        return False
    current_page = _page_number(get_current_page(driver, verbose=False).get("current_page"))
    if current_page == target_page:
        return True
    try:
        go_to_page(driver, target_page, delay_seconds=_delay(delays, "go_to_page"), verbose=verbose)
        return True
    except Exception:
        pass

    visited: set[int] = set()
    for _ in range(12):
        if current_page == target_page:
            return True
        visible = _visible_page_numbers(driver)
        if target_page in visible:
            try:
                go_to_page(driver, target_page, delay_seconds=_delay(delays, "go_to_page"), verbose=verbose)
                return True
            except Exception:
                pass
        candidates = [number for number in visible if current_page is None or number > current_page]
        if target_page is not None and current_page is not None and target_page < current_page:
            candidates = [number for number in visible if number < current_page]
        if not candidates:
            break
        next_page = min(candidates) if target_page < (current_page or target_page) else max(candidates)
        if next_page in visited:
            break
        visited.add(next_page)
        try:
            go_to_page(driver, next_page, delay_seconds=_delay(delays, "go_to_page"), verbose=verbose)
        except Exception:
            break
        current_page = next_page
    return _page_number(get_current_page(driver, verbose=False).get("current_page")) == target_page


def _page_snapshot(
    driver,
    *,
    page_number: int | None,
    start_index: int,
    listings: list[dict[str, Any]],
) -> dict[str, Any]:
    current_page = get_current_page(driver, verbose=False).get("current_page")
    visible_pages = get_visible_pages(driver, verbose=False).get("pages", [])
    return {
        "page": page_number,
        "current_page": current_page,
        "visible_pages": visible_pages,
        "current_url": getattr(driver, "current_url", "") or "",
        "start_index": start_index,
        "end_index": start_index + len(listings) - 1 if listings else start_index - 1,
        "listing_count": len(listings),
        "listings": listings,
    }


def _mock_log_path(base: str | None, suffix: str) -> str:
    root = (base or "mock://mongodb/linkedin").rstrip("/")
    return f"{root}/{suffix.strip('/')}"


def _normalize_filter_value(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _is_truthy_text(value: str) -> bool:
    return value.casefold() in {"1", "true", "yes", "on", "checked", "enabled"}


def _normalize_filters_payload(filters: Any) -> list[dict[str, Any]]:
    if isinstance(filters, list):
        return [item for item in filters if isinstance(item, dict)]
    if not isinstance(filters, dict):
        return []

    section_aliases = {
        "experience_level": ("Experience level", "checkbox"),
        "experience level": ("Experience level", "checkbox"),
        "date_posted": ("Date posted", "radio"),
        "date posted": ("Date posted", "radio"),
        "job_type": ("Job type", "checkbox"),
        "job type": ("Job type", "checkbox"),
        "remote": ("Remote", "checkbox"),
        "easy_apply": ("Easy Apply", "switch"),
        "easy apply": ("Easy Apply", "switch"),
        "has_verifications": ("Has verifications", "checkbox"),
        "has verifications": ("Has verifications", "checkbox"),
        "location": ("Location", "checkbox"),
        "industry": ("Industry", "checkbox"),
        "job_function": ("Job function", "checkbox"),
        "job function": ("Job function", "checkbox"),
        "title": ("Title", "checkbox"),
        "benefits": ("Benefits", "checkbox"),
        "commitments": ("Commitments", "checkbox"),
        "in_your_network": ("In your network", "checkbox"),
        "in your network": ("In your network", "checkbox"),
        "fair_chance_employer": ("Fair Chance Employer", "checkbox"),
        "fair chance employer": ("Fair Chance Employer", "checkbox"),
        "under_10_applicants": ("Under 10 applicants", "checkbox"),
        "under 10 applicants": ("Under 10 applicants", "checkbox"),
    }
    boolean_toggle_labels = {
        "Easy Apply": "Toggle Easy Apply filter",
        "Has verifications": "Toggle Has verifications filter",
        "Under 10 applicants": "Toggle Under 10 applicants filter",
        "In your network": "Toggle In your network filter",
        "Fair Chance Employer": "Toggle Fair Chance Employer filter",
    }

    normalized: list[dict[str, Any]] = []
    for key, value in filters.items():
        lookup_key = str(key).strip().lower()
        section_name, section_type = section_aliases.get(lookup_key, (str(key).replace("_", " ").strip(), "checkbox"))
        normalized_value = _normalize_filter_value(value)
        if not normalized_value:
            continue
        if section_type == "radio":
            normalized.append({"section": section_name, "type": "radio", "input": normalized_value})
        elif section_type == "switch":
            state = _is_truthy_text(normalized_value)
            normalized.append({"section": section_name, "type": "switch", "state": state})
        else:
            if section_name in boolean_toggle_labels and _is_truthy_text(normalized_value):
                normalized.append(
                    {
                        "section": section_name,
                        "type": "checkbox",
                        "inputs": [{"name": boolean_toggle_labels[section_name], "state": True}],
                    }
                )
                continue
            inputs = []
            for item in re.split(r"[;,|]", normalized_value):
                choice = _normalize_filter_value(item)
                if choice:
                    inputs.append({"name": choice, "state": True})
            if inputs:
                normalized.append({"section": section_name, "type": "checkbox", "inputs": inputs})
    return normalized


def _session_state_for_listings(
    driver: Any,
    filters: dict[str, Any] | None,
    listings: dict[str, Any] | None,
    search_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pages = get_visible_pages(driver, verbose=False)
    current_page = get_current_page(driver, verbose=False)
    search_task = search_task or {}
    return {
        "current_url": getattr(driver, "current_url", "") or "",
        "current_page": current_page.get("current_page"),
        "visible_pages": pages.get("pages", []),
        "filter_snapshot": filters or {},
        "listing_count": len((listings or {}).get("listings", []) if isinstance(listings, dict) else []),
        "search_task_id": search_task.get("id", ""),
        "pages_requested": search_task.get("pages_requested", []),
        "pages_fetched": search_task.get("pages_fetched", []),
        "visible_unfetched_pages": search_task.get("visible_unfetched_pages", []),
    }


def _resolve_listing_indexes_by_id(listings: list[dict[str, Any]], listing_id: str) -> list[int]:
    needle = str(listing_id or "").strip()
    if not needle:
        return []
    matches: list[int] = []
    for index, listing in enumerate(listings):
        if not isinstance(listing, dict):
            continue
        candidates = {
            str(listing.get("job_id", "")).strip(),
            str(listing.get("listing_id", "")).strip(),
        }
        if needle in candidates:
            matches.append(index)
    return matches


def _collect_requested_pages(
    driver,
    requested_pages: list[int],
    *,
    merged_delays: dict[str, Any],
    verbose: bool,
    now: datetime | None,
    combined_listings: list[dict[str, Any]] | None = None,
    page_cache: list[dict[str, Any]] | None = None,
    fetched_pages: list[int] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if combined_listings is None:
        combined_listings = []
    if page_cache is None:
        page_cache = []
    if fetched_pages is None:
        fetched_pages = []
    if warnings is None:
        warnings = []
    current_page_number = _page_number(get_current_page(driver, verbose=verbose).get("current_page"))
    requested_queue = _unique_pages([page for page in requested_pages if isinstance(page, int) and page > 0])

    for requested_page in requested_queue:
        if requested_page in fetched_pages:
            warnings.append(f"Skipped already fetched page {requested_page}")
            continue

        if current_page_number != requested_page:
            if not _restore_page_from_cache(driver, requested_page, merged_delays, verbose=verbose):
                warnings.append(f"Could not restore page {requested_page}")
                continue
            current_page_number = _page_number(get_current_page(driver, verbose=verbose).get("current_page"))

        if current_page_number != requested_page:
            warnings.append(f"Could not reach page {requested_page}")
            continue

        page_payload = extract_listings(driver, verbose=verbose, now=now)
        page_listings = page_payload.get("listings", []) if isinstance(page_payload, dict) else []
        page_cache.append(
            _page_snapshot(
                driver,
                page_number=current_page_number,
                start_index=len(combined_listings),
                listings=list(page_listings),
            )
        )
        combined_listings.extend(page_listings)
        fetched_pages.append(requested_page)
        current_page_number = requested_page

    visible_unfetched_pages = [
        page
        for page in _visible_page_numbers(driver)
        if page not in fetched_pages
    ]
    return {
        "listings": combined_listings,
        "page_cache": page_cache,
        "pages_fetched": fetched_pages,
        "visible_unfetched_pages": visible_unfetched_pages,
        "warnings": warnings,
    }


def fetch_job_listings(
    driver,
    keyword: str,
    location: str,
    filters: list[dict[str, Any]] | None = None,
    filter_by: str = "Jobs",
    pages: Any = 1,
    delays: dict[str, Any] | None = None,
    log_path: str | None = None,
    verbose: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    merged_delays = _merge_delays(delays)
    logger = TreeLogger("linkedin_fetch_job_listings", verbose=verbose)
    result: dict[str, Any] = {
        "status": "partial",
        "keyword": keyword,
        "location": location,
        "filter_by": filter_by,
        "filters": filters or [],
        "pages_requested": [],
        "listings": [],
        "pagination": {"pages": [], "current_page": None},
        "warnings": [],
        "log_path": _mock_log_path(log_path, "fetch_job_listings"),
        "session_state": {},
        "search_task": {},
        "dev": {"page_cache": [], "search_task": {}},
        "logs": None,
    }

    if driver is None:
        raise ValueError("fetch_job_listings requires a browser driver")

    logger.event(
        "Open LinkedIn jobs search page",
        details=[f"log_path={result['log_path']}"] if result["log_path"] else None,
        verbose=verbose,
    )
    open_jobs_search_page(
        driver,
        delay_seconds=_delay(merged_delays, "open_jobs_search_page"),
        verbose=verbose,
    )

    if keyword:
        logger.event("Set keyword", f"keyword={keyword}", verbose=verbose)
        set_keyword_input(driver, keyword, delay_seconds=_delay(merged_delays, "set_keyword_input"), verbose=verbose)
    else:
        result["warnings"].append("Missing keyword")

    if location:
        logger.event("Set location", f"location={location}", verbose=verbose)
        set_location_input(driver, location, delay_seconds=_delay(merged_delays, "set_location_input"), verbose=verbose)
    else:
        result["warnings"].append("Missing location")

    logger.event("Click search", verbose=verbose)
    click_search_button(driver, delay_seconds=_delay(merged_delays, "click_search_button"), verbose=verbose)

    logger.event("Open filters panel", verbose=verbose)
    open_all_filters_menu(driver, delay_seconds=_delay(merged_delays, "open_all_filters_menu"), verbose=verbose)
    current_filters = parse_filters_state(driver.page_source, verbose=verbose)
    result["filter_snapshot"] = current_filters

    if filters:
        logger.event("Sync filters", verbose=verbose)
        normalized_filters = _normalize_filters_payload(filters)
        synced = sync_filters_state(
            driver,
            {"filter_by": filter_by, "filters": normalized_filters},
            delay_seconds=_delay(merged_delays, "sync_filters_state"),
            verbose=verbose,
        )
        result["filter_snapshot_after_sync"] = synced

    logger.event("Show results", verbose=verbose)
    show_results(driver, delay_seconds=_delay(merged_delays, "show_results"), verbose=verbose)
    requested_pages = _parse_pages_spec(pages)
    result["pages_requested"] = requested_pages

    page_result = _collect_requested_pages(
        driver,
        requested_pages,
        merged_delays=merged_delays,
        verbose=verbose,
        now=now,
        combined_listings=[],
        page_cache=[],
        fetched_pages=[],
        warnings=result["warnings"],
    )

    result["listings"] = page_result["listings"]
    result["pagination"] = {
        "pages": get_visible_pages(driver, verbose=verbose).get("pages", []),
        "current_page": get_current_page(driver, verbose=verbose).get("current_page"),
    }
    search_task = _make_search_task(
        keyword=keyword,
        location=location,
        filter_by=filter_by,
        filters=filters,
        pages_requested=requested_pages,
        listings=result["listings"],
        pages_fetched=page_result["pages_fetched"],
        visible_unfetched_pages=page_result["visible_unfetched_pages"],
        warnings=page_result["warnings"],
        page_cache=page_result["page_cache"],
    )
    result["search_task"] = _search_task_summary(search_task)
    result["dev"]["page_cache"] = page_result["page_cache"]
    result["dev"]["search_task"] = search_task
    result["session_state"] = _session_state_for_listings(
        driver,
        result.get("filter_snapshot_after_sync") or current_filters,
        {"listings": result["listings"]},
        search_task,
    )
    result["logs"] = logger.to_dict(verbose=verbose)
    result["status"] = "success" if not result["warnings"] else "partial"
    return result


def resume_search_task(
    driver,
    search_task_id: str,
    pages: Any = 1,
    search_tasks: dict[str, Any] | None = None,
    delays: dict[str, Any] | None = None,
    log_path: str | None = None,
    verbose: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    merged_delays = _merge_delays(delays)
    logger = TreeLogger("linkedin_resume_search_task", verbose=verbose)
    task_store = search_tasks if isinstance(search_tasks, dict) else {}
    base_record = task_store.get(str(search_task_id).strip())

    result: dict[str, Any] = {
        "status": "partial",
        "search_task_id": str(search_task_id).strip(),
        "pages_requested": _parse_pages_spec(pages),
        "listings": [],
        "pagination": {"pages": [], "current_page": None},
        "warnings": [],
        "log_path": _mock_log_path(log_path, "resume_search_task"),
        "session_state": {},
        "search_task": {},
        "dev": {"page_cache": [], "search_task": {}},
        "logs": None,
    }

    if driver is None:
        raise ValueError("resume_search_task requires a browser driver")
    if not isinstance(base_record, dict):
        result["warnings"].append(f"Search task not found: {search_task_id}")
        result["status"] = "partial"
        result["logs"] = logger.to_dict(verbose=verbose)
        return result

    base_search_task = base_record.get("search_task") if isinstance(base_record.get("search_task"), dict) else {}
    base_page_cache = _page_cache_from_listings_payload(base_record)
    base_listings = base_record.get("listings") if isinstance(base_record.get("listings"), list) else []
    fetched_pages = list(base_search_task.get("pages_fetched", [])) if isinstance(base_search_task, dict) else []
    combined_listings = list(base_listings)
    page_cache = list(base_page_cache)

    if page_cache:
        seed_page = _page_number(page_cache[0].get("page"))
        if seed_page is not None:
            _restore_page_from_cache(driver, seed_page, merged_delays, verbose=verbose)

    page_result = _collect_requested_pages(
        driver,
        result["pages_requested"],
        merged_delays=merged_delays,
        verbose=verbose,
        now=now,
        combined_listings=combined_listings,
        page_cache=page_cache,
        fetched_pages=fetched_pages,
        warnings=result["warnings"],
    )

    result["listings"] = page_result["listings"]
    result["pagination"] = {
        "pages": get_visible_pages(driver, verbose=verbose).get("pages", []),
        "current_page": get_current_page(driver, verbose=verbose).get("current_page"),
    }
    task_payload = {
        "id": str(search_task_id).strip(),
        "keyword": base_search_task.get("keyword", base_record.get("keyword", "")),
        "location": base_search_task.get("location", base_record.get("location", "")),
        "filter_by": base_search_task.get("filter_by", base_record.get("filter_by", "")),
        "filters": base_search_task.get("filters", base_record.get("filters", [])),
        "pages_requested": result["pages_requested"],
        "pages_fetched": page_result["pages_fetched"],
        "listing_count": len(result["listings"]),
        "visible_unfetched_pages": page_result["visible_unfetched_pages"],
        "warnings": page_result["warnings"],
        "page_cache": page_result["page_cache"],
    }
    result["search_task"] = _search_task_summary(task_payload)
    result["dev"]["page_cache"] = page_result["page_cache"]
    result["dev"]["search_task"] = task_payload
    result["session_state"] = _session_state_for_listings(
        driver,
        base_record.get("filter_snapshot_after_sync") or base_record.get("filter_snapshot") or {},
        {"listings": result["listings"]},
        task_payload,
    )
    result["logs"] = logger.to_dict(verbose=verbose)
    result["status"] = "success" if not result["warnings"] else "partial"
    return result


def _page_cache_from_listings_payload(listings_json: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(listings_json, dict):
        dev = listings_json.get("dev")
        if isinstance(dev, dict):
            cache = dev.get("page_cache")
            if isinstance(cache, list):
                return [item for item in cache if isinstance(item, dict)]
        cache = listings_json.get("page_cache")
        if isinstance(cache, list):
            return [item for item in cache if isinstance(item, dict)]
    return []


def _page_cache_entry_for_index(page_cache: list[dict[str, Any]], index: int) -> tuple[dict[str, Any] | None, int | None]:
    for entry in page_cache:
        try:
            start_index = int(entry.get("start_index", -1))
            end_index = int(entry.get("end_index", -1))
        except Exception:
            continue
        if start_index <= index <= end_index:
            return entry, index - start_index
    return None, None


def _group_indexes_by_page(
    page_cache: list[dict[str, Any]],
    indexes: list[int],
) -> list[tuple[dict[str, Any], list[int]]]:
    groups: list[tuple[dict[str, Any], list[int]]] = []
    seen_pages: list[int] = []
    for index in indexes:
        entry, local_index = _page_cache_entry_for_index(page_cache, index)
        if entry is None or local_index is None:
            continue
        page_number = _page_number(entry.get("page"))
        if page_number is None:
            continue
        existing = next((group for group in groups if _page_number(group[0].get("page")) == page_number), None)
        if existing is None:
            groups.append((entry, [local_index]))
        else:
            existing[1].append(local_index)
    return groups


def fetch_listings_description(
    driver,
    listings_json: dict[str, Any] | list[dict[str, Any]],
    listing_id: str | None = None,
    delays: dict[str, Any] | None = None,
    log_path: str | None = None,
    verbose: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    merged_delays = _merge_delays(delays)
    logger = TreeLogger("linkedin_fetch_listings_description", verbose=verbose)
    listings = listings_json.get("listings", []) if isinstance(listings_json, dict) else (listings_json if isinstance(listings_json, list) else [])
    page_cache = _page_cache_from_listings_payload(listings_json)

    resolved_indexes: list[int] = []
    if listing_id:
        resolved_indexes = _resolve_listing_indexes_by_id(listings, listing_id)

    result: dict[str, Any]
    if page_cache and resolved_indexes:
        grouped_indexes = _group_indexes_by_page(page_cache, resolved_indexes[:1])
        if not grouped_indexes:
            grouped_indexes = []
        result = {
            "dev": {"source": "linkedin", "warnings": [], "interactables": [], "page_cache": page_cache},
            "ai": [],
        }
        for entry, local_indexes in grouped_indexes or []:
            target_page = _page_number(entry.get("page"))
            if target_page is not None:
                _restore_page_from_cache(driver, target_page, merged_delays, verbose=verbose)
            page_listings = entry.get("listings", [])
            page_payload = {"listings": page_listings} if isinstance(page_listings, list) else {"listings": []}
            page_result = _extract_listing_detail(
                driver,
                page_payload,
                index=local_indexes if len(local_indexes) > 1 else local_indexes[0],
                delay_seconds=_delay(merged_delays, "click_listing_card"),
                delay_jitter=_delay(merged_delays, "click_listing_card_jitter", 0),
                verbose=verbose,
            )
            result["dev"]["warnings"].extend(page_result.get("dev", {}).get("warnings", []))
            result["dev"]["interactables"].extend(page_result.get("dev", {}).get("interactables", []))
            result["ai"].extend(page_result.get("ai", []))
            if not result["dev"].get("job_id"):
                result["dev"]["job_id"] = page_result.get("dev", {}).get("job_id", "")
            result.setdefault("dev", {}).update(
                {k: v for k, v in page_result.get("dev", {}).items() if k not in {"warnings", "interactables", "job_id"}}
            )
        if not grouped_indexes:
            result["dev"]["warnings"].append("No cached page matched the requested listing(s)")
    else:
        if not listing_id:
            result = {
                "status": "error",
                "dev": {"source": "linkedin", "warnings": ["listing_id is required"], "interactables": []},
                "ai": [],
            }
        else:
            target_index = resolved_indexes[0] if resolved_indexes else 0
            result = _extract_listing_detail(
                driver,
                listings_json,
                index=target_index,
                delay_seconds=_delay(merged_delays, "click_listing_card"),
                delay_jitter=_delay(merged_delays, "click_listing_card_jitter", 0),
                verbose=verbose,
            )

    if listing_id and not resolved_indexes:
        result.setdefault("dev", {}).setdefault("warnings", []).append(f"Listing id not found: {listing_id}")
        result["status"] = "partial"

    result["log_path"] = _mock_log_path(log_path, "fetch_listings_description")
    result["session_state"] = {
        "current_url": getattr(driver, "current_url", "") or "",
        "resolved_listing_id": listing_id or "",
    }
    result.setdefault("dev", {})["log_path"] = result["log_path"]
    result.setdefault("dev", {})["session_state"] = result["session_state"]
    result.setdefault("dev", {})["source"] = "linkedin"
    result["logs"] = logger.to_dict(verbose=verbose)
    return result


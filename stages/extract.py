from __future__ import annotations

from copy import deepcopy
from typing import Any

from browser.linkedin_jobs import (
    extract_listings,
    get_current_page,
    get_visible_pages,
    open_all_filters_menu,
    open_jobs_search_page,
    parse_filters_state,
    read_result_type,
    set_keyword_input,
    set_location_input,
    show_results,
    sync_filters_state,
)
from core.logging import TreeLogger


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _merge_stage_output(base: dict[str, Any], update: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_stage_output(result[key], value, verbose=verbose)
        else:
            result[key] = deepcopy(value)
    return result


def run_extract_stage(payload: dict[str, Any], context: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
    _vlog(verbose, "stage: extract")
    driver = context.get("driver")
    profile = context.get("profile", {})
    request = context.get("request", {})
    logger = context.get("logger") or TreeLogger("extract", verbose=verbose)
    store = context.get("store")

    keyword = payload.get("keyword") or request.get("keyword") or ""
    location = payload.get("location") or request.get("location") or ""
    filters = payload.get("filters") or request.get("filters") or []
    filter_by = payload.get("filter_by") or request.get("filter_by") or "Jobs"

    result: dict[str, Any] = {
        "stage": "extract",
        "success": False,
        "keyword": keyword,
        "location": location,
        "filter_by": filter_by,
        "filters": filters,
        "listings": [],
        "pagination": {"pages": [], "current_page": None},
        "warnings": [],
        "logs": None,
    }

    if driver is None:
        raise ValueError("run_extract_stage requires a browser driver in context['driver']")

    root = logger.event("Open LinkedIn jobs search page", verbose=verbose)
    page_state = open_jobs_search_page(driver, profile.get("linkedin", {}).get("jobs_search_url"), verbose=verbose)
    logger.child(root, "Page ready", f"page_ready={page_state['page_ready']}", verbose=verbose)

    if keyword:
        logger.event("Set keyword", f"keyword={keyword}", verbose=verbose)
        set_keyword_input(driver, keyword, verbose=verbose)
    else:
        result["warnings"].append("Missing keyword")

    if location:
        logger.event("Set location", f"location={location}", verbose=verbose)
        set_location_input(driver, location, verbose=verbose)
    else:
        result["warnings"].append("Missing location")

    logger.event("Open filters panel", verbose=verbose)
    open_all_filters_menu(driver, verbose=verbose)
    current_filters = parse_filters_state(driver.page_source, verbose=verbose)
    logger.child(root, "Read current filters", f"sections={len(current_filters['filters'])}", verbose=verbose)
    result["filter_snapshot"] = current_filters

    if filters or filter_by:
        synced = sync_filters_state(driver, {"filter_by": filter_by, "filters": filters}, verbose=verbose)
        result["filter_snapshot_after_sync"] = synced

    logger.event("Apply filters", verbose=verbose)
    show_results(driver, verbose=verbose)
    listings = extract_listings(driver, verbose=verbose)
    result["listings"] = listings["listings"]
    pages = get_visible_pages(driver, verbose=verbose)
    current_page = get_current_page(driver, verbose=verbose)
    result["pagination"] = {
        "pages": pages["pages"],
        "current_page": current_page["current_page"],
    }
    result["success"] = True
    result["logs"] = logger.to_dict(verbose=verbose)

    if store is not None:
        run_record = {
            "stage": "extract",
            "keyword": keyword,
            "location": location,
            "filter_by": filter_by,
            "status": "success",
            "output": result,
        }
        saved = store.save_run(run_record, verbose=verbose)
        store.save_stage_output(saved["_id"], "extract", result, verbose=verbose)
        result["run_id"] = saved["_id"]

    return result

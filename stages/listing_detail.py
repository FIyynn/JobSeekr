from __future__ import annotations

import random
import time
from copy import deepcopy
from typing import Any

from browser.linkedin_jobs import click_listing_card, read_job_detail_panel
from parsers.listing_detail import parse_listing_detail


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _as_listings(listings_json: Any) -> list[dict[str, Any]]:
    if isinstance(listings_json, dict):
        listings = listings_json.get("listings") or []
        return listings if isinstance(listings, list) else []
    if isinstance(listings_json, list):
        return listings_json
    return []


def _resolve_indexes(count: int, index: int) -> list[int]:
    if count <= 0:
        return []
    if index < 0:
        return list(range(count))
    if index >= count:
        return [count - 1]
    return [index]


def _pause(delay_seconds: float | int, delay_jitter: float | int) -> None:
    base = float(delay_seconds or 0)
    jitter = float(delay_jitter or 0)
    if base <= 0 and jitter <= 0:
        return
    sleep_for = base + (random.uniform(0, jitter) if jitter > 0 else 0.0)
    if sleep_for > 0:
        time.sleep(sleep_for)


def extract_listing_detail(
    driver,
    listings_json: dict[str, Any] | list[dict[str, Any]],
    index: int = 0,
    delay_seconds: float | int = 1,
    delay_jitter: float | int = 0,
    verbose: bool = True,
) -> dict[str, Any]:
    listings = _as_listings(listings_json)
    targets = _resolve_indexes(len(listings), int(index))
    result: dict[str, Any] = {
        "dev": {
            "source": "linkedin",
            "index": int(index),
            "job_id": "",
            "warnings": [],
            "interactables": [],
        },
        "ai": [],
    }

    if driver is None:
        raise ValueError("extract_listing_detail requires a browser driver")
    if not listings:
        result["dev"]["warnings"].append("No listings to read")
        return result
    if not targets:
        result["dev"]["warnings"].append("No target listings resolved")
        return result

    for position, target_index in enumerate(targets):
        listing = deepcopy(listings[target_index])
        _vlog(verbose, f"detail: click {target_index + 1}/{len(listings)}")
        click_info = click_listing_card(
            driver,
            target_index,
            delay_seconds=delay_seconds,
            verbose=verbose,
        )
        panel = read_job_detail_panel(driver, verbose=verbose)
        parsed = parse_listing_detail(panel.get("html", ""), verbose=verbose)
        if not parsed.get("listed_on"):
            parsed["listed_on"] = listing.get("listed_on") or listing.get("listed_time") or None
        if not listing.get("listed_on") and parsed.get("listed_on"):
            listing["listed_on"] = parsed["listed_on"]
        if not result["dev"]["job_id"]:
            result["dev"]["job_id"] = parsed.get("job_id", "") or listing.get("job_id", "")
        dev_listing = {
            key: listing.get(key)
            for key in ("link", "job_id")
            if key in listing
        }
        company_profile = parsed.get("company_profile", {}) if isinstance(parsed.get("company_profile"), dict) else {}
        result["dev"]["interactables"].append(
            {
                "listing_link": dev_listing.get("link", ""),
                "company_url": parsed.get("company_url", ""),
                "company_logo_url": parsed.get("company_logo_url", ""),
                "apply_button_xpath": parsed.get("apply_button_xpath", ""),
                "save_button_xpath": parsed.get("save_button_xpath", ""),
            }
        )
        ai_listing = {
            key: listing.get(key)
            for key in ("title", "company", "location", "job_id", "promoted", "easy_apply", "listed_on")
            if key in listing
        }
        job_description = parsed.get("job_description", "")
        if isinstance(job_description, dict):
            job_description = job_description.get("raw_text", "")
        ai_detail = {
            key: parsed.get(key)
            for key in (
                "job_id",
                "title",
                "company",
                "location",
                "posted_at",
                "apply_activity",
                "promotion_status",
                "response_insights",
                "listing_preferences",
                "missing_required_qualifications",
                "missing required qualifications?",
                "hiring_team",
            )
            if key in parsed
        }
        ai_detail["job_description"] = job_description if isinstance(job_description, str) else ""
        ai_item = {"listing": ai_listing, "detail": ai_detail}
        if company_profile:
            ai_item["company_profile"] = company_profile
        result["ai"].append(ai_item)
        if position < len(targets) - 1:
            _pause(delay_seconds, delay_jitter)

    return result

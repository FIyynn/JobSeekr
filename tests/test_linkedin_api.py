from __future__ import annotations

import unittest
from unittest.mock import patch

from services.linkedin import linkedin


class FakeDriver:
    def __init__(self):
        self.current_url = "https://www.linkedin.com/jobs/search"
        self.page_source = "<html></html>"
        self.current_page = "1"


class LinkedInFacadeTests(unittest.TestCase):
    def test_parse_pages_spec_supports_single_pages_ranges_and_lists(self):
        self.assertEqual(linkedin._parse_pages_spec(1), [1])
        self.assertEqual(linkedin._parse_pages_spec([1, 2, 5]), [1, 2, 5])
        self.assertEqual(linkedin._parse_pages_spec("1-3"), [1, 2, 3])
        self.assertEqual(linkedin._parse_pages_spec("1,3,5"), [1, 3, 5])

    def test_normalize_filters_payload_maps_known_controls_correctly(self):
        normalized = linkedin._normalize_filters_payload(
            {
                "experience_level": "Entry level",
                "date_posted": "Past week",
                "remote": "Remote",
                "easy_apply": "true",
                "has_verifications": "true",
                "under_10_applicants": "true",
                "in_your_network": "true",
                "fair_chance_employer": "true",
            }
        )

        lookup = {item["section"]: item for item in normalized}
        self.assertEqual(lookup["Experience level"]["type"], "checkbox")
        self.assertEqual(lookup["Experience level"]["inputs"][0]["name"], "Entry level")
        self.assertEqual(lookup["Date posted"]["type"], "radio")
        self.assertEqual(lookup["Date posted"]["input"], "Past week")
        self.assertEqual(lookup["Remote"]["type"], "checkbox")
        self.assertEqual(lookup["Easy Apply"]["type"], "switch")
        self.assertEqual(lookup["Has verifications"]["type"], "checkbox")
        self.assertEqual(lookup["Has verifications"]["inputs"][0]["name"], "Toggle Has verifications filter")
        self.assertEqual(lookup["Under 10 applicants"]["type"], "checkbox")
        self.assertEqual(lookup["Under 10 applicants"]["inputs"][0]["name"], "Toggle Under 10 applicants filter")
        self.assertEqual(lookup["In your network"]["inputs"][0]["name"], "Toggle In your network filter")
        self.assertEqual(lookup["Fair Chance Employer"]["inputs"][0]["name"], "Toggle Fair Chance Employer filter")

    def test_fetch_job_listings_uses_delay_map(self):
        driver = FakeDriver()
        with (
            patch.object(linkedin, "open_jobs_search_page", return_value={"page_ready": True}) as open_page,
            patch.object(linkedin, "set_keyword_input", return_value={"keyword_value": "engineer"}) as set_keyword,
            patch.object(linkedin, "set_location_input", return_value={"location_value": "Dubai"}) as set_location,
            patch.object(linkedin, "click_search_button", return_value={"search_clicked": True}) as click_search,
            patch.object(linkedin, "open_all_filters_menu", return_value={"modal_open": True}) as open_filters,
            patch.object(linkedin, "parse_filters_state", return_value={"filters": []}) as parse_filters,
            patch.object(linkedin, "sync_filters_state", return_value={"filters": []}) as sync_filters,
            patch.object(linkedin, "show_results", return_value={"results_shown": True}) as show_results,
            patch.object(linkedin, "extract_listings", return_value={"listings": [{"job_id": "1"}]}) as extract_listings,
            patch.object(linkedin, "get_visible_pages", return_value={"pages": [{"text": "1", "current": True}]}) as get_pages,
            patch.object(linkedin, "get_current_page", return_value={"current_page": "1"}) as get_current_page,
        ):
            result = linkedin.fetch_job_listings(
                driver,
                keyword="engineer",
                location="Dubai",
                filters=[],
                delays={"open_jobs_search_page": 0.2, "open_all_filters_menu": 0, "show_results": 0.7},
                log_path="mock://mongo/runs",
                verbose=False,
            )

        open_page.assert_called_once()
        set_keyword.assert_called_once_with(driver, "engineer", delay_seconds=0.0, verbose=False)
        set_location.assert_called_once_with(driver, "Dubai", delay_seconds=0.0, verbose=False)
        click_search.assert_called_once_with(driver, delay_seconds=0.0, verbose=False)
        open_filters.assert_called_once_with(driver, delay_seconds=0.0, verbose=False)
        sync_filters.assert_not_called()
        show_results.assert_called_once_with(driver, delay_seconds=0.7, verbose=False)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["log_path"], "mock://mongo/runs/fetch_job_listings")
        self.assertEqual(result["session_state"]["current_page"], "1")
        self.assertEqual(result["listings"], [{"job_id": "1"}])

    def test_fetch_job_listings_fetches_requested_pages(self):
        driver = FakeDriver()
        page_data = {
            "1": {"listings": [{"job_id": str(i)} for i in range(1, 11)]},
            "2": {"listings": [{"job_id": str(i)} for i in range(11, 21)]},
        }

        def extract_listings_side_effect(driver_obj, verbose=False, now=None):
            return page_data[driver_obj.current_page]

        def get_current_page_side_effect(driver_obj, verbose=False):
            return {"current_page": driver_obj.current_page}

        def get_visible_pages_side_effect(driver_obj, verbose=False):
            current = driver_obj.current_page
            return {
                "pages": [
                    {"text": "1", "current": current == "1"},
                    {"text": "2", "current": current == "2"},
                ],
                "current_page": current,
            }

        def go_to_page_side_effect(driver_obj, page, delay_seconds=0, verbose=False):
            driver_obj.current_page = str(page)
            return {"navigated": True, "current_page": page}

        with (
            patch.object(linkedin, "open_jobs_search_page", return_value={"page_ready": True}),
            patch.object(linkedin, "set_keyword_input", return_value={"keyword_value": "engineer"}),
            patch.object(linkedin, "set_location_input", return_value={"location_value": "Dubai"}),
            patch.object(linkedin, "click_search_button", return_value={"search_clicked": True}),
            patch.object(linkedin, "open_all_filters_menu", return_value={"modal_open": True}),
            patch.object(linkedin, "parse_filters_state", return_value={"filters": []}),
            patch.object(linkedin, "sync_filters_state", return_value={"filters": []}),
            patch.object(linkedin, "show_results", return_value={"results_shown": True}),
            patch.object(linkedin, "extract_listings", side_effect=extract_listings_side_effect) as extract_listings,
            patch.object(linkedin, "get_visible_pages", side_effect=get_visible_pages_side_effect),
            patch.object(linkedin, "get_current_page", side_effect=get_current_page_side_effect),
            patch.object(linkedin, "go_to_page", side_effect=go_to_page_side_effect) as go_to_page,
        ):
            result = linkedin.fetch_job_listings(
                driver,
                keyword="engineer",
                location="Dubai",
                filters=[],
                pages=[1, 2],
                verbose=False,
            )

        self.assertEqual(extract_listings.call_count, 2)
        go_to_page.assert_called_once_with(driver, 2, delay_seconds=0.0, verbose=False)
        self.assertEqual(len(result["listings"]), 20)
        self.assertEqual(len(result["dev"]["page_cache"]), 2)
        self.assertEqual(result["dev"]["page_cache"][0]["page"], 1)
        self.assertEqual(result["dev"]["page_cache"][1]["page"], 2)
        self.assertEqual(result["search_task"]["pages_fetched"], [1, 2])
        self.assertEqual(result["search_task"]["listing_count"], 20)

    def test_resume_search_task_skips_already_fetched_pages(self):
        driver = FakeDriver()
        driver.current_page = "2"
        base_record = {
            "listings": [{"job_id": "1"}],
            "search_task": {
                "id": "abc123",
                "keyword": "engineer",
                "location": "Dubai",
                "filter_by": "Jobs",
                "filters": [],
                "pages_requested": [1, 2],
                "pages_fetched": [1],
                "listing_count": 1,
                "visible_unfetched_pages": [2],
                "warnings": [],
            },
            "dev": {
                "page_cache": [
                    {"page": 1, "start_index": 0, "end_index": 0, "listings": [{"job_id": "1"}]}
                ],
                "search_task": {
                    "id": "abc123",
                    "keyword": "engineer",
                    "location": "Dubai",
                    "filter_by": "Jobs",
                    "filters": [],
                    "pages_requested": [1, 2],
                    "pages_fetched": [1],
                    "listing_count": 1,
                    "visible_unfetched_pages": [2],
                    "warnings": [],
                },
            },
        }

        def extract_listings_side_effect(driver_obj, verbose=False, now=None):
            return {"listings": [{"job_id": "2"}]}

        with (
            patch.object(linkedin, "extract_listings", side_effect=extract_listings_side_effect) as extract_listings,
            patch.object(linkedin, "get_visible_pages", return_value={"pages": [{"text": "1", "current": False}, {"text": "2", "current": True}], "current_page": "2"}),
            patch.object(linkedin, "get_current_page", return_value={"current_page": "2"}),
            patch.object(linkedin, "go_to_page", return_value={"navigated": True, "current_page": 2}) as go_to_page,
        ):
            result = linkedin.resume_search_task(
                driver,
                "abc123",
                pages=[1, 2, 2],
                search_tasks={"abc123": base_record},
                verbose=False,
            )

        self.assertEqual(extract_listings.call_count, 1)
        go_to_page.assert_called_once_with(driver, 1, delay_seconds=0.0, verbose=False)
        self.assertEqual(result["search_task"]["pages_fetched"], [1, 2])
        self.assertEqual(result["search_task"]["listing_count"], 2)
        self.assertTrue(any("Skipped already fetched page 1" in warning for warning in result["warnings"]))

    def test_fetch_listings_description_resolves_listing_id_and_range(self):
        driver = FakeDriver()
        listings_json = {
            "listings": [
                {"job_id": "1", "title": "First", "link": "https://www.linkedin.com/jobs/view/1/"},
                {"job_id": "2", "title": "Second", "link": "https://www.linkedin.com/jobs/view/2/"},
                {"job_id": "3", "title": "Third", "link": "https://www.linkedin.com/jobs/view/3/"},
            ]
            ,
            "dev": {
                "page_cache": [
                    {"page": 1, "start_index": 0, "end_index": 1, "listings": [{"job_id": "1"}, {"job_id": "2"}]},
                    {"page": 2, "start_index": 2, "end_index": 2, "listings": [{"job_id": "3"}]},
                ]
            },
        }
        with (
            patch.object(linkedin, "_extract_listing_detail", return_value={"dev": {"warnings": []}, "ai": []}) as extract_detail,
            patch.object(linkedin, "go_to_page", return_value={"navigated": True, "current_page": 2}) as go_to_page,
            patch.object(linkedin, "get_current_page", return_value={"current_page": "1"}),
            patch.object(linkedin, "get_visible_pages", return_value={"pages": [{"text": "1", "current": True}], "current_page": "1"}),
        ):
            result = linkedin.fetch_listings_description(
                driver,
                listings_json,
                listing_id="2",
                delays={"click_listing_card": 0.1},
                log_path="mock://mongo/runs",
                verbose=False,
            )

        self.assertEqual(result["log_path"], "mock://mongo/runs/fetch_listings_description")
        self.assertEqual(result["session_state"]["resolved_listing_id"], "2")
        self.assertEqual(result["dev"]["source"], "linkedin")
        go_to_page.assert_not_called()
        extract_detail.assert_called_once()

    def test_fetch_listings_description_restores_cached_older_page(self):
        driver = FakeDriver()
        listings_json = {
            "listings": [
                {"job_id": "1", "title": "First", "link": "https://www.linkedin.com/jobs/view/1/"},
                {"job_id": "2", "title": "Second", "link": "https://www.linkedin.com/jobs/view/2/"},
                {"job_id": "3", "title": "Third", "link": "https://www.linkedin.com/jobs/view/3/"},
                {"job_id": "4", "title": "Fourth", "link": "https://www.linkedin.com/jobs/view/4/"},
            ],
            "dev": {
                "page_cache": [
                    {"page": 1, "start_index": 0, "end_index": 1, "listings": [{"job_id": "1"}, {"job_id": "2"}]},
                    {"page": 2, "start_index": 2, "end_index": 3, "listings": [{"job_id": "3"}, {"job_id": "4"}]},
                ]
            },
        }
        captured = {}

        def extract_detail_side_effect(driver_obj, page_payload, index=0, delay_seconds=0, delay_jitter=0, verbose=False):
            captured["payload"] = page_payload
            captured["index"] = index
            return {"dev": {"warnings": [], "interactables": [], "job_id": "3"}, "ai": [{"job_id": "3"}]}

        with (
            patch.object(linkedin, "_extract_listing_detail", side_effect=extract_detail_side_effect) as extract_detail,
            patch.object(linkedin, "go_to_page", return_value={"navigated": True, "current_page": 2}) as go_to_page,
            patch.object(linkedin, "get_current_page", side_effect=lambda driver, verbose=False: {"current_page": driver.current_page}),
            patch.object(linkedin, "get_visible_pages", side_effect=lambda driver, verbose=False: {"pages": [{"text": driver.current_page, "current": True}], "current_page": driver.current_page}),
        ):
            result = linkedin.fetch_listings_description(
                driver,
                listings_json,
                listing_id="3",
                delays={"click_listing_card": 0},
                verbose=False,
            )

        go_to_page.assert_called_with(driver, 2, delay_seconds=0.0, verbose=False)
        extract_detail.assert_called_once()
        self.assertEqual(captured["index"], 0)
        self.assertEqual(len(captured["payload"]["listings"]), 2)
        self.assertEqual(result["ai"][0]["job_id"], "3")


if __name__ == "__main__":
    unittest.main()

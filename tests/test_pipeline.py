from __future__ import annotations

import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

from shared.config import load_app_config, resolve_extract_request
from shared.logging import TreeLogger
from browser import linkedin_jobs as lj


class PipelineTests(unittest.TestCase):
    def test_config_merge(self):
        app_config = load_app_config()
        request = resolve_extract_request(app_config, preset_name="linkedin_jobs_clean", overrides={"keyword": "data analyst"})
        self.assertEqual(request["filter_by"], "Jobs")
        self.assertEqual(request["keyword"], "data analyst")
        self.assertGreater(len(request["filters"]), 0)

    def test_logger_tree(self):
        logger = TreeLogger("extract")
        parent = logger.event("Open page")
        logger.child(parent, "Page ready", "page_ready=True")
        output = logger.to_dict()
        self.assertEqual(output["stage"], "extract")
        self.assertEqual(output["events"][0]["children"][0]["message"], "Page ready")

    def test_sync_skips_unrequested_sections(self):
        current_snapshot = {
            "filter_by": {"selected": "Jobs", "options": []},
            "filters": [
                {
                    "section": "Sort by",
                    "type": "radio",
                    "inputs": [
                        {"name": "Most relevant", "state": True},
                        {"name": "Most recent", "state": False},
                    ],
                },
                {
                    "section": "Company",
                    "type": "checkbox",
                    "inputs": [
                        {"name": "Example Co", "state": True},
                    ],
                },
            ],
        }
        seen_sections: list[str] = []

        with (
            patch.object(lj, "_fast_filters_snapshot", return_value=current_snapshot),
            patch.object(lj, "_ensure_all_filters_menu_open", return_value={"modal_open": True}),
            patch.object(lj, "_filters_scope", return_value=MagicMock()),
            patch.object(lj, "_section_block_in_scope", side_effect=lambda scope, section, verbose=True: seen_sections.append(section) or object()),
            patch.object(lj, "_set_radio_group"),
            patch.object(lj, "_set_checkbox_group"),
            patch.object(lj, "_set_switch"),
            patch.object(lj, "_set_pills"),
        ):
            result = lj.sync_filters_state(
                object(),
                {
                    "filter_by": "Jobs",
                    "filters": [
                        {
                            "section": "Sort by",
                            "type": "radio",
                            "input": "Most recent",
                        }
                    ],
                },
                delay_seconds=0,
                verbose=False,
            )

        self.assertEqual(seen_sections, ["Sort by", "Company"])
        self.assertEqual(result["filter_by"]["selected"], "Jobs")

    def test_sync_clears_unrequested_checkbox_sections(self):
        current_snapshot = {
            "filter_by": {"selected": "Jobs", "options": []},
            "filters": [
                {
                    "section": "Company",
                    "type": "checkbox",
                    "inputs": [
                        {"name": "Example Co", "state": True},
                    ],
                },
                {
                    "section": "Sort by",
                    "type": "radio",
                    "inputs": [
                        {"name": "Most relevant", "state": True},
                        {"name": "Most recent", "state": False},
                    ],
                },
            ],
        }

        with (
            patch.object(lj, "_fast_filters_snapshot", return_value=current_snapshot),
            patch.object(lj, "_ensure_all_filters_menu_open", return_value={"modal_open": True}),
            patch.object(lj, "_filters_scope", return_value=MagicMock()),
            patch.object(lj, "_section_block_in_scope", return_value=object()),
            patch.object(lj, "_set_radio_group"),
            patch.object(lj, "_set_checkbox_group") as set_checkbox_group,
            patch.object(lj, "_set_switch"),
            patch.object(lj, "_set_pills"),
        ):
            lj.sync_filters_state(
                object(),
                {
                    "filter_by": "Jobs",
                    "filters": [
                        {
                            "section": "Sort by",
                            "type": "radio",
                            "input": "Most recent",
                        }
                    ],
                },
                delay_seconds=0,
                verbose=False,
            )

        self.assertEqual(set_checkbox_group.call_count, 1)
        self.assertEqual(set_checkbox_group.call_args.args[1], [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from tests.llm_test_linkedin.linkedin_runtime import (
    build_messages,
    load_instruction_bundle,
    parse_command_block,
    parse_model_output,
    format_tool_result_for_llm,
    validate_tool_args,
)


class LinkedInLlmRuntimeParserTests(unittest.TestCase):
    def test_parse_valid_search_command(self):
        parsed = parse_model_output(
            '<cmd>linkedin.fetch_job_listings({"keyword":"engineer","location":"Dubai","pages":"1-3"})</cmd>'
        )
        self.assertEqual(parsed["kind"], "command")
        self.assertEqual(parsed["tool"], "linkedin.fetch_job_listings")
        self.assertEqual(parsed["args"], {"keyword": "engineer", "location": "Dubai", "pages": "1-3"})

    def test_legacy_flat_filter_keys_are_coerced(self):
        parsed = parse_model_output(
            '<cmd>linkedin.fetch_job_listings({"keyword":"engineer","location":"Dubai","experience_level":"Entry level","date_posted":"Past month","job_type":"Full-time","pages":"1-3"})</cmd>'
        )
        self.assertEqual(parsed["kind"], "command")
        self.assertEqual(
            parsed["args"],
            {
                "keyword": "engineer",
                "location": "Dubai",
                "pages": "1-3",
                "filters": {
                    "experience_level": "Entry level",
                    "date_posted": "Past month",
                    "job_type": "Full-time",
                },
            },
        )

    def test_parse_valid_resume_command(self):
        parsed = parse_model_output(
            '<cmd>linkedin.resume_search_task({"search_task_id":"abc123","pages":[4,5,6]})</cmd>'
        )
        self.assertEqual(parsed["kind"], "command")
        self.assertEqual(parsed["tool"], "linkedin.resume_search_task")
        self.assertEqual(parsed["args"], {"search_task_id": "abc123", "pages": [4, 5, 6]})

    def test_parse_valid_detail_command(self):
        parsed = parse_model_output(
            '<cmd>linkedin.fetch_listings_description({"listing_id":"4432971734"})</cmd>'
        )
        self.assertEqual(parsed["kind"], "command")
        self.assertEqual(parsed["tool"], "linkedin.fetch_listings_description")

    def test_unclosed_final_response_is_error(self):
        parsed = parse_model_output("<final_response>done")
        self.assertEqual(parsed["kind"], "error")
        self.assertIn("Unclosed <final_response> tag", parsed["message"])

    def test_non_linkedin_tool_is_rejected(self):
        parsed = parse_command_block('webagent_click({"target_id":"i1"})')
        self.assertEqual(parsed["kind"], "error")
        self.assertIn("Unknown tool", parsed["message"])

    def test_hidden_runtime_args_are_rejected(self):
        ok, message = validate_tool_args(
            "linkedin.fetch_job_listings",
            {"keyword": "engineer", "location": "Dubai", "driver": "x"},
        )
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)

    def test_removed_max_listings_argument_is_rejected(self):
        ok, message = validate_tool_args(
            "linkedin.fetch_job_listings",
            {"keyword": "engineer", "location": "Dubai", "max_listings": 20},
        )
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)

    def test_pages_argument_is_allowed(self):
        ok, message = validate_tool_args(
            "linkedin.fetch_job_listings",
            {"keyword": "engineer", "location": "Dubai", "pages": [1, 2, 3]},
        )
        self.assertTrue(ok)

    def test_resume_search_task_requires_id_and_pages(self):
        ok, message = validate_tool_args(
            "linkedin.resume_search_task",
            {"search_task_id": "abc123", "pages": [1, 2]},
        )
        self.assertTrue(ok)
        ok, message = validate_tool_args("linkedin.resume_search_task", {"pages": [1]})
        self.assertFalse(ok)
        self.assertIn("Missing required argument", message)

    def test_listing_selection_requirement(self):
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"listing_id": "123"})
        self.assertTrue(ok)
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"listing_index": 0})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"driver": "x"})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)

    def test_search_result_is_formatted_compactly(self):
        text = format_tool_result_for_llm(
            {
                "tool": "linkedin.fetch_job_listings",
                "status": "success",
                "keyword": "engineer",
                "location": "Dubai",
                "listings": [
                    {
                        "title": "Software Engineer",
                        "company": "Acme",
                        "location": "Dubai, UAE",
                        "job_id": "123",
                        "easy_apply": True,
                    }
                ],
                "pagination": {"current_page": "1", "pages": [{"text": "1", "current": True}]},
                "search_task": {
                    "id": "abc123",
                    "keyword": "engineer",
                    "location": "Dubai",
                    "filters": [{"section": "Experience level", "type": "checkbox"}],
                    "pages_requested": [1, 2],
                    "pages_fetched": [1],
                    "listing_count": 1,
                    "visible_unfetched_pages": [2, 3],
                },
            }
        )
        self.assertIn("listings:", text)
        self.assertIn("[0] Software Engineer", text)
        self.assertIn("pagination:", text)
        self.assertIn("search task:", text)
        self.assertIn("search_task_id: abc123", text)
        self.assertNotIn("logs:", text)
        self.assertNotIn("page_cache", text)

    def test_instruction_bundle_includes_reflection_phase(self):
        bundle = load_instruction_bundle()
        planning = build_messages("Task text", bundle, phase="planning")
        tool = build_messages("Task text", bundle, phase="tool")
        reflection = build_messages("Task text", bundle, phase="reflection")
        self.assertIn("Create the first action plan only.", planning[-1]["content"])
        self.assertIn("Use the tool instructions", tool[-1]["content"])
        self.assertIn("Reflect on what has already been done", reflection[-1]["content"])


if __name__ == "__main__":
    unittest.main()

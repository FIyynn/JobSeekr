from __future__ import annotations

import unittest

from tests.llm_test.llm_runtime import (
    parse_command_block,
    parse_model_output,
    validate_tool_args,
)


class LlmRuntimeParserTests(unittest.TestCase):
    def test_parse_valid_command(self):
        parsed = parse_model_output('<cmd>webagent_fetch_page({"url":"https://example.com"})</cmd>')
        self.assertEqual(parsed["kind"], "command")
        self.assertEqual(parsed["tool"], "webagent_fetch_page")
        self.assertEqual(parsed["args"], {"url": "https://example.com"})

    def test_parse_final_response(self):
        parsed = parse_model_output("<final_response>done</final_response>")
        self.assertEqual(parsed["kind"], "final_response")
        self.assertEqual(parsed["text"], "done")

    def test_unclosed_final_response_is_error(self):
        parsed = parse_model_output("<final_response>done")
        self.assertEqual(parsed["kind"], "error")
        self.assertIn("Unclosed <final_response> tag", parsed["message"])

    def test_missing_command_is_plain_text(self):
        parsed = parse_model_output("just a normal response")
        self.assertEqual(parsed["kind"], "text")

    def test_malformed_command_is_error(self):
        parsed = parse_command_block("webagent_click target_id=i1")
        self.assertEqual(parsed["kind"], "error")

    def test_unknown_tool_is_rejected(self):
        parsed = parse_command_block('not_real({"x":1})')
        self.assertEqual(parsed["kind"], "error")
        self.assertIn("Unknown tool", parsed["message"])

    def test_hidden_args_are_rejected(self):
        ok, message = validate_tool_args("webagent_fetch_page", {"url": "https://example.com", "driver": "x"})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)

    def test_listing_description_requires_visible_selection(self):
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"listing_index": 2})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"listing_id": "123"})
        self.assertTrue(ok)
        ok, message = validate_tool_args("linkedin.fetch_listings_description", {"driver": "x"})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", message)


if __name__ == "__main__":
    unittest.main()

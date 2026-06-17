from __future__ import annotations

import unittest

from core.config import load_app_config, resolve_extract_request
from core.logging import TreeLogger


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


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from services.web import webagent


class FakeDriver:
    def __init__(self):
        self.current_url = "about:blank"
        self.page_source = "<html></html>"
        self.visited = []

    def get(self, url):
        self.current_url = url
        self.visited.append(url)


class FakeStore:
    def __init__(self):
        self.saved = []

    def insert_one(self, collection, document, verbose=True):
        self.saved.append((collection, document, verbose))
        return {"_id": "mock"}


class WebagentApiTests(unittest.TestCase):
    def test_fetch_page_wraps_markdown_and_logs(self):
        driver = FakeDriver()
        store = FakeStore()
        with patch.object(webagent, "output_markdown", return_value=("markdown", {"interactables": [1], "images": []})) as output:
            result = webagent.webagent_fetch_page(
                driver,
                "https://example.com",
                verbose=False,
                store=store,
                wait_seconds=0,
            )
        output.assert_called_once_with(driver, remove_json=True, remove_links=True)
        self.assertEqual(driver.current_url, "https://example.com")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["markdown"], "markdown")
        self.assertEqual(result["session_state"]["interactable_count"], 1)
        self.assertEqual(store.saved[0][0], "webagent_runs")

    def test_click_forwards_to_interact(self):
        driver = FakeDriver()
        store = FakeStore()
        with patch.object(webagent, "_interact", return_value={"status": "success", "message": "Clicked i1."}) as interact_mock:
            result = webagent.webagent_click(
                driver,
                "markdown",
                {"interactables": [{"id": "i1"}]},
                "i1",
                verbose=False,
                store=store,
            )
        interact_mock.assert_called_once_with(driver, "markdown", {"interactables": [{"id": "i1"}]}, "click", "i1", delay_seconds=0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["log_path"], "mock://mongodb/webagent/click")
        self.assertEqual(store.saved[0][0], "webagent_actions")

    def test_type_encodes_payload(self):
        driver = FakeDriver()
        with patch.object(webagent, "_interact", return_value={"status": "success"}) as interact_mock:
            result = webagent.webagent_type(
                driver,
                "markdown",
                {"interactables": [{"id": "t1"}]},
                "t1",
                "engineer",
                verbose=False,
            )
        interact_mock.assert_called_once_with(driver, "markdown", {"interactables": [{"id": "t1"}]}, "input_text", "t1?value=engineer", delay_seconds=0)
        self.assertEqual(result["status"], "success")

    def test_clear_text_forwards_to_interact(self):
        driver = FakeDriver()
        with patch.object(webagent, "_interact", return_value={"status": "success"}) as interact_mock:
            result = webagent.webagent_clear_text(
                driver,
                "markdown",
                {"interactables": [{"id": "t1"}]},
                "t1",
                verbose=False,
            )
        interact_mock.assert_called_once_with(driver, "markdown", {"interactables": [{"id": "t1"}]}, "clear", "t1", delay_seconds=0)
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()

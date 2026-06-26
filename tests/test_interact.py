from __future__ import annotations

from copy import deepcopy
import unittest

from browser.interact import interact, _choose_diff_summary, _row_state_diffs, _markdown_diffs, _diff_text
from browser.markdown import output_markdown


GENERIC_HTML = """
<html>
  <body>
    <main>
      <h1>Example Title</h1>
      <p>Some intro text with <a href="/jobs">a link</a> and a <button aria-label="Run action">Run</button>.</p>
      <label for="email">Email</label>
      <input id="email" type="text" placeholder="name@example.com">
      <select name="choice">
        <option>Alpha</option>
        <option selected>Beta</option>
      </select>
      <div>
        <img alt="Logo" src="https://example.com/logo.png"> Company name
      </div>
    </main>
  </body>
</html>
"""

STATE_BEFORE_HTML = """
<html>
  <body>
    <main>
      <button aria-pressed="false">Toggle view</button>
    </main>
  </body>
</html>
"""

STATE_AFTER_HTML = """
<html>
  <body>
    <main>
      <button aria-pressed="true">Toggle view</button>
    </main>
  </body>
</html>
"""


class FakePageDriver:
    def __init__(self, url: str, html: str):
        self.current_url = url
        self.page_source = html


class FakeElement:
    def __init__(self, text: str = "", tag_name: str = "div", selected: bool = False, on_send_keys=None):
        self.text = text
        self.tag_name = tag_name
        self._selected = selected
        self.value = ""
        self.clicked = False
        self.cleared = False
        self.selected_text = ""
        self.on_send_keys = on_send_keys

    def click(self):
        self.clicked = True

    def clear(self):
        self.cleared = True
        self.value = ""

    def send_keys(self, value):
        self.value = f"{self.value}{value}"
        if callable(self.on_send_keys):
            self.on_send_keys(self.value)

    def is_selected(self):
        return self._selected

    def get_attribute(self, name):
        if name == "value":
            return self.value
        return ""

    def select_by_visible_text(self, value):
        self.selected_text = value


class LiveFakeDriver(FakePageDriver):
    def __init__(self, url: str, html: str):
        super().__init__(url, html)
        self.elements = {}
        self.scripts = []

    def register(self, locator: str, element: FakeElement):
        self.elements[locator] = element

    def find_element(self, by, value):
        return self.elements[value]

    def execute_script(self, script, *args):
        self.scripts.append((script, args))


class InteractTests(unittest.TestCase):
    def test_resolves_stable_id_and_noops_click(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        button = next(item for item in dev["interactables"] if item["type"] == "button")
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        driver.register(button["locator"]["value"], FakeElement(text=button["text"], tag_name="button"))
        result = interact(driver, markdown, dev, "click", button["id"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["interaction_type"], "click")
        self.assertEqual(result["target_id"], button["id"])

    def test_resolves_image_target(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        self.assertEqual(dev["images"][0]["id"], "img1")
        image = dev["images"][0]
        image["locator"] = {"kind": "css", "value": "#image-hover-target"}
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        driver.register(image["locator"]["value"], FakeElement(text=image["alt"], tag_name="img"))
        result = interact(driver, markdown, dev, "hover", image["id"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["target"]["kind"], "image")
        self.assertEqual(result["target"]["id"], image["id"])

    def test_input_text_with_encoded_target_payload(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        input_target = next(item for item in dev["interactables"] if item["type"] == "input")
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        driver.register(input_target["locator"]["value"], FakeElement(text=input_target["text"], tag_name="input"))
        result = interact(driver, markdown, dev, "input_text", f'{input_target["id"]}?value=hello%20world')
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["payload"]["value"], "hello world")
        self.assertEqual(result["diffs"]["deleted_element_count"], 1)
        self.assertEqual(result["diffs"]["changed"], [])

    def test_input_text_keeps_first_half_and_captures_second_half(self):
        before_html = """
        <html>
          <body>
            <main>
              <div class="search-shell">
                <input id="search_form_input" name="q" role="combobox" placeholder="Search privately" value="">
              </div>
            </main>
          </body>
        </html>
        """
        after_first_html = """
        <html>
          <body>
            <main>
              <div class="search-shell">
                <input id="search_form_input" name="q" role="combobox" placeholder="Search privately" value="engin">
                <div class="controls">
                  <button type="button">clear</button>
                  <button type="button">search</button>
                </div>
              </div>
            </main>
          </body>
        </html>
        """
        after_second_html = """
        <html>
          <body>
            <main>
              <div class="search-shell">
                <input id="search_form_input" name="q" role="combobox" placeholder="Search privately" value="engineer">
                <div class="controls">
                  <button type="button">clear</button>
                  <button type="button">search</button>
                </div>
                <div class="suggestions">
                  <button type="button">engineer jobs</button>
                  <button type="button">engineer remote</button>
                </div>
              </div>
            </main>
          </body>
        </html>
        """
        markdown, dev = output_markdown(FakePageDriver("https://duckduckgo.com", before_html))
        input_target = next(item for item in dev["interactables"] if item["type"] == "input")
        driver = LiveFakeDriver("https://duckduckgo.com", before_html)
        state = {"count": 0}

        def _update_page_source(_value):
            state["count"] += 1
            driver.page_source = after_first_html if state["count"] == 1 else after_second_html

        driver.register(
            input_target["locator"]["value"],
            FakeElement(text=input_target["text"], tag_name="input", on_send_keys=_update_page_source),
        )
        result = interact(driver, markdown, dev, "input_text", f'{input_target["id"]}?value=engineer')
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["target_id"], input_target["id"])
        self.assertIn("engineer jobs", " ".join(result["diffs"]["added"]))
        self.assertIn("engineer remote", " ".join(result["diffs"]["added"]))

    def test_select_option_changes_line(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        select_target = next(item for item in dev["interactables"] if item["type"] == "select")
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        driver.register(select_target["locator"]["value"], FakeElement(text=select_target["text"], tag_name="select"))
        result = interact(driver, markdown, dev, "select_option", f'{select_target["id"]}?value=Gamma')
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["diffs"]["deleted_element_count"], 1)
        self.assertEqual(result["diffs"]["changed"], [])

    def test_clear_returns_deleted_count(self):
        markdown = "Notes [[i1]]"
        interactables = {
            "interactables": [
                {
                    "id": "i1",
                    "order": 1,
                    "type": "input",
                    "text": "Notes",
                    "value": "Line one\nLine two",
                    "state": {"input_type": "text", "checked": False, "disabled": False},
                    "locator": {"kind": "css", "value": "#notes"},
                }
            ],
            "images": [],
        }
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        driver.register("#notes", FakeElement(text="Notes", tag_name="input"))
        snapshot = deepcopy(interactables)
        result = interact(driver, markdown, interactables, "clear", "i1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["diffs"]["deleted_element_count"], 2)
        self.assertEqual(interactables, snapshot)

    def test_unsupported_interaction_fails_cleanly(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        result = interact(driver, markdown, dev, "drag", "i1")
        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported interaction type", result["message"])

    def test_missing_target_fails_cleanly(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        driver = LiveFakeDriver("https://example.com", GENERIC_HTML)
        result = interact(driver, markdown, dev, "click", "i999")
        self.assertEqual(result["status"], "error")
        self.assertIn("Target not found", result["message"])

    def test_prefers_tighter_diff_summary(self):
        broad = {
            "changed": [],
            "added": ["line " * 100, "more " * 100],
            "deleted_element_count": 12,
        }
        tight = {
            "changed": [],
            "added": ["Only one small change"],
            "deleted_element_count": 1,
        }
        chosen = _choose_diff_summary(broad, tight)
        self.assertEqual(chosen, tight)

    def test_row_state_diffs_ignore_unrelated_reflow(self):
        before = """
        <html>
          <body>
            <main>
              <article><a href="/a">Alpha</a></article>
              <article><a href="/b">Beta</a></article>
              <article><a href="/c">Gamma</a></article>
            </main>
          </body>
        </html>
        """
        after = """
        <html>
          <body>
            <main>
              <article><a href="/banner">Banner new</a></article>
              <article><a href="/a">Alpha</a></article>
              <article><a href="/b">Beta</a></article>
              <article><a href="/c">Gamma</a></article>
            </main>
          </body>
        </html>
        """
        _, before_dev = output_markdown(FakePageDriver("https://example.com", before))
        _, after_dev = output_markdown(FakePageDriver("https://example.com", after))
        diffs = _row_state_diffs(before_dev, after_dev, "i2")
        self.assertEqual(diffs["deleted_element_count"], 0)
        self.assertIn("Banner new", " ".join(diffs["added"]))
        self.assertNotIn("Gamma", " ".join(diffs["added"]))
        self.assertNotIn("Alpha", " ".join(diffs["added"]))

    def test_row_state_diffs_capture_control_state_changes(self):
        _, before_dev = output_markdown(FakePageDriver("https://example.com", STATE_BEFORE_HTML))
        _, after_dev = output_markdown(FakePageDriver("https://example.com", STATE_AFTER_HTML))
        diffs = _row_state_diffs(before_dev, after_dev, "i1")
        self.assertTrue(diffs["changed"])
        self.assertIn("pressed", " ".join(diffs["changed"]).lower())

    def test_markdown_diffs_strip_embedded_links(self):
        line = "2. || More actions [[i35]] || [Search domain fast.com](https://duckduckgo.com/?q=tester+site:fast.com&kp=1&t=h_) [[i36]] || [Internet Speed Test | Fast.com](https://fast.com/) [[i38]]"
        cleaned = _diff_text(line)
        self.assertIn("Search domain fast.com [[i36]]", cleaned)
        self.assertIn("Internet Speed Test | Fast.com [[i38]]", cleaned)
        self.assertNotIn("(https://duckduckgo.com/?q=tester+site:fast.com&kp=1&t=h_)", cleaned)
        self.assertNotIn("(https://fast.com/)", cleaned)

    def test_markdown_diffs_strip_broken_link_artifacts(self):
        line = "2. || More actions [[i35]] || fast.com || Only include results for this site](https://duckduckgo.com/?q=tester%20-site%3Afast.com) [[i36]] || Redo search without this site](https://duckduckgo.com/?q=tester%20-site%3Afast.com) [[i37]] || https://fast.com](https://fast.com/) [[i41]]"
        cleaned = _diff_text(line)
        self.assertIn("Only include results for this site [[i36]]", cleaned)
        self.assertIn("Redo search without this site [[i37]]", cleaned)
        self.assertIn("https://fast.com [[i41]]", cleaned)
        self.assertNotIn("](https://duckduckgo.com/?q=tester%20-site%3Afast.com)", cleaned)
        self.assertNotIn("](https://fast.com/)", cleaned)


if __name__ == "__main__":
    unittest.main()

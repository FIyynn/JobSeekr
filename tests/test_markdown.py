from __future__ import annotations

import unittest

from browser.markdown import _remove_markdown_links, output_markdown


class FakePageDriver:
    def __init__(self, url: str, html: str):
        self.current_url = url
        self.page_source = html


GENERIC_HTML = """
<html>
  <head><title>Example Page</title></head>
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
      <table>
        <tr><th>Column</th><th>Value</th></tr>
        <tr><td>One</td><td>Two</td></tr>
      </table>
      <div>Repeat line</div>
      <div>Repeat line</div>
    </main>
  </body>
</html>
"""


STATEFUL_HTML = """
<html>
  <body>
    <main>
      <button aria-pressed="true">Toggle view</button>
    </main>
  </body>
</html>
"""

UNCHECKED_FILTER_HTML = """
<html>
  <body>
    <main>
      <label for="easy-apply">Easy Apply</label>
      <input id="easy-apply" type="checkbox">
    </main>
  </body>
</html>
"""

SNAPSHOT_STATE_HTML = """
<html>
  <body>
    <main>
      <input id="radio-1" type="radio" data-codex-input-type="radio" data-codex-checked="true" data-codex-selected-text="Most relevant">
      <label for="radio-1">Most relevant</label>
    </main>
  </body>
</html>
"""


AMAZON_HTML = """
<html>
  <body>
    <article>
      <a href="/dp/B0001">
        <img alt="Product image" src="/image.jpg">
        <span>Product card</span>
      </a>
      <button>Buy now</button>
    </article>
  </body>
</html>
"""


ALIEXPRESS_HTML = """
<html>
  <body>
    <div>
      <p>Line one</p>
      <p>Line one</p>
      <script>ignored()</script>
      <div><a href="https://example.com/desc.htm">Description</a></div>
    </div>
  </body>
</html>
"""


JOB_CARD_GROUPED_HTML = """
<html>
  <body>
    <li data-occludable-job-id="4429888660" class="job-card-container">
      <div class="job-card-container relative job-card-list">
        <img alt="McDermott International, Ltd logo" src="https://example.com/logo.png">
        <a href="/jobs/view/4429888660/" aria-label="EFL Instructor, Military Setting in the UAE" class="job-card-container__link">
          <span aria-hidden="true"><strong>EFL Instructor, Military Setting in the UAE</strong></span>
        </a>
        <div class="company">McDermott International, Ltd</div>
        <div class="meta">Dubai, Dubai, United Arab Emirates (On-site) - Viewed - Easy Apply</div>
        <button aria-label="Dismiss EFL Instructor, Military Setting in the UAE job" type="button">
          <span aria-hidden="true">Dismiss</span>
        </button>
      </div>
    </li>
  </body>
</html>
"""

GOOGLE_MENU_HTML = """
<html>
  <body>
    <li data-layout="organic" class="result-row">
      <article data-testid="result">
        <div role="menu" aria-hidden="false">
          <a href="?q=tester%20site%3Awww.utest.com" role="menuitem">Only include results for this site</a>
          <a href="?q=tester%20-site%3Awww.utest.com" role="menuitem">Redo search without this site</a>
          <a href="#" role="menuitem">Block this site from all results</a>
          <div role="menuitem" tabindex="0">Share feedback about this site</div>
        </div>
        <div class="result-links">
          <a href="https://www.utest.com/">uTest</a>
          <a href="https://www.utest.com/">uTest - The Professional Network for Testers</a>
        </div>
      </article>
    </li>
  </body>
</html>
"""

DUCKDUCKGO_RESULT_HTML = """
<html>
  <body>
    <li data-layout="organic" class="wLL07_0Xnd1QZpzpfR4W">
      <article id="r1-1" data-handled-by-react="true" data-testid="result" data-nrn="result">
        <div class="OHr0VX9IuNcv6iakvT6A">
          <button type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="contextMenu-www.utest.com">
            <svg viewBox="0 0 16 16"></svg>
          </button>
          <div role="menu" id="contextMenu-www.utest.com" aria-hidden="false">
            <a class="bcz7ZQmpP9fW9gyprTn7" href="?q=tester%20site%3Awww.utest.com" role="menuitem">
              <span>Only include results for this site</span>
            </a>
            <a class="bcz7ZQmpP9fW9gyprTn7" href="?q=tester%20-site%3Awww.utest.com" role="menuitem">
              <span>Redo search without this site</span>
            </a>
            <a class="bcz7ZQmpP9fW9gyprTn7" href="#" role="menuitem">
              <span>Block this site from all results</span>
            </a>
            <div class="bcz7ZQmpP9fW9gyprTn7" role="menuitem" tabindex="0">
              <span>Share feedback about this site</span>
            </div>
          </div>
        </div>
        <div class="OQ_6vPwNhCeusNiEDcGp">
          <div class="mwuQiMOjmFJ5vmN6Vcqw CmOawDMavJGKvqBIPeeC SgSTKoqQXa0tEszD2zWF VkOimy54PtIClAT3GMbr LQVY1Jpkk8nyJ6HBWKAk">
            <span class="DpVR46dTZaePK29PDkz8">
              <a href="/?q=tester+site:www.utest.com&amp;kp=1&amp;t=h_" data-testid="result-extras-site-search-link">
                <div class="c_ZIRTZwvW2k4q8TtKU0"><img src="//external-content.duckduckgo.com/ip3/www.utest.com.ico" height="16" width="16" loading="lazy"></div>
              </a>
            </span>
            <div class="pAgARfGNTRe_uaK72TAD">
              <p>uTest</p>
              <a href="https://www.utest.com/" data-testid="result-extras-url-link">
                <div><p>https://www.utest.com</p></div>
              </a>
            </div>
          </div>
        </div>
        <div class="ikg2IXiCD14iVX7AdZo1">
          <h2>
            <a href="https://www.utest.com/" data-testid="result-title-a">
              <span>uTest - The Professional Network for Testers</span>
            </a>
          </h2>
        </div>
      </article>
    </li>
  </body>
</html>
"""

FAST_RESULT_HTML = """
<html>
  <body>
    <li data-layout="organic" class="wLL07_0Xnd1QZpzpfR4W">
      <article id="r1-1" data-handled-by-react="true" data-testid="result" data-nrn="result">
        <div class="OHr0VX9IuNcv6iakvT6A">
          <button type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="contextMenu-fast.com">
            <svg viewBox="0 0 16 16"></svg>
          </button>
          <div role="menu" id="contextMenu-fast.com" aria-hidden="true"></div>
        </div>
        <div class="OQ_6vPwNhCeusNiEDcGp">
          <div class="mwuQiMOjmFJ5vmN6Vcqw">
            <span class="DpVR46dTZaePK29PDkz8">
              <a href="/?q=tester+site:fast.com&amp;kp=1&amp;t=h_" rel="noopener" title="Search domain fast.com" data-testid="result-extras-site-search-link">
                <div class="c_ZIRTZwvW2k4q8TtKU0"><img src="//external-content.duckduckgo.com/ip3/fast.com.ico" height="16" width="16" loading="lazy"></div>
              </a>
            </span>
            <div class="pAgARfGNTRe_uaK72TAD">
              <p>Fast.com</p>
              <a href="https://fast.com/" rel="noopener" target="_self" data-testid="result-extras-url-link">
                <div><p>https://fast.com</p></div>
              </a>
            </div>
          </div>
        </div>
        <div class="ikg2IXiCD14iVX7AdZo1">
          <h2>
            <a href="https://fast.com/" rel="noopener" target="_self" data-testid="result-title-a">
              <span>Internet Speed Test | Fast.com</span>
            </a>
          </h2>
        </div>
        <div class="E2eLOJr8HctVnDOTM8fs">
          <div data-result="snippet">
            <div>
              <span><span>How fast is your download speed? In seconds, FAST.com's simple Internet speed test will estimate your ISP speed.</span></span>
            </div>
          </div>
        </div>
      </article>
    </li>
  </body>
</html>
"""

JOB_CARD_GROUPED_HTML_SHIFTED = """
<html>
  <body>
    <a href="/feed/">Feed</a>
    <button aria-label="Ignored control">Ignored</button>
    <li data-occludable-job-id="4429888660" class="job-card-container">
      <div class="job-card-container relative job-card-list">
        <img alt="McDermott International, Ltd logo" src="https://example.com/logo.png">
        <a href="/jobs/view/4429888660/" aria-label="EFL Instructor, Military Setting in the UAE" class="job-card-container__link">
          <span aria-hidden="true"><strong>EFL Instructor, Military Setting in the UAE</strong></span>
        </a>
        <div class="company">McDermott International, Ltd</div>
        <div class="meta">Dubai, Dubai, United Arab Emirates (On-site) - Viewed - Easy Apply</div>
        <button aria-label="Dismiss EFL Instructor, Military Setting in the UAE job" type="button">
          <span aria-hidden="true">Dismiss</span>
        </button>
      </div>
    </li>
  </body>
</html>
"""


class MarkdownTests(unittest.TestCase):
    def test_output_markdown_generic_page(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        self.assertIn("Example Title", markdown)
        self.assertIn("a link", markdown)
        self.assertNotIn("[a link](https://example.com/jobs)", markdown)
        self.assertIn("[[i1]]", markdown)
        self.assertIn("[[i2]]", markdown)
        self.assertIn("[[i3]]", markdown)
        self.assertEqual(dev["counts"]["interactables"], 4)
        self.assertEqual([item["type"] for item in dev["interactables"]], ["link", "button", "input", "select"])
        self.assertEqual(dev["interactables"][2]["text"], "Email")

    def test_output_markdown_preserves_order(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        self.assertLess(markdown.index("a link"), markdown.index("Run"))
        self.assertLess(markdown.index("Run"), markdown.index("Email"))
        self.assertLess(markdown.index("Email"), markdown.index("Beta"))

    def test_output_markdown_amazon_like_page(self):
        markdown, dev = output_markdown(FakePageDriver("https://www.amazon.com/dp/B0001", AMAZON_HTML))
        self.assertIn("Product card", markdown)
        self.assertIn("Buy now", markdown)
        self.assertEqual(dev["interactables"][0]["type"], "link")
        self.assertTrue(dev["interactables"][0]["href"].startswith("https://www.amazon.com/dp/B0001"))
        self.assertEqual(dev["interactables"][1]["type"], "button")

    def test_output_markdown_dedupes_noise(self):
        markdown, dev = output_markdown(FakePageDriver("https://www.aliexpress.com/item/1.html", ALIEXPRESS_HTML))
        self.assertEqual(markdown.count("Line one"), 1)
        self.assertNotIn("ignored()", markdown)
        self.assertEqual(dev["counts"]["interactables"], 1)

    def test_output_markdown_removes_json_lines_by_default(self):
        html = """
        <html>
          <body>
            <div>Keep this</div>
            <div>{"drop": true}</div>
            <div>[1, 2, 3]</div>
            <div>Keep that</div>
          </body>
        </html>
        """
        markdown, _ = output_markdown(FakePageDriver("https://example.com", html))
        self.assertIn("Keep this", markdown)
        self.assertIn("Keep that", markdown)
        self.assertNotIn('{"drop": true}', markdown)
        self.assertNotIn("[1, 2, 3]", markdown)

    def test_output_markdown_can_keep_json_lines(self):
        html = """
        <html>
          <body>
            <div>Keep this</div>
            <div>{"drop": true}</div>
          </body>
        </html>
        """
        markdown, _ = output_markdown(FakePageDriver("https://example.com", html), remove_json=False)
        self.assertIn('{"drop": true}', markdown)

    def test_output_markdown_can_keep_links(self):
        markdown, _ = output_markdown(
            FakePageDriver("https://example.com", GENERIC_HTML),
            remove_links=False,
        )
        self.assertIn("[a link](https://example.com/jobs)", markdown)

    def test_output_markdown_removes_embedded_links_but_keeps_standalone_links(self):
        html = """
        <html>
          <body>
            <p>Intro with <a href="/jobs">job link</a> in a sentence.</p>
            <p><a href="/standalone">Standalone link</a></p>
          </body>
        </html>
        """
        markdown, _ = output_markdown(FakePageDriver("https://example.com", html))
        self.assertIn("Intro with job link [[i1]] in a sentence.", markdown)
        self.assertIn("[Standalone link](https://example.com/standalone) [[i2]]", markdown)

    def test_output_markdown_removes_embedded_links_in_mixed_rows(self):
        markdown = (
            "2. || More actions [[i35]] || "
            "[Search domain fast.com](https://duckduckgo.com/?q=tester+site:fast.com&kp=1&t=h_) [[i36]] || "
            "Fast.com || [https://fast.com](https://fast.com/) [[i37]] || "
            "[Internet Speed Test | Fast.com](https://fast.com/) [[i38]] "
            "How fast is your download speed?"
        )
        cleaned = _remove_markdown_links(markdown)
        self.assertIn("Search domain fast.com", cleaned)
        self.assertIn("Fast.com", cleaned)
        self.assertIn("https://fast.com", cleaned)
        self.assertIn("Internet Speed Test | Fast.com", cleaned)
        self.assertNotIn("(https://duckduckgo.com/?q=tester+site:fast.com&kp=1&t=h_)", cleaned)
        self.assertNotIn("(https://fast.com/)", cleaned)

    def test_output_markdown_flattens_nested_link_wrappers(self):
        markdown = "- [All]([https://duckduckgo.com/?q=tester&kp=1&t=h_&ia=web](https://duckduckgo.com/?q=tester&kp=1&t=h_&ia=web)) [[i12]]"
        cleaned = _remove_markdown_links(markdown)
        self.assertEqual(cleaned, "- [All] [[i12]]")

    def test_output_markdown_keeps_image_and_bare_urls(self):
        html = """
        <html>
          <body>
            <div>
              <img alt="Logo" src="https://example.com/logo.png"> Company name
            </div>
            <div>https://www.linkedin.com/feed/?nis=true [[i5]]</div>
          </body>
        </html>
        """
        markdown, dev = output_markdown(FakePageDriver("https://example.com", html))
        self.assertIn("![Logo] (img1)", markdown)
        self.assertIn("https://www.linkedin.com/feed/?nis=true", markdown)
        self.assertIn("[[i5]]", markdown)
        self.assertEqual(dev["counts"]["images"], 1)
        self.assertEqual(dev["images"][0]["id"], "img1")

    def test_output_markdown_keeps_image_inside_link(self):
        html = """
        <html>
          <body>
            <a href="/?q=tester" title="Search domain fast.com">
              <div><img alt="fast.com icon" src="https://example.com/icon.png"></div>
            </a>
          </body>
        </html>
        """
        markdown, dev = output_markdown(FakePageDriver("https://duckduckgo.com", html))
        self.assertIn("![fast.com icon] (img1)", markdown)
        self.assertIn("Search domain fast.com [[i1]]", markdown)
        self.assertEqual(dev["counts"]["images"], 1)

    def test_output_markdown_shows_compact_control_state(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", STATEFUL_HTML))
        self.assertIn("Toggle view [pressed] [[i1]]", markdown)
        self.assertEqual(dev["interactables"][0]["state"].get("pressed"), True)
        self.assertEqual(dev["interactables"][0]["state_text"], " [pressed]")

    def test_output_markdown_shows_unchecked_filters(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", UNCHECKED_FILTER_HTML))
        self.assertIn("Easy Apply [unchecked] [[i1]]", markdown)
        self.assertEqual(dev["interactables"][0]["state"].get("checked"), False)

    def test_output_markdown_uses_snapshot_state_annotations(self):
        markdown, dev = output_markdown(FakePageDriver("https://example.com", SNAPSHOT_STATE_HTML))
        self.assertIn("Most relevant [checked] [[i1]]", markdown)
        self.assertTrue(dev["interactables"][0]["state"].get("checked"))

    def test_output_markdown_builds_rows_with_multiple_actions(self):
        markdown, dev = output_markdown(FakePageDriver("https://www.linkedin.com/jobs/search", JOB_CARD_GROUPED_HTML))
        self.assertIn("||", markdown)
        self.assertIn("McDermott International, Ltd", markdown)
        self.assertIn("Dismiss EFL Instructor, Military Setting in the UAE job", markdown)
        self.assertGreaterEqual(dev["counts"]["rows"], 1)
        row = dev["rows"][0]
        self.assertTrue(row["has_multiple_groups"])
        self.assertGreaterEqual(len(row["groups"]), 3)
        self.assertEqual([item["type"] for item in row["items"]], ["image", "link", "button"])
        self.assertEqual(row["groups"][0]["kind"], "media")
        self.assertEqual(row["groups"][1]["kind"], "primary")
        self.assertEqual(row["groups"][-1]["kind"], "action")
        self.assertTrue(row["groups"][-1]["text"].startswith("Dismiss"))
        self.assertTrue(row["items"][-1]["aria_label"].startswith("Dismiss EFL Instructor"))
        self.assertTrue(row["stable_id"])
        self.assertTrue(row["groups"][1]["stable_id"])

    def test_output_markdown_keeps_stable_ids_across_snapshots(self):
        _, dev_a = output_markdown(FakePageDriver("https://www.linkedin.com/jobs/search", JOB_CARD_GROUPED_HTML))
        _, dev_b = output_markdown(FakePageDriver("https://www.linkedin.com/jobs/search", JOB_CARD_GROUPED_HTML_SHIFTED))

        row_a = next(row for row in dev_a["rows"] if "Dismiss EFL Instructor" in row["text"])
        row_b = next(row for row in dev_b["rows"] if "Dismiss EFL Instructor" in row["text"])
        self.assertEqual(row_a["stable_id"], row_b["stable_id"])
        self.assertEqual(row_a["primary_interactable_stable_id"], row_b["primary_interactable_stable_id"])
        self.assertEqual(row_a["secondary_interactable_stable_ids"], row_b["secondary_interactable_stable_ids"])
        self.assertEqual(row_a["items"][1]["stable_id"], row_b["items"][1]["stable_id"])
        self.assertEqual(row_a["items"][-1]["stable_id"], row_b["items"][-1]["stable_id"])

    def test_output_markdown_separates_menuitem_targets(self):
        markdown, dev = output_markdown(FakePageDriver("https://www.google.com/search?q=tester", GOOGLE_MENU_HTML))
        menu_targets = [item for item in dev["interactables"] if item["type"] == "button" and "site" in item["text"].lower()]
        self.assertGreaterEqual(len(menu_targets), 4)
        self.assertIn("Only include results for this site", markdown)
        self.assertIn("Redo search without this site", markdown)
        self.assertIn("Block this site from all results", markdown)
        self.assertIn("Share feedback about this site", markdown)
        self.assertNotIn("Only include results for this site Redo search without this site Block this site from all results Share feedback about this site", markdown)

    def test_output_markdown_splits_duckduckgo_result_row(self):
        markdown, dev = output_markdown(FakePageDriver("https://duckduckgo.com/?q=tester&kp=1&t=h_&ia=web", DUCKDUCKGO_RESULT_HTML))
        row = dev["rows"][0]
        self.assertTrue(row["has_multiple_groups"])
        self.assertGreaterEqual(len(row["groups"]), 4)
        self.assertIn("Only include results for this site", markdown)
        self.assertIn("Redo search without this site", markdown)
        self.assertIn("Block this site from all results", markdown)
        self.assertIn("Share feedback about this site", markdown)
        self.assertIn("uTest - The Professional Network for Testers", markdown)

    def test_output_markdown_prefers_title_over_utility_button(self):
        markdown, dev = output_markdown(FakePageDriver("https://duckduckgo.com/?q=tester&kp=1&t=h_&ia=web", FAST_RESULT_HTML))
        row = dev["rows"][0]
        self.assertEqual(row["primary_interactable_id"], "i4")
        self.assertEqual(row["groups"][0]["text"], "More actions [[i1]]")
        self.assertEqual(dev["interactables"][0]["locator"]["value"], 'button[aria-controls="contextMenu-fast.com"]')
        self.assertIn("Internet Speed Test | Fast.com", markdown)
        self.assertIn("||", markdown)
        self.assertNotIn("Only include results for this site Redo search without this site Block this site from all results Share feedback about this site", markdown)

    def test_output_markdown_generic_page_stays_unchanged(self):
        markdown, _ = output_markdown(FakePageDriver("https://example.com", GENERIC_HTML))
        self.assertNotIn(" || ", markdown)


if __name__ == "__main__":
    unittest.main()

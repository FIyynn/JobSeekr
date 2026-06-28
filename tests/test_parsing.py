from __future__ import annotations

import unittest
from datetime import datetime, timezone

from browser.linkedin_jobs import parse_filters_state, parse_listings, parse_pages


FILTER_HTML = """
<div data-test-modal-container="" aria-hidden="false">
  <button aria-expanded="false" aria-label="Showing results of type: Jobs. Click to filter results by a different type." class="search-reusables__vertical-select-trigger" type="button">Jobs</button>
  <ul class="search-advanced-filter__navigation-container">
    <li><div role="button" aria-label="Show only results of type: People">People</div></li>
    <li><div role="button" aria-label="Show only results of type: Jobs selected">Jobs</div></li>
  </ul>
  <li class="search-reusables__secondary-filters-filter">
    <fieldset>
      <legend class="a11y-text">Sort by filter</legend>
      <h3>Sort by</h3>
      <input name="sort-by-filter-value" id="advanced-filter-sortBy-DD" type="radio" value="DD">
      <label for="advanced-filter-sortBy-DD"><span aria-hidden="true">Most recent</span></label>
      <input name="sort-by-filter-value" id="advanced-filter-sortBy-R" type="radio" value="R" checked>
      <label for="advanced-filter-sortBy-R"><span aria-hidden="true">Most relevant</span></label>
    </fieldset>
  </li>
  <li class="search-reusables__secondary-filters-filter">
    <fieldset>
      <legend class="a11y-text">Easy Apply filter</legend>
      <h3>Easy Apply</h3>
      <input name="easy-apply" id="easy-apply" type="checkbox" checked>
      <label for="easy-apply"><span aria-hidden="true">Easy Apply</span></label>
    </fieldset>
  </li>
  <li class="search-reusables__secondary-filters-filter">
    <fieldset>
      <legend class="a11y-text">Connections filter</legend>
      <h3>Connections</h3>
      <button aria-label="1st" aria-pressed="false">1st</button>
      <button aria-label="2nd" aria-pressed="true">2nd</button>
    </fieldset>
  </li>
</div>
"""

LISTING_HTML = """
<li data-occludable-job-id="4428303185" class="scaffold-layout__list-item">
  <div data-job-id="4428303185" class="job-card-container relative job-card-list">
    <div class="artdeco-entity-lockup__content">
      <div class="full-width artdeco-entity-lockup__title">
        <a href="/jobs/view/4428303185/" aria-label="Risk Manager Crypto Fund DeFi And Quant Strategies" class="job-card-container__link">
          <span aria-hidden="true"><strong>Risk Manager Crypto Fund DeFi And Quant Strategies</strong></span>
        </a>
      </div>
      <div class="artdeco-entity-lockup__subtitle">
        <span dir="ltr">TALENTMATE</span>
      </div>
      <div class="artdeco-entity-lockup__caption">
        <ul class="job-card-container__metadata-wrapper">
          <li><span dir="ltr">Dubai, Dubai, United Arab Emirates (Hybrid)</span></li>
        </ul>
      </div>
    </div>
    <ul class="job-card-list__footer-wrapper">
      <li class="job-card-container__footer-job-state t-bold">Viewed</li>
    </ul>
  </div>
</li>
"""

LISTING_HTML_EASY = """
<li data-occludable-job-id="4428303186" class="scaffold-layout__list-item">
  <div data-job-id="4428303186" class="job-card-container relative job-card-list">
    <div class="artdeco-entity-lockup__content">
      <div class="full-width artdeco-entity-lockup__title">
        <a href="/jobs/view/4428303186/" aria-label="Easy Apply Test" class="job-card-container__link">
          <span aria-hidden="true"><strong>Easy Apply Test</strong></span>
        </a>
      </div>
      <div class="artdeco-entity-lockup__subtitle">
        <span dir="ltr">Example Corp</span>
      </div>
      <div class="artdeco-entity-lockup__caption">
        <ul class="job-card-container__metadata-wrapper">
          <li><span dir="ltr">Remote</span></li>
        </ul>
      </div>
    </div>
    <ul class="job-card-list__footer-wrapper">
      <li class="job-card-container__footer-job-state t-bold">Easy Apply</li>
    </ul>
  </div>
</li>
"""

LISTING_HTML_RELATIVE = """
<li data-occludable-job-id="4428303187" class="scaffold-layout__list-item">
  <div data-job-id="4428303187" class="job-card-container relative job-card-list">
    <div class="artdeco-entity-lockup__content">
      <div class="full-width artdeco-entity-lockup__title">
        <a href="/jobs/view/4428303187/" aria-label="Relative Time Test" class="job-card-container__link">
          <span aria-hidden="true"><strong>Relative Time Test</strong></span>
        </a>
      </div>
      <div class="artdeco-entity-lockup__subtitle">
        <span dir="ltr">Example Corp</span>
      </div>
      <div class="artdeco-entity-lockup__caption">
        <ul class="job-card-container__metadata-wrapper">
          <li><span dir="ltr">Remote</span></li>
        </ul>
      </div>
    </div>
    <ul class="job-card-list__footer-wrapper">
      <li class="job-card-container__footer-item">1 day ago</li>
    </ul>
  </div>
</li>
"""

PAGES_HTML = """
<button class="jobs-search-pagination__indicator-button" aria-label="Page 1">1</button>
<button class="jobs-search-pagination__indicator-button" aria-label="Page 2" aria-current="page">2</button>
<button class="jobs-search-pagination__indicator-button" aria-label="Page 3">3</button>
"""


class ParsingTests(unittest.TestCase):
    def test_filters_state(self):
        parsed = parse_filters_state(FILTER_HTML)
        self.assertEqual(parsed["filter_by"]["selected"], "Jobs")
        self.assertEqual(len(parsed["filters"]), 3)
        self.assertEqual(parsed["filters"][0]["type"], "radio")
        self.assertEqual(parsed["filters"][1]["type"], "switch")
        self.assertEqual(parsed["filters"][2]["type"], "multiselect_pill")

    def test_listings(self):
        parsed = parse_listings(LISTING_HTML, now=datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(parsed["listings"][0]["title"], "Risk Manager Crypto Fund DeFi And Quant Strategies")
        self.assertEqual(parsed["listings"][0]["company"], "TALENTMATE")
        self.assertEqual(parsed["listings"][0]["location"], "Dubai, Dubai, United Arab Emirates (Hybrid)")
        self.assertTrue(parsed["listings"][0]["link"].endswith("/jobs/view/4428303185/"))
        self.assertEqual(parsed["listings"][0]["listed_on"], None)

    def test_listings_easy_apply_footer_is_not_time(self):
        parsed = parse_listings(LISTING_HTML_EASY, now=datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(parsed["listings"][0]["listed_on"])

    def test_listings_relative_time_to_listed_on(self):
        parsed = parse_listings(LISTING_HTML_RELATIVE, now=datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(parsed["listings"][0]["listed_on"], "2026-06-13T12:00:00+00:00")

    def test_pages(self):
        parsed = parse_pages(PAGES_HTML)
        self.assertEqual(parsed["current_page"], "2")
        self.assertEqual(parsed["pages"][1]["current"], True)


if __name__ == "__main__":
    unittest.main()


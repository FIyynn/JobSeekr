from __future__ import annotations

from datetime import datetime, timezone
import unittest

from browser.linkedin_jobs import JOB_DETAIL_WRAPPER_SELECTOR, LISTING_SELECTOR
from parsers.listing_detail import parse_listing_detail
from stages.listing_detail import _resolve_indexes, extract_listing_detail


DETAIL_HTML = """
<div class="jobs-search__job-details--wrapper">
  <div aria-label="Business Development Representative (EMEA)" class="jobs-search__job-details--container">
    <div class="jobs-details__main-content jobs-details__main-content--single-pane full-width">
      <a href="/jobs/view/4425564732/?alternateChannel=search&amp;refId=bUOJ%2BEnXcnfNJnx%2FNbf2iQ%3D%3D&amp;trackingId=S2UmVn9iJgXPgR6ResMBJA%3D%3D&amp;trk=d_flagship3_search_srp_jobs">
        <h1 class="t-24 t-bold inline">Business Development Representative (EMEA)</h1>
      </a>

      <div class="job-details-jobs-unified-top-card__company-name">
        <a href="https://www.linkedin.com/company/respondio/life">respond.io</a>
      </div>

      <img src="https://media.licdn.com/dms/image/v2/D560BAQFpTLCXYO3zdQ/company-logo_100_100/company-logo_100_100/0/1722393406714/respondio_logo?e=1782950400&amp;v=beta&amp;t=sNLeOt6XBWresqtAGClv2-lzKKJS042HCZn1BtNzIeY" alt="respond.io company logo">

      <div class="job-details-jobs-unified-top-card__sticky-header">
        <div class="job-details-jobs-unified-top-card__title-container">
          <h2 class="t-16 t-black t-bold truncate">Business Development Representative (EMEA)</h2>
        </div>
        <div class="t-14 truncate">respond.io · EMEA (Remote)</div>
      </div>

      <div class="job-details-jobs-unified-top-card__primary-description-container">
        <div class="t-black--light mt2 job-details-jobs-unified-top-card__tertiary-description-container">
          <span>EMEA · 5 days ago · Over 100 people clicked apply</span>
          <p><span>Promoted by hirer</span> · <span>Responses managed off LinkedIn</span></p>
        </div>
      </div>

      <div class="job-details-fit-level-preferences">
        <button type="button"><span>Remote</span></button>
        <button type="button"><span>Full-time</span></button>
      </div>

      <div class="job-details-fit-level-card__guide-entry-points--free"></div>

      <div class="jobs-description__container">
        <div class="mt4">
          <p>*Resume MUST be in English</p>
          <p>Location: EMEA</p>
          <p>Role: Business Development Representative (EMEA)</p>
          <p>Department: Sales Department</p>
          <p>About Respond.io</p>
          <p>Founded in Hong Kong in early 2017, respond.io is an AI-powered business messaging platform.</p>
          <p>Role Description</p>
          <p>We are seeking a highly motivated Business Development Representative to join our team and accelerate their career in sales.</p>
        </div>
      </div>

      <div class="jobs-apply-button--top-card">
        <button aria-label="Apply to Business Development Representative (EMEA) on company website" type="button">
          <span class="artdeco-button__text">Apply</span>
        </button>
      </div>

      <button class="jobs-save-button">
        <span class="jobs-save-button__text">Save</span>
      </button>

      <div class="job-details-module">
        <div class="job-details-people-who-can-help__section--two-pane artdeco-card ph5 pv4">
          <h2 class="text-heading-medium mb2">Meet the hiring team</h2>
          <div class="display-flex align-items-center mt4">
            <a href="https://www.linkedin.com/in/aimanbinsaufi" aria-label="View Aiman Saufi's verified profile graphic">
              <img alt="Aiman Saufi">
            </a>
            <div class="hirer-card__hirer-information">
              <a href="https://www.linkedin.com/in/aimanbinsaufi">
                <span class="jobs-poster__name">Aiman Saufi</span>
              </a>
              <div class="hirer-card__connection-degree-container">
                <span class="hirer-card__connection-degree">3rd+</span>
              </div>
              <div class="linked-area flex-1">
                <div class="text-body-small t-black">Talent Management, Talent Acquisition, People Experience @ Respond.io | B2B SaaS | Fintech | xFave</div>
                <div class="t-12 hirer-card__job-poster">Job poster</div>
              </div>
            </div>
            <button>Message</button>
          </div>
        </div>
      </div>

      <section class="jobs-company jobs-box--fadein mb4">
        <div class="jobs-company__box">
          <h2 class="text-heading-large">About the company</h2>
          <div class="display-flex align-items-center mt5">
            <a href="/company/respondio/life/" data-view-name="job-details-about-company-logo-link">
              <img title="respond.io" src="https://media.licdn.com/dms/image/v2/D560BAQFpTLCXYO3zdQ/company-logo_100_100/company-logo_100_100/0/1722393406714/respondio_logo?e=1782950400&amp;v=beta&amp;t=sNLeOt6XBWresqtAGClv2-lzKKJS042HCZn1BtNzIeY" alt="respond.io company logo">
            </a>
            <div class="artdeco-entity-lockup__content">
              <div class="artdeco-entity-lockup__title">
                <a href="/company/respondio/life/" data-view-name="job-details-about-company-name-link">respond.io</a>
              </div>
              <div class="artdeco-entity-lockup__subtitle">97,868 followers</div>
            </div>
          </div>
          <div class="t-14 mt5">
            IT Services and IT Consulting
            <span class="jobs-company__inline-information">51-200 employees</span>
            <span class="jobs-company__inline-information">196 on LinkedIn</span>
          </div>
          <p class="jobs-company__company-description text-body-small-open">
            <div class="inline-show-more-text">
              Respond.io is a customer conversation management platform that helps businesses maximize leads and enable sales over chat, voice calls and email.
            </div>
          </p>
        </div>
      </section>
    </div>
  </div>
</div>
"""


class FakeCard:
    def __init__(self, driver, index: int, listing: dict):
        self.driver = driver
        self.index = index
        self.listing = listing

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        if name in {"data-occludable-job-id", "data-job-id"}:
            return str(self.listing.get("job_id", ""))
        return ""

    def click(self):
        self.driver.selected_index = self.index


class FakePanel:
    def __init__(self, html: str):
        self._html = html

    @property
    def text(self):
        return "Business Development Representative (EMEA)"

    def get_attribute(self, name):
        if name == "outerHTML":
            return self._html
        return ""


class FakeDriver:
    def __init__(self, listings: list[dict[str, str]], detail_html: str):
        self._cards = [FakeCard(self, idx, listing) for idx, listing in enumerate(listings)]
        self._detail_html = detail_html
        self.selected_index = 0

    def find_elements(self, by, selector):
        if selector == LISTING_SELECTOR:
            return self._cards
        if selector == JOB_DETAIL_WRAPPER_SELECTOR:
            return [FakePanel(self._detail_html)]
        return []

    def find_element(self, by, selector):
        if selector == JOB_DETAIL_WRAPPER_SELECTOR:
            return FakePanel(self._detail_html)
        raise Exception("not found")

    def execute_script(self, script, element):
        return None


class ListingDetailTests(unittest.TestCase):
    def test_resolve_indexes(self):
        self.assertEqual(_resolve_indexes(3, 0), [0])
        self.assertEqual(_resolve_indexes(3, 2), [2])
        self.assertEqual(_resolve_indexes(3, 99), [2])
        self.assertEqual(_resolve_indexes(3, -1), [0, 1, 2])

    def test_parse_listing_detail(self):
        parsed = parse_listing_detail(
            DETAIL_HTML,
            now=datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(parsed["source"], "linkedin")
        self.assertEqual(parsed["job_id"], "4425564732")
        self.assertIn("/jobs/view/4425564732/", parsed["listing_url"])
        self.assertEqual(parsed["title"], "Business Development Representative (EMEA)")
        self.assertEqual(parsed["company"], "respond.io")
        self.assertEqual(parsed["company_url"], "/company/respondio/life/")
        self.assertIn("company-logo_100_100", parsed["company_logo_url"])
        self.assertEqual(parsed["location"], "EMEA")
        self.assertEqual(parsed["posted_at"], "5 days ago")
        self.assertEqual(parsed["listed_on"], "2026-06-09T12:00:00+00:00")
        self.assertEqual(parsed["apply_activity"], "Over 100 people clicked apply")
        self.assertEqual(parsed["promotion_status"], "Promoted by hirer")
        self.assertEqual(parsed["application_management"], "Responses managed off LinkedIn")
        self.assertEqual(parsed["response_insights"], "")
        self.assertEqual(parsed["listing_preferences"], ["Remote", "Full-time"])
        self.assertTrue(parsed["missing_required_qualifications"])
        self.assertTrue(parsed["missing required qualifications?"])
        self.assertEqual(parsed["apply_button_xpath"], ".//button[contains(@class, 'jobs-apply-button')]")
        self.assertEqual(parsed["save_button_xpath"], ".//button[contains(@class, 'jobs-save-button')]")
        self.assertEqual(len(parsed["hiring_team"]), 1)
        self.assertEqual(parsed["hiring_team"][0]["name"], "Aiman Saufi")
        self.assertIn("aimanbinsaufi", parsed["hiring_team"][0]["profile_url"])
        self.assertEqual(parsed["hiring_team"][0]["connection_degree"], "3rd+")
        self.assertEqual(parsed["hiring_team"][0]["role_label"], "Job poster")
        self.assertEqual(parsed["company_profile"]["name"], "respond.io")
        self.assertEqual(parsed["company_profile"]["url"], "/company/respondio/life/")
        self.assertEqual(parsed["company_profile"]["followers"], "97,868 followers")
        self.assertEqual(parsed["company_profile"]["industry"], "IT Services and IT Consulting")
        self.assertEqual(parsed["company_profile"]["size"], "51-200 employees")
        self.assertEqual(parsed["company_profile"]["linkedin_employee_count"], "196 on LinkedIn")
        self.assertIn("customer conversation management platform", parsed["company_profile"]["description"])
        self.assertIn("Resume MUST be in English", parsed["job_description"]["raw_text"])
        self.assertIn("Role Description", parsed["job_description"]["raw_text"])

    def test_extract_listing_detail_smoke(self):
        driver = FakeDriver(
            [
                {"job_id": "1", "title": "First", "link": "https://www.linkedin.com/jobs/view/1/", "easy_apply": False},
                {"job_id": "2", "title": "Second", "link": "https://www.linkedin.com/jobs/view/2/", "easy_apply": True},
                {"job_id": "3", "title": "Third", "link": "https://www.linkedin.com/jobs/view/3/", "easy_apply": False},
            ],
            DETAIL_HTML,
        )
        result = extract_listing_detail(
            driver,
            {"listings": [c.listing for c in driver._cards]},
            index=99,
            delay_seconds=0,
            delay_jitter=0,
            verbose=False,
        )
        self.assertEqual(result["dev"]["source"], "linkedin")
        self.assertEqual(result["dev"]["index"], 99)
        self.assertEqual(result["dev"]["job_id"], "4425564732")
        self.assertEqual(result["dev"]["interactables"][0]["listing_link"], "https://www.linkedin.com/jobs/view/3/")
        self.assertEqual(result["dev"]["interactables"][0]["company_url"], "/company/respondio/life/")
        self.assertIn("company_logo_url", result["dev"]["interactables"][0])
        self.assertIn("apply_button_xpath", result["dev"]["interactables"][0])
        self.assertEqual(result["ai"][0]["listing"]["title"], "Third")
        self.assertIn("company_profile", result["ai"][0])
        self.assertEqual(result["ai"][0]["company_profile"]["name"], "respond.io")
        self.assertNotIn("company_profile", result["ai"][0]["detail"])
        self.assertIsInstance(result["ai"][0]["detail"]["job_description"], str)
        self.assertIn("Resume MUST be in English", result["ai"][0]["detail"]["job_description"])
        self.assertNotIn("raw_text", result["ai"][0]["detail"])

    def test_extract_listing_detail_all(self):
        driver = FakeDriver(
            [
                {"job_id": "1", "title": "First", "link": "https://www.linkedin.com/jobs/view/1/", "easy_apply": False},
                {"job_id": "2", "title": "Second", "link": "https://www.linkedin.com/jobs/view/2/", "easy_apply": True},
            ],
            DETAIL_HTML,
        )
        result = extract_listing_detail(
            driver,
            {"listings": [c.listing for c in driver._cards]},
            index=-1,
            delay_seconds=0,
            delay_jitter=0,
            verbose=False,
        )
        self.assertEqual(result["dev"]["index"], -1)
        self.assertEqual(len(result["dev"]["interactables"]), 2)
        self.assertEqual(len(result["ai"]), 2)


if __name__ == "__main__":
    unittest.main()

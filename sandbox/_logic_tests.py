"""
Offline (no-browser) correctness tests for JobHuntrr routing & filtering logic:
- geographic prefilter
- ATS platform detection
- QA label -> value mapping (Tier 1)
Writes results to stdout; used to judge core apply logic without a live browser.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_settings import bootstrap_settings
bootstrap_settings()

PASS, FAIL = "PASS", "FAIL"
results = []

def check(name, got, expected):
    ok = got == expected
    results.append((ok, name, got, expected))
    print(f"[{PASS if ok else FAIL}] {name}: got={got!r} expected={expected!r}")

# ── 1. Geo filter ────────────────────────────────────────────────────────────
from agents.job_fit import is_outside_target_geo
print("\n== Geographic prefilter ==")
geo_cases = [
    ({"company": "Lazard", "location": "New York, United States"}, True),
    ({"company": "SolomonEdwards", "location": "Bala-Cynwyd, PA"}, True),
    ({"company": "Atrium", "location": "Little Falls, NJ"}, True),
    ({"company": "Tether", "location": "Singapore"}, True),
    ({"company": "Lunate", "location": "Abu Dhabi Emirate, United Arab Emirates"}, False),
    ({"company": "Fionics", "location": "Dubai, United Arab Emirates"}, False),
    ({"company": "Spotify", "location": ""}, False),          # unknown -> keep
    ({"company": "X", "location": "Remote"}, False),           # remote -> keep
    ({"company": "Brevan Howard", "location": "London, United Kingdom"}, False),  # elite override
]
for job, expected_outside in geo_cases:
    outside, reason = is_outside_target_geo(job)
    check(f"geo {job['company']}/{job['location'] or '(blank)'}", outside, expected_outside)

from agents.job_fit import is_ai_agent_only_job
normal_agent_context = {
    "title": "Forward Deployed Engineer",
    "company": "Google",
    "location": "Dubai, UAE",
    "description": (
        "Build AI applications and autonomous agent workflows for enterprise "
        "customers. Human applicants are welcome."
    ),
}
got, _ = is_ai_agent_only_job(normal_agent_context)
check("generic autonomous-agent wording is not bot-only", got, False)

bot_only_job = {
    "title": "AI Agent Challenge",
    "company": "G42",
    "location": "Abu Dhabi, UAE",
    "description": (
        "Applications from individual candidates will not be considered. "
        "Submissions must be made by a developer representing a specific AI agent."
    ),
}
got, _ = is_ai_agent_only_job(bot_only_job)
check("explicit AI-agent-only posting is skipped", got, True)

# ── 2. Platform detection ─────────────────────────────────────────────────────
from agents.form_filler import _detect_platform
print("\n== ATS platform detection (URL only) ==")
class _FakePage:
    def content(self):
        raise Exception("no DOM in offline test")
fp = _FakePage()
plat_cases = [
    ("https://www.linkedin.com/jobs/view/4401423656", "linkedin"),
    ("https://mubadala.wd3.myworkdayjobs.com/en-US/Mubadala/job/Abu-Dhabi/Analyst_R123", "workday"),
    ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse"),
    ("https://jobs.lever.co/acme/abcdef", "lever"),
    ("https://jobs.ashbyhq.com/acme/uuid", "ashby"),
    ("https://apply.workable.com/acme/j/ABCDEF/", "workable"),
    ("https://naffco.teamtailor.com/jobs/7837070-data-analysis-dashboard-specialist", "teamtailor"),
    ("https://jobs.icims.com/acme/jobs/123/analyst/job", "icims"),
    ("https://jobs.smartrecruiters.com/Acme/123-analyst", "smartrecruiters"),
    ("https://acme.taleo.net/careersection/ext/jobdetail.ftl?job=123", "taleo"),
    ("https://ehjd.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/fabCareers/job/106", "oracle_recruiting"),
    ("https://acme.bamboohr.com/careers/123", "ai_driven"),
    ("https://careers.somegov.ae/apply/role", "ai_driven"),
]
for url, expected in plat_cases:
    check(f"detect {url[:45]}", _detect_platform(url, fp), expected)

# ── 3. QA label -> value (Tier 1, deterministic, no LLM) ──────────────────────
from agents.form_filler import _qa_value_for_label, _ensure_qa_contact
from config.config import APPLICATION_QA
qa = dict(APPLICATION_QA)
_ensure_qa_contact(qa, force_reload=True)
print("\n== QA label mapping (Tier 1 deterministic) ==")
def nonempty(label):
    v = _qa_value_for_label(label, qa)
    return bool(v)
label_cases = [
    "First Name", "Last Name", "Email Address", "Phone Number",
    "LinkedIn Profile URL", "Are you legally authorized to work?",
    "Do you require visa sponsorship?", "What is your nationality?",
    "Highest level of education", "University / School",
]
for lbl in label_cases:
    v = _qa_value_for_label(lbl, qa)
    print(f"   {lbl:45} -> {v!r}")

# University-name rule must NOT hijack grade/status/level sub-questions
print("\n== University label disambiguation ==")
uni = qa.get("university")
check("University name -> institution", _qa_value_for_label("University name", qa) == uni, True)
check("University grade != institution", _qa_value_for_label("University grade", qa) != uni, True)
check("current university status != institution", _qa_value_for_label("What is your current university status?", qa) != uni, True)
check("High School Mathematics Grade != institution", _qa_value_for_label("High School (or equivalent) Mathematics Grade", qa) != uni, True)
check("Highest level of education", bool(_qa_value_for_label("Highest level of education", qa)), True)
check("Where are you located?", _qa_value_for_label("Where are you located?*", qa), qa.get("location") or "Abu Dhabi, UAE")
check("demographic text defaults to decline", _qa_value_for_label("Gender identity", qa), "Decline to self-identify")

from agents.form_filler import _pick_option_from_label
check(
    "race select chooses explicit decline",
    _pick_option_from_label("Race / ethnicity", ["Asian", "Prefer not to answer"], qa),
    "Prefer not to answer",
)
check(
    "disability select chooses explicit decline",
    _pick_option_from_label("Disability status", ["Yes", "No", "Decline to self-identify"], qa),
    "Decline to self-identify",
)
check(
    "sensitive select is not inferred without decline option",
    _pick_option_from_label("Veteran status", ["Protected veteran", "Not a protected veteran"], qa),
    None,
)

# Behavioral prompts should use the full essay path rather than the weak short-answer path.
from agents.application_qa import is_essay_question, _essay_is_grounded
check("hardest technical problem -> essay", is_essay_question("What is one of the hardest technical problems you have worked on?"), True)
polygon_anchor = {
    "title": "CEO & Co-Founder, Polygon Technical Infrastructures",
    "summary": "Cybersecurity/software services founder: full-stack delivery, DevOps, client-facing technical work, security-first execution under real constraints.",
}
check("reject invented essay metric", _essay_is_grounded("I reduced deployment time by 25%.", polygon_anchor), False)
check("reject invented essay tool", _essay_is_grounded("I implemented Terraform policy gates.", polygon_anchor), False)

# ── 4. Lenient JSON parser (AI-driven mapper robustness) ──────────────────────
from agents.form_filler import _loads_lenient_json
print("\n== Lenient JSON parsing (AI-driven mapper) ==")
json_cases = [
    ('{"#first_name": "Rashed", "#email": "a@b.com"}', {"#first_name": "Rashed", "#email": "a@b.com"}),
    ('```json\n{"#a": "1", "#b": "2",}\n```', {"#a": "1", "#b": "2"}),          # fence + trailing comma
    ('<think>reasoning</think>\n{"#x": "y"}', {"#x": "y"}),                       # qwen3 think block
    ('prose before {"#p": "q"} prose after', {"#p": "q"}),                        # surrounding prose
    ("{\u201c#sn\u201d: \u201cval\u201d}", {"#sn": "val"}),                       # smart quotes
]
for raw, expected in json_cases:
    got = _loads_lenient_json(raw)
    check(f"json {raw[:28]!r}", got, expected)
check("json garbage -> None", _loads_lenient_json("no json here"), None)

# ── 5. Signup field mapping (profile settings -> auth forms) ─────────────────
from agents.account_signup import signup_value_for_field, load_signup_identity, auto_signup_enabled
from agents import account_signup
print("\n== Signup field mapping (profile settings) ==")
ident = load_signup_identity(qa)
check("auto_signup enabled when email+password set", auto_signup_enabled(ident), True)
check("email field", signup_value_for_field("Email Address", identity=ident), ident.get("email"))
check("password field populated", bool(signup_value_for_field("Password", input_type="password", identity=ident)), True)
check("confirm password matches", signup_value_for_field("Confirm password", input_type="password", identity=ident) == ident.get("password"), True)
check("first name", signup_value_for_field("First Name", identity=ident), ident.get("first_name"))
check("university grade not email", signup_value_for_field("University grade", identity=ident) != ident.get("email"), True)
check("auth-wall entry point exists", callable(account_signup.clear_auth_wall), True)

class _FakeCaptchaFrame:
    url = "https://geo.captcha-delivery.com/captcha/"
    def content(self):
        return "<html><body>Access is temporarily restricted</body></html>"
class _FakeCaptchaPage:
    url = "https://jobs.smartrecruiters.com/oneclick-ui/"
    frames = [_FakeCaptchaFrame()]
check("embedded CAPTCHA frame detected", account_signup._detect_captcha(_FakeCaptchaPage()), True)
from agents import form_filler
check("form filler CAPTCHA frame detected", form_filler._detect_captcha_challenge(_FakeCaptchaPage()), True)
captcha_job = {}
check("embedded CAPTCHA defers auth wall", account_signup.clear_auth_wall(_FakeCaptchaPage(), captcha_job, {}), False)
check("embedded CAPTCHA status is explicit", captcha_job.get("submission_status"), "captcha_required")

class _FakeBlockedLocator:
    def inner_text(self, timeout=0):
        return "403 Forbidden"
class _FakeBlockedFrame:
    def locator(self, selector):
        return _FakeBlockedLocator()
class _FakeBlockedPage:
    url = "https://careers.icims.com/jobs/6485/user-technician/job"
    frames = [_FakeBlockedFrame()]
check("hard portal access block detected", account_signup._detect_access_blocked(_FakeBlockedPage()), True)
blocked_job = {}
check("hard portal block defers auth wall", account_signup.clear_auth_wall(_FakeBlockedPage(), blocked_job, {}), False)
check("hard portal block status is explicit", blocked_job.get("submission_status"), "portal_blocked")

# -- 6. AFK unattended apply policy --
from agents import apply_prompts
from agents import form_filler
print("\n== AFK unattended apply policy ==")
handler_calls = []
apply_prompts.register_prompt_handler(lambda *args: handler_calls.append(args) or "unexpected")
check("unattended apply enabled", apply_prompts.UNATTENDED_APPLY, True)
check("interactive apply disabled", apply_prompts.INTERACTIVE_APPLY, False)
check("text prompt uses default", apply_prompts.prompt_text("Q", "body", "default"), "default")
check("manual action deferred", apply_prompts.prompt_user_action("captcha"), False)
check("prompt handler never called", handler_calls, [])

class _FakeBody:
    def __init__(self, text):
        self.text = text
    def inner_text(self, timeout=0):
        return self.text

class _FakeConfirmationPage:
    def __init__(self, text, url="https://jobs.example.com/thanks"):
        self.text = text
        self.url = url
    def locator(self, selector):
        return _FakeBody(self.text)

confirmed_job = {}
check(
    "thank-you page confirms submission",
    form_filler._mark_submission_confirmed(
        _FakeConfirmationPage("Thank you for applying. We received your application."),
        confirmed_job,
        "greenhouse",
    ),
    True,
)
check("confirmed submission marked applied", confirmed_job.get("applied"), True)
check("confirmed submission has evidence", bool(confirmed_job.get("confirmation_text")), True)

pending_job = {}
pending_page = _FakeConfirmationPage("Review your answers before submitting.")
check(
    "review page does not confirm submission",
    form_filler._mark_submission_confirmed(pending_page, pending_job, "greenhouse"),
    False,
)
form_filler._mark_submission_unconfirmed(pending_page, pending_job, "greenhouse")
check("click-only outcome is not applied", pending_job.get("applied"), False)
check("click-only outcome is quarantined", pending_job.get("submission_status"), "confirmation_pending")

class _FakeLinkedInPage:
    url = "https://www.linkedin.com/login"
    def goto(self, *args, **kwargs):
        return None

class _FakeLinkedInContext:
    pages = [_FakeLinkedInPage()]

old_pause = form_filler._pause
form_filler._pause = lambda *args, **kwargs: None
try:
    check(
        "LinkedIn missing credentials skips immediately",
        form_filler._ensure_linkedin_login(_FakeLinkedInContext(), "", ""),
        False,
    )
    expired_job = {}
    check(
        "LinkedIn expired session skips immediately",
        form_filler._linkedin_apply_job(
            _FakeLinkedInContext(), _FakeLinkedInPage(), expired_job, {}, "", "", "",
            True, validate_fit=True,
        ),
        False,
    )
    check("LinkedIn expired session deferred", expired_job.get("decision"), "manual_review")
    ok_fit, fit_msg = form_filler._validate_job_before_apply(
        {
            "title": "Analyst",
            "company": "ACME",
            "location": "Dubai, UAE",
            "description": "Entry-level analyst role in Dubai.",
        },
        None,
        "",
        "",
        "",
        validate_fit=False,
    )
    check("validate fit path has no missing import", (ok_fit, fit_msg), (True, ""))
finally:
    form_filler._pause = old_pause

# ── Summary ───────────────────────────────────────────────────────────────────
# -- 7. Search results appear in Jobs before scoring finishes --
from pathlib import Path
from tempfile import TemporaryDirectory
from agents import job_logger
from storage.job_store import JobStore
print("\n== Search result visibility ==")
with TemporaryDirectory() as tmp:
    search_store = JobStore(Path(tmp) / "jobs.db")
    original_get_store = job_logger.get_store
    job_logger.get_store = lambda: search_store
    try:
        raw_job = {
            "company": "Example Capital",
            "title": "Analyst",
            "location": "Abu Dhabi, UAE",
            "job_url": "https://example.com/jobs/analyst",
            "score": None,
            "decision": None,
        }
        preview_result = job_logger.persist_search_results([raw_job])
        preview_job = search_store.list_jobs()[0]
        check("raw search result logged", preview_result["logged"], 1)
        check("raw search result visible as discovered", preview_job["decision"], "discovered")
        check("discovery payload not mutated", raw_job["decision"], None)

        scored_job = dict(raw_job, score=88, decision="auto_apply")
        final_result = search_store.log_jobs_batch([scored_job], update_existing=True)
        final_jobs = search_store.list_jobs()
        check("scored result updates initial row", final_result["logged"], 1)
        check("search result remains one row", len(final_jobs), 1)
        check("search result receives score", final_jobs[0]["score"], 88)
        check("search result receives decision", final_jobs[0]["decision"], "auto_apply")

        search_store.mark_applied(
            final_jobs[0]["id"],
            "Confirmed submitted via test",
            submission_status="confirmed",
            confirmation_url="https://example.com/jobs/analyst/thanks",
            confirmation_text="thank you for applying",
        )
        rediscovered = dict(scored_job, score=91, decision="auto_apply", applied=False)
        search_store.log_jobs_batch([rediscovered], update_existing=True)
        terminal_job = search_store.list_jobs()[0]
        check("rediscovery preserves confirmed applied state", terminal_job["applied"], True)
        check("rediscovery preserves terminal decision", terminal_job["decision"], "applied")
        check("rediscovery preserves confirmation URL", terminal_job["confirmation_url"], "https://example.com/jobs/analyst/thanks")
    finally:
        job_logger.get_store = original_get_store

# -- 8. Indexed web signals: LinkedIn hiring posts + direct openings --
from agents.web_signal_discovery import (
    _clean_result_url,
    _looks_like_direct_opening,
    _looks_like_employee_post,
    _is_external_google_result_url,
    _matches_role_term,
    discover_google_jobs,
    discover_web_signals,
)
from agents.scorer import _hold_employee_post_for_review
import pandas as pd
print("\n== Indexed web-signal discovery ==")
check(
    "recognize LinkedIn employee post",
    _looks_like_employee_post("https://www.linkedin.com/posts/recruiter_hiring-activity-123"),
    True,
)
check(
    "recognize Greenhouse direct opening",
    _looks_like_direct_opening("https://boards.greenhouse.io/acme/jobs/12345"),
    True,
)
check(
    "reject Greenhouse listing page",
    _looks_like_direct_opening("https://boards.greenhouse.io/acme"),
    False,
)
check(
    "recognize iCIMS direct opening",
    _looks_like_direct_opening("https://jobs.icims.com/acme/jobs/123/analyst/job"),
    True,
)
check(
    "recognize Taleo direct opening",
    _looks_like_direct_opening("https://acme.taleo.net/careersection/ext/jobdetail.ftl?job=123"),
    True,
)
check(
    "reject Workday internal posting",
    _looks_like_direct_opening("https://acme.wd1.myworkdayjobs.com/internalposting/job/Dubai/Analyst_R123"),
    False,
)
check(
    "unwrap DuckDuckGo result URL",
    _clean_result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Facme%2Fabc"),
    "https://jobs.lever.co/acme/abc",
)
check("accept matching hiring-post text", _matches_role_term("Hiring investment analyst in Dubai", "investment analyst"), True)
check("reject unrelated hiring-post text", _matches_role_term("Hiring SEO specialist in Dubai", "investment analyst"), False)
check("accept external Google result URL", _is_external_google_result_url("https://jobs.lever.co/acme/abc"), True)
check("reject Google navigation URL", _is_external_google_result_url("https://www.google.com/search?q=jobs.lever.co"), False)

def _fake_indexed_search(query, days_fresh, limit):
    if "site:linkedin.com/posts" in query:
        return [{
            "title": "We are hiring an Investment Analyst | LinkedIn",
            "url": "https://www.linkedin.com/posts/recruiter_hiring-investment-analyst-activity-123",
            "snippet": "Join our Dubai team",
            "provider": "fake",
        }]
    if "site:myworkdayjobs.com" in query:
        return [{
            "title": "Investment Analyst - Acme",
            "url": "https://boards.greenhouse.io/acme/jobs/12345",
            "snippet": "Dubai investment role",
            "provider": "fake",
        }]
    return []

signals = discover_web_signals(
    [{"term": "investment analyst", "location": "Dubai"}],
    max_results=10,
    search_fn=_fake_indexed_search,
    linkedin_posts_fn=lambda *args, **kwargs: [],
    google_jobs_fn=lambda *args, **kwargs: [],
)
check("indexed collector sources", [j["source"] for j in signals], ["employee_post", "web_indexed"])
check("employee post has no apply URL", signals[0]["job_url_direct"], "")
check("indexed ATS opening has apply URL", signals[1]["job_url_direct"], signals[1]["job_url"])

employee_post = {
    "source": "employee_post",
    "job_url_direct": "",
    "decision": "auto_apply",
    "fit_reason": "Strong fit.",
}
_hold_employee_post_for_review(employee_post)
check("employee post held for review", employee_post["decision"], "manual_review")

google_jobs = discover_google_jobs(
    [{"term": "investment analyst", "location": "Dubai"}],
    scrape_fn=lambda **kwargs: pd.DataFrame([{
        "title": "Investment Analyst",
        "company": "Acme",
        "location": "Dubai, UAE",
        "description": "Investment role",
        "job_url": "https://www.google.com/search?q=ignored",
        "job_url_direct": "https://jobs.lever.co/acme/abcdef",
        "date_posted": "",
    }]),
)
check("Google Jobs keeps direct ATS URL", google_jobs[0]["job_url"], "https://jobs.lever.co/acme/abcdef")
check("Google Jobs opening enters web-indexed source", google_jobs[0]["source"], "web_indexed")

from agents import discovery as discovery_agent
from agents import web_signal_discovery as web_signal_agent
old_scrape_jobs = discovery_agent.scrape_jobs
old_jobspy_available = discovery_agent.JOBSPY_AVAILABLE
old_discover_web_signals = web_signal_agent.discover_web_signals
old_web_signal_search = os.environ.get("WEB_SIGNAL_SEARCH")
old_web_signal_max = os.environ.get("WEB_SIGNAL_MAX_RESULTS")
try:
    discovery_agent.JOBSPY_AVAILABLE = True
    discovery_agent.scrape_jobs = lambda **kwargs: pd.DataFrame()
    web_signal_agent.discover_web_signals = lambda *args, **kwargs: [{
        "title": "Investment Analyst",
        "company": "Acme",
        "location": "Dubai, UAE",
        "description": "Dubai investment role",
        "job_url": "https://boards.greenhouse.io/acme/jobs/67890",
        "job_url_direct": "https://boards.greenhouse.io/acme/jobs/67890",
        "date_posted": "",
        "source": "web_indexed",
        "apply_method": "ATS",
        "score": None,
        "decision": None,
        "skip_reason": "",
        "fit_reason": "",
        "applied": False,
    }]
    os.environ["WEB_SIGNAL_SEARCH"] = "1"
    os.environ["WEB_SIGNAL_MAX_RESULTS"] = "1"
    integrated = discovery_agent.discover_jobs(
        queries=[{"term": "investment analyst", "location": "Dubai"}],
        sites=["linkedin"],
        hours_fresh=48,
        blocked_companies=[],
        blocked_keywords=[],
        blocked_titles=[],
        max_years=7,
        max_results=3,
    )
    check("indexed signals enter discovery pipeline", [j["source"] for j in integrated], ["web_indexed"])

    web_signal_agent.discover_web_signals = lambda *args, **kwargs: []
    discovery_agent.scrape_jobs = lambda **kwargs: pd.DataFrame([
        {
            "title": f"Analyst {i}",
            "company": "Acme",
            "location": "Dubai, UAE",
            "description": "Dubai analyst role",
            "job_url": f"https://www.linkedin.com/jobs/view/{i}",
            "job_url_direct": "",
            "date_posted": "",
            "site": "linkedin",
        }
        for i in range(1, 4)
    ])
    linkedin_only = discovery_agent.discover_jobs(
        queries=[{"term": "analyst", "location": "Dubai"}],
        sites=["linkedin"],
        hours_fresh=48,
        blocked_companies=[],
        blocked_keywords=[],
        blocked_titles=[],
        max_years=7,
        max_results=3,
    )
    check("empty indexed search does not reduce LinkedIn capacity", len(linkedin_only), 3)
finally:
    discovery_agent.scrape_jobs = old_scrape_jobs
    discovery_agent.JOBSPY_AVAILABLE = old_jobspy_available
    web_signal_agent.discover_web_signals = old_discover_web_signals
    if old_web_signal_search is None:
        os.environ.pop("WEB_SIGNAL_SEARCH", None)
    else:
        os.environ["WEB_SIGNAL_SEARCH"] = old_web_signal_search
    if old_web_signal_max is None:
        os.environ.pop("WEB_SIGNAL_MAX_RESULTS", None)
    else:
        os.environ["WEB_SIGNAL_MAX_RESULTS"] = old_web_signal_max

print("\n== Autonomous submission safeguards ==")

class _ContextConfirmationPage:
    def __init__(self, text, url):
        self.text = text
        self.url = url
        self.context = None
    def locator(self, selector):
        return _FakeBody(self.text)
    def is_closed(self):
        return False

form_page = _ContextConfirmationPage("Review your answers.", "https://example.com/form")
thanks_page = _ContextConfirmationPage(
    "Thank you for applying. We received your application.",
    "https://example.com/thanks",
)
class _ConfirmationContext:
    pages = [form_page, thanks_page]
confirmation_context = _ConfirmationContext()
form_page.context = confirmation_context
thanks_page.context = confirmation_context
redirect_job = {}
confirmed_page = form_filler._wait_for_submission_confirmation(
    form_page, redirect_job, "test", timeout_s=0
)
check("new-tab confirmation is detected", confirmed_page is thanks_page, True)
check("new-tab confirmation records URL", redirect_job.get("confirmation_url"), thanks_page.url)

class _FakeNavButton:
    def __init__(self, label):
        self.label = label
        self.clicked = False
    def count(self):
        return 1
    def is_visible(self, timeout=0):
        return True
    def is_disabled(self, timeout=0):
        return False
    def inner_text(self, timeout=0):
        return self.label
    def get_attribute(self, name):
        return ""
    def click(self, timeout=0):
        self.clicked = True

class _FakeNavLocator:
    def __init__(self, button):
        self.first = button

class _FakeNavPage:
    def __init__(self, button):
        self.button = button
    def locator(self, selector):
        return _FakeNavLocator(self.button)
    def wait_for_load_state(self, *args, **kwargs):
        return None

old_pause = form_filler._pause
form_filler._pause = lambda *args, **kwargs: None
try:
    terminal_button = _FakeNavButton("Submit Application")
    check("generic navigator rejects Submit", form_filler._click_generic_next(_FakeNavPage(terminal_button)), False)
    check("generic navigator did not click Submit", terminal_button.clicked, False)
    continue_button = _FakeNavButton("Continue")
    check("generic navigator advances Continue", form_filler._click_generic_next(_FakeNavPage(continue_button)), True)
    check("generic navigator clicked Continue", continue_button.clicked, True)

    class _FakeLinkedInSubmitPage:
        def __init__(self):
            self.submit = _FakeNavButton("Submit application")
        def locator(self, selector):
            if "easy-apply-modal" in selector or "Easy Apply" in selector:
                return self
            button = self.submit if "Submit application" in selector else _FakeNavButton("")
            if button is not self.submit:
                button.count = lambda: 0
            return _FakeNavLocator(button)
        def wait_for_load_state(self, *args, **kwargs):
            return None

    dry_submit_page = _FakeLinkedInSubmitPage()
    check(
        "LinkedIn nav dry-run does not click Submit",
        form_filler._linkedin_click_next_or_submit(dry_submit_page, allow_submit=False),
        "not_found",
    )
    check("LinkedIn dry-run Submit remains unclicked", dry_submit_page.submit.clicked, False)
    live_submit_page = _FakeLinkedInSubmitPage()
    check(
        "LinkedIn nav live can click Submit",
        form_filler._linkedin_click_next_or_submit(live_submit_page, allow_submit=True),
        "submit",
    )
    check("LinkedIn live Submit clicked", live_submit_page.submit.clicked, True)
finally:
    form_filler._pause = old_pause

from agents import scorer as scorer_agent
old_score_job = scorer_agent.score_job
callback_titles = []
scorer_agent.score_job = lambda job, *args, **kwargs: job
try:
    scorer_agent.score_jobs_batch(
        [{"title": "A", "company": "X"}, {"title": "B", "company": "Y"}],
        "", "", "", {},
        progress_callback=lambda job: callback_titles.append(job["title"]),
    )
finally:
    scorer_agent.score_job = old_score_job
check("scorer callback fires per job", callback_titles, ["A", "B"])

with TemporaryDirectory() as tmp:
    retry_store = JobStore(Path(tmp) / "jobs.db")
    retry_id = retry_store.upsert_job({
        "company": "ACME",
        "title": "Retry Test",
        "job_url": "https://example.com/retry",
        "decision": "auto_apply",
        "apply_attempts": 2,
    })
    check("retry queue includes attempt two", len(retry_store.fetch_pending_apply()), 1)
    retry_store.update_job(retry_id, apply_attempts=3)
    check("retry queue excludes attempt three", len(retry_store.fetch_pending_apply()), 0)
    check("exhausted retry becomes manual review", retry_store.get_job(retry_id)["decision"], "manual_review")
    rejected = False
    try:
        retry_store.update_job(retry_id, applied=True)
    except ValueError:
        rejected = True
    check("store rejects applied without evidence", rejected, True)

old_scrape_jobs = discovery_agent.scrape_jobs
old_jobspy_available = discovery_agent.JOBSPY_AVAILABLE
old_web_signal_search = os.environ.get("WEB_SIGNAL_SEARCH")
try:
    discovery_agent.JOBSPY_AVAILABLE = True
    os.environ["WEB_SIGNAL_SEARCH"] = "0"
    def _partially_failing_scrape(**kwargs):
        site = kwargs["site_name"][0]
        if site == "glassdoor":
            raise RuntimeError("unsupported region")
        return pd.DataFrame([{
            "title": "Investment Analyst",
            "company": "ACME",
            "location": "Dubai, UAE",
            "description": "Investment analyst role",
            "job_url": f"https://example.com/{site}/analyst",
            "job_url_direct": "",
            "date_posted": "",
            "site": site,
        }])
    discovery_agent.scrape_jobs = _partially_failing_scrape
    resilient_jobs = discovery_agent.discover_jobs(
        queries=[{"term": "investment analyst", "location": "Dubai"}],
        sites=["linkedin", "glassdoor"],
        hours_fresh=48,
        blocked_companies=[],
        blocked_keywords=[],
        blocked_titles=[],
        max_years=7,
        max_results=3,
    )
    check("unsupported JobSpy site does not discard working site", len(resilient_jobs), 1)
    check("working JobSpy provider survives partial failure", resilient_jobs[0]["source"], "linkedin")
finally:
    discovery_agent.scrape_jobs = old_scrape_jobs
    discovery_agent.JOBSPY_AVAILABLE = old_jobspy_available
    if old_web_signal_search is None:
        os.environ.pop("WEB_SIGNAL_SEARCH", None)
    else:
        os.environ["WEB_SIGNAL_SEARCH"] = old_web_signal_search

print("\n== Cover letter, search API, and verification retry helpers ==")
from agents import account_signup
from agents import web_signal_discovery
from config import env_settings

with TemporaryDirectory() as tmp:
    pdf_path = Path(tmp) / "cover-letter.pdf"
    form_filler._write_text_pdf(str(pdf_path), "Dear Hiring Manager,\n\nTest attachment.")
    pdf_bytes = pdf_path.read_bytes()
    check("generated cover letter is PDF", pdf_bytes.startswith(b"%PDF-1.4"), True)
    check("generated cover letter PDF is complete", pdf_bytes.rstrip().endswith(b"%%EOF"), True)

    old_settings_path = env_settings.PROFILE_SETTINGS_PATH
    old_migrated_flag = env_settings._MIGRATED_FLAG
    old_legacy_path = env_settings.LEGACY_ENV_PATH
    try:
        env_settings.PROFILE_SETTINGS_PATH = Path(tmp) / "profile_settings.json"
        env_settings._MIGRATED_FLAG = Path(tmp) / ".migrated"
        env_settings.LEGACY_ENV_PATH = Path(tmp) / ".env"
        raw = env_settings._default_raw_settings()
        raw["cover_letter_path"] = str(pdf_path)
        env_settings._write_raw_settings(raw)
        check(
            "cover letter path persists in profile settings",
            env_settings.load_profile_settings()["cover_letter_path"],
            str(pdf_path),
        )
    finally:
        env_settings.PROFILE_SETTINGS_PATH = old_settings_path
        env_settings._MIGRATED_FLAG = old_migrated_flag
        env_settings.LEGACY_ENV_PATH = old_legacy_path

    old_pending_file = account_signup._PENDING_VERIFY_FILE
    try:
        account_signup._PENDING_VERIFY_FILE = Path(tmp) / "pending_email_verify.json"
        verify_job = {
            "id": 17,
            "title": "Analyst",
            "company": "ACME",
            "job_url": "https://jobs.example.com/analyst",
        }
        account_signup.add_email_verify_pending(
            verify_job, "https://jobs.example.com/account", "candidate@example.com"
        )
        check("email-verification retry one claimed", len(account_signup.pop_email_verify_pending()), 1)
        account_signup.add_email_verify_pending(
            verify_job, "https://jobs.example.com/account", "candidate@example.com"
        )
        check("email-verification retry count survives requeue", account_signup._load_email_verify_pending()[0]["retries"], 1)
        check("email-verification retry two claimed", len(account_signup.pop_email_verify_pending()), 1)
        check("email-verification retry three claimed", len(account_signup.pop_email_verify_pending()), 1)
        check("email-verification retry four blocked", account_signup.pop_email_verify_pending(), [])
        account_signup.clear_email_verify_pending(verify_job)
        check("email-verification queue clears after success", account_signup._load_email_verify_pending(), [])
    finally:
        account_signup._PENDING_VERIFY_FILE = old_pending_file

old_serpapi_key = os.environ.get("SERPAPI_API_KEY")
old_requests_get = web_signal_discovery.requests.get
class _FakeSerpResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {"organic_results": [{
            "title": "Investment Analyst - ACME",
            "link": "https://jobs.lever.co/acme/analyst",
            "snippet": "Dubai investment role",
        }]}
try:
    os.environ["SERPAPI_API_KEY"] = "test-key"
    web_signal_discovery.requests.get = lambda *args, **kwargs: _FakeSerpResponse()
    indexed = web_signal_discovery.search_public_web("investment analyst Dubai", 7, 3)
    check("SerpApi is preferred when configured", indexed[0]["provider"], "serpapi")
finally:
    web_signal_discovery.requests.get = old_requests_get
    if old_serpapi_key is None:
        os.environ.pop("SERPAPI_API_KEY", None)
    else:
        os.environ["SERPAPI_API_KEY"] = old_serpapi_key

# ── LinkedIn outreach (external lead discovery) ───────────────────────────────
from agents import linkedin_outreach

prompt = linkedin_outreach.generate_lead_discovery_prompt("Lunate\nADQ", run_focus="UAE recruiters")
check("LLM prompt mentions target companies", "Lunate" in prompt and "ADQ" in prompt, True)
check(
    "LLM prompt requests CSV output",
    "LinkedIn URL" in prompt and "LinkedIn connection message" in prompt,
    True,
)

check(
    "parse linkedin profile urls",
    linkedin_outreach.parse_linkedin_profile_urls(
        "https://www.linkedin.com/in/jane-doe/ linkedin.com/in/john_smith"
    ),
    ["https://www.linkedin.com/in/jane-doe", "https://linkedin.com/in/john_smith"],
)
mapped = linkedin_outreach._map_csv_row({
    "profile_url": "https://www.linkedin.com/in/test-user",
    "name": "Test User",
    "connection message": "Hi Test",
})
check("csv column alias mapping", mapped.get("LinkedIn URL"), "https://www.linkedin.com/in/test-user")
check("csv name alias mapping", mapped.get("Person name"), "Test User")
check("csv message alias mapping", mapped.get("LinkedIn connection message"), "Hi Test")

sample_msg = linkedin_outreach._connection_message(
    "Jane",
    "Acme Corp",
    "Recruiter / Talent",
    "Talent Acquisition",
    linkedin_outreach._company_info("Acme Corp", {}),
)
check("connection message uses profile name not hardcoded", "Rashed Alneyadi" in sample_msg, False)
check("connection message has sign-off from profile", "Best," in sample_msg, True)
prompt_bullets = linkedin_outreach._outreach_message_bullets()
check("message bullets come from profile qa", "Nationality:" in prompt_bullets or "Education:" in prompt_bullets, True)

n_pass = sum(1 for ok, *_ in results if ok)
print(f"\n=== {n_pass}/{len(results)} assertions passed ===")
sys.exit(0 if n_pass == len(results) else 1)

"""
Discover public hiring signals that are not returned by JobSpy:
- LinkedIn employee / recruiter hiring posts indexed by web search
- Direct ATS and company-careers openings indexed by web search

SerpApi or Google Programmable Search is used when credentials are configured.
Bing RSS and DuckDuckGo HTML fallbacks keep unattended discovery working
without keys.
"""

import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("web_signal_discovery")

GOOGLE_SEARCH_URL = "https://customsearch.googleapis.com/customsearch/v1"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
DUCKDUCKGO_SEARCH_URL = "https://html.duckduckgo.com/html/"
BING_RSS_SEARCH_URL = "https://www.bing.com/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Rotate user-agents to reduce rate-limiting from search engines
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
_ua_index = 0

def _next_user_agent() -> str:
    global _ua_index
    ua = _USER_AGENTS[_ua_index % len(_USER_AGENTS)]
    _ua_index += 1
    return ua

ATS_SITE_FILTER = (
    "(site:myworkdayjobs.com OR site:boards.greenhouse.io OR "
    "site:job-boards.greenhouse.io OR site:jobs.lever.co OR "
    "site:jobs.ashbyhq.com OR site:apply.workable.com OR "
    "site:jobs.smartrecruiters.com OR site:careers.smartrecruiters.com OR "
    "site:jobs.icims.com OR site:taleo.net)"
)

_EMPLOYEE_POST_RE = re.compile(r"linkedin\.com/(?:posts/|feed/update/)", re.I)
_CAREER_PATH_RE = re.compile(
    r"/(?:job|jobs|career|careers|position|positions|vacancy|vacancies|"
    r"opening|openings|opportunity|opportunities|requisition|apply)(?:/|[-_])",
    re.I,
)
_TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "trk", "trackingid", "ref", "refid",
}
_TERM_STOP_WORDS = {
    "and", "or", "the", "a", "an", "for", "of", "in", "with", "jobs", "job",
    "graduate", "junior", "senior",
}


def _env_int(key: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key, "1" if default else "0").strip().lower()
    return value not in ("0", "false", "no", "off")


def _clean_result_url(url: str) -> str:
    """Normalize search-result redirects and remove common tracking parameters."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc.lower() and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            url = unquote(target)
            parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    kept_query = []
    for pair in parsed.query.split("&"):
        if not pair:
            continue
        key = pair.partition("=")[0].lower()
        if key not in _TRACKING_QUERY_KEYS:
            kept_query.append(pair)
    return urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path, "", "&".join(kept_query), "")
    )


def _google_search(query: str, days_fresh: int, limit: int) -> list[dict]:
    key = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
    cx = os.getenv("GOOGLE_SEARCH_CX", "").strip()
    if not key or not cx:
        return []
    response = requests.get(
        GOOGLE_SEARCH_URL,
        params={
            "key": key,
            "cx": cx,
            "q": query,
            "num": min(limit, 10),
            "dateRestrict": f"d{max(1, days_fresh)}",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return [
        {
            "title": str(item.get("title") or "").strip(),
            "url": _clean_result_url(item.get("link") or ""),
            "snippet": str(item.get("snippet") or "").strip(),
            "provider": "google",
        }
        for item in response.json().get("items", [])
    ][:limit]


def _serpapi_search(query: str, days_fresh: int, limit: int) -> list[dict]:
    """Search indexed pages through SerpApi when a key is configured."""
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key:
        return []
    response = requests.get(
        SERPAPI_SEARCH_URL,
        params={
            "engine": "google",
            "q": query,
            "num": min(limit, 10),
            "tbs": f"qdr:d{max(1, days_fresh)}",
            "api_key": key,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return [
        {
            "title": str(item.get("title") or "").strip(),
            "url": _clean_result_url(item.get("link") or ""),
            "snippet": str(item.get("snippet") or "").strip(),
            "provider": "serpapi",
        }
        for item in response.json().get("organic_results", [])
        if item.get("link")
    ][:limit]


def _http_get_with_retry(url: str, params: dict, timeout: int = 20,
                          max_retries: int = 3) -> "requests.Response":
    """GET with rotating user-agent, Accept headers, and exponential backoff."""
    import time as _time
    last_exc = None
    for attempt in range(max_retries):
        if attempt:
            _time.sleep(2 ** attempt)  # 2s, 4s backoff
        headers = {
            "User-Agent": _next_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                logger.debug("Rate-limited by %s (attempt %d/%d)", url, attempt + 1, max_retries)
                last_exc = requests.HTTPError(f"429 Too Many Requests: {url}")
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
    raise last_exc or requests.RequestException(f"All {max_retries} attempts failed for {url}")


def _duckduckgo_search(query: str, days_fresh: int, limit: int) -> list[dict]:
    after = (datetime.utcnow() - timedelta(days=max(1, days_fresh))).strftime("%Y-%m-%d")
    # DuckDuckGo HTML endpoint — POST works more reliably than GET
    import time as _time
    last_exc = None
    for attempt in range(3):
        if attempt:
            _time.sleep(2 ** attempt)
        try:
            headers = {
                "User-Agent": _next_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://duckduckgo.com/",
            }
            response = requests.post(
                DUCKDUCKGO_SEARCH_URL,
                data={"q": f"{query} after:{after}", "b": "", "kl": "us-en"},
                headers=headers,
                timeout=20,
            )
            if response.status_code == 429:
                last_exc = requests.HTTPError("429")
                continue
            response.raise_for_status()
            break
        except requests.RequestException as e:
            last_exc = e
    else:
        raise last_exc or requests.RequestException("DuckDuckGo search failed")

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for block in soup.select(".result, .web-result"):
        link = block.select_one(".result__a, a[data-testid='result-title-a']")
        if not link:
            continue
        href = link.get("href") or ""
        cleaned = _clean_result_url(href)
        if not cleaned:
            continue
        snippet = block.select_one(".result__snippet, [data-result='snippet']")
        results.append({
            "title": link.get_text(" ", strip=True),
            "url": cleaned,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            "provider": "duckduckgo",
        })
        if len(results) >= limit:
            break
    return results


def _bing_rss_search(query: str, days_fresh: int, limit: int) -> list[dict]:
    after = (datetime.utcnow() - timedelta(days=max(1, days_fresh))).strftime("%Y-%m-%d")
    response = _http_get_with_retry(
        BING_RSS_SEARCH_URL,
        params={"q": f"{query} after:{after}", "format": "rss"},
        timeout=20,
    )
    try:
        root = ElementTree.fromstring(response.text)
    except ElementTree.ParseError:
        # Bing sometimes returns HTML 200 instead of valid RSS on bot detection
        return []
    results = []
    for item in root.findall("./channel/item"):
        url = _clean_result_url(item.findtext("link") or "")
        if not url:
            continue
        results.append({
            "title": (item.findtext("title") or "").strip(),
            "url": url,
            "snippet": BeautifulSoup(
                item.findtext("description") or "", "html.parser"
            ).get_text(" ", strip=True),
            "provider": "bing",
        })
        if len(results) >= limit:
            break
    return results


def _ddg_instant_search(query: str, limit: int = 5) -> list[dict]:
    """
    DuckDuckGo Instant Answer JSON API — no HTML scraping, lower block rate.
    Returns Related Topics as job-signal results when available.
    """
    try:
        response = _http_get_with_retry(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=15,
            max_retries=2,
        )
        data = response.json()
    except Exception:
        return []
    results = []
    # Pull from RelatedTopics
    for topic in data.get("RelatedTopics", []):
        if len(results) >= limit:
            break
        if isinstance(topic, dict) and topic.get("FirstURL"):
            url = _clean_result_url(topic["FirstURL"])
            if url:
                results.append({
                    "title": (topic.get("Text") or "")[:200],
                    "url": url,
                    "snippet": (topic.get("Text") or "")[:300],
                    "provider": "ddg_instant",
                })
    return results


def search_public_web(query: str, days_fresh: int, limit: int = 5) -> list[dict]:
    """Search Google when configured, otherwise use public indexed fallbacks."""
    if os.getenv("SERPAPI_API_KEY", "").strip():
        try:
            results = _serpapi_search(query, days_fresh, limit)
            if results:
                return results
        except Exception as e:
            logger.warning(f"SerpApi indexed search failed, using fallback: {e}")
    if os.getenv("GOOGLE_SEARCH_API_KEY", "").strip() and os.getenv("GOOGLE_SEARCH_CX", "").strip():
        try:
            return _google_search(query, days_fresh, limit)
        except Exception as e:
            logger.warning(f"Google indexed search failed, using fallback: {e}")
    try:
        results = _bing_rss_search(query, days_fresh, limit)
        if results:
            return results
    except Exception as e:
        logger.debug(f"Bing search failed: {e}")
    try:
        results = _duckduckgo_search(query, days_fresh, limit)
        if results:
            return results
    except Exception as e:
        logger.debug(f"DuckDuckGo search failed: {e}")
    # Last resort: DDG instant answer JSON (no HTML scraping needed)
    try:
        results = _ddg_instant_search(query, limit)
        if results:
            return results
    except Exception as e:
        logger.debug(f"DDG instant search failed: {e}")
    logger.warning(f"All public web search engines failed for query: {query[:80]}")
    return []


def _is_external_google_result_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    return (
        parsed.scheme in ("http", "https")
        and bool(host)
        and "google." not in host
        and not host.endswith("google.com")
        and not host.endswith("googleusercontent.com")
    )


class GoogleBrowserSearch:
    """Reuse one normal Chromium browser for public Google result pages."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    def start(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._page = self._browser.new_page(user_agent=USER_AGENT, locale="en-US")
        return self

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._playwright:
                self._playwright.stop()
            self._page = None
            self._browser = None
            self._playwright = None

    def search(self, query: str, days_fresh: int, limit: int = 5) -> list[dict]:
        if not self._page:
            return search_public_web(query, days_fresh, limit)
        after = (datetime.utcnow() - timedelta(days=max(1, days_fresh))).strftime("%Y-%m-%d")
        try:
            self._page.goto(
                f"https://www.google.com/search?q={quote_plus(f'{query} after:{after}')}&num={min(10, limit * 2)}&hl=en",
                wait_until="domcontentloaded",
                timeout=25000,
            )
            self._page.wait_for_timeout(1200)
            if "/sorry/" in self._page.url or "captcha" in self._page.url.lower():
                return search_public_web(query, days_fresh, limit)
            links = self._page.locator("a[href]")
            results = []
            seen = set()
            for i in range(min(links.count(), 300)):
                link = links.nth(i)
                url = _clean_result_url(link.get_attribute("href") or "")
                if not url or url in seen or not _is_external_google_result_url(url):
                    continue
                seen.add(url)
                try:
                    title = link.inner_text(timeout=1000).strip()
                except Exception:
                    title = ""
                try:
                    snippet = link.evaluate(
                        """el => {
                            let node = el;
                            for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
                                const text = (node.innerText || '').trim();
                                if (text.length >= 80 && text.length <= 1800) return text;
                            }
                            return (el.innerText || '').trim();
                        }"""
                    )
                except Exception:
                    snippet = title
                results.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "provider": "google_browser",
                    }
                )
                if len(results) >= limit:
                    break
            return results or search_public_web(query, days_fresh, limit)
        except Exception as e:
            logger.warning(f"Browser Google search failed, using fallback: {e}")
            return search_public_web(query, days_fresh, limit)


def _select_queries(queries: list[dict], limit: int) -> list[dict]:
    """Sample across the full search plan so one role family cannot dominate."""
    if len(queries) <= limit:
        return list(queries)
    if limit <= 1:
        return [queries[0]]
    indexes = {
        round(i * (len(queries) - 1) / (limit - 1))
        for i in range(limit)
    }
    return [queries[i] for i in sorted(indexes)]


def _looks_like_employee_post(url: str) -> bool:
    return bool(_EMPLOYEE_POST_RE.search(url or ""))


def _matches_role_term(text: str, term: str) -> bool:
    """Require meaningful role-keyword overlap before accepting broad search results."""
    text = (text or "").lower()
    term = (term or "").lower().strip()
    if not term:
        return False
    if term in text:
        return True
    tokens = [
        token for token in re.findall(r"[a-z0-9+#]+", term)
        if len(token) > 2 and token not in _TERM_STOP_WORDS
    ]
    if not tokens:
        return False
    required = min(2, len(tokens))
    return sum(1 for token in tokens if token in text) >= required


def _looks_like_direct_opening(url: str) -> bool:
    """Reject search/listing pages while keeping ATS and custom job-detail URLs."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    low_path = path.lower()
    if not host or not path or "linkedin.com" in host:
        return False
    if re.search(r"/(?:internal|employee|staff)(?:posting|jobs?|careers?)?/", low_path):
        return False
    if "myworkdayjobs.com" in host:
        return "/job/" in low_path
    if "greenhouse.io" in host:
        return bool(re.search(r"/jobs?/\d+", low_path))
    if "jobs.lever.co" in host or "jobs.ashbyhq.com" in host:
        return len([p for p in path.split("/") if p]) >= 2
    if "apply.workable.com" in host:
        return "/j/" in low_path
    if "smartrecruiters.com" in host:
        return len([p for p in path.split("/") if p]) >= 2
    if "icims.com" in host:
        return "/jobs/" in low_path and "/job" in low_path
    if "taleo.net" in host:
        return "jobdetail.ftl" in low_path and bool(parsed.query)
    return bool(_CAREER_PATH_RE.search(low_path))


def _infer_company(url: str, title: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [p for p in parsed.path.split("/") if p]
    if "jobs.lever.co" in host or "jobs.ashbyhq.com" in host:
        return parts[0].replace("-", " ").title() if parts else host
    if "greenhouse.io" in host and parts:
        first = parts[0]
        if first not in ("jobs", "job"):
            return first.replace("-", " ").title()
    if "myworkdayjobs.com" in host:
        return host.split(".")[0].replace("-", " ").title()
    clean_title = re.split(r"\s+[|-]\s+", title or "")[-1].strip()
    if clean_title and clean_title.lower() not in ("linkedin", "jobs", "careers"):
        return clean_title[:120]
    return host[:120]


# ── Employee post query builder ───────────────────────────────────────────────

# Phrases that hiring managers / recruiters write in LinkedIn posts
_HIRING_POST_PHRASES = (
    '"we are hiring"',
    '"we\'re hiring"',
    '"now hiring"',
    '"looking to hire"',
    '"open to connections"',
    '"DM me"',
    '"reach out"',
    '"join our team"',
    '"apply now"',
    '"exciting opportunity"',
)

# Target employers to scan specifically (add more as you discover them)
_TARGET_EMPLOYERS_UAE = (
    "Mubadala",
    "G42",
    "ADQ",
    "Brevan Howard",
    "Millennium",
    "Citadel",
    "Wintermute",
    "Optiver",
    "Jump Trading",
    "ADNOC",
    "Abu Dhabi Investment Authority",
    "MCM",
)


def _build_employee_post_queries(queries: list[dict]) -> list[str]:
    """
    Build Google/Bing search strings optimised for finding LinkedIn employee
    hiring posts that are NOT indexed by jobspy.

    Strategy:
      1. site:linkedin.com/posts + role term + UAE/GCC location
      2. site:linkedin.com/posts + role term + hiring phrase
      3. role term + "UAE" + ATS site filters (finds hidden ATS postings)
    """
    search_strings: list[str] = []
    seen: set[str] = set()

    for q in queries:
        term = (q.get("term") or "").strip()
        location = (q.get("location") or "UAE").strip()
        if not term:
            continue

        # 1. Direct LinkedIn post search with location
        s1 = f'site:linkedin.com/posts "{term}" ("{location}" OR "UAE" OR "Abu Dhabi" OR "Dubai") hiring'
        if s1 not in seen:
            seen.add(s1)
            search_strings.append(s1)

        # 2. LinkedIn feed/update search (older LinkedIn post URL format)
        s2 = f'site:linkedin.com/feed/update "{term}" hiring (UAE OR "Abu Dhabi" OR Dubai)'
        if s2 not in seen:
            seen.add(s2)
            search_strings.append(s2)

        # 3. Hiring phrase variant — most likely to surface recruiter posts
        phrase = _HIRING_POST_PHRASES[hash(term) % len(_HIRING_POST_PHRASES)]
        s3 = f'site:linkedin.com "{term}" {phrase} (UAE OR "Abu Dhabi" OR Dubai OR GCC)'
        if s3 not in seen:
            seen.add(s3)
            search_strings.append(s3)

        # 4. ATS site filter for hidden direct openings not in jobspy
        s4 = f'"{term}" ({location} OR UAE) {ATS_SITE_FILTER}'
        if s4 not in seen:
            seen.add(s4)
            search_strings.append(s4)

    # 5. Target-employer specific posts (one per employer, sampled)
    for employer in _TARGET_EMPLOYERS_UAE[:6]:
        s5 = f'site:linkedin.com/posts "{employer}" hiring (analyst OR researcher OR trader OR engineer)'
        if s5 not in seen:
            seen.add(s5)
            search_strings.append(s5)

    return search_strings


def _normalize_signal_job(result: dict, kind: str, location: str) -> dict:
    """Convert a raw search result into the standard job dict shape."""
    url = _clean_result_url(result.get("url") or "")
    title = (result.get("title") or "").strip()[:240]
    snippet = (result.get("snippet") or "").strip()
    employee_post = kind == "employee_post"
    return {
        "title": title or ("Employee / recruiter hiring post" if employee_post else "Indexed opening"),
        "company": _infer_company(url, title),
        "location": location,
        "description": (
            f"Indexed via {result.get('provider') or 'web search'}.\n{title}\n{snippet}"
        ).strip(),
        "job_url": url,
        "job_url_direct": "" if employee_post else url,
        "date_posted": "",
        "source": kind,
        "apply_method": "Hiring post - review" if employee_post else "ATS",
        "search_provider": result.get("provider") or "web",
        "score": None,
        "decision": None,
        "skip_reason": "",
        "fit_reason": "",
        "applied": False,
        "discovered_at": datetime.utcnow().isoformat(),
    }


def discover_google_jobs(
    queries: list[dict],
    max_results: int = 15,
    scrape_fn=None,
) -> list[dict]:
    """Collect Google Jobs rows that expose a direct external ATS opening."""
    if not queries or max_results <= 0:
        return []
    if scrape_fn is None:
        try:
            from jobspy import scrape_jobs as scrape_fn
        except ImportError:
            return []

    jobs: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        if len(jobs) >= max_results:
            break
        term = (query.get("term") or "").strip()
        location = (query.get("location") or "UAE").strip()
        if not term:
            continue
        try:
            frame = scrape_fn(
                site_name=["google"],
                search_term=term,
                google_search_term=f"{term} jobs in {location}",
                location=location,
                results_wanted=min(10, max_results - len(jobs)),
                hours_old=24 * 14,
                country_indeed="united arab emirates",
            )
        except Exception as exc:
            logger.debug("Google Jobs discovery failed for %r: %s", term, exc)
            continue
        if frame is None or getattr(frame, "empty", True):
            continue
        for _, row in frame.iterrows():
            direct_url = _clean_result_url(
                str(row.get("job_url_direct") or row.get("job_url") or "")
            )
            if (
                not direct_url
                or direct_url in seen
                or not _looks_like_direct_opening(direct_url)
            ):
                continue
            seen.add(direct_url)
            result = {
                "title": str(row.get("title") or "").strip(),
                "url": direct_url,
                "snippet": str(row.get("description") or "").strip(),
                "provider": "google_jobs",
            }
            job = _normalize_signal_job(result, "web_indexed", location)
            job["company"] = str(row.get("company") or job["company"]).strip()
            job["date_posted"] = str(row.get("date_posted") or "").strip()
            jobs.append(job)
            if len(jobs) >= max_results:
                break
    return jobs


def discover_hiring_signals(
    queries: list[dict],
    days_fresh: int = 14,
    max_results: int = 40,
    dedup_seen: set | None = None,
    search_fn=None,
) -> list[dict]:
    """Search the public web for LinkedIn hiring posts and ATS openings not found by jobspy."""
    if not queries:
        return []
    dedup = set(dedup_seen or set())
    search_strings = _build_employee_post_queries(queries)
    search_strings = search_strings[:min(len(search_strings), 20)]
    results: list[dict] = []
    for search_q in search_strings:
        if len(results) >= max_results:
            break
        try:
            raw = (search_fn or search_public_web)(
                search_q, days_fresh=days_fresh, limit=5
            )
        except Exception as exc:
            logger.debug("Web signal search failed for %r: %s", search_q[:60], exc)
            continue
        for r in raw:
            url = _clean_result_url(r.get("url") or "")
            if not url or url in dedup:
                continue
            title_text = (r.get("title") or "") + " " + (r.get("snippet") or "")
            matched = any(
                _matches_role_term(title_text, q.get("term", ""))
                for q in queries
            )
            if not matched:
                continue
            dedup.add(url)
            is_post = _looks_like_employee_post(url)
            is_opening = _looks_like_direct_opening(url)
            if not is_post and not is_opening:
                continue
            kind = "employee_post" if is_post else "web_indexed"
            location = (queries[0].get("location") or "UAE") if queries else "UAE"
            job = _normalize_signal_job(r, kind, location)
            results.append(job)
            logger.info("  Signal [%s]: %s -- %s", kind, job["title"][:60], url[:70])
    logger.info("Web signal discovery: %d hiring signals found", len(results))
    return results


def discover_web_signals(
    queries: list[dict],
    max_results: int = 15,
    max_queries: int = 6,
    days_fresh: int = 14,
    dedup_seen: set | None = None,
    search_fn=None,
    linkedin_posts_fn=None,
    google_jobs_fn=None,
) -> list[dict]:
    """Compatibility entry point used by the main discovery agent."""
    selected = list(queries[:max_queries]) if max_queries > 0 else list(queries)
    jobs = discover_hiring_signals(
        selected,
        days_fresh=days_fresh,
        max_results=max_results,
        dedup_seen=dedup_seen,
        search_fn=search_fn,
    )
    seen = {job.get("job_url") for job in jobs if job.get("job_url")}
    if linkedin_posts_fn and len(jobs) < max_results:
        try:
            extra_posts = linkedin_posts_fn(selected, max_results=max_results - len(jobs))
        except TypeError:
            extra_posts = linkedin_posts_fn(selected)
        for job in extra_posts or []:
            url = job.get("job_url") or ""
            if url and url not in seen:
                seen.add(url)
                jobs.append(job)
    if len(jobs) < max_results:
        google_jobs = (google_jobs_fn or discover_google_jobs)(
            selected, max_results=max_results - len(jobs)
        )
        for job in google_jobs or []:
            url = job.get("job_url") or ""
            if url and url not in seen:
                seen.add(url)
                jobs.append(job)
    return jobs[:max_results]

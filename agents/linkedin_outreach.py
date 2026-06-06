"""
LinkedIn outreach for JobHuntrr.

Lead discovery happens outside the program: copy the generated LLM prompt into
ChatGPT/Claude/etc., then import the returned CSV (or paste profile URLs).

The program only uses Playwright for sending — navigate to each /in/ URL with
the saved LinkedIn session and send the connection or follow-up message.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTREACH_PATH = DATA_DIR / "linkedin_outreach.json"
OUTREACH_CSV_PATH = DATA_DIR / "linkedin_outreach.csv"
OUTREACH_MD_PATH = DATA_DIR / "linkedin_outreach.md"
EMPLOYER_REGISTRY_PATH = DATA_DIR / "employer_registry.json"

logger = logging.getLogger("linkedin_outreach")

OUTREACH_STATUSES = [
    "Not sent",
    "Sent connection request",
    "Message sent",
    "Accepted",
    "Follow-up sent",
    "Replied",
    "Referral requested",
    "Referred",
    "Applied",
    "Rejected",
    "No response",
    "Waterfall level 4 exhausted",
    "Archive",
]

EXCLUDED_DEFAULT_TARGETS = {"adia", "adic", "mubadala"}


def _registry_company_names() -> list[str]:
    """Target companies from employer registry (no hardcoded list)."""
    names = []
    try:
        registry = _load_registry()
        for item in registry.values():
            name = (item.get("name") or "").strip()
            if name and name.lower() not in EXCLUDED_DEFAULT_TARGETS:
                names.append(name)
    except Exception:
        pass
    return names


FIELDNAMES = [
    "id",
    "opportunity_id",
    "Waterfall level",
    "IPS score",
    "Company",
    "Company category",
    "Company priority score",
    "Person name",
    "Person title",
    "LinkedIn URL",
    "Person category",
    "Person priority score",
    "Why this person",
    "Message angle",
    "LinkedIn connection message",
    "Follow-up message after acceptance",
    "Suggested role types",
    "Careers page URL",
    "Current relevant roles",
    "Outreach status",
    "Date messaged",
    "Date accepted",
    "Date followed up",
    "Reply status",
    "Notes",
]

ROLE_THEMES = {
    "investment": "investment, portfolio analytics, strategy, private markets, venture capital",
    "ai": "AI engineering, applied AI, data science, research engineering",
    "space": "space, robotics, geospatial AI, satellite data, strategic technology",
    "quant": "quant research, trading technology, portfolio/risk analytics",
    "strategy": "strategy, corporate development, founder/operator roles",
}

PERSON_SEARCH_GROUPS = [
    (
        "Recruiter / Talent",
        '"Talent Acquisition" OR Recruiter OR "Early Careers" OR Emiratization OR "UAE National Recruitment"',
    ),
    (
        "Investment / Strategy",
        '"Investment Analyst" OR "Investment Associate" OR "Portfolio Manager" OR Strategy OR "Corporate Development" OR "Private Equity"',
    ),
    (
        "AI / Technical / Research",
        '"AI Engineer" OR "Data Scientist" OR "Research Scientist" OR "Machine Learning Engineer" OR "Quantitative Researcher"',
    ),
    (
        "Space / Robotics / Geospatial",
        '"Robotics Engineer" OR Geospatial OR Satellite OR "Space Systems" OR "Remote Sensing"',
    ),
]


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_companies(text: str) -> list[str]:
    """Parse newline/comma separated companies, excluding ADIA/ADIC/Mubadala by default."""
    raw = text or default_companies_text()
    parts = []
    for line in raw.replace(",", "\n").splitlines():
        company = line.strip(" -*\t")
        if not company:
            continue
        if company.lower() in EXCLUDED_DEFAULT_TARGETS:
            continue
        if company not in parts:
            parts.append(company)
    return parts


def default_companies_text() -> str:
    return "\n".join(generate_relevant_companies(limit=40))


def generate_relevant_companies(*, limit: int = 30, run_focus: str = "") -> list[str]:
    """
    Build a practical target-company list from the employer registry, local job DB,
    and the default outreach set. This is deterministic so the GUI button stays fast.
    """
    candidates: dict[str, int] = {}

    def add(name: str, score: int) -> None:
        clean = (name or "").strip()
        if not clean or clean.lower() in EXCLUDED_DEFAULT_TARGETS:
            return
        candidates[clean] = max(candidates.get(clean, 0), score)

    focus = (run_focus or "").lower()

    registry = _load_registry()
    for item in registry.values():
        name = item.get("name") or ""
        category = _company_category(name)
        score = _company_priority(name, category) + 3
        text = f"{name} {category} {item.get('careers_url', '')}".lower()
        if focus and any(token in text for token in re.findall(r"[a-z0-9]+", focus)):
            score += 2
        add(name, score)

    for name in _registry_company_names():
        category = _company_category(name)
        add(name, _company_priority(name, category) + 2)

    try:
        from storage.job_store import JobStore
        store = JobStore()
        jobs = store.list_jobs(limit=500, include_closed=False)
        for job in jobs:
            company = job.get("company") or ""
            score = 5
            if (job.get("decision") or "") == "auto_apply":
                score += 2
            score += min(3, int((job.get("score") or 0) // 30))
            text = f"{company} {job.get('title', '')} {job.get('location', '')}".lower()
            if any(k in text for k in ("abu dhabi", "dubai", "uae", "adgm", "difc", "qatar", "riyadh")):
                score += 1
            if any(k in text for k in ("investment", "quant", "ai", "data", "strategy", "space", "robot", "portfolio")):
                score += 1
            if focus and any(token in text for token in re.findall(r"[a-z0-9]+", focus)):
                score += 2
            add(company, score)
    except Exception as exc:
        logger.info("Could not use local jobs for company generation: %s", exc)

    # Boost companies already in outreach history that are still active
    # (not archived/no-response) — they're warm targets worth keeping visible
    try:
        for row in load_rows():
            company = (row.get("Company") or "").strip()
            status = (row.get("Outreach status") or "").strip().lower()
            if not company:
                continue
            if status in ("archive", "no response", "rejected"):
                continue  # don't re-surface dead ends
            boost = 4 if status in ("accepted", "replied", "referred") else 2
            add(company, boost)
    except Exception as exc:
        logger.debug("Could not read outreach history for company generation: %s", exc)

    ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0].lower()))
    return [name for name, _score in ranked[: max(1, limit)]]


def load_rows() -> list[dict]:
    """Load outreach rows from both legacy JSON and opportunity store."""
    legacy_rows = []
    if OUTREACH_PATH.exists():
        try:
            data = json.loads(OUTREACH_PATH.read_text(encoding="utf-8"))
            legacy_rows = data if isinstance(data, list) else []
        except Exception:
            pass

    # Merge with opportunity store attempts
    existing_urls: set[str] = set()
    for legacy_row in legacy_rows:
        for key in ("LinkedIn URL", "LinkedIn profile URL"):
            url = _clean_linkedin_profile_url(str(legacy_row.get(key) or ""))
            if url:
                existing_urls.add(url.lower())

    try:
        from storage.opportunity_store import get_opportunity_store

        store = get_opportunity_store()
        attempts = store.fetch_all_outreach_attempts(limit=500)

        for att in attempts:
            linkedin_url = _clean_linkedin_profile_url(str(att.get("linkedin_url") or ""))
            if linkedin_url and linkedin_url.lower() in existing_urls:
                continue

            row = {
                "id": att.get("id") or str(uuid.uuid4()),
                "opportunity_id": att.get("opportunity_id") or "",
                "Waterfall level": att.get("level") or 1,
                "IPS": att.get("ips") or "",
                "Company": att.get("company") or "",
                "Person name": att.get("contact_name") or "",
                "Person title": att.get("contact_title") or "",
                "LinkedIn profile URL": linkedin_url,
                "Suggested message": att.get("draft_message") or "",
                "Person priority score": att.get("sps") or "",
                "Outreach status": (
                    "Not sent" if att.get("status") == "draft_ready" else "Pending"
                ),
                "Notes": att.get("notes") or "",
            }
            legacy_rows.append(row)
            if linkedin_url:
                existing_urls.add(linkedin_url.lower())
    except Exception as exc:
        logger.debug("Failed to load opportunity store attempts: %s", exc)

    return legacy_rows


def save_rows(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTREACH_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_rows(new_rows: list[dict]) -> list[dict]:
    existing = load_rows()
    keyed = {}
    for row in existing:
        key = _row_key(row)
        if key:
            keyed[key] = row
    for row in new_rows:
        key = _row_key(row)
        if not key:
            continue
        old = keyed.get(key)
        if old:
            keep = {
                "Outreach status": old.get("Outreach status") or "Not sent",
                "Date messaged": old.get("Date messaged") or "",
                "Date accepted": old.get("Date accepted") or "",
                "Date followed up": old.get("Date followed up") or "",
                "Reply status": old.get("Reply status") or "",
                "Notes": old.get("Notes") or "",
                "opportunity_id": old.get("opportunity_id") or row.get("opportunity_id") or "",
            }
            old.update(row)
            old.update(keep)
            keyed[key] = old
        else:
            keyed[key] = row
    rows = list(keyed.values())
    rows.sort(
        key=lambda r: (
            -int(r.get("Company priority score") or 0),
            -int(r.get("Person priority score") or 0),
            r.get("Company", ""),
        )
    )
    save_rows(rows)
    return rows


def update_row(row_id: str, **fields) -> bool:
    rows = load_rows()
    changed = False
    for row in rows:
        if row.get("id") == row_id:
            for key, value in fields.items():
                if key in FIELDNAMES:
                    row[key] = value
            changed = True
            break
    if changed:
        save_rows(rows)
    return changed


def delete_rows(row_ids: list[str]) -> int:
    """Remove outreach rows by id. Returns number of rows deleted."""
    if not row_ids:
        return 0
    drop = {rid.strip() for rid in row_ids if rid and rid.strip()}
    if not drop:
        return 0
    rows = load_rows()
    kept = [row for row in rows if row.get("id") not in drop]
    deleted = len(rows) - len(kept)
    if deleted:
        save_rows(kept)
    return deleted


def find_people_for_company(
    company: str,
    *,
    run_focus: str = "",
    max_people: int = 8,
    headless: bool = False,
) -> tuple[list[dict], str]:
    """
    Use the saved LinkedIn session to resolve a company/manual-search lead into
    real visible LinkedIn people rows. This does not send messages.
    """
    del headless  # LinkedIn session helper intentionally opens a visible browser.
    company = (company or "").strip()
    if not company:
        return [], "No company selected"

    try:
        from playwright.sync_api import sync_playwright
        from agents.form_filler import (
            _ensure_linkedin_login,
            _get_linkedin_context,
        )
    except Exception as exc:
        return [], f"Playwright/LinkedIn helper import failed: {exc}"

    registry = _load_registry()
    company_info = _company_info(company, registry)
    roles = _current_roles_for_company(company)
    leads: list[dict] = []
    seen: set[str] = set()

    with sync_playwright() as playwright:
        ctx = _get_linkedin_context(playwright)
        try:
            if not _ensure_linkedin_login(ctx):
                return [], "LinkedIn login or verification required"
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for category, query_bits in PERSON_SEARCH_GROUPS:
                if len(leads) >= max_people:
                    break
                query = _linkedin_people_query(company, category, query_bits, run_focus)
                url = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(query)
                logger.info("LinkedIn people search: %s", query)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    _wait_short(page)
                    block_reason = _linkedin_block_reason(page)
                    if block_reason:
                        return [], f"LinkedIn blocked people search: {block_reason}"
                    for lead in _extract_people_from_search_page(page, company, category):
                        key = _lead_key(lead)
                        if key in seen:
                            continue
                        seen.add(key)
                        leads.append(lead)
                        if len(leads) >= max_people:
                            break
                except Exception as exc:
                    logger.info("LinkedIn people search failed for %s/%s: %s", company, category, exc)
                    continue
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    if not leads:
        return [], f"No visible LinkedIn people found for {company}"

    rows = [
        _build_row(company_info, lead, roles, run_focus)
        for lead in leads[: max(1, max_people)]
    ]
    merged = merge_rows(rows)
    saved_keys = {_row_key(row) for row in rows}
    saved = [row for row in merged if _row_key(row) in saved_keys]
    return saved, f"Added {len(saved)} real LinkedIn people row(s) for {company}"


def guided_send_for_company(
    company: str,
    *,
    run_focus: str = "",
    max_people: int = 8,
    headless: bool = False,
) -> tuple[bool, str, dict]:
    """
    Full one-person guided outreach flow:
    find real people for a company, choose the strongest sendable row, send its
    tailored message, then update tracking. This intentionally sends at most one
    person per explicit user action.
    """
    rows, status = find_people_for_company(
        company,
        run_focus=run_focus,
        max_people=max_people,
        headless=headless,
    )
    if not rows:
        return False, status, {}

    row = _best_sendable_row(rows)
    if not row:
        return False, f"No sendable real LinkedIn profile row found for {company}", {}

    message = (row.get("LinkedIn connection message") or "").strip()
    if not message:
        return False, f"No connection message available for {row.get('Person name')}", row

    ok, send_status = send_linkedin_connection(
        row.get("LinkedIn URL") or "",
        row.get("Person name") or "",
        row.get("Company") or company,
        message,
        headless=headless,
    )
    if not ok:
        return False, send_status, row

    today = datetime.now().strftime("%Y-%m-%d")
    update_row(
        row.get("id") or "",
        **{
            "Outreach status": "Sent connection request",
            "Date messaged": today,
        },
    )
    row.update({
        "Outreach status": "Sent connection request",
        "Date messaged": today,
    })
    return True, f"{send_status}: {row.get('Person name')} @ {row.get('Company')}", row


def import_linkedin_profile_urls(
    urls_text: str,
    *,
    company: str = "",
    run_focus: str = "",
    default_category: str = "Recruiter / Talent",
    headless: bool = False,
    scrape_profiles: bool = False,
) -> tuple[list[dict], str]:
    """
    Create outreach rows from user-provided LinkedIn /in/ URLs.

    By default does not open LinkedIn (no people search, no profile scraping).
    Set scrape_profiles=True only if you need Playwright to read name/title from
    each profile page.
    """
    del headless  # LinkedIn session helper intentionally opens a visible browser.
    urls = parse_linkedin_profile_urls(urls_text)
    if not urls:
        return [], "No valid LinkedIn profile URLs found"

    company = (company or "").strip()
    if not scrape_profiles:
        raw_rows = [
            {
                "LinkedIn URL": url,
                "Company": company,
                "Person category": default_category,
            }
            for url in urls
        ]
        return import_outreach_rows(raw_rows, run_focus=run_focus, default_company=company)

    try:
        from playwright.sync_api import sync_playwright
        from agents.form_filler import (
            _ensure_linkedin_login,
            _get_linkedin_context,
        )
    except Exception as exc:
        return [], f"Playwright/LinkedIn helper import failed: {exc}"

    registry = _load_registry()
    leads: list[dict] = []

    with sync_playwright() as playwright:
        ctx = _get_linkedin_context(playwright)
        try:
            if not _ensure_linkedin_login(ctx):
                return [], "LinkedIn login or verification required"
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for url in urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    _wait_short(page)
                    block_reason = _linkedin_block_reason(page)
                    if block_reason:
                        return [], f"LinkedIn blocked profile import for {url}: {block_reason}"
                    lead = _lead_from_profile_page(page, url, company, default_category)
                    if lead:
                        leads.append(lead)
                except Exception as exc:
                    logger.info("LinkedIn profile import failed for %s: %s", url, exc)
                    continue
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    leads = _dedupe_leads(leads)
    if not leads:
        return [], "No readable LinkedIn profiles found"

    rows: list[dict] = []
    for lead in leads:
        row_company = company or lead.get("company") or "LinkedIn Contact"
        company_info = _company_info(row_company, registry)
        roles = _current_roles_for_company(row_company)
        rows.append(_build_row(company_info, lead, roles, run_focus))
    merged = merge_rows(rows)
    saved_keys = {_row_key(row) for row in rows}
    saved = [row for row in merged if _row_key(row) in saved_keys]
    return saved, f"Imported {len(saved)} LinkedIn profile row(s)"


def parse_linkedin_profile_urls(text: str) -> list[str]:
    urls = []
    for match in re.findall(r"https?://[^\s,;\"'<>]+", text or ""):
        clean = _clean_linkedin_profile_url(match)
        if clean and clean not in urls:
            urls.append(clean)
    for token in re.split(r"[\s,;]+", text or ""):
        token = token.strip()
        if token.startswith("linkedin.com/in/") or token.startswith("www.linkedin.com/in/"):
            clean = _clean_linkedin_profile_url("https://" + token)
            if clean and clean not in urls:
                urls.append(clean)
    return urls


def _lead_from_profile_page(page, url: str, company: str, default_category: str) -> dict | None:
    body = _normalize_space(_safe_inner_text(page.locator("body")))
    name = ""
    title = ""
    for selector in (
        "h1",
        ".text-heading-xlarge",
        ".pv-text-details__left-panel h1",
    ):
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=800):
                name = _normalize_space(loc.inner_text(timeout=1200))
                if name:
                    break
        except Exception:
            continue

    for selector in (
        ".text-body-medium.break-words",
        ".pv-text-details__left-panel .text-body-medium",
        "section:has(h1) .text-body-medium",
    ):
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible(timeout=800):
                title = _normalize_space(loc.inner_text(timeout=1200))
                if title:
                    break
        except Exception:
            continue

    if not name:
        name, title = _parse_linkedin_card_text(body, company)
    if not title:
        title = _infer_title_from_profile_text(body, company)
    if not name:
        return None

    inferred_company = company or _infer_company_from_profile_text(body, title)
    category = _infer_person_category(default_category, title)
    if category == "Archive":
        category = default_category
    return {
        "name": name,
        "title": title or default_category,
        "url": _clean_linkedin_profile_url(url),
        "category": category,
        "source": "manual_profile_import",
        "company": inferred_company,
        "snippet": body[:500],
    }


def _infer_title_from_profile_text(text: str, company: str) -> str:
    lines = [
        _normalize_space(line)
        for line in re.split(r"[\r\n]+| {2,}", text or "")
        if _normalize_space(line)
    ]
    for line in lines[:30]:
        low = line.lower()
        if company and company.lower() in low:
            return line
        if any(k in low for k in (
            "talent", "recruit", "human capital", "people",
            "investment", "portfolio", "strategy", "analyst", "associate",
            "ai", "data", "machine learning", "research", "engineer", "quant",
        )):
            return line
    return ""


def _infer_company_from_profile_text(text: str, title: str) -> str:
    if title and " at " in title.lower():
        parts = re.split(r"\bat\b", title, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            company = _normalize_space(parts[1])
            if company:
                return company[:80]
    for marker in ("Current:", "Experience", "Company"):
        idx = (text or "").find(marker)
        if idx >= 0:
            chunk = _normalize_space((text or "")[idx:idx + 200])
            for company in _registry_company_names():
                if company.lower() in chunk.lower():
                    return company
    return ""


def _best_sendable_row(rows: list[dict]) -> dict:
    sendable = []
    for row in rows:
        url = (row.get("LinkedIn URL") or "").lower()
        person = (row.get("Person name") or "").lower()
        status = (row.get("Outreach status") or "Not sent").lower()
        if "linkedin.com/in/" not in url:
            continue
        if person.startswith("manual linkedin search"):
            continue
        if status not in ("not sent", ""):
            continue
        sendable.append(row)
    if not sendable:
        return {}
    sendable.sort(
        key=lambda r: (
            -int(r.get("Person priority score") or 0),
            0 if (r.get("Person category") or "") == "Recruiter / Talent" else 1,
            r.get("Person name") or "",
        )
    )
    return sendable[0]


def _linkedin_people_query(company: str, category: str, query_bits: str, run_focus: str) -> str:
    focus = (run_focus or "").strip()
    company_q = f'"{company}"'
    if category == "Recruiter / Talent":
        base = f'{company_q} "Talent Acquisition" recruiter UAE'
    elif category == "Investment / Strategy":
        base = f'{company_q} investment analyst associate portfolio strategy UAE'
    elif category == "AI / Technical / Research":
        base = f'{company_q} AI data scientist machine learning research UAE'
    elif category == "Space / Robotics / Geospatial":
        base = f'{company_q} space robotics geospatial satellite UAE'
    else:
        base = f"{company_q} {query_bits} UAE"
    if focus:
        base = f"{base} {focus[:80]}"
    return base


def _extract_people_from_search_page(page, company: str, default_category: str) -> list[dict]:
    leads: list[dict] = []
    try:
        items = page.locator("li:has(a[href*='/in/'])")
        count = min(items.count(), 20)
    except Exception:
        items = None
        count = 0

    for i in range(count):
        try:
            item = items.nth(i)
            raw_text = _safe_inner_text(item)
            if not _normalize_space(raw_text):
                continue
            link = item.locator("a[href*='/in/']").first
            href = link.get_attribute("href") or ""
            lead = _lead_from_linkedin_card(company, default_category, href, raw_text)
            if lead:
                leads.append(lead)
        except Exception:
            continue

    if leads:
        return _dedupe_leads(leads)

    # Fallback for LinkedIn UI variants where result cards are not list items.
    try:
        cards = page.evaluate(
            """(company) => {
                const companyText = (company || '').toLowerCase();
                const compactCompany = companyText.replace(/[^a-z0-9]/g, '');
                const mentionsCompany = (text) => {
                    const low = (text || '').toLowerCase();
                    if (!companyText) return true;
                    if (low.includes(companyText)) return true;
                    return compactCompany && low.replace(/[^a-z0-9]/g, '').includes(compactCompany);
                };
                const out = [];
                const seen = new Set();
                for (const a of document.querySelectorAll("a[href*='/in/']")) {
                    const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                    if (!href || seen.has(href)) continue;
                    const anchorText = (a.innerText || '').trim();
                    if (!mentionsCompany(anchorText)) continue;
                    let node = a;
                    let best = anchorText;
                    for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
                        const text = (node.innerText || '').trim();
                        if (text.length > best.length) best = text;
                        if (text.length > 80 && /Connect|Message|Follow|Current:|Past:| at /i.test(text)) {
                            best = text;
                            break;
                        }
                    }
                    seen.add(href);
                    out.push({href, text: best});
                }
                return out;
            }""",
            company,
        )
        for card in cards[:30]:
            href = card.get("href") or ""
            text = card.get("text") or ""
            lead = _lead_from_linkedin_card(company, default_category, href, text)
            if lead:
                leads.append(lead)
    except Exception:
        pass
    return _dedupe_leads(leads)


def _lead_from_linkedin_card(company: str, default_category: str, href: str, text: str) -> dict | None:
    url = _clean_linkedin_profile_url(href)
    if not url:
        return None
    if not _card_mentions_company(text, company):
        return None
    name, title = _parse_linkedin_card_text(text, company)
    if not name or name.lower() in {"linkedin member", "private profile"}:
        return None
    category = _infer_person_category(default_category, title)
    if category == "Archive":
        return None
    return {
        "name": name,
        "title": title or default_category,
        "url": url,
        "category": category,
        "source": "linkedin_people_search",
        "snippet": _normalize_space(text)[:500],
    }


def _card_mentions_company(text: str, company: str) -> bool:
    company = _normalize_space(company).lower()
    if not company:
        return True
    haystack = _normalize_space(text).lower()
    if company in haystack:
        return True
    compact_company = re.sub(r"[^a-z0-9]", "", company)
    compact_text = re.sub(r"[^a-z0-9]", "", haystack)
    return bool(compact_company and compact_company in compact_text)


def _clean_linkedin_profile_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    if "linkedin.com/in/" not in href.lower():
        return ""
    href = href.split("?")[0].split("#")[0].rstrip("/")
    # Drop mini-profile and tracking URLs that are not actual public profile pages.
    if "/in/" not in href:
        return ""
    return href


def _parse_linkedin_card_text(text: str, company: str) -> tuple[str, str]:
    lines = [
        _normalize_space(line)
        for line in re.split(r"[\r\n]+", text or "")
        if _normalize_space(line)
    ]
    filtered = []
    noise = {
        "view profile",
        "connect",
        "message",
        "follow",
        "1st",
        "2nd",
        "3rd",
    }
    for line in lines:
        low = line.lower()
        if low in noise:
            continue
        if "followers" in low or "connections" in low:
            continue
        if low.startswith("member's name"):
            continue
        filtered.append(line)
    if not filtered:
        return "", ""
    name = _clean_linkedin_result_name(filtered[0])
    title = ""
    for line in filtered[1:5]:
        low = line.lower()
        if company.lower() in low or any(k in low for k in (
            "recruit", "talent", "investment", "analyst", "associate",
            "portfolio", "strategy", "data", "ai", "machine learning",
            "research", "engineer", "quant", "space", "geospatial",
        )):
            title = line
            break
    if not title and len(filtered) > 1:
        title = filtered[1]
    title = _clean_linkedin_result_title(title, company)
    return name, title


def _clean_linkedin_result_name(name: str) -> str:
    clean = _normalize_space(name)
    clean = re.split(r"\s+•\s+", clean, maxsplit=1)[0]
    clean = re.split(r"\s+\b(?:1st|2nd|3rd|3rd\+|[0-9]+(?:st|nd|rd|th))\b", clean, maxsplit=1, flags=re.I)[0]
    clean = re.sub(r"\s+is a mutual connection\b.*$", "", clean, flags=re.I).strip()
    return clean


def _clean_linkedin_result_title(title: str, company: str) -> str:
    clean = _normalize_space(title)
    clean = re.sub(r"\bConnect\b.*$", "", clean).strip()
    clean = re.sub(r"\bMessage\b.*$", "", clean).strip()
    clean = re.sub(r"\bCurrent:\s*", "", clean).strip()
    if company and company.lower() in clean.lower():
        return clean
    return clean


def _dedupe_leads(leads: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for lead in leads:
        key = _lead_key(lead)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(lead)
    return out


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def send_linkedin_connection(
    linkedin_url: str,
    person_name: str,
    company: str,
    message: str,
    headless: bool = False,
    dry_run: bool = False,
    send_mode: str = "connect",
) -> tuple[bool, str]:
    """
    Open LinkedIn profile, click Message, paste message, send.
    If dry_run=True, stops immediately before the final Send/Invitation click.
    Returns (success, status_message).
    Uses saved LinkedIn session - does NOT log in from scratch.
    """
    del headless  # LinkedIn session helper intentionally opens a visible browser.
    if not message.strip():
        return False, "No message text available"

    try:
        from playwright.sync_api import sync_playwright
        from agents.form_filler import (
            _ensure_linkedin_login,
            _get_linkedin_context,
        )
    except Exception as exc:
        return False, f"Playwright/LinkedIn helper import failed: {exc}"

    with sync_playwright() as playwright:
        ctx = _get_linkedin_context(playwright)
        try:
            if not _ensure_linkedin_login(ctx):
                return False, "LinkedIn login or verification required - send manually"
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            return _send_via_page(page, linkedin_url, person_name, company, message, dry_run=dry_run, send_mode=send_mode)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _dismiss_messaging_panel(page) -> None:
    """
    Close the floating LinkedIn Messaging panel if it's open.
    It sits at the bottom of the page and intercepts pointer events on profile buttons.
    """
    try:
        # Collapse button on the messaging panel (the chevron/down arrow)
        close = page.locator(
            "button[aria-label*='Minimize'], "
            "button[aria-label*='Close messaging'], "
            "button[aria-label*='collapse'], "
            ".msg-overlay-bubble-header__controls button"
        )
        if close.count():
            close.first.click(timeout=3000)
            logger.info("[DM] Dismissed messaging panel.")
            return
        # Fallback: hide via JS
        page.evaluate("""
            () => {
                const panel = document.querySelector('._12eabaa3, .msg-overlay-list-bubble, .msg-overlay-conversation-bubble');
                if (panel) panel.style.display = 'none';
            }
        """)
        logger.info("[DM] Hid messaging panel via JS.")
    except Exception as exc:
        logger.info("[DM] Could not dismiss messaging panel: %s", exc)


def _find_connect_div(page):
    """
    Find the primary Connect element on a LinkedIn profile.
    The actual clickable element is an <a> tag:
      <a aria-label="Invite [Name] to connect" href="/preload/custom-invite/...">
        ...<div><span><span>Connect</span></span></div>...
      </a>
    Target the <a> directly — clicking the inner div fails due to overlays.
    """
    # Most reliable: aria-label contains "connect" (case-insensitive)
    for selector in (
        "a[aria-label*='connect' i]",
        "a[href*='/preload/custom-invite/']",
        "a[href*='custom-invite']",
    ):
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 10)
            for i in range(count):
                el = loc.nth(i)
                try:
                    if el.is_visible(timeout=500):
                        logger.info("[DM] Found Connect <a> via selector: %s", selector)
                        return el
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: button/div whose trimmed text is exactly "Connect"
    for selector in ("button:text-is('Connect')", "div:has(> span > span:text-is('Connect'))"):
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 10)
            for i in range(count):
                el = loc.nth(i)
                try:
                    if not el.is_visible(timeout=500):
                        continue
                    text = _normalize_space(el.inner_text(timeout=500))
                    if text.lower() == "connect":
                        return el
                except Exception:
                    continue
        except Exception:
            continue

    return None


def _find_more_button(page):
    """
    Find the three-dots / More button on a LinkedIn profile page.
    HTML: <button aria-label="More" ...><svg id="overflow-web-ios-small">...</svg></button>
    """
    # Primary: exact aria-label="More" on a button
    for selector in (
        "button[aria-label='More']",
        "button[aria-label*='More actions']",
    ):
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 5)
            for i in range(count):
                el = loc.nth(i)
                if el.is_visible(timeout=500):
                    logger.info("[DM] Found More button via: %s", selector)
                    return el
        except Exception:
            continue

    # Fallback: button containing the overflow SVG
    try:
        loc = page.locator("button:has(svg#overflow-web-ios-small)")
        if loc.count() and loc.first.is_visible(timeout=500):
            logger.info("[DM] Found More button via SVG child selector.")
            return loc.first
    except Exception:
        pass

    # XPath fallback: walk up from the SVG
    try:
        loc = page.locator("xpath=//svg[@id='overflow-web-ios-small']/ancestor::button[1]")
        if loc.count() and loc.first.is_visible(timeout=500):
            logger.info("[DM] Found More button via XPath ancestor.")
            return loc.first
    except Exception:
        pass

    logger.info("[DM] More button not found.")
    return None


def _check_connection_status(page) -> str:
    """
    Inspect the profile action buttons to detect whether we've already connected,
    have a pending request, or have already messaged this person.
    Returns a human-readable status string if we should skip, or "" if clear to send.
    """
    try:
        # Grab all visible button/link text in the profile header area
        body_text = _normalize_space(_safe_inner_text(
            page.locator("main section:has(h1)")
        )).lower()
    except Exception:
        body_text = ""

    # Check for explicit status indicators
    signals = {
        "pending":   "connection request already pending",
        "withdraw":  "connection request already pending (withdraw visible)",
        "following": "already following this person",
    }
    for keyword, reason in signals.items():
        if keyword in body_text:
            return reason

    # If the only action available is "Message" (no Connect), they're already connected
    has_connect = bool(re.search(r"\bconnect\b", body_text))
    has_message = bool(re.search(r"\bmessage\b", body_text))
    has_pending = bool(re.search(r"\bpending\b", body_text))

    if has_pending:
        return "connection request already pending"
    if has_message and not has_connect:
        # Already a 1st-degree connection — still fine to message, not a skip
        return ""
    return ""


def _send_via_page(
    page,
    linkedin_url: str,
    person_name: str,
    company: str,
    message: str,
    *,
    dry_run: bool = False,
    send_mode: str = "connect",
) -> tuple[bool, str]:
    """Send one connection/message using an already-open, already-logged-in page."""
    try:
        logger.info("[DM] Resolving LinkedIn target for %s @ %s (url=%s)", person_name, company, linkedin_url)
        target_url = _resolve_linkedin_target(page, linkedin_url, person_name, company)
        if not target_url:
            logger.info("[DM] No matching LinkedIn profile found for %s", person_name)
            return False, "No matching LinkedIn profile found"

        logger.info("[DM] Navigating to profile: %s", target_url)
        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        _wait_short(page)
        try:
            page.evaluate("window.scrollTo(0, 0)")
            _wait_short(page)
        except Exception:
            pass

        block_reason = _linkedin_block_reason(page)
        if block_reason:
            logger.info("[DM] LinkedIn block detected for %s: %s", person_name, block_reason)
            return False, f"LinkedIn verification detected - send manually: {block_reason}"

        # ── Pre-flight check: already connected / pending / messaged? ─────────
        already_status = _check_connection_status(page)
        if already_status:
            logger.info("[DM] Skipping %s — %s", person_name, already_status)
            return False, f"Already {already_status} — skipping"

        logger.info("[DM] Profile loaded. Mode=%s. Attempting to send to %s", send_mode, person_name)
        ok, status = _send_from_linkedin_profile(page, message, person_name=person_name, dry_run=dry_run, send_mode=send_mode)
        if ok:
            logger.info("[DM] Message sent successfully to %s: %s", person_name, status)
            time.sleep(2.0)
        else:
            logger.info("[DM] Message failed for %s: %s", person_name, status)
        return ok, status
    except Exception as exc:
        logger.info("[DM] Unexpected error sending to %s: %s", person_name, exc)
        return False, f"Unexpected error: {exc}"


def _resolve_linkedin_target(page, linkedin_url: str, person_name: str, company: str) -> str:
    url = (linkedin_url or "").strip()
    if "linkedin.com/in/" in url.lower():
        return url

    query = " ".join(part for part in (person_name, company) if part).strip()
    if not query:
        return url if url else ""
    search_url = "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(query)
    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
    _wait_short(page)

    name_l = (person_name or "").lower()
    company_l = (company or "").lower()
    cards = page.locator("a[href*='/in/']").all()
    fallback = ""
    for link in cards[:25]:
        try:
            href = link.get_attribute("href") or ""
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.linkedin.com" + href
            text = _safe_inner_text(link).lower()
            block = _safe_inner_text(link.locator("xpath=ancestor::li[1]")).lower()
            combined = f"{text} {block}"
            if not fallback:
                fallback = href
            if name_l and name_l not in combined:
                continue
            if company_l and company_l not in combined:
                continue
            return href.split("?")[0]
        except Exception:
            continue
    return fallback.split("?")[0] if fallback else ""


def _send_from_linkedin_profile(
    page,
    message: str,
    *,
    person_name: str = "",
    dry_run: bool = False,
    send_mode: str = "connect",
) -> tuple[bool, str]:
    # ── If mode is "message", skip Connect entirely and go straight to Message ─
    if send_mode == "message":
        logger.info("[DM] Mode=message — skipping Connect, going straight to Message flow.")
        return _do_message_flow(page, message, person_name=person_name, dry_run=dry_run)

    # ── Step 1: Find Connect button ───────────────────────────────────────────
    # Primary: <div><span><span>Connect</span></span></div>
    connect_clicked = False
    connect = _find_connect_div(page)
    if connect:
        try:
            logger.info("[DM] Found primary Connect div — clicking (force=True to bypass overlays).")
            _dismiss_messaging_panel(page)
            connect.click(force=True, timeout=7000)
            _wait_short(page)
            connect_clicked = True
        except Exception as exc:
            logger.info("[DM] Primary Connect click failed: %s", exc)

    # Fallback: More / three-dots → Connect in dropdown
    if not connect_clicked:
        logger.info("[DM] No primary Connect div found — trying More/three-dots menu.")
        overflow = _find_more_button(page)
        if overflow:
            try:
                _dismiss_messaging_panel(page)
                overflow.click(force=True, timeout=7000)
                _wait_short(page)
                # Connect appears as <p>Connect</p> in the dropdown
                connect_in_menu = _first_visible(page, [
                    "p:text-is('Connect')",
                    "span:text-is('Connect')",
                    "div:text-is('Connect')",
                    "[role='menuitem']:has-text('Connect')",
                ])
                if connect_in_menu:
                    logger.info("[DM] Found Connect in dropdown — clicking.")
                    connect_in_menu.click(force=True, timeout=7000)
                    _wait_short(page)
                    connect_clicked = True
                else:
                    logger.info("[DM] Dropdown opened but Connect option not found.")
            except Exception as exc:
                logger.info("[DM] More/dropdown flow failed: %s", exc)
        else:
            logger.info("[DM] No More/three-dots button found either.")

    if connect_clicked:
        try:
            # Click "Add a note" to open the note textarea
            add_note = _first_visible(page, [
                "button[aria-label='Add a note']",
                "button:has-text('Add a note')",
                "button[aria-label*='Add a note']",
            ])
            if add_note:
                logger.info("[DM] Clicking 'Add a note'.")
                add_note.click(timeout=5000)
                _wait_short(page)
            else:
                logger.info("[DM] 'Add a note' button not found — proceeding without note.")

            # Fill the note textarea
            textarea = _first_visible(page, [
                "textarea[name='message']",
                "textarea",
            ])
            if textarea:
                note = _fit_linkedin_note(textarea, message)
                textarea.fill(note, timeout=5000)
                logger.info("[DM] Note filled (%d chars).", len(note))
                _wait_short(page)
            else:
                logger.info("[DM] Note textarea not found — sending without note.")

            # Click Send invitation — target by aria-label as shown in the HTML
            send = _first_visible(page, [
                "button[aria-label='Send invitation']",
                "button[aria-label*='Send invitation']",
                "button:has-text('Send invitation')",
                "button:has-text('Send')",
            ])
            if not send:
                return False, "Connect dialog opened but Send invitation button not found"
            if dry_run:
                return True, "DRY RUN: Connect dialog ready, Send invitation button found"
            send.click(timeout=7000)
            return True, "Sent connection request with note"
        except Exception as exc:
            logger.info("[DM] Connect note/send flow failed: %s — trying Message fallback.", exc)

    # ── Step 2: Fall back to Message (already connected / 1st degree) ─────────
    return _do_message_flow(page, message, person_name=person_name, dry_run=dry_run)


def _do_message_flow(page, message: str, *, person_name: str = "", dry_run: bool = False) -> tuple[bool, str]:
    """Click Message, fill compose box, click Send."""
    message_btn = _linkedin_profile_action_button(page, "message", person_name)
    if not message_btn:
        message_btn = _linkedin_profile_overflow_action_button(page, "message", person_name)
    if message_btn:
        try:
            logger.info("[DM] Found Message button — sending direct message.")
            message_btn.click(timeout=7000)
            _wait_short(page)
            box = _first_visible(page, [
                "div[role='textbox'][contenteditable='true']",
                ".msg-form__contenteditable[contenteditable='true']",
                "[aria-label*='Write a message'][contenteditable='true']",
            ])
            if not box:
                return False, "Message window opened but compose box was not found"
            box.click(timeout=5000)
            page.keyboard.insert_text(message)
            _wait_short(page)
            send = _first_visible(page, [
                "button.msg-form__send-btn",
                "button[type='submit'].artdeco-button--primary",
                "button[type='submit']:has-text('Send')",
                "button:has-text('Send')",
                "button[aria-label*='Send']",
            ])
            if not send:
                return False, "Message compose filled but send button was not found"
            if dry_run:
                return True, "DRY RUN: Message compose filled and Send button found"
            send.click(timeout=7000)
            return True, "Sent direct message"
        except Exception as exc:
            return False, f"Message flow failed: {exc}"
    return False, "No Connect or Message button found on this profile"


def _linkedin_profile_action_button(page, action: str, person_name: str = ""):
    """Find the target person's own profile action, avoiding recommendation-card buttons."""
    action = (action or "").lower()
    names = [part.lower() for part in re.split(r"\s+", person_name or "") if len(part) > 1]

    text_candidate = _linkedin_profile_action_text(page, action, person_name)
    if text_candidate:
        return text_candidate

    selectors = [
        "main section:has(h1) button, main section:has(h1) a[role='button']",
        "main button, main a[role='button']",
        "button, a[role='button']",
    ]
    best = None
    best_score = -1
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 120)):
                item = loc.nth(i)
                try:
                    if not item.is_visible(timeout=350):
                        continue
                except Exception:
                    continue
                text = ""
                try:
                    text = item.inner_text(timeout=400) or ""
                except Exception:
                    pass
                aria = item.get_attribute("aria-label") or ""
                label = _normalize_space(f"{text} {aria}").lower()
                action_pattern = r"\bconnect\b" if action == "connect" else r"\bmessage\b"
                if not re.search(action_pattern, label):
                    continue
                try:
                    box = item.bounding_box(timeout=400) or {}
                except Exception:
                    box = {}
                y = float(box.get("y") or 9999)
                score = 0
                if y < 850:
                    score += 5
                if y < 650:
                    score += 3
                if y < 90:
                    score -= 8
                if action == "connect" and "invite" in label:
                    score += 4
                if action == "message" and "message" in label:
                    score += 4
                if names and any(name in label for name in names[:2]):
                    score += 6
                if "recommend" in label or "follow" in label:
                    score -= 4
                if score > best_score:
                    best = item
                    best_score = score
            if best and best_score >= 5:
                return best
        except Exception:
            continue
    return best if best_score >= 5 else None


def _linkedin_profile_action_text(page, action: str, person_name: str = ""):
    """Find LinkedIn header actions rendered as nested text instead of plain buttons."""
    action = (action or "").lower()
    if action == "connect":
        label = "Connect"
    elif action == "message":
        label = "Message"
    elif action == "more":
        label = "More"
    else:
        return None
    names = [part.lower() for part in re.split(r"\s+", person_name or "") if len(part) > 1]
    candidate_locators = []
    try:
        candidate_locators.append(page.locator("main").get_by_text(label, exact=True))
    except Exception:
        pass
    for selector in (
        f'main section:has(h1) :text-is("{label}")',
        f'main :text-is("{label}")',
    ):
        try:
            candidate_locators.append(page.locator(selector))
        except Exception:
            pass

    best = None
    best_score = -1
    for loc in candidate_locators:
        try:
            for i in range(min(loc.count(), 30)):
                item = loc.nth(i)
                try:
                    if not item.is_visible(timeout=300):
                        continue
                    text = _normalize_space(item.inner_text(timeout=300) or "")
                    if text.lower() != label.lower():
                        continue
                    box = item.bounding_box(timeout=300) or {}
                except Exception:
                    continue
                y = float(box.get("y") or 9999)
                x = float(box.get("x") or 0)
                score = 0
                if 80 <= y <= 520:
                    score += 10
                elif y < 750:
                    score += 4
                else:
                    score -= 8
                if x < 1200:
                    score += 2
                if names:
                    try:
                        block = _safe_inner_text(item.locator("xpath=ancestor::section[1]")).lower()
                    except Exception:
                        block = ""
                    if any(name in block for name in names[:2]):
                        score += 5
                try:
                    outer = _safe_inner_text(item.locator("xpath=ancestor::*[self::button or self::a or @role='button'][1]")).lower()
                except Exception:
                    outer = ""
                if "recommend" in outer or "suggested" in outer:
                    score -= 10
                if score > best_score:
                    best = item
                    best_score = score
        except Exception:
            continue
    return best if best_score >= 5 else None


def _linkedin_profile_overflow_action_button(page, action: str, person_name: str = ""):
    """Open the profile header More menu and find Connect/Message if LinkedIn hides it there."""
    more = _linkedin_profile_action_text(page, "more", person_name)
    if not more:
        more = _linkedin_header_more_button(page)
    if not more:
        return None
    try:
        more.click(timeout=5000)
        _wait_short(page)
    except Exception:
        return None

    label = "Connect" if (action or "").lower() == "connect" else "Message"
    selectors = [
        f"div[role='menu'] div[role='menuitem']:has-text('{label}')",
        f"div[role='menu'] button:has-text('{label}')",
        f'div[role="menu"] :text-is("{label}")',
    ]
    return _first_visible(page, selectors)


def _linkedin_header_more_button(page):
    best = None
    best_score = -1
    try:
        loc = page.locator("main").get_by_text("More", exact=True)
        for i in range(min(loc.count(), 12)):
            item = loc.nth(i)
            try:
                if not item.is_visible(timeout=300):
                    continue
                box = item.bounding_box(timeout=300) or {}
            except Exception:
                continue
            y = float(box.get("y") or 9999)
            score = 10 if 80 <= y <= 520 else 0
            if score > best_score:
                best = item
                best_score = score
    except Exception:
        pass
    if best and best_score >= 5:
        return best
    return _first_visible(page, [
        "main section:has(h1) button:has-text('More')",
        "main section:has(h1) a[role='button']:has-text('More')",
    ])


def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = min(loc.count(), 8)
            for i in range(count):
                item = loc.nth(i)
                if item.is_visible(timeout=1000):
                    return item
        except Exception:
            continue
    return None


def _fit_linkedin_note(textarea, message: str) -> str:
    limit = 300
    try:
        raw = textarea.get_attribute("maxlength")
        if raw and raw.isdigit():
            limit = max(50, int(raw))
    except Exception:
        pass
    msg = " ".join((message or "").split())
    if len(msg) <= limit:
        return msg
    return msg[: max(0, limit - 3)].rstrip() + "..."


def _safe_inner_text(locator) -> str:
    try:
        return locator.inner_text(timeout=1500)
    except Exception:
        return ""


def _wait_short(page) -> None:
    try:
        page.wait_for_timeout(1200)
    except Exception:
        time.sleep(1.2)


def _linkedin_block_reason(page) -> str:
    """Return a concrete LinkedIn hard-block reason, or empty string if usable."""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if "/checkpoint/" in url or "checkpoint/challenge" in url:
        return f"checkpoint URL ({page.url})"
    if "/login" in url or "uas/login" in url:
        return f"login URL ({page.url})"
    if "challenge" in url and "linkedin.com" in url:
        return f"challenge URL ({page.url})"

    text = _safe_inner_text(page.locator("body")).lower()
    hard_signals = (
        "security verification",
        "verify your identity",
        "verification required",
        "we need to verify",
        "enter the code",
        "unusual activity",
        "temporarily restricted",
        "your account has been restricted",
        "let's do a quick security check",
        "please complete this security check",
    )
    for signal in hard_signals:
        if signal in text:
            return f"page text contains '{signal}'"
    return ""


def export_csv(path: Path | None = None) -> Path:
    path = path or OUTREACH_CSV_PATH
    rows = load_rows()
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in FIELDNAMES})
    return path


def export_markdown(path: Path | None = None) -> Path:
    path = path or OUTREACH_MD_PATH
    rows = load_rows()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("Company", "Unknown"), []).append(row)
    lines = ["# JobHuntrr LinkedIn Outreach Plan", "", f"Generated: {_now_iso()}", ""]
    for company, items in grouped.items():
        first = items[0]
        lines.extend([
            f"## {company}",
            "",
            f"- Category: {first.get('Company category', '')}",
            f"- Company priority: {first.get('Company priority score', '')}/10",
            f"- Careers: {first.get('Careers page URL', '')}",
            f"- Suggested roles: {first.get('Suggested role types', '')}",
            f"- Current roles: {first.get('Current relevant roles', '') or 'None found in local DB'}",
            "",
        ])
        for row in items:
            lines.extend([
                f"### {row.get('Person name') or 'Manual search lead'}",
                "",
                f"- Title: {row.get('Person title', '')}",
                f"- LinkedIn: {row.get('LinkedIn URL', '')}",
                f"- Category: {row.get('Person category', '')}",
                f"- Priority: {row.get('Person priority score', '')}/10",
                f"- Why: {row.get('Why this person', '')}",
                "",
                "**Connection message:**",
                "",
                row.get("LinkedIn connection message", ""),
                "",
                "**Follow-up after acceptance:**",
                "",
                row.get("Follow-up message after acceptance", ""),
                "",
            ])
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def build_outreach_plan(
    companies_text: str,
    *,
    run_focus: str = "",
    max_people_per_company: int = 8,
    use_public_search: bool = True,
    max_companies: int = 20,
) -> list[dict]:
    """Build and persist outreach rows. Safe: no LinkedIn sending or UI automation."""
    companies = parse_companies(companies_text)[:max(1, max_companies)]
    registry = _load_registry()
    rows: list[dict] = []
    for company in companies:
        logger.info("LinkedIn outreach: planning %s", company)
        info = _company_info(company, registry)
        roles = _current_roles_for_company(company)
        leads = _find_people_leads(
            company,
            max_people=max_people_per_company,
            use_public_search=use_public_search,
            run_focus=run_focus,
        )
        for lead in leads:
            rows.append(_build_row(info, lead, roles, run_focus))
    merged = merge_rows(rows)
    logger.info("LinkedIn outreach: saved %d total row(s)", len(merged))
    return merged


def _row_key(row: dict) -> str:
    url = (row.get("LinkedIn URL") or "").strip().lower()
    if url:
        return url
    return "|".join([
        (row.get("Company") or "").strip().lower(),
        (row.get("Person name") or "").strip().lower(),
        (row.get("Person title") or "").strip().lower(),
        (row.get("Person category") or "").strip().lower(),
    ])


def _load_registry() -> dict[str, dict]:
    if not EMPLOYER_REGISTRY_PATH.exists():
        return {}
    try:
        items = json.loads(EMPLOYER_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for item in items if isinstance(items, list) else []:
        name = item.get("name")
        if name:
            out[name.lower()] = item
    return out


def _company_info(company: str, registry: dict[str, dict]) -> dict:
    reg = registry.get(company.lower(), {})
    category = _company_category(company)
    return {
        "name": company,
        "category": category,
        "priority": _company_priority(company, category),
        "careers_url": reg.get("careers_url") or _guess_careers_url(company),
        "linkedin_page": _linkedin_company_search_url(company),
        "website": _guess_company_site(company),
        "why": _company_fit_reason(company, category),
        "roles": _suggested_role_types(category),
    }


def _company_category(company: str) -> str:
    c = company.lower()
    if any(k in c for k in ("lunate", "adq", "eia", "adio", "qia", "qatar development", "invest qatar", "qnb")):
        return "Investment / Sovereign / Strategic Capital"
    if any(k in c for k in ("g42", "core42", "presight", "aiq", "inception", "m42", "tii", "technology innovation", "aspire")):
        return "AI / Strategic Technology"
    if any(k in c for k in ("space42", "mbrsc", "space agency", "bayanat", "yahsat", "thuraya", "halcon", "edge")):
        return "Space / Defense / Geospatial"
    if any(k in c for k in ("brevan", "millennium", "point72", "squarepoint", "balyasny", "verition", "schonfeld", "exodus", "citadel", "jane street", "optiver", "imc", "drw")):
        return "Hedge Fund / Trading / Quant"
    return "Target Company"


def _company_priority(company: str, category: str) -> int:
    c = company.lower()
    if c in {"lunate", "adq", "g42", "core42", "presight", "space42", "brevan howard", "millennium", "qia"}:
        return 10
    if category in ("Investment / Sovereign / Strategic Capital", "Hedge Fund / Trading / Quant"):
        return 9
    if category in ("AI / Strategic Technology", "Space / Defense / Geospatial"):
        return 8
    return 7


def _company_fit_reason(company: str, category: str) -> str:
    if "Investment" in category:
        return f"{company} fits the investment/sovereign-backed path using the candidate's investment and quantitative analytics positioning."
    if "Hedge" in category:
        return f"{company} fits the quant/trading path where mathematics, Python, systematic research, and risk analytics are relevant."
    if "AI" in category:
        return f"{company} fits the AI/strategic technology path using software, quantitative modeling, and applied research positioning."
    if "Space" in category:
        return f"{company} fits the space/robotics/geospatial path using the candidate's relevant research and technical positioning."
    return f"{company} is relevant to the candidate's stated career targets."


def _suggested_role_types(category: str) -> str:
    if "Investment" in category:
        return "Investment Analyst; Portfolio Analyst; Strategy Analyst; Private Markets Analyst; Corporate Development"
    if "Hedge" in category:
        return "Quantitative Analyst; Trading Analyst; Portfolio Analytics; Quant Developer; Research Engineer"
    if "AI" in category:
        return "AI Engineer; Applied AI; Data Scientist; Research Engineer; Strategy Analyst"
    if "Space" in category:
        return "Space Systems Analyst; Robotics Engineer; Geospatial AI; Data Scientist; Strategy Analyst"
    return "Analyst; Strategy; AI/Data; Investment; Product"


def _guess_company_site(company: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", company.lower())
    if compact:
        return f"https://www.{compact}.com"
    return ""


def _guess_careers_url(company: str) -> str:
    site = _guess_company_site(company)
    return f"{site}/careers" if site else ""


def _linkedin_company_search_url(company: str) -> str:
    return "https://www.linkedin.com/search/results/companies/?keywords=" + quote_plus(company)


def _linkedin_people_search_url(company: str, category: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(
        f'{company} {category} UAE'
    )


def _current_roles_for_company(company: str) -> list[dict]:
    try:
        from storage.job_store import JobStore
        store = JobStore()
        jobs = store.list_jobs(search=company, limit=200, include_closed=False)
    except Exception:
        return []
    c = company.lower()
    relevant = []
    for job in jobs:
        text = f"{job.get('company','')} {job.get('title','')}".lower()
        if c in text:
            relevant.append(job)
    relevant.sort(key=lambda j: j.get("score") or 0, reverse=True)
    return relevant[:8]


def _roles_summary(roles: list[dict]) -> str:
    if not roles:
        return ""
    return "; ".join(
        f"{r.get('title')} ({r.get('score', 0)}/100, {r.get('decision')})"
        for r in roles[:5]
    )


def _find_people_leads(
    company: str,
    *,
    max_people: int,
    use_public_search: bool,
    run_focus: str,
) -> list[dict]:
    leads = []
    if use_public_search:
        try:
            from agents.web_signal_discovery import search_public_web
            for category, query_bits in PERSON_SEARCH_GROUPS:
                if len(leads) >= max_people:
                    break
                query = (
                    f'site:linkedin.com/in "{company}" ({query_bits}) '
                    f'("UAE" OR "Dubai" OR "Abu Dhabi" OR "ADGM" OR "DIFC" OR "Qatar" OR "Riyadh")'
                )
                if run_focus:
                    query += f" {run_focus[:120]}"
                for result in search_public_web(query, days_fresh=3650, limit=3):
                    lead = _lead_from_search_result(company, category, result)
                    if lead and _lead_key(lead) not in {_lead_key(x) for x in leads}:
                        leads.append(lead)
                        if len(leads) >= max_people:
                            break
        except Exception as exc:
            logger.info("Public LinkedIn people search unavailable for %s: %s", company, exc)

    if not leads:
        for category, _ in PERSON_SEARCH_GROUPS[:4]:
            leads.append({
                "name": "Manual LinkedIn search",
                "title": f"Search {category} people at {company}",
                "url": _linkedin_people_search_url(company, category),
                "category": category,
                "source": "manual_search",
                "snippet": "Open this LinkedIn people search and choose relevant profiles manually.",
            })
            if len(leads) >= max_people:
                break
    return leads[:max_people]


def _lead_key(lead: dict) -> str:
    return (lead.get("url") or f"{lead.get('name')}|{lead.get('title')}").lower()


def _lead_from_search_result(company: str, category: str, result: dict) -> dict | None:
    url = result.get("url") or ""
    if "linkedin.com/in/" not in url.lower():
        return None
    title = re.sub(r"\s*\|\s*LinkedIn.*$", "", result.get("title") or "", flags=re.I).strip()
    title = re.sub(r"\s+-\s+LinkedIn.*$", "", title, flags=re.I).strip()
    name, role = _split_linkedin_title(title, company)
    inferred = _infer_person_category(category, role or title)
    if inferred == "Archive":
        return None
    return {
        "name": name or "LinkedIn profile",
        "title": role or title or category,
        "url": url,
        "category": inferred,
        "source": result.get("provider") or "public_search",
        "snippet": result.get("snippet") or "",
    }


def _split_linkedin_title(title: str, company: str) -> tuple[str, str]:
    if not title:
        return "", ""
    for sep in (" - ", " – ", " | "):
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts[0], " - ".join(parts[1:])
    if company.lower() in title.lower():
        return title, ""
    return title, ""


def _infer_person_category(default: str, title: str) -> str:
    t = (title or "").lower()
    if any(k in t for k in ("ceo", "coo", "cfo", "chief executive", "chief financial", "founder", "board advisor")):
        return "Archive"
    if any(k in t for k in ("recruit", "talent", "people", "human capital", "emiratization", "early careers")):
        return "Recruiter / Talent"
    if any(k in t for k in ("investment", "portfolio", "private equity", "venture", "strategy", "corporate development")):
        return "Investment / Strategy"
    if (
        re.search(r"\bai\b|\bartificial intelligence\b", t)
        or any(k in t for k in ("data scientist", "machine learning", "research", "quant", "software"))
    ):
        return "AI / Technical / Research"
    if any(k in t for k in ("space", "robot", "geospatial", "satellite", "remote sensing")):
        return "Space / Robotics / Geospatial"
    if any(k in t for k in ("cio", "chief", "partner", "managing director")):
        return "Archive"
    return default


def _person_priority(lead: dict, company_info: dict) -> int:
    score = 4
    cat = lead.get("category", "")
    title = (lead.get("title") or "").lower()
    if cat == "Recruiter / Talent":
        score += 3
    if cat in ("Investment / Strategy", "AI / Technical / Research", "Space / Robotics / Geospatial"):
        score += 2
    if any(k in title for k in ("uae", "dubai", "abu dhabi", "adgm", "difc", "qatar", "riyadh")):
        score += 1
    if any(k in title for k in ("analyst", "associate", "manager", "scientist", "engineer", "researcher")):
        score += 1
    if any(k in title for k in ("ceo", "coo", "cio", "chief", "founder")):
        score -= 4
    if lead.get("source") == "manual_search":
        score -= 1
    if company_info.get("priority", 0) >= 9:
        score += 1
    return max(1, min(10, score))


def _message_type(category: str, title: str) -> str:
    t = (title or "").lower()
    if category == "Recruiter / Talent":
        return "recruiter"
    if category == "Investment / Strategy":
        if any(k in t for k in ("analyst", "associate")):
            return "peer"
        return "investment"
    if category == "Space / Robotics / Geospatial":
        return "space"
    if category == "AI / Technical / Research":
        return "technical"
    return "peer"


def _area_for_company(company_info: dict) -> str:
    category = company_info.get("category", "")
    if "Investment" in category:
        return "investments and strategy"
    if "Hedge" in category:
        return "quant, trading, and research"
    if "AI" in category:
        return "AI and strategic technology"
    if "Space" in category:
        return "space, robotics, and geospatial AI"
    return "investment, AI, quant, and strategy"


def _get_application_qa() -> dict:
    try:
        from config.config import APPLICATION_QA
        return dict(APPLICATION_QA or {})
    except Exception:
        return {}


def _load_profile_meta() -> dict:
    try:
        from config.md_loader import PROFILE_PATH, _parse_frontmatter
        if PROFILE_PATH.exists():
            meta, _ = _parse_frontmatter(PROFILE_PATH.read_text(encoding="utf-8"))
            return meta if isinstance(meta, dict) else {}
    except Exception:
        pass
    return {}


def _outreach_identity() -> dict:
    """Candidate name and sign-off from profile settings / profile frontmatter."""
    qa = _get_application_qa()
    meta = _load_profile_meta()
    full = (qa.get("full_name") or meta.get("name") or "").strip()
    first = (qa.get("first_name") or "").strip()
    if not first and full:
        first = full.split()[0]
    sign_off = f"Best,\n{first}" if first else "Best"
    return {
        "first_name": first or "there",
        "full_name": full or "the candidate",
        "sign_off": sign_off,
    }


def _profile_section_snippet(section_titles: tuple[str, ...], max_chars: int = 220) -> str:
    try:
        from config.profile_grounding import get_profile_excerpt
        text = get_profile_excerpt(8000)
    except Exception:
        return ""
    if not text:
        return ""
    for title in section_titles:
        pattern = rf"(?is)##\s*{re.escape(title)}\s*\n+(.*?)(?=\n##|\Z)"
        match = re.search(pattern, text)
        if not match:
            continue
        chunk = _normalize_space(match.group(1))
        sentences = re.split(r"(?<=[.!?])\s+", chunk)
        out = " ".join(s for s in sentences[:2] if s).strip()
        if out:
            return out[:max_chars].rstrip()
    return ""


def _credential_summary() -> str:
    qa = _get_application_qa()
    meta = _load_profile_meta()
    pieces: list[str] = []
    nat = (qa.get("nationality") or meta.get("nationality") or "").strip()
    if nat:
        pieces.append(nat)
    degree = (qa.get("degree_field") or "").strip()
    uni = (qa.get("university") or "").strip()
    grad = (qa.get("graduation_year") or "").strip()
    if degree and uni:
        edu = f"{degree} graduate from {uni}"
        if grad:
            edu += f" ({grad})"
        pieces.append(edu)
    elif degree:
        pieces.append(degree)
    years = (qa.get("years_experience") or "").strip()
    if years:
        pieces.append(f"{years} of experience")
    hook = _profile_section_snippet(
        ("Core Identity", "Core identity", "Professional summary", "Summary")
    )
    if hook:
        base = ", ".join(pieces)
        return f"{base}. {hook}" if base else hook
    if pieces:
        return ", ".join(pieces)
    return "with a background described in my profile"


def _outreach_message_bullets() -> str:
    """Compact credential hints for the external LLM prompt."""
    qa = _get_application_qa()
    meta = _load_profile_meta()
    bullets: list[str] = []
    for label, key, meta_key in (
        ("Nationality", "nationality", "nationality"),
        ("Education", "degree_field", None),
        ("University", "university", None),
        ("Experience", "years_experience", None),
        ("Languages", "languages", "languages"),
        ("Location", "location", None),
    ):
        val = (qa.get(key) or (meta.get(meta_key) if meta_key else "") or "").strip()
        if val:
            bullets.append(f"{label}: {val}")
    positioning = _profile_section_snippet(
        ("Core Identity", "Core identity", "Strongest skills", "Experience")
    )
    if positioning:
        bullets.append(f"Positioning: {positioning[:180]}")
    return "; ".join(bullets) if bullets else "Use the candidate profile section — do not invent credentials"


def _intro_opening() -> str:
    ident = _outreach_identity()
    return f"Hope you're doing well. I'm {ident['full_name']}, {_credential_summary()}."


def _background_phrase() -> str:
    """Short phrase for follow-ups referencing the candidate's background."""
    cred = _credential_summary()
    if len(cred) > 200:
        return cred[:197].rstrip() + "..."
    return cred


def _technical_highlight() -> str:
    snippet = _profile_section_snippet(
        ("Experience", "Tools & technical foundation", "Strongest skills"),
        max_chars=180,
    )
    return snippet or "technical and research experience from my profile"


def _space_highlight() -> str:
    try:
        from config.profile_grounding import get_profile_excerpt
        text = (get_profile_excerpt(8000) or "").lower()
    except Exception:
        text = ""
    if any(k in text for k in ("space", "robot", "geospatial", "satellite", "astrobee")):
        return _profile_section_snippet(("Experience", "Core Identity", "Core identity"), max_chars=180)
    return "space, robotics, geospatial AI, and strategic technology"


def _connection_message(name: str, company: str, category: str, title: str, company_info: dict) -> str:
    first = _first_name(name)
    area = _area_for_company(company_info)
    msg_type = _message_type(category, title)
    intro = _intro_opening()
    sign_off = _outreach_identity()["sign_off"]
    if msg_type == "recruiter":
        return (
            f"Hi {first},\n\n"
            f"{intro}\n\n"
            f"I'm exploring opportunities in {area} and thought my background may be relevant to {company}. "
            "Would love to connect and learn if there are any current or upcoming roles that could be a fit.\n\n"
            "Looking forward to connecting.\n\n"
            f"{sign_off}"
        )
    if msg_type == "investment":
        return (
            f"Hi {first},\n\n"
            f"{intro}\n\n"
            f"I've been following {company}'s growth and would love to connect and learn more about your "
            "experience there and any advice you might have for someone looking to build a career in investments.\n\n"
            "Looking forward to connecting.\n\n"
            f"{sign_off}"
        )
    if msg_type == "technical":
        return (
            f"Hi {first},\n\n"
            f"{intro}\n\n"
            f"I've been following {company}'s work in {area} and would love to connect and learn more about "
            "your experience there.\n\n"
            "Looking forward to connecting.\n\n"
            f"{sign_off}"
        )
    if msg_type == "space":
        highlight = _space_highlight()
        return (
            f"Hi {first},\n\n"
            f"{intro}\n\n"
            f"I'm interested in {highlight}, and your work at {company} stood out to me. "
            "Would love to connect and learn more about your experience there.\n\n"
            "Looking forward to connecting.\n\n"
            f"{sign_off}"
        )
    return (
        f"Hi {first},\n\n"
        f"{intro}\n\n"
        f"I've been following {company}'s growth and would love to connect and learn more about your "
        f"experience there and any advice you might have for someone looking to build a career in {area}.\n\n"
        "Looking forward to connecting.\n\n"
        f"{sign_off}"
    )


def _followup_message(name: str, company: str, category: str, title: str) -> str:
    first = _first_name(name)
    msg_type = _message_type(category, title)
    background = _background_phrase()
    if msg_type == "recruiter":
        return (
            f"Thanks for connecting, {first}. I really appreciate it.\n\n"
            f"I'm currently exploring roles where my background ({background}) could be useful. "
            f"If there are any teams at {company} hiring for analyst, investment, AI, data, or strategy profiles, "
            "I'd be grateful for any guidance on where to apply or who to speak with."
        )
    if msg_type == "investment" or msg_type == "peer":
        return (
            f"Thanks for connecting, {first}. I appreciate it.\n\n"
            f"I'm trying to understand where someone with my background ({background}) could best fit "
            "in the investment space. Would you have any advice on which types of teams or roles I should focus on?"
        )
    if msg_type == "space":
        highlight = _space_highlight()
        return (
            f"Thanks for connecting, {first}. I appreciate it.\n\n"
            f"My background includes work related to {highlight}, and I'm looking to continue in that direction. "
            "Would you have any advice on where my background might fit best?"
        )
    tech = _technical_highlight()
    return (
        f"Thanks for connecting, {first}. I appreciate it.\n\n"
        f"I'm interested in roles where my skills intersect ({tech}). Given your work at {company}, "
        "I'd be grateful for any advice on what technical or research teams might be most relevant for someone with my background."
    )


def _first_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name == "Manual LinkedIn search":
        return "there"
    return name.split()[0]


def _build_row(company_info: dict, lead: dict, roles: list[dict], run_focus: str) -> dict:
    company = company_info["name"]
    category = lead.get("category") or "Recruiter / Talent"
    title = lead.get("title") or ""
    score = _person_priority(lead, company_info)
    role_summary = _roles_summary(roles)
    angle = _message_angle(category, company_info, run_focus)
    row = {
        "id": str(uuid.uuid4()),
        "Company": company,
        "Company category": company_info["category"],
        "Company priority score": company_info["priority"],
        "Person name": lead.get("name", ""),
        "Person title": title,
        "LinkedIn URL": lead.get("url", ""),
        "Person category": category,
        "Person priority score": score,
        "Why this person": _why_person(lead, company_info),
        "Message angle": angle,
        "LinkedIn connection message": _connection_message(
            lead.get("name", ""), company, category, title, company_info
        ),
        "Follow-up message after acceptance": _followup_message(
            lead.get("name", ""), company, category, title
        ),
        "Suggested role types": company_info["roles"],
        "Careers page URL": company_info["careers_url"],
        "Current relevant roles": role_summary,
        "Outreach status": "Not sent",
        "Date messaged": "",
        "Date accepted": "",
        "Date followed up": "",
        "Reply status": "",
        "Notes": lead.get("snippet", ""),
    }
    return row


def _why_person(lead: dict, company_info: dict) -> str:
    if lead.get("source") == "manual_search":
        return "Manual LinkedIn people-search lead; review profiles and choose the strongest match before sending."
    category = lead.get("category", "")
    if category == "Recruiter / Talent":
        return "Likely hiring/recruiting contact for relevant roles at a target company."
    if category == "Investment / Strategy":
        return "Relevant investment/strategy professional who may provide advice or route to the right team."
    if category == "AI / Technical / Research":
        return "Relevant technical/research professional for AI, data, quant, or research-oriented roles."
    if category == "Space / Robotics / Geospatial":
        return "Relevant to the candidate's space, robotics, and geospatial interests."
    return company_info.get("why", "")


def _message_angle(category: str, company_info: dict, run_focus: str) -> str:
    if run_focus:
        return f"{run_focus.strip()[:180]} | {_area_for_company(company_info)}"
    if category == "Recruiter / Talent":
        return "Ask about fit for current/upcoming analyst, investment, AI, data, or strategy roles."
    if category == "Investment / Strategy":
        return "Ask for advice on entering investment/strategy teams using the candidate's profile positioning."
    if category == "AI / Technical / Research":
        return "Use software, AI, quantitative modeling, and research background from the profile."
    if category == "Space / Robotics / Geospatial":
        return "Use strongest space/robotics experience from the candidate profile."
    return "Ask for advice and context; keep the message natural, specific, and low-pressure."


# ── External lead discovery (LLM handoff) ─────────────────────────────────────

CSV_IMPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "LinkedIn URL": (
        "linkedin url", "linkedin_url", "url", "profile url", "profile_url", "linkedin",
    ),
    "Person name": ("person name", "person_name", "name", "full name", "full_name"),
    "Person title": ("person title", "person_title", "title", "job title", "job_title"),
    "Company": ("company", "company name", "company_name", "organization"),
    "Person category": (
        "person category", "person_category", "category", "contact type", "contact_type",
    ),
    "LinkedIn connection message": (
        "linkedin connection message", "connection message", "connection_message",
        "message", "connection note", "connection_note", "linkedin message",
    ),
    "Follow-up message after acceptance": (
        "follow-up message after acceptance", "follow-up message", "followup message",
        "follow_up_message", "followup", "follow-up",
    ),
    "Why this person": ("why this person", "why_this_person", "why", "rationale"),
    "Message angle": ("message angle", "message_angle", "angle"),
    "Outreach status": ("outreach status", "outreach_status", "status"),
    "Notes": ("notes", "note", "comments"),
}

_ALIAS_TO_FIELD: dict[str, str] = {}
for _field, _aliases in CSV_IMPORT_ALIASES.items():
    _ALIAS_TO_FIELD[_field.strip().lower()] = _field
    for _alias in _aliases:
        _ALIAS_TO_FIELD[_alias.strip().lower()] = _field


def _canonical_csv_field(header: str) -> str:
    key = (header or "").strip().lower()
    if not key:
        return ""
    if key in _ALIAS_TO_FIELD:
        return _ALIAS_TO_FIELD[key]
    for field in FIELDNAMES:
        if field.lower() == key:
            return field
    return ""


def _map_csv_row(raw: dict) -> dict:
    """Map arbitrary CSV column names to canonical outreach FIELDNAMES."""
    mapped: dict = {}
    for header, value in (raw or {}).items():
        field = _canonical_csv_field(str(header))
        if not field or field == "id":
            continue
        mapped[field] = (value or "").strip() if isinstance(value, str) else value
    url = _clean_linkedin_profile_url(str(mapped.get("LinkedIn URL") or ""))
    if url:
        mapped["LinkedIn URL"] = url
    return mapped


def _finalize_import_row(mapped: dict, *, default_company: str = "", run_focus: str = "") -> dict | None:
    url = _clean_linkedin_profile_url(str(mapped.get("LinkedIn URL") or ""))
    if not url:
        return None
    row = {field: "" for field in FIELDNAMES}
    row.update(mapped)
    row["LinkedIn URL"] = url
    if not (row.get("Company") or "").strip() and default_company:
        row["Company"] = default_company.strip()
    if not (row.get("Person category") or "").strip():
        row["Person category"] = "Recruiter / Talent"
    if not (row.get("Outreach status") or "").strip():
        row["Outreach status"] = "Not sent"
    if run_focus and not (row.get("Message angle") or "").strip():
        row["Message angle"] = run_focus.strip()[:180]
    if not row.get("id"):
        row["id"] = str(uuid.uuid4())
    for score_field in ("Company priority score", "Person priority score"):
        try:
            row[score_field] = int(row.get(score_field) or 0)
        except (TypeError, ValueError):
            row[score_field] = 0
    return row


def import_outreach_rows(
    raw_rows: list[dict],
    *,
    run_focus: str = "",
    default_company: str = "",
) -> tuple[list[dict], str]:
    """Import outreach rows from dicts (URL paste or pre-mapped CSV rows)."""
    if not raw_rows:
        return [], "No rows to import"

    prepared: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        mapped = _map_csv_row(raw)
        row = _finalize_import_row(
            mapped,
            default_company=default_company,
            run_focus=run_focus,
        )
        if row is None:
            skipped += 1
            continue
        prepared.append(row)

    if not prepared:
        return [], f"No valid rows imported ({skipped} skipped — missing LinkedIn URL)"

    before = len(load_rows())
    merged = merge_rows(prepared)
    added = len(merged) - before
    status = f"Imported {len(prepared)} row(s)"
    if skipped:
        status += f", {skipped} skipped (no LinkedIn URL)"
    if added < len(prepared):
        status += f"; {len(prepared) - added} merged with existing"
    status += f"; {len(merged)} total in outreach list"
    return prepared, status


def import_outreach_csv(
    path: str | Path,
    *,
    run_focus: str = "",
    default_company: str = "",
) -> tuple[list[dict], str]:
    """Read an external LLM CSV and merge into linkedin_outreach.json."""
    csv_path = Path(path)
    if not csv_path.exists():
        return [], f"CSV not found: {csv_path}"

    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return [], f"Could not read CSV: {exc}"

    # Strip markdown code fences if the LLM wrapped the output.
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)

    try:
        reader = csv.DictReader(text.splitlines())
        raw_rows = list(reader)
    except Exception as exc:
        return [], f"Invalid CSV: {exc}"

    if not raw_rows:
        return [], "CSV has no data rows"

    return import_outreach_rows(
        raw_rows,
        run_focus=run_focus,
        default_company=default_company,
    )


def generate_lead_discovery_prompt(
    companies_text: str,
    *,
    run_focus: str = "",
    max_people_per_company: int = 8,
) -> str:
    """
    Build a copy-paste prompt for an external LLM (ChatGPT, Claude, etc.) to
    find LinkedIn contacts and draft tailored connection messages. No browser use.
    """
    companies = parse_companies(companies_text)
    focus = (run_focus or "").strip()
    max_people = max(1, min(15, int(max_people_per_company or 8)))

    profile_excerpt = ""
    try:
        from config.profile_grounding import get_profile_excerpt
        profile_excerpt = get_profile_excerpt(3500)
    except Exception:
        profile_excerpt = "(Profile excerpt unavailable — paste your profile manually.)"

    company_block = "\n".join(f"- {c}" for c in companies) if companies else "- (add target companies)"
    category_lines = "\n".join(
        f"  - **{cat}**: search for {bits[:120]}..."
        if len(bits) > 120
        else f"  - **{cat}**: search for {bits}"
        for cat, bits in PERSON_SEARCH_GROUPS
    )

    sample_connection = _connection_message(
        "Sarah", companies[0] if companies else "Target Company",
        "Recruiter / Talent", "Talent Acquisition Manager",
        _company_info(companies[0] if companies else "Target Company", _load_registry()),
    )

    ident = _outreach_identity()
    message_bullets = _outreach_message_bullets()
    sign_off_hint = ident["sign_off"].replace("\n", "\\n")
    focus_line = f"\n**Campaign focus:** {focus}\n" if focus else ""

    return f"""You are helping with LinkedIn outreach for a job search. Find real LinkedIn profile URLs for relevant people at the target companies, then write tailored connection messages.

## Candidate profile
{profile_excerpt}

## Target companies (up to {max_people} people per company, prioritize quality over quantity)
{company_block}
{focus_line}
## Who to find at each company
{category_lines}

Priority order:
1. Recruiters / talent acquisition / early careers (best for warm intros)
2. Investment / strategy / portfolio professionals (advice + routing)
3. AI / data / research / quant professionals (technical fit)
4. Space / robotics / geospatial professionals (if relevant to company)

Skip: CEOs, CFOs, founders, board advisors, managing directors — too senior for cold outreach.

Geography preference: prioritize locations and regions listed in the candidate profile and requirements.

## Message style
- Natural, specific, low-pressure — not salesy
- First name greeting; sign off "{sign_off_hint}"
- Connection notes must be ≤300 characters (LinkedIn limit)
- Credential highlights to weave in (from profile only): {message_bullets}
- Tailor the angle to the person's role and company category
- Do NOT invent experience the candidate does not have

Example connection message tone:
---
{sample_connection}
---

## Output format

Return a CSV with EXACTLY these column headers on the first row, then one row per person:

Company,Company category,Company priority score,Person name,Person title,LinkedIn URL,Person category,Person priority score,Why this person,Message angle,LinkedIn connection message,Follow-up message after acceptance,Suggested role types,Careers page URL,Current relevant roles

Rules:
- LinkedIn URL must be a real linkedin.com/in/ URL — omit the person if you cannot find one
- Connection message: ≤300 characters, warm, specific, references the candidate's background
- Follow-up message: sent after they accept the connection; offer CV / short call
- Company priority score: 1-10
- Person priority score: 1-10
- Return ONLY the CSV rows (including header). No markdown fences, no explanations.
"""

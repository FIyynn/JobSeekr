from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag


BASE_URL = "https://www.linkedin.com"
MIDDOT_SPLIT = r"\s*(?:·|•|Â·|â€¢)\s*"


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("Â·", "·").replace("â€¢", "•").replace("â€”", "—").replace("â€™", "'")
    return re.sub(r"\s+", " ", text).strip()


def _text(node: Tag | None, separator: str = " ") -> str:
    if node is None:
        return ""
    return _clean(node.get_text(separator, strip=True))


def _block_text(node: Tag | None) -> str:
    if node is None:
        return ""
    text = node.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def _absolute_link(href: str) -> str:
    if not href:
        return ""
    return urljoin(BASE_URL, href)


def _linkedin_path(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        parsed = urlparse(href)
        if "linkedin.com" in parsed.netloc:
            path = parsed.path or ""
            if parsed.query:
                path += f"?{parsed.query}"
            if parsed.fragment:
                path += f"#{parsed.fragment}"
            return path
    return href


def _company_path(href: str) -> str:
    path = _linkedin_path(href)
    if "/company/" in path and "?" not in path and not path.endswith("/"):
        return f"{path}/"
    return path


def _job_id_from_url(url: str) -> str:
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return match.group(1) if match else ""


def _split_middot_text(text: str) -> list[str]:
    cleaned = _clean(text)
    if not cleaned:
        return []
    return [part.strip() for part in re.split(MIDDOT_SPLIT, cleaned) if part.strip()]


def _first_text_match(text: str, patterns: tuple[str, ...]) -> str:
    cleaned = _clean(text)
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return _clean(match.group(0))
    return ""


def _listed_on_from_text(text: str, now: datetime | None = None) -> str | None:
    cleaned = _clean(text)
    if not cleaned:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    relative = re.search(
        r"\b(?:(?P<count>\d+)\s+)?(?P<unit>minute|hour|day|week|month|year)s?\s+ago\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if relative:
        count = int(relative.group("count") or 1)
        unit = relative.group("unit").lower()
        if unit == "minute":
            delta = timedelta(minutes=count)
        elif unit == "hour":
            delta = timedelta(hours=count)
        elif unit == "day":
            delta = timedelta(days=count)
        elif unit == "week":
            delta = timedelta(weeks=count)
        elif unit == "month":
            delta = timedelta(days=30 * count)
        else:
            delta = timedelta(days=365 * count)
        return (current - delta).isoformat()

    if re.search(r"\byesterday\b", cleaned, flags=re.IGNORECASE):
        return (current - timedelta(days=1)).isoformat()

    if re.search(r"\btoday\b", cleaned, flags=re.IGNORECASE):
        return current.isoformat()

    return None


def _find_heading_section(root: Tag, heading_text: str) -> Tag | None:
    needle = _clean(heading_text).casefold()
    for candidate in root.select("section, div.job-details-module, div.jobs-company__box, div.jobs-description__container"):
        heading = candidate.find(["h2", "h3"])
        if heading and needle in _text(heading).casefold():
            return candidate
    return None


def _select_first(root: Tag, selectors: tuple[str, ...]) -> Tag | None:
    for selector in selectors:
        node = root.select_one(selector)
        if node is not None:
            return node
    return None


def _direct_text(node: Tag | None) -> str:
    if node is None:
        return ""
    parts: list[str] = []
    for child in node.contents:
        if isinstance(child, NavigableString):
            text = _clean(str(child))
            if text:
                parts.append(text)
    return _clean(" ".join(parts))


def _parse_top_card(root: Tag, now: datetime | None = None) -> dict[str, Any]:
    top_card = root.select_one(".job-details-jobs-unified-top-card, .jobs-details__main-content, .job-view-layout") or root

    title_node = _select_first(
        top_card,
        (
            ".job-details-jobs-unified-top-card__job-title h1 a",
            ".job-details-jobs-unified-top-card__job-title h2 a",
            ".job-details-jobs-unified-top-card__job-title h2",
            "h1 a",
            "h2 a",
            "h1",
            "h2",
        ),
    )
    company_node = _select_first(
        top_card,
        (
            ".job-details-jobs-unified-top-card__company-name a",
            ".job-details-jobs-unified-top-card__company-name",
        ),
    )
    title_link = title_node if title_node and title_node.name == "a" else title_node.find_parent("a") if title_node else None
    company_link = company_node if company_node and company_node.name == "a" else company_node.find_parent("a") if company_node else None
    title = _text(title_node)
    listing_url = _linkedin_path(title_link.get("href", "")) if title_link else ""
    company = _text(company_node) or _text(top_card.select_one(".job-details-jobs-unified-top-card__company-name"))
    company_about_link = root.select_one("a[data-view-name='job-details-about-company-name-link']")
    if company_about_link is None:
        company_about_link = root.select_one("section.jobs-company a[href*='/company/']")
    company_url = _company_path(company_about_link.get("href", "")) if company_about_link else _company_path(company_link.get("href", "")) if company_link else ""
    if not company_url:
        company_about = top_card.select_one("a[data-view-name='job-details-about-company-name-link'], a[href*='/company/']")
        if company_about:
            company_url = _company_path(company_about.get("href", ""))

    sticky_node = top_card.select_one(".job-details-jobs-unified-top-card__sticky-header .t-14.truncate")
    sticky_parts = _split_middot_text(_text(sticky_node))
    if sticky_parts and company and sticky_parts[0].casefold() == company.casefold():
        sticky_parts = sticky_parts[1:]

    location = sticky_parts[0] if sticky_parts else ""
    location = re.sub(r"\s*\(.*\)\s*$", "", location).strip()
    posted_at = ""
    apply_activity = ""
    for part in sticky_parts[1:]:
        if not posted_at and re.search(r"\b(today|yesterday|\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago)\b", part, flags=re.IGNORECASE):
            posted_at = part
            continue
        if not apply_activity:
            apply_activity = part

    tertiary_node = top_card.select_one(".job-details-jobs-unified-top-card__tertiary-description-container")
    tertiary_text = _text(tertiary_node)
    if not posted_at:
        posted_at = _first_text_match(
            tertiary_text,
            (r"\b\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago\b", r"\byesterday\b", r"\btoday\b"),
        )
    if not apply_activity:
        apply_activity = _first_text_match(
            tertiary_text,
            (
                r"\b(?:Over\s+)?\d+\+?\s+people clicked apply\b",
                r"\b(?:Over\s+)?\d+\+?\s+applicants\b",
                r"\b\d+\+?\s+people clicked apply\b",
                r"\b\d+\+?\s+applicants\b",
            ),
        )
    if not apply_activity and ("clicked apply" in tertiary_text.lower() or "applicants" in tertiary_text.lower()):
        apply_activity = _clean(tertiary_text)

    listed_on = _listed_on_from_text(posted_at, now=now) if posted_at else None

    promotion_status = "Promoted by hirer" if "Promoted by hirer" in tertiary_text else ""
    application_management = "Responses managed off LinkedIn" if "Responses managed off LinkedIn" in tertiary_text else ""
    response_insights = "No response insights available yet" if "No response insights available yet" in _clean(root.get_text(" ", strip=True)) else ""

    listing_preferences = [
        _text(button)
        for button in top_card.select(".job-details-fit-level-preferences button")
        if _text(button)
    ]

    apply_button = top_card.select_one(
        ".jobs-apply-button--top-card .jobs-apply-button, .jobs-apply-button--top-card button, .jobs-apply-button"
    )
    save_button = top_card.select_one(".jobs-save-button")
    missing_required_qualifications = bool(
        top_card.find(string=re.compile(r"missing required qualifications", flags=re.IGNORECASE))
        or top_card.select_one(".job-details-fit-level-card__guide-entry-points--free")
    )

    job_id = _job_id_from_url(listing_url)
    if not job_id:
        match = re.search(r"/jobs/view/(\d+)", str(top_card))
        if match:
            job_id = match.group(1)

    return {
        "listing_url": listing_url,
        "job_id": job_id,
        "title": title,
        "company": company,
        "company_url": company_url,
        "location": location,
        "posted_at": posted_at,
        "listed_on": listed_on,
        "apply_activity": apply_activity,
        "promotion_status": promotion_status,
        "application_management": application_management,
        "response_insights": response_insights,
        "listing_preferences": listing_preferences,
        "apply_button_xpath": ".//button[contains(@class, 'jobs-apply-button')]" if apply_button else "",
        "save_button_xpath": ".//button[contains(@class, 'jobs-save-button')]" if save_button else "",
        "missing_required_qualifications": missing_required_qualifications,
        "missing required qualifications?": missing_required_qualifications,
        "company_logo_url": "",
    }


def _parse_hiring_team(root: Tag) -> list[dict[str, Any]]:
    section = _find_heading_section(root, "Meet the hiring team")
    if section is None:
        return []

    team: list[dict[str, Any]] = []
    for card in section.select("div.display-flex.align-items-center.mt4"):
        link = card.select_one("a[href*='/in/']")
        name_node = card.select_one(".jobs-poster__name")
        connection_node = card.select_one(".hirer-card__connection-degree")
        headline_node = card.select_one(".linked-area .text-body-small")
        role_node = card.select_one(".hirer-card__job-poster")
        message_button = card.select_one("button")

        if not link and not name_node:
            continue

        name = _text(name_node)
        if not name and link:
            name = _clean(link.get("aria-label") or link.get_text(" ", strip=True))
            name = re.sub(r"^(View|Show)\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"['’]s.*$", "", name).strip()

        team.append(
            {
                "name": name,
                "profile_url": _absolute_link(link.get("href", "")) if link else "",
                "connection_degree": _text(connection_node),
                "headline": _text(headline_node),
                "role_label": _text(role_node),
                "message_button_xpath": ".//button[normalize-space(.)='Message']" if message_button else "",
            }
        )
    return team


def _parse_job_description(root: Tag) -> dict[str, Any]:
    section = _find_heading_section(root, "About the job")
    if section is None:
        section = root.select_one(".jobs-description__container, .jobs-description-content, .jobs-box__html-content") or root

    body = section.select_one(".mt4, .jobs-description-content__text, .jobs-description__content")
    raw_text = _block_text(body or section)
    if raw_text.startswith("About the job\n"):
        raw_text = raw_text[len("About the job\n") :].strip()
    return {
        "raw_text": raw_text,
    }


def _parse_company_profile(root: Tag) -> dict[str, Any]:
    section = _find_heading_section(root, "About the company")
    if section is None:
        return {}

    logo_link = section.select_one("a[href*='/company/']")
    logo_img = section.select_one("a[href*='/company/'] img, img[alt*='company logo'], img[title]")
    name_link = section.select_one("a[data-view-name='job-details-about-company-name-link'], .artdeco-entity-lockup__title a")
    subtitle = section.select_one(".artdeco-entity-lockup__subtitle")
    company_description_node = section.select_one(".jobs-company__company-description .inline-show-more-text, .jobs-company__company-description, .inline-show-more-text")
    industry_block = section.select_one(".t-14.mt5")

    industry = ""
    size = ""
    linkedin_employee_count = ""
    if industry_block:
        direct_text = _direct_text(industry_block)
        if direct_text:
            industry = direct_text
        span_texts = [_text(span) for span in industry_block.find_all("span", recursive=False)]
        span_texts = [text for text in span_texts if text]
        if span_texts:
            if len(span_texts) >= 1:
                size = span_texts[0]
            if len(span_texts) >= 2:
                linkedin_employee_count = span_texts[1]

    company_description = _block_text(company_description_node)
    company_description = company_description.replace("show more", "").strip()

    return {
        "name": _text(name_link),
        "url": _company_path(name_link.get("href", "")) if name_link else _company_path(logo_link.get("href", "")) if logo_link else "",
        "followers": _text(subtitle),
        "industry": industry,
        "size": size,
        "linkedin_employee_count": linkedin_employee_count,
        "description": company_description,
        "company_logo_url": logo_img.get("src", "") if logo_img else "",
    }


def parse_listing_detail(html: str, verbose: bool = True, now: datetime | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.select_one("div.jobs-search__job-details--wrapper") or soup

    top_card = _parse_top_card(root, now=now)
    company_profile = _parse_company_profile(root)
    if not top_card.get("company_logo_url") and company_profile.get("company_logo_url"):
        top_card["company_logo_url"] = company_profile["company_logo_url"]

    raw_text = _clean(root.get_text(" ", strip=True))

    return {
        "source": "linkedin",
        **top_card,
        "company_profile": company_profile,
        "hiring_team": _parse_hiring_team(root),
        "job_description": _parse_job_description(root),
        "raw_text": raw_text,
    }

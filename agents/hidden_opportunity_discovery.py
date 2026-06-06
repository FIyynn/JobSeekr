"""
Hidden Opportunity Discovery — finds hiring signals outside job boards.

Sources:
  - DuckDuckGo search (free, no API key) for indexed LinkedIn posts
  - Public web search snippets for company/recruiter hiring posts
  - Manual paste import (URL, text, or screenshot transcript)

Signal strength:
  HIGH   — recruiter asks for CVs, "DM me", "happy to refer", team expansion stated
  MEDIUM — company expansion, new office/fund/team, hiring intent implied
  LOW    — generic growth announcement, no clear CTA, stale post
"""

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("hidden_opportunity_discovery")

from engine.signal_detector import discover_all_signals

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
SIGNALS_DB = DATA_DIR / "signals.db"

# ── Signal keyword lists ────────────────────────────────────────────────────

HIGH_SIGNAL_CTAS = [
    "dm me", "direct message", "message me", "send your cv", "send me your cv",
    "send your resume", "send cv", "happy to refer", "i can refer", "reach out directly",
    "email me", "share your cv", "share your profile", "please share your profile",
    "send your cv to me", "message me directly",
]

HIGH_SIGNAL_HIRING_WORDS = [
    "we're hiring", "we are hiring", "open role", "open roles", "hiring for",
    "looking for a", "looking for an", "vacancies", "join our team", "apply now",
    "referrals welcome", "building a team", "growing our team", "expanding our team",
]

MEDIUM_SIGNAL_WORDS = [
    "excited to grow", "growing our", "building our", "expanding our", "launching a new",
    "new office", "new business unit", "new fund", "new strategy", "team expansion",
    "scaling rapidly", "scaling our", "strengthening our", "opening our",
    "expanding in abu dhabi", "expanding in dubai", "growing our uae", "growing our gcc",
    "building our adgm", "building our difc", "opening our middle east",
]

UAE_NATIONAL_WORDS = [
    "uae national", "uaen", "emiratization", "emiratised", "emiratized",
    "emirati", "uae nationals", "national graduate", "nationalisation",
]

RECRUITER_TITLES = [
    "talent acquisition", "recruiter", "recruitment", "hr business partner",
    "talent partner", "graduate recruitment", "early careers", "emiratization",
    "people partner", "head of talent",
]

HIRING_MANAGER_TITLES = [
    "hiring manager", "engineering manager", "head of engineering", "head of ai",
    "investment director", "portfolio manager", "team lead", "principal",
    "managing director", "vice president", "associate director", "head of",
]

# ── Search query templates ──────────────────────────────────────────────────

_BASE_QUERIES = [
    'site:linkedin.com/posts "DM me" "Abu Dhabi" hiring',
    'site:linkedin.com/posts "send your CV" "Abu Dhabi" OR "Dubai"',
    'site:linkedin.com/posts "happy to refer" "Abu Dhabi" OR "UAE"',
    'site:linkedin.com/posts "we\'re hiring" "UAE National" OR "UAEN" OR "Emiratization"',
    'site:linkedin.com/posts "open role" "Abu Dhabi" OR "ADGM" OR "DIFC"',
    'site:linkedin.com/posts "growing our" "Abu Dhabi" team',
    'site:linkedin.com/posts "building our" "Dubai" OR "Abu Dhabi" team',
    'site:linkedin.com/posts "graduate program" "UAE National" OR "Emirati"',
    'site:linkedin.com/posts "early careers" "Emirati" OR "UAE"',
    'site:linkedin.com/posts "expanding our" "investment" OR "quant" UAE',
    'site:linkedin.com/posts "join our team" "ADGM" OR "DIFC"',
    'site:linkedin.com/posts "Emiratization" "open" Abu Dhabi',
    'site:linkedin.com/posts "hiring" "analyst" OR "associate" "Abu Dhabi" 2024 OR 2025 OR 2026',
    'site:linkedin.com/posts "hiring" "engineer" OR "quant" "Abu Dhabi" OR "Dubai"',
    '"DM me" OR "send your CV" "analyst" OR "investment" "Abu Dhabi" site:linkedin.com',
]

def _build_company_queries(companies: list[str]) -> list[str]:
    queries = []
    for c in companies[:12]:
        queries.append(f'site:linkedin.com/posts "{c}" hiring OR "open role" OR "DM me"')
        queries.append(f'"{c}" "we\'re hiring" OR "join our team" UAE OR "Abu Dhabi"')
    return queries


# ── Database ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SIGNALS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            signal_strength TEXT,
            company TEXT,
            person TEXT,
            title TEXT,
            post_date TEXT,
            post_url TEXT,
            hiring_language TEXT,
            cta TEXT,
            role_mentioned TEXT,
            location_mentioned TEXT,
            is_uae_national INTEGER DEFAULT 0,
            relevance_score INTEGER DEFAULT 0,
            why_relevant TEXT,
            message_to_send TEXT,
            followup_message TEXT,
            status TEXT DEFAULT 'Not reviewed',
            notes TEXT,
            raw_snippet TEXT,
            discovered_at TEXT,
            source TEXT DEFAULT 'web_search'
        )
    """)
    conn.commit()
    return conn


def load_signals(status_filter: Optional[str] = None) -> list[dict]:
    with _get_conn() as conn:
        if status_filter and status_filter != "All":
            rows = conn.execute(
                "SELECT * FROM signals WHERE status = ? ORDER BY relevance_score DESC, discovered_at DESC",
                (status_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY relevance_score DESC, discovered_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_signal(signal: dict) -> str:
    sid = signal.get("id") or str(uuid.uuid4())
    signal["id"] = sid
    signal.setdefault("discovered_at", datetime.now().isoformat())
    signal.setdefault("status", "Not reviewed")
    with _get_conn() as conn:
        existing = conn.execute("SELECT id FROM signals WHERE post_url = ?",
                                (signal.get("post_url") or "",)).fetchone()
        if existing and not signal.get("id"):
            return existing["id"]
        conn.execute("""
            INSERT INTO signals (
                id, signal_strength, company, person, title, post_date, post_url,
                hiring_language, cta, role_mentioned, location_mentioned, is_uae_national,
                relevance_score, why_relevant, message_to_send, followup_message,
                status, notes, raw_snippet, discovered_at, source
            ) VALUES (
                :id, :signal_strength, :company, :person, :title, :post_date, :post_url,
                :hiring_language, :cta, :role_mentioned, :location_mentioned, :is_uae_national,
                :relevance_score, :why_relevant, :message_to_send, :followup_message,
                :status, :notes, :raw_snippet, :discovered_at, :source
            ) ON CONFLICT(id) DO UPDATE SET
                signal_strength=excluded.signal_strength,
                company=excluded.company,
                person=excluded.person,
                title=excluded.title,
                post_date=excluded.post_date,
                hiring_language=excluded.hiring_language,
                cta=excluded.cta,
                role_mentioned=excluded.role_mentioned,
                location_mentioned=excluded.location_mentioned,
                is_uae_national=excluded.is_uae_national,
                relevance_score=excluded.relevance_score,
                why_relevant=excluded.why_relevant,
                message_to_send=excluded.message_to_send,
                followup_message=excluded.followup_message,
                raw_snippet=excluded.raw_snippet
        """, {
            "id": sid,
            "signal_strength": signal.get("signal_strength", "LOW"),
            "company": signal.get("company", ""),
            "person": signal.get("person", ""),
            "title": signal.get("title", ""),
            "post_date": signal.get("post_date", ""),
            "post_url": signal.get("post_url", ""),
            "hiring_language": signal.get("hiring_language", ""),
            "cta": signal.get("cta", ""),
            "role_mentioned": signal.get("role_mentioned", ""),
            "location_mentioned": signal.get("location_mentioned", ""),
            "is_uae_national": 1 if signal.get("is_uae_national") else 0,
            "relevance_score": signal.get("relevance_score", 0),
            "why_relevant": signal.get("why_relevant", ""),
            "message_to_send": signal.get("message_to_send", ""),
            "followup_message": signal.get("followup_message", ""),
            "status": signal.get("status", "Not reviewed"),
            "notes": signal.get("notes", ""),
            "raw_snippet": signal.get("raw_snippet", ""),
            "discovered_at": signal.get("discovered_at", datetime.now().isoformat()),
            "source": signal.get("source", "web_search"),
        })
    return sid


def update_signal(signal_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [signal_id]
    with _get_conn() as conn:
        conn.execute(f"UPDATE signals SET {cols} WHERE id=?", vals)


def delete_signals(ids: list[str]) -> int:
    if not ids:
        return 0
    with _get_conn() as conn:
        conn.execute(
            f"DELETE FROM signals WHERE id IN ({','.join('?' * len(ids))})", ids
        )
    return len(ids)


# ── Signal extraction / scoring ─────────────────────────────────────────────

def _lower(s: str) -> str:
    return (s or "").lower()


def _extract_cta(text: str) -> str:
    t = _lower(text)
    for phrase in HIGH_SIGNAL_CTAS:
        if phrase in t:
            # Find context around the phrase
            idx = t.find(phrase)
            start = max(0, idx - 20)
            end = min(len(text), idx + len(phrase) + 60)
            return text[start:end].strip()
    return ""


def _extract_hiring_language(text: str) -> str:
    t = _lower(text)
    for phrase in HIGH_SIGNAL_HIRING_WORDS + MEDIUM_SIGNAL_WORDS:
        if phrase in t:
            idx = t.find(phrase)
            start = max(0, idx - 10)
            end = min(len(text), idx + len(phrase) + 80)
            return text[start:end].strip()
    return ""


def _extract_role(text: str) -> str:
    t = _lower(text)
    role_patterns = [
        r"\b(investment analyst|quantitative analyst|quant analyst|data analyst|"
        r"software engineer|ai engineer|machine learning engineer|"
        r"strategy analyst|portfolio analyst|research analyst|"
        r"associate|analyst|engineer|developer|manager|director)\b"
    ]
    for pat in role_patterns:
        m = re.search(pat, t)
        if m:
            return m.group(0).title()
    return ""


def _extract_location(text: str) -> str:
    t = _lower(text)
    locations = [
        "abu dhabi", "dubai", "adgm", "difc", "uae", "gcc", "riyadh",
        "doha", "bahrain", "muscat", "kuwait",
    ]
    for loc in locations:
        if loc in t:
            return loc.title()
    return ""


def _is_uae_national(text: str) -> bool:
    t = _lower(text)
    return any(kw in t for kw in UAE_NATIONAL_WORDS)


def _score_signal(text: str, title: str = "") -> tuple[str, int]:
    """Returns (signal_strength, relevance_score 0-100)."""
    t = _lower(text + " " + title)
    score = 0
    strength = "LOW"

    # HIGH signal indicators
    high_hits = sum(1 for phrase in HIGH_SIGNAL_CTAS if phrase in t)
    hiring_hits = sum(1 for phrase in HIGH_SIGNAL_HIRING_WORDS if phrase in t)
    medium_hits = sum(1 for phrase in MEDIUM_SIGNAL_WORDS if phrase in t)

    is_recruiter = any(rt in t for rt in RECRUITER_TITLES)
    is_hiring_mgr = any(ht in t for ht in HIRING_MANAGER_TITLES)
    uae_nat = _is_uae_national(text)

    if high_hits >= 1:
        strength = "HIGH"
        score += 40 + high_hits * 10
    elif hiring_hits >= 1:
        strength = "HIGH"
        score += 30 + hiring_hits * 8
    elif medium_hits >= 1:
        strength = "MEDIUM"
        score += 20 + medium_hits * 5

    if is_recruiter:
        score += 15
    if is_hiring_mgr:
        score += 10
    if uae_nat:
        score += 20  # Rashed is Emirati — this is directly relevant

    # Role relevance for Rashed
    rashed_roles = [
        "analyst", "quant", "investment", "portfolio", "strategy", "ai", "machine learning",
        "data scientist", "engineer", "research", "associate",
    ]
    role_hits = sum(1 for r in rashed_roles if r in t)
    score += min(role_hits * 5, 20)

    score = min(score, 100)
    if score == 0:
        strength = "LOW"
    elif score < 30 and strength == "LOW":
        strength = "LOW"

    return strength, score


def _why_relevant(text: str, signal_strength: str, is_uae_nat: bool) -> str:
    reasons = []
    t = _lower(text)
    if is_uae_nat:
        reasons.append("UAE National / Emiratization opportunity — directly relevant to Rashed")
    if any(p in t for p in HIGH_SIGNAL_CTAS):
        reasons.append("Direct action CTA found (DM me / send CV / happy to refer)")
    if any(p in t for p in ["quant", "investment analyst", "portfolio"]):
        reasons.append("Matches target role family (quant/investment)")
    if any(p in t for p in ["ai", "machine learning", "data science", "engineer"]):
        reasons.append("Matches AI/engineering background")
    if any(p in t for p in ["graduate", "early career", "analyst"]):
        reasons.append("Entry/graduate level opportunity — matches career stage")
    if not reasons:
        reasons.append(f"{signal_strength} hiring signal in target geography")
    return "; ".join(reasons)


def _draft_message(
    person: str,
    company: str,
    cta: str,
    role: str,
    signal_strength: str,
    post_quote: str = "",
) -> str:
    """
    Draft a connection note that references the specific post.
    post_quote: the exact hiring language extracted from the post (1 sentence max).
    LinkedIn connection notes are capped at 300 characters.
    """
    name = (person or "").split()[0] if person else "there"
    role_str = f" {role}" if role else ""
    # Trim post quote to keep message under 300 chars
    quote = ""
    if post_quote:
        q = post_quote.strip().rstrip(".")
        quote = f' ("{q[:80]}")' if len(q) > 10 else ""

    if "refer" in _lower(cta):
        msg = (
            f"Hi {name}, saw your post{quote} — I'd love to be considered{role_str}. "
            f"I'm Rashed Alneyadi, NYU Maths, ex-ADIA & ADIC, quant/AI background. "
            f"Happy to send my CV directly."
        )
    elif any(p in _lower(cta) for p in ["dm me", "message me", "send your cv", "send cv"]):
        msg = (
            f"Hi {name}, your post{quote} caught my attention. "
            f"I'm Rashed Alneyadi — NYU Maths, ex-ADIA & ADIC, quant/AI focus. "
            f"Would love to connect and share my profile{role_str}."
        )
    elif any(p in _lower(cta) for p in ["growing", "building", "expanding", "scaling"]):
        msg = (
            f"Hi {name}, noticed your post{quote} about the team at {company}. "
            f"I'm Rashed Alneyadi — NYU Maths, ex-ADIA & ADIC, quant/AI background. "
            f"Would love to explore if there's a fit."
        )
    else:
        msg = (
            f"Hi {name}, came across your post{quote} at {company}. "
            f"I'm Rashed Alneyadi — NYU Maths, ex-ADIA & ADIC, quant/AI focus. "
            f"Would love to connect."
        )
    # Hard cap at 300 chars (LinkedIn limit for connection notes)
    if len(msg) > 295:
        msg = msg[:292] + "..."
    return msg


def _draft_followup(person: str, company: str, role: str, post_quote: str = "") -> str:
    name = (person or "").split()[0] if person else "there"
    role_str = f" for a {role} role" if role else ""
    post_ref = f' regarding your post ("{post_quote[:60]}")' if post_quote else ""
    return (
        f"Hi {name}, thanks for connecting! Following up{post_ref} — "
        f"I'm very interested in opportunities{role_str} at {company}. "
        f"Happy to share my CV or jump on a quick call at your convenience."
    )


# ── DuckDuckGo search ────────────────────────────────────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]
_ua_idx = 0


def _next_ua() -> str:
    global _ua_idx
    ua = _USER_AGENTS[_ua_idx % len(_USER_AGENTS)]
    _ua_idx += 1
    return ua


def _ddg_search(query: str, max_results: int = 8) -> list[dict]:
    """Search via DuckDuckGo HTML endpoint — free, no API key."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "provider": "duckduckgo",
                })
        return results
    except ImportError:
        pass
    except Exception as e:
        logger.debug("DDG library search failed: %s — falling back to HTML scrape", e)

    # HTML fallback
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": _next_ua(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html",
            },
            timeout=20,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for tag in soup.select(".result")[:max_results]:
            title_tag = tag.select_one(".result__title")
            link_tag = tag.select_one(".result__url")
            snip_tag = tag.select_one(".result__snippet")
            url_raw = ""
            if title_tag and title_tag.find("a"):
                url_raw = title_tag.find("a").get("href", "")
                # DDG wraps URLs — extract real URL from uddg param
                m = re.search(r"uddg=([^&]+)", url_raw)
                if m:
                    from urllib.parse import unquote
                    url_raw = unquote(m.group(1))
            results.append({
                "title": title_tag.get_text(strip=True) if title_tag else "",
                "url": url_raw,
                "snippet": snip_tag.get_text(strip=True) if snip_tag else "",
                "provider": "duckduckgo_html",
            })
        return results
    except Exception as e:
        logger.warning("DuckDuckGo HTML fallback failed: %s", e)
        return []


# ── Parse a search result into a signal dict ────────────────────────────────

def _parse_result_to_signal(result: dict) -> Optional[dict]:
    text = f"{result.get('title', '')} {result.get('snippet', '')}"
    url = result.get("url", "")

    strength, rel_score = _score_signal(text)
    if rel_score < 10:
        return None

    cta = _extract_cta(text)
    hiring_lang = _extract_hiring_language(text)
    role = _extract_role(text)
    location = _extract_location(text)
    uae_nat = _is_uae_national(text)

    # Try to extract person and company from title / URL
    person = ""
    company = ""
    title_str = result.get("title", "")
    # LinkedIn post titles often look like: "John Smith on LinkedIn: ..."
    m = re.match(r"^([A-Z][a-zA-Z\s\-\.]{3,40}) on LinkedIn[:\s]", title_str)
    if m:
        person = m.group(1).strip()
    # Company from snippet heuristics
    m2 = re.search(r"at ([A-Z][A-Za-z0-9\s&\.]{2,30})[,\.\s]", title_str + " " + text[:200])
    if m2:
        company = m2.group(1).strip()

    post_quote = cta or hiring_lang
    why = _why_relevant(text, strength, uae_nat)
    message = _draft_message(person, company, cta or hiring_lang, role, strength, post_quote=post_quote)
    followup = _draft_followup(person, company, role, post_quote=post_quote)

    return {
        "id": str(uuid.uuid4()),
        "signal_strength": strength,
        "company": company,
        "person": person,
        "title": "",
        "post_date": "",
        "post_url": url,
        "hiring_language": hiring_lang or cta,
        "cta": cta,
        "role_mentioned": role,
        "location_mentioned": location,
        "is_uae_national": uae_nat,
        "relevance_score": rel_score,
        "why_relevant": why,
        "message_to_send": message,
        "followup_message": followup,
        "status": "Not reviewed",
        "notes": "",
        "raw_snippet": text[:500],
        "discovered_at": datetime.now().isoformat(),
        "source": result.get("provider", "web_search"),
    }


# ── Manual paste import ──────────────────────────────────────────────────────

def import_from_paste(
    text: str,
    url: str = "",
    company: str = "",
    person: str = "",
    person_title: str = "",
) -> dict:
    """
    Import a signal from manually pasted post text or URL.
    Returns the saved signal dict.
    """
    strength, rel_score = _score_signal(text + " " + person_title)
    if rel_score < 5:
        # Still import it, just mark LOW so user can review
        strength = "LOW"
        rel_score = max(rel_score, 5)

    cta = _extract_cta(text)
    hiring_lang = _extract_hiring_language(text)
    role = _extract_role(text + " " + person_title)
    location = _extract_location(text)
    uae_nat = _is_uae_national(text + " " + person_title)

    post_quote = cta or hiring_lang
    why = _why_relevant(text, strength, uae_nat)
    message = _draft_message(person, company, cta or hiring_lang, role, strength, post_quote=post_quote)
    followup = _draft_followup(person, company, role, post_quote=post_quote)

    signal = {
        "id": str(uuid.uuid4()),
        "signal_strength": strength,
        "company": company,
        "person": person,
        "title": person_title,
        "post_date": "",
        "post_url": url,
        "hiring_language": hiring_lang or cta or text[:200],
        "cta": cta,
        "role_mentioned": role,
        "location_mentioned": location,
        "is_uae_national": uae_nat,
        "relevance_score": rel_score,
        "why_relevant": why,
        "message_to_send": message,
        "followup_message": followup,
        "status": "Not reviewed",
        "notes": "",
        "raw_snippet": text[:500],
        "discovered_at": datetime.now().isoformat(),
        "source": "manual_import",
    }
    upsert_signal(signal)
    logger.info("Manual import: %s signal from %s (%s)", strength, company or url, person or "unknown")
    return signal


# ── Main discovery run ───────────────────────────────────────────────────────

def run_signal_discovery(
    extra_companies: Optional[list[str]] = None,
    max_queries: int = 20,
    results_per_query: int = 8,
    delay_seconds: float = 2.0,
    progress_callback=None,
) -> list[dict]:
    """
    Fire search queries, parse results, score signals, persist to DB.
    Returns list of new/updated signal dicts.
    """
    found = []
    seen_urls: set[str] = set()

    try:
        logger.info("[SIGNAL] Running signal detector (expansion/leadership/vacancy)")
        detector_signals = discover_all_signals(companies=extra_companies, include_hidden_db=False)
        for signal_type, signals in detector_signals.items():
            for sig in signals:
                sig_dict = {
                    "id": str(uuid.uuid4()),
                    "signal_strength": sig.get("signal_strength", "MEDIUM"),
                    "company": sig.get("company", ""),
                    "person": sig.get("contact_name", ""),
                    "title": sig.get("contact_title", ""),
                    "post_date": sig.get("detected_at", ""),
                    "post_url": sig.get("source_url", ""),
                    "hiring_language": sig.get("signal_type", ""),
                    "cta": sig.get("hiring_cta", ""),
                    "role_mentioned": sig.get("probable_role", ""),
                    "location_mentioned": sig.get("location", ""),
                    "relevance_score": sig.get("confidence_score", 50),
                    "why_relevant": sig.get("reasoning", ""),
                    "message_to_send": sig.get("outreach_draft", ""),
                    "status": "Not reviewed",
                    "notes": f"Detector: {signal_type}",
                    "raw_snippet": sig.get("evidence_text", "")[:500],
                    "discovered_at": sig.get("detected_at") or datetime.now().isoformat(),
                    "source": "signal_detector",
                }
                url = sig_dict.get("post_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    upsert_signal(sig_dict)
                    found.append(sig_dict)
                    logger.info(
                        "[SIGNAL DETECTOR] %s (%d) — %s",
                        sig_dict["signal_strength"],
                        sig_dict["relevance_score"],
                        sig_dict.get("company") or "(unknown)",
                    )
    except Exception as exc:
        logger.warning("[SIGNAL] Signal detector failed: %s", exc)

    # Then run DuckDuckGo search queries
    queries = list(_BASE_QUERIES)
    if extra_companies:
        queries.extend(_build_company_queries(extra_companies))

    queries = queries[:max_queries]
    total = len(queries)

    logger.info("[SIGNAL] Running DuckDuckGo search — %d queries", total)

    for i, query in enumerate(queries):
        logger.info("[SIGNAL] Query %d/%d: %s", i + 1, total, query[:80])
        if progress_callback:
            progress_callback(i, total, query)
        try:
            results = _ddg_search(query, max_results=results_per_query)
        except Exception as e:
            logger.warning("[SIGNAL] Search failed for query %d: %s", i + 1, e)
            results = []

        for r in results:
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            signal = _parse_result_to_signal(r)
            if signal:
                upsert_signal(signal)
                found.append(signal)
                logger.info(
                    "[SIGNAL] %s (%d) — %s | %s",
                    signal["signal_strength"],
                    signal["relevance_score"],
                    signal.get("company") or "(company unknown)",
                    (signal.get("hiring_language") or signal.get("cta") or "")[:60],
                )

        if i < total - 1:
            time.sleep(delay_seconds)

    logger.info("[SIGNAL] Discovery done — %d signals found / updated", len(found))
    return found


SIGNAL_STATUSES = [
    "Not reviewed",
    "Worth messaging",
    "Message drafted",
    "Sent connection request",
    "Accepted",
    "Follow-up sent",
    "Replied",
    "CV sent",
    "Applied",
    "Referral requested",
    "Referred",
    "No response",
    "Archived",
]


# ── Push signals → Outreach pipeline ────────────────────────────────────────

def push_signals_to_outreach(
    signal_ids: Optional[list[str]] = None,
    min_strength: str = "MEDIUM",
    skip_no_url: bool = True,
) -> tuple[int, int, list[str]]:
    """
    Convert signal rows into outreach rows ready to send via the LinkedIn pipeline.
    Returns (pushed, skipped_no_url, warnings).
    """
    from agents.linkedin_outreach import merge_rows

    strength_order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    min_rank = strength_order.get(min_strength, 1)

    all_signals = load_signals()
    if signal_ids:
        signals = [s for s in all_signals if s["id"] in set(signal_ids)]
    else:
        signals = [
            s for s in all_signals
            if strength_order.get(s.get("signal_strength", "LOW"), 0) >= min_rank
        ]

    pushed = 0
    skipped_no_url = 0
    warnings: list[str] = []
    new_rows = []

    for sig in signals:
        linkedin_url = (sig.get("post_url") or "").strip()
        is_profile_url = "linkedin.com/in/" in linkedin_url.lower()

        if skip_no_url and not is_profile_url:
            skipped_no_url += 1
            warnings.append(
                f"Skipped '{sig.get('person') or sig.get('company') or sig['id'][:8]}'"
                f" — no /in/ profile URL"
            )
            if not linkedin_url:
                continue

        person = sig.get("person") or ""
        company = sig.get("company") or ""
        title = sig.get("title") or ""
        role = sig.get("role_mentioned") or ""
        post_quote = (sig.get("cta") or sig.get("hiring_language") or "")[:80]
        signal_strength = sig.get("signal_strength", "LOW")
        relevance = sig.get("relevance_score", 0)

        if sig.get("is_uae_national"):
            person_cat = "Emiratization / UAE National"
        elif any(t in (title or "").lower() for t in ["recruiter", "talent", "hr", "people"]):
            person_cat = "Recruiter / Talent"
        elif any(t in (title or "").lower() for t in ["investment", "portfolio", "strategy", "quant"]):
            person_cat = "Investment / Strategy"
        elif any(t in (title or "").lower() for t in ["engineer", "ai", "data", "research", "tech"]):
            person_cat = "AI / Technical"
        else:
            person_cat = "Hiring Signal"

        connection_msg = sig.get("message_to_send") or _draft_message(
            person, company,
            sig.get("cta") or sig.get("hiring_language") or "",
            role, signal_strength, post_quote=post_quote,
        )
        followup_msg = sig.get("followup_message") or _draft_followup(
            person, company, role, post_quote=post_quote,
        )

        why = sig.get("why_relevant") or ""
        if post_quote:
            why = f'Post: "{post_quote}" | ' + why

        try:
            from storage.opportunity_store import get_opportunity_store
            get_opportunity_store().upsert_from_signal(sig)
        except Exception:
            pass

        row = {
            "id": str(uuid.uuid4()),
            "opportunity_id": sig.get("opportunity_id") or "",
            "Waterfall level": "1",
            "IPS score": "",
            "Company": company,
            "Company category": "Signal — " + signal_strength,
            "Company priority score": min(10, relevance // 10),
            "Person name": person or f"Signal contact ({company})",
            "Person title": title,
            "LinkedIn URL": linkedin_url,
            "Person category": person_cat,
            "Person priority score": min(10, relevance // 10),
            "Why this person": why,
            "Message angle": f"Hidden signal — {signal_strength}: {post_quote[:60]}",
            "LinkedIn connection message": connection_msg,
            "Follow-up message after acceptance": followup_msg,
            "Suggested role types": role or "Per signal context",
            "Careers page URL": "",
            "Current relevant roles": sig.get("role_mentioned") or "",
            "Outreach status": "Not sent",
            "Date messaged": "",
            "Date accepted": "",
            "Date followed up": "",
            "Reply status": "",
            "Notes": (
                f"[Signal] {signal_strength} | score {relevance} | "
                f"post: {sig.get('post_url') or 'none'} | "
                f"source: {sig.get('source', 'web_search')}"
            ),
        }
        new_rows.append(row)
        update_signal(sig["id"], status="Message drafted")
        pushed += 1

    if new_rows:
        merge_rows(new_rows)
        logger.info(
            "[SIGNAL->OUTREACH] Pushed %d rows (%d skipped — no profile URL)",
            pushed, skipped_no_url,
        )

    return pushed, skipped_no_url, warnings

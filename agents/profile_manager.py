"""
Profile & requirements file management, link extraction, resume parse, enrichment.
"""

import json
import logging
import re
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("profile_manager")

ROOT = Path(__file__).parent.parent
PROFILE_PATH = ROOT / "data" / "applicant_profile.md"
REQUIREMENTS_PATH = ROOT / "data" / "applicant_requirements.md"
PROFILE_META_PATH = ROOT / "data" / "profile_links.json"
KNOWN_SKILLS_PATH = ROOT / "data" / "known_skills.json"

from config.config import OLLAMA_BASE_URL, OLLAMA_MODEL

_SOURCE_FAILURE_MARKERS = (
    "fetch failed",
    "not installed",
    "log in via setup_linkedin",
    "playwright not installed",
)

SOURCE_LABELS = {
    "resume_pdf": "Resume",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "website": "Website",
    "other_link": "Other link",
}

PROMPT_PACK_PATH = ROOT / "jobhuntr_prompt_pack_rashed.md"
SOURCE_FETCH_ORDER = ("resume_pdf", "linkedin", "website", "github", "other_link")

PROFILE_REQUIRED_SECTIONS = (
    "## Source Confidence Rules",
    "## Verified Core Profile",
    "## Verified Experience",
    "### ADIA",
    "### ADIC",
    "### MIT Zero Robotics",
    "### NYUAD Quantum",
    "### Polygon Technical Infrastructures",
    "### RECtify",
    "## Technical Evidence",
    "## Strongest Role Fit Ranking",
    "## Employer Boost Signals",
    "## Application Answer Rules",
)

REQUIREMENTS_REQUIRED_SECTIONS = (
    "## Primary Career Direction",
    "## Role Keyword Expansions",
)

EXPERIENCE_EVIDENCE_ROLES = (
    ("### ADIA", "ADIA Private Equity Intern"),
    ("### ADIC", "ADIC Active Investments & Equity Intern"),
    ("### MIT Zero Robotics", "MIT Zero Robotics / MBRSC space robotics research"),
    ("### NYUAD Quantum", "NYUAD Quantum Computation Research Assistant"),
    ("### Polygon Technical Infrastructures", "Polygon Technical Infrastructures CEO & Co-Founder"),
    ("### RECtify", "RECtify Brokers Co-Founder"),
)


def get_default_resume_path() -> str:
    """Resume PDF path from profile_settings.json, else project default."""
    try:
        from config.env_settings import load_profile_settings

        rp = (load_profile_settings().get("resume_path") or "").strip()
        if rp and Path(rp).exists():
            return rp
    except Exception:
        pass
    default = ROOT / "Rashed_Alneyadi_Resume.pdf"
    return str(default) if default.exists() else ""


def _source_is_usable(name: str, text: str) -> bool:
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(m in low for m in _SOURCE_FAILURE_MARKERS):
        return False
    min_len = 80
    if name == "resume_pdf":
        min_len = 100
    elif name == "linkedin":
        min_len = 200
    elif name in ("other_link",) or name.startswith("link_"):
        min_len = 40
    return len(text.strip()) >= min_len


def _extract_prompt_pack_section(section_num: int, max_chars: int = 14000) -> str:
    """Read-only: pull structure from jobhuntr_prompt_pack_rashed.md (never modified)."""
    if not PROMPT_PACK_PATH.exists():
        return ""
    text = PROMPT_PACK_PATH.read_text(encoding="utf-8")
    start_pat = rf"^# {section_num}\. "
    next_pat = rf"^# {section_num + 1}\. "
    start = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(start_pat, line):
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(next_pat, lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])[:max_chars]


def _section_outline(md: str) -> str:
    return "\n".join(line for line in md.splitlines() if line.startswith("#"))


def _format_sources_for_prompt(sources: dict[str, str]) -> str:
    """Bundle resume + all links with source-priority labels for the LLM."""
    parts: list[str] = []
    seen: set[str] = set()
    for key in SOURCE_FETCH_ORDER:
        if key in sources:
            label = SOURCE_LABELS.get(key, key)
            parts.append(f"### {label} (priority)\n{sources[key][:7500]}")
            seen.add(key)
    for key, content in sources.items():
        if key in seen:
            continue
        label = SOURCE_LABELS.get(key, key.replace("link_", "").replace("_", " ").title())
        parts.append(f"### {label}\n{content[:6000]}")
    return "\n\n".join(parts)


def _validate_sections(text: str, required: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [s for s in required if s.lower() not in low]


def _canonical_enhanced_schema() -> str:
    """
    Perfected structure: enhanced file if present, else prompt pack §2 (read-only).
    """
    from config.md_loader import PROFILE_ENHANCED_PATH

    enhanced_path = PROFILE_ENHANCED_PATH
    if enhanced_path.exists():
        text = enhanced_path.read_text(encoding="utf-8").strip()
        if "Verified Experience" in text and "### ADIA" in text and len(text) > 2500:
            return text
    pack = _extract_prompt_pack_section(2, max_chars=32000)
    if not pack:
        return ""
    lines = pack.splitlines()
    if lines and re.match(r"^# 2\.\s", lines[0]):
        lines[0] = "# Enhanced Profile Layer"
    return "\n".join(lines)


def _canonical_requirements_schema() -> str:
    from config.md_loader import REQUIREMENTS_ENHANCED_PATH

    enhanced_path = REQUIREMENTS_ENHANCED_PATH
    if enhanced_path.exists():
        text = enhanced_path.read_text(encoding="utf-8").strip()
        if "Primary Career Direction" in text and len(text) > 1500:
            return text
    pack = _extract_prompt_pack_section(4, max_chars=28000)
    if not pack:
        return ""
    lines = pack.splitlines()
    if lines and re.match(r"^# 4\.\s", lines[0]):
        lines[0] = "# Enhanced Requirements Layer"
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    text = text.strip()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict = {}
    for line in parts[1].strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower().replace(" ", "_"), val.strip().strip('"')
        if val.isdigit():
            meta[key] = int(val)
        else:
            meta[key] = val
    return meta, parts[2].strip()


def _build_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v is not None and str(v).strip():
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


CORE_LINK_KEYS = ("linkedin", "github", "website", "other")


def load_links() -> dict:
    """Links from profile_settings.json / profile_links.json + profile markdown."""
    links = {k: "" for k in CORE_LINK_KEYS}
    settings_path = ROOT / "data" / "profile_settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for k in CORE_LINK_KEYS:
                if data.get(k):
                    links[k] = data[k]
            for name, url in (data.get("extra_links") or {}).items():
                if url:
                    links[str(name)] = url
        except Exception:
            pass
    if PROFILE_META_PATH.exists():
        try:
            data = json.loads(PROFILE_META_PATH.read_text(encoding="utf-8"))
            for k in CORE_LINK_KEYS:
                if data.get(k):
                    links[k] = data[k]
            for name, url in (data.get("extra_links") or {}).items():
                if url:
                    links[str(name)] = url
        except Exception:
            pass
    if PROFILE_PATH.exists():
        raw = PROFILE_PATH.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        for key in ("linkedin", "github", "website", "other"):
            if meta.get(key):
                links[key] = meta[key]
        for line in body.splitlines():
            low = line.lower()
            if "linkedin" in low and "http" in low:
                m = re.search(r"https?://[^\s\)]+", line)
                if m:
                    links["linkedin"] = m.group(0).rstrip(")")
            elif "github" in low and "http" in low:
                m = re.search(r"https?://[^\s\)]+", line)
                if m:
                    links["github"] = m.group(0).rstrip(")")
            elif "website" in low and "http" in low:
                m = re.search(r"https?://[^\s\)]+", line)
                if m:
                    links["website"] = m.group(0).rstrip(")")
    return links


def save_links(links: dict) -> None:
    core = {k: (links.get(k) or "").strip() for k in CORE_LINK_KEYS}
    extra = {
        k: (links.get(k) or "").strip()
        for k in links
        if k not in CORE_LINK_KEYS and links.get(k)
    }
    payload = {**core, "extra_links": extra}
    PROFILE_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_META_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    settings_path = ROOT / "data" / "profile_settings.json"
    if settings_path.exists():
        try:
            s = json.loads(settings_path.read_text(encoding="utf-8"))
            s.update(core)
            s["extra_links"] = extra
            settings_path.write_text(json.dumps(s, indent=2), encoding="utf-8")
        except Exception:
            pass

    meta, body = {}, ""
    if PROFILE_PATH.exists():
        meta, body = _parse_frontmatter(PROFILE_PATH.read_text(encoding="utf-8"))
    meta.update(core)
    body = _upsert_links_section(body, {**core, **extra})
    PROFILE_PATH.write_text(_build_frontmatter(meta) + body, encoding="utf-8")
    _sync_application_qa(links)


def _upsert_links_section(body: str, links: dict) -> str:
    lines = [
        "## Links\n",
        f"- **LinkedIn (required):** {links.get('linkedin') or '(not set)'}",
        f"- **GitHub:** {links.get('github') or '(optional)'}",
        f"- **Website:** {links.get('website') or '(optional)'}",
        f"- **Other:** {links.get('other') or '(optional)'}",
    ]
    for key, url in sorted(links.items()):
        if key not in CORE_LINK_KEYS and url:
            lines.append(f"- **{key}:** {url}")
    block = "\n".join(lines) + "\n"
    if "## Links" in body:
        body = re.sub(r"## Links.*?(?=\n## |\Z)", block + "\n", body, flags=re.DOTALL)
    else:
        if "## Identity" in body:
            body = body.replace("## Identity", block + "\n## Identity", 1)
        else:
            body = block + "\n" + body
    return body


def _sync_application_qa(links: dict):
    """Update config APPLICATION_QA linkedin from profile links."""
    try:
        from config import config as cfg
        if links.get("linkedin"):
            cfg.APPLICATION_QA["linkedin"] = links["linkedin"]
    except Exception:
        pass


def load_profile_body() -> str:
    if not PROFILE_PATH.exists():
        return ""
    raw = PROFILE_PATH.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(raw)
    return body


def save_profile_body(body: str, links: dict = None) -> None:
    meta = {}
    if PROFILE_PATH.exists():
        meta, _ = _parse_frontmatter(PROFILE_PATH.read_text(encoding="utf-8"))
    if links:
        save_links(links)
        meta.update(links)
    PROFILE_PATH.write_text(_build_frontmatter(meta) + body.strip() + "\n", encoding="utf-8")


def load_requirements_body() -> str:
    if not REQUIREMENTS_PATH.exists():
        return ""
    raw = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(raw)
    return body


def save_requirements_body(body: str) -> None:
    meta = {}
    if REQUIREMENTS_PATH.exists():
        meta, _ = _parse_frontmatter(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    REQUIREMENTS_PATH.write_text(_build_frontmatter(meta) + body.strip() + "\n", encoding="utf-8")


def validate_linkedin_required(links: dict = None) -> tuple[bool, str]:
    links = links or load_links()
    url = (links.get("linkedin") or "").strip()
    if not url:
        return False, "LinkedIn URL is required. Add it in Profile → Links."
    if "linkedin.com" not in url.lower():
        return False, "LinkedIn URL must be a linkedin.com profile link."
    return True, ""


def extract_resume_text(pdf_path: str, max_chars: int = 12000) -> str:
    path = Path(pdf_path)
    if not path.exists():
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages[:8]:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        return "\n".join(parts)[:max_chars]
    except ImportError:
        logger.warning("pypdf not installed — pip install pypdf")
        return ""
    except Exception as e:
        logger.error(f"Resume parse error: {e}")
        return ""


def fetch_github_text(url: str) -> str:
    if not url or "github.com" not in url.lower():
        return ""
    try:
        path = urlparse(url).path.strip("/").split("/")
        user = path[0] if path else ""
        if not user:
            return ""
        api = f"https://api.github.com/users/{user}"
        r = requests.get(api, timeout=15, headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return f"GitHub page: {url}"
        data = r.json()
        lines = [
            f"GitHub user: {data.get('name', user)}",
            f"Bio: {data.get('bio', '')}",
            f"Location: {data.get('location', '')}",
            f"Company: {data.get('company', '')}",
            f"Blog: {data.get('blog', '')}",
        ]
        repos = requests.get(
            f"https://api.github.com/users/{user}/repos?sort=updated&per_page=8",
            timeout=15,
            headers={"Accept": "application/vnd.github+json"},
        )
        if repos.status_code == 200:
            for repo in repos.json()[:8]:
                lines.append(
                    f"Repo: {repo.get('name')} — {repo.get('description', '')} "
                    f"({repo.get('language', '')})"
                )
        return "\n".join(lines)[:8000]
    except Exception as e:
        return f"GitHub fetch failed: {e}"


def fetch_website_text(url: str) -> str:
    if not url or not url.startswith("http"):
        return ""
    try:
        r = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobHuntrr/1.0)"},
        )
        html = r.text[:500000]
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except Exception as e:
        return f"Website fetch failed: {e}"


def fetch_linkedin_profile_text(profile_url: str, headless: bool = True, retries: int = 3) -> str:
    """Use saved LinkedIn session to read profile page text."""
    if not profile_url or "linkedin.com" not in profile_url.lower():
        return ""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright not installed."

    session_dir = ROOT / "data" / "linkedin_session"
    last_err = ""
    for attempt in range(retries):
        text = ""
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(session_dir),
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport={"width": 1440, "height": 900},
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    page.wait_for_timeout(4000)
                for sel in ("main", ".scaffold-layout-main", "body"):
                    try:
                        loc = page.locator(sel).first
                        if loc.count():
                            text = loc.inner_text(timeout=10000)
                            if len(text) > 200:
                                break
                    except Exception:
                        pass
                if not text:
                    text = page.locator("body").inner_text(timeout=10000)
                ctx.close()
            if len((text or "").strip()) > 200:
                return (text or "")[:10000]
            last_err = f"LinkedIn page too short ({len(text or '')} chars)"
        except Exception as e:
            last_err = str(e)
        if attempt < retries - 1:
            logger.warning("LinkedIn fetch attempt %s failed, retrying: %s", attempt + 1, last_err)
            time.sleep(2 * (attempt + 1))
    return f"LinkedIn profile fetch failed (log in via setup_linkedin.py first): {last_err}"


BACKUP_DIR = ROOT / "data" / "profile_backups"
ENRICH_REQ_STAMP_PATH = ROOT / "data" / ".last_requirements_enrich"


def backup_profile() -> Optional[Path]:
    """Copy current profile to data/profile_backups/ before risky edits."""
    if not PROFILE_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"applicant_profile_{ts}.md"
    dest.write_text(PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info(f"Profile backup: {dest}")
    return dest


def _strip_llm_markdown(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _filter_profile_bullets(text: str) -> str:
    skip = (
        "here are the extracted",
        "markdown bullets",
        "linkedin (required)",
        "**github:**",
        "**website:**",
        "**other:**",
    )
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(p in low for p in skip):
            continue
        if re.search(r"https?://", line) and len(line) < 100:
            continue
        lines.append(line)
    return "\n".join(lines)


def _normalize_bullet_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("-"):
            line = f"- {line.lstrip('•* ')}"
        if len(line) > 12:
            lines.append(line)
    return _filter_profile_bullets("\n".join(lines))


def _resume_fallback_bullets(text: str, max_bullets: int = 14) -> str:
    """Deterministic resume highlights when LLM returns nothing."""
    keywords = (
        "intern", "founder", "nyu", "python", "matlab", "mit", "adic", "adia",
        "research", "quant", "portfolio", "robot", "quantum", "ceo", "co-founder",
        "excel", "investment", "private equity", "attribution", "emirati",
    )
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 20 or len(line) > 280:
            continue
        low = line.lower()
        if any(k in low for k in keywords) or re.search(r"\d+%|\d+\+|\$\d", line):
            lines.append(f"- {line}")
        if len(lines) >= max_bullets:
            break
    return "\n".join(lines)


def _ollama_generate(prompt: str, model: str, base_url: str, num_predict: int = 2000, temp: float = 0.2) -> str:
    r = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temp, "num_predict": num_predict},
        },
        timeout=60,
    )
    r.raise_for_status()
    return _strip_llm_markdown(r.json().get("response", ""))


def _extract_bullets_for_source(
    source_key: str,
    content: str,
    dedup_against: str,
    model: str,
    base_url: str,
) -> str:
    """Extract bullets from one source; dedup only against source profile (not enhanced layer)."""
    label = SOURCE_LABELS.get(source_key, source_key)
    prompt = f"""You are building a job-search profile supplement from the candidate's {label}.

Extract 6-15 factual markdown bullets (lines starting with "-") from the SOURCE below.
- Include employers, titles, dates, tools, metrics, projects, education, languages.
- Skip only facts that are already stated verbatim in ALREADY IN SOURCE PROFILE.
- Do NOT invent employers or degrees.
- Output bullet lines only.

ALREADY IN SOURCE PROFILE:
{dedup_against[:4500]}

SOURCE ({label}):
{content[:6500]}
"""
    if source_key == "website":
        prompt += "\nIgnore link lists, nav menus, and duplicate profile headers. Focus on experience, projects, and skills.\n"
    try:
        bullets = _normalize_bullet_lines(_ollama_generate(prompt, model, base_url))
        if len(bullets) >= 40:
            return bullets
        retry = f"""List at least 8 bullet facts from this {label} text for a job profile.
Skip exact duplicates of: {dedup_against[:2000]}
SOURCE:
{content[:6500]}
Markdown bullets only (- lines)."""
        bullets = _normalize_bullet_lines(
            _ollama_generate(retry, model, base_url, num_predict=1500, temp=0.35)
        )
        if len(bullets) >= 40:
            return bullets
    except Exception as e:
        logger.error("Enrich LLM error for %s: %s", source_key, e)
    if source_key == "resume_pdf":
        return _resume_fallback_bullets(content)
    return ""


def _enrichment_bullets_from_sources(
    sources: dict[str, str],
    dedup_against: str,
    model: str,
    base_url: str,
) -> str:
    """LLM extract bullets from all sources (legacy combined block)."""
    model = model or OLLAMA_MODEL
    base_url = base_url or OLLAMA_BASE_URL
    parts = []
    for key, content in sources.items():
        b = _extract_bullets_for_source(key, content, dedup_against, model, base_url)
        if b:
            label = SOURCE_LABELS.get(key, key)
            parts.append(f"### {label}\n{b}")
    return "\n\n".join(parts)


def _replace_evidence_under_heading(doc: str, heading_prefix: str, new_bullets: str) -> str:
    """Replace lines under 'Evidence:' until Positioning: or next ### heading."""
    if not new_bullets.strip():
        return doc
    pattern = (
        rf"({re.escape(heading_prefix)}[^\n]*\n(?:.*?\n)*?Evidence:\s*\n)"
        rf"(?:- .+\n)+"
        rf"(\s*\nPositioning:)"
    )
    block = f"\\1{new_bullets.strip()}\n\\2"
    updated, n = re.subn(pattern, block, doc, count=1, flags=re.DOTALL)
    return updated if n else doc


def _merge_evidence_into_schema(
    schema: str,
    sources: dict[str, str],
    source_body: str,
    model: str,
    base_url: str,
) -> str:
    """Update Evidence bullets per role from resume + all links (keeps pack structure)."""
    bundle = _format_sources_for_prompt(sources)
    out = schema
    for heading, role_name in EXPERIENCE_EVIDENCE_ROLES:
        if heading.lower() not in out.lower():
            continue
        prompt = f"""Write 4-8 markdown Evidence bullets (- lines) for: {role_name}

Use ONLY facts from SOURCES below (resume = highest priority).
No URLs, no GitHub repo names, no invented employers.
Do not repeat generic filler.

SOURCE PROFILE (dedup):
{source_body[:2500]}

SOURCES:
{bundle[:20000]}

Evidence bullets only:"""
        try:
            bullets = _normalize_bullet_lines(_ollama_generate(prompt, model, base_url, 1200, 0.15))
            if bullets:
                out = _replace_evidence_under_heading(out, heading, bullets)
        except Exception as e:
            logger.warning("Evidence merge failed for %s: %s", role_name, e)
    return out


def _build_structured_enhanced_profile(
    sources: dict[str, str],
    source_body: str,
    model: str,
    base_url: str,
) -> str:
    """
    Merge resume + all links into the perfected Enhanced Profile structure
    (prompt pack §2 or existing data/enhanced file as template — read-only).
    Updates Evidence under each role from combined sources.
    """
    schema = _canonical_enhanced_schema()
    if not schema:
        logger.warning("No enhanced profile schema — using minimal headings")
        schema = "\n".join(PROFILE_REQUIRED_SECTIONS)

    logger.info(
        "Merging resume + links into Enhanced Profile schema (%d chars, %d sources)",
        len(schema),
        len(sources),
    )
    out = _merge_evidence_into_schema(schema, sources, source_body, model, base_url)
    if _validate_sections(out, PROFILE_REQUIRED_SECTIONS):
        logger.warning("Schema validation failed after evidence merge — using canonical schema")
        out = schema
    return out.strip() + "\n"


def _build_structured_requirements_enhanced(
    sources: dict[str, str],
    requirements_main: str,
    model: str,
    base_url: str,
) -> str:
    """Update Enhanced Requirements Layer from pack §4 / existing enhanced file."""
    schema = _canonical_requirements_schema()
    if not schema:
        schema = (
            "# Enhanced Requirements Layer\n\n"
            "## Primary Career Direction\n\n"
            "## Role Keyword Expansions\n"
        )
    bundle = _format_sources_for_prompt(sources)

    prompt = f"""UPDATE this Enhanced Requirements Layer document using SOURCES.

- Keep all ## headings and section order from DOCUMENT.
- Add/refine role keywords, industries, tools, and employer hints from SOURCES.
- Do NOT override hard skips in SOURCE REQUIREMENTS.

SOURCE REQUIREMENTS:
{requirements_main[:4500]}

DOCUMENT:
{schema[:20000]}

SOURCES:
{bundle[:18000]}

Return full markdown only."""

    out = _ollama_generate(prompt, model, base_url, num_predict=7000, temp=0.12)
    if not out.lstrip().startswith("#"):
        out = "# Enhanced Requirements Layer\n\n" + out
    if _validate_sections(out, REQUIREMENTS_REQUIRED_SECTIONS):
        return schema.strip() + "\n"
    return out.strip() + "\n"


def enrich_profile_append_only(
    sources: dict[str, str],
    current_body: str,
    model: str = None,
    base_url: str = None,
) -> str:
    """
    Safe enrich (single file): adds/updates ## Enrichment from links on applicant_profile.md.
    """
    model = model or OLLAMA_MODEL
    base_url = base_url or OLLAMA_BASE_URL
    bullets = _enrichment_bullets_from_sources(sources, current_body, model, base_url)
    if not bullets:
        return current_body
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    header = f"## Enrichment from links ({stamp})"
    base = re.sub(
        r"## Enrichment from links \(\d{4}-\d{2}-\d{2}\).*?(?=\n## |\Z)",
        "",
        current_body,
        flags=re.DOTALL,
    ).strip()
    block = f"{header}\n\n{bullets}\n"
    return base + "\n\n" + block + "\n"


def enrich_profile_enhanced_layer(
    sources: dict[str, str],
    source_body: str,
    model: str = None,
    base_url: str = None,
) -> tuple[str, list[str]]:
    """
    Dual-layer enrich: merge resume + all links into structured Enhanced Profile Layer
    (same format as jobhuntr_prompt_pack_rashed.md section #2).
    """
    from config.md_loader import load_profile_enhanced, save_profile_enhanced

    model = model or OLLAMA_MODEL
    base_url = base_url or OLLAMA_BASE_URL
    enhanced_current = load_profile_enhanced() or ""

    out = _build_structured_enhanced_profile(sources, source_body, model, base_url)
    missing = _validate_sections(out, PROFILE_REQUIRED_SECTIONS)
    if missing or len(out) < 2000 or "Verified Experience" not in out:
        logger.error(
            "Structured profile enrich incomplete (len=%s, missing=%s)",
            len(out),
            missing,
        )
        return enhanced_current, []

    save_profile_enhanced(out)
    written = [SOURCE_LABELS.get(k, k) for k in sources]
    logger.info(
        "Structured enhanced profile written (%d chars) from sources: %s",
        len(out),
        ", ".join(written),
    )
    return out, written


def enrich_requirements_enhanced_layer(
    sources: dict[str, str],
    requirements_main: str,
    model: str = None,
    base_url: str = None,
) -> str:
    """Dual-layer: structured Enhanced Requirements Layer from resume + all links."""
    from config.md_loader import load_requirements_enhanced, save_requirements_enhanced

    model = model or OLLAMA_MODEL
    base_url = base_url or OLLAMA_BASE_URL
    enhanced_current = load_requirements_enhanced()

    try:
        out = _build_structured_requirements_enhanced(
            sources, requirements_main, model, base_url
        )
    except Exception as e:
        logger.error("Requirements enrich error: %s", e)
        return enhanced_current

    missing = _validate_sections(out, REQUIREMENTS_REQUIRED_SECTIONS)
    if missing or len(out) < 800:
        logger.warning("Requirements enhanced layer short or missing %s", missing)
        return enhanced_current

    save_requirements_enhanced(out)
    return out


def migrate_inline_enrichment_to_dual_layer() -> bool:
    """
    Dual layer: remove ## Enrichment from source profile (belongs in enhanced/ only).
    If enhanced file is empty, move the block there once.
    """
    from config.md_loader import (
        load_profile_enhanced,
        save_profile_enhanced,
        use_dual_layer,
    )
    if not use_dual_layer():
        return False
    body = load_profile_body()
    m = re.search(
        r"(## Enrichment from links[^\n]*\n.*)",
        body,
        flags=re.DOTALL,
    )
    if not m:
        return False
    block = m.group(1).strip()
    clean = body[: m.start()].strip() + "\n"
    save_profile_body(clean, load_links())
    enhanced = load_profile_enhanced()
    if not enhanced.strip():
        save_profile_enhanced(
            "# Enhanced profile layer\n\n"
            "> Migrated from inline enrichment on source profile.\n\n"
            + block
            + "\n"
        )
    logger.info("Removed inline enrichment from source profile (dual layer).")
    return True


def enrich_profile_with_ollama(
    sources: dict[str, str],
    current_body: str,
    model: str = None,
    base_url: str = None,
    safe_mode: bool = True,
    dual_layer: bool = None,
) -> str:
    """Merge resume + links into profile (source file or enhanced layer)."""
    from config.md_loader import use_dual_layer

    if dual_layer is None:
        dual_layer = use_dual_layer()
    if dual_layer:
        backup_profile()
        migrate_inline_enrichment_to_dual_layer()
        out, _written = enrich_profile_enhanced_layer(sources, current_body, model, base_url)
        return out
    if safe_mode:
        backup_profile()
        return enrich_profile_append_only(sources, current_body, model, base_url)
    backup_profile()
    model = model or OLLAMA_MODEL
    base_url = base_url or OLLAMA_BASE_URL
    combined = "\n\n".join(
        f"### {name}\n{content[:6000]}"
        for name, content in sources.items()
        if content and len(content) > 50
    )
    if not combined.strip():
        return current_body

    prompt = f"""You are updating a job applicant profile markdown document.

CRITICAL RULES:
- NEVER delete or shorten existing sections from the current profile.
- Copy ALL existing section headers and content; only ADD facts or UPDATE lines with new sourced info.
- Do not invent employers or degrees.
- Keep ## Links section unchanged.

Current profile (preserve entirely, then enrich):
{current_body[:8000]}

New sources:
{combined[:14000]}

Return the FULL updated profile markdown (body only, no frontmatter).
Every section that exists in the current profile must still exist in your output.
"""
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.2, "num_predict": 4000}},
            timeout=60,
        )
        r.raise_for_status()
        out = r.json().get("response", "").strip()
        if out.startswith("```"):
            out = re.sub(r"^```\w*\n?", "", out)
            out = re.sub(r"\n?```$", "", out)
        return out if len(out) > 200 else current_body
    except Exception as e:
        logger.error(f"Profile enrich LLM error: {e}")
        return current_body


def collect_all_sources(
    links: dict,
    resume_path: str = "",
    headless: bool = True,
) -> dict[str, str]:
    ok, err = validate_linkedin_required(links)
    if not ok:
        raise ValueError(err)
    if not resume_path:
        resume_path = get_default_resume_path()

    raw: dict[str, str] = {}
    skipped: list[str] = []

    if resume_path:
        t = extract_resume_text(resume_path)
        if t:
            raw["resume_pdf"] = t
        else:
            logger.warning("Resume path set but no text extracted: %s", resume_path)

    raw["linkedin"] = fetch_linkedin_profile_text(links["linkedin"], headless=headless)

    if links.get("github"):
        raw["github"] = fetch_github_text(links["github"])
    if links.get("website"):
        raw["website"] = fetch_website_text(links["website"])
    if links.get("other") and str(links["other"]).strip().startswith("http"):
        raw["other_link"] = fetch_website_text(str(links["other"]).strip())

    for key, url in links.items():
        if key in CORE_LINK_KEYS or key == "linkedin":
            continue
        u = (url or "").strip()
        if u.startswith("http"):
            raw[f"link_{key}"] = fetch_website_text(u)

    sources: dict[str, str] = {}
    for name, text in raw.items():
        if _source_is_usable(name, text):
            sources[name] = text
        else:
            preview = (text or "")[:120].replace("\n", " ")
            skipped.append(f"{name}({len(text or '')} chars: {preview})")

    if skipped:
        logger.warning("Skipped sources: %s", "; ".join(skipped))
    logger.info("Usable sources for enrich: %s", list(sources.keys()))

    if not sources:
        raise ValueError(
            "No usable content from resume or links. "
            "Check resume PDF path, run setup_linkedin.py for LinkedIn session, "
            "and verify GitHub/website URLs."
        )
    if "resume_pdf" not in sources:
        logger.warning(
            "Resume not in usable sources — enrich will rely on links only"
        )
    return sources


def load_known_skills() -> set[str]:
    default = {
        "excel", "python", "matlab", "java", "javascript", "typescript", "react",
        "sql", "machine learning", "data analysis", "financial modeling",
        "arabic", "english", "power bi", "tableau", "git", "docker", "aws",
        "statistics", "quantitative", "investment research", "private equity",
    }
    if KNOWN_SKILLS_PATH.exists():
        try:
            data = json.loads(KNOWN_SKILLS_PATH.read_text(encoding="utf-8"))
            return set(s.lower() for s in data.get("skills", [])) | default
        except Exception:
            pass
    return default


def extract_skills_from_text(text: str) -> set[str]:
    text = text.lower()
    found = set()
    for skill in load_known_skills():
        if skill in text:
            found.add(skill)
    return found


def find_profile_gaps(job_description: str = "", extra_required: list = None) -> list[str]:
    """Skills mentioned in job (or list) but not in profile body."""
    try:
        from config.md_loader import get_candidate_profile_for_prompt
        profile = get_candidate_profile_for_prompt().lower()
    except Exception:
        profile = load_profile_body().lower()
    links = load_links()
    profile += " " + " ".join(links.values()).lower()
    required = extract_skills_from_text(job_description)
    if extra_required:
        required |= set(s.lower() for s in extra_required)
    missing = []
    for skill in sorted(required):
        if skill not in profile and skill.replace(" ", "") not in profile.replace(" ", ""):
            missing.append(skill)
    return missing


ENRICH_STAMP_PATH = ROOT / "data" / ".last_profile_enrich"
ENRICH_MAX_AGE_DAYS = int(os.getenv("PROFILE_ENRICH_MAX_AGE_DAYS", "7"))
AUTO_ENRICH_TIMEOUT_SECONDS = int(os.getenv("AUTO_ENRICH_TIMEOUT_SECONDS", "90"))


def run_profile_enrich(
    links: dict = None,
    resume_path: str = "",
    *,
    dual_layer: bool = None,
    safe_mode: bool = True,
    headless: bool = True,
    enrich_requirements: bool = True,
) -> dict:
    """
    Full enrich: fetch resume + links, write enhanced layer (default) or source file.
    Returns {ok, mode, sources, bullets_added, message}.
    """
    from config.md_loader import load_applicant_requirements, use_dual_layer

    links = links or load_links()
    ok, err = validate_linkedin_required(links)
    if not ok:
        return {"ok": False, "mode": "", "sources": {}, "bullets_added": False, "message": err}

    if dual_layer is None:
        dual_layer = use_dual_layer()
    if not resume_path:
        resume_path = get_default_resume_path()

    migrate_inline_enrichment_to_dual_layer()
    try:
        sources = collect_all_sources(links, resume_path=resume_path, headless=headless)
    except ValueError as e:
        return {"ok": False, "mode": "", "sources": {}, "bullets_added": False, "message": str(e)}

    backup_profile()
    current = load_profile_body()
    sections_written: list[str] = []

    if dual_layer:
        _, sections_written = enrich_profile_enhanced_layer(sources, current)
        if enrich_requirements:
            enrich_requirements_enhanced_layer(sources, load_applicant_requirements())
        mode = "dual_layer → structured Enhanced Profile (resume + all links)"
        bullets_added = len(sections_written) > 0
    else:
        updated = enrich_profile_with_ollama(
            sources, current, safe_mode=safe_mode, dual_layer=False,
        )
        save_profile_body(updated, links)
        bullets_added = "## Enrichment from links" in updated and updated != current
        mode = "legacy append → source profile" if safe_mode else "legacy full merge → source profile"

    ENRICH_STAMP_PATH.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
    ENRICH_REQ_STAMP_PATH.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
    reload_runtime_candidate_profile()

    src_summary = ", ".join(f"{k}({len(v)})" for k, v in sources.items())
    sect_part = f" Sections written: {', '.join(sections_written)}." if sections_written else ""
    if dual_layer and not bullets_added:
        return {
            "ok": False,
            "mode": mode,
            "sources": {k: len(v) for k, v in sources.items()},
            "bullets_added": False,
            "message": (
                f"Fetched sources ({src_summary}) but could not extract bullets. "
                f"Check Ollama is running ({OLLAMA_MODEL}) and run setup_linkedin.py if LinkedIn failed."
            ),
        }
    return {
        "ok": True,
        "mode": mode,
        "sources": {k: len(v) for k, v in sources.items()},
        "bullets_added": bullets_added,
        "sections": sections_written,
        "message": f"Enrich complete [{mode}]. Sources: {src_summary}.{sect_part}",
    }


def _run_profile_enrich_child(
    queue,
    links: dict,
    resume_path: str,
    headless: bool,
) -> None:
    """Run optional auto-enrich in a subprocess so callers can hard-timeout it."""
    try:
        result = run_profile_enrich(
            links,
            resume_path=resume_path,
            headless=headless,
            enrich_requirements=True,
        )
        queue.put({"ok": True, "result": result})
    except Exception as exc:
        queue.put({"ok": False, "error": str(exc)})


def maybe_auto_enrich_profile(
    headless: bool = True,
    force: bool = False,
    resume_path: str = "",
) -> bool:
    """
    Auto-enrich profile from links + resume before pipeline runs.
    Controlled by AUTO_ENRICH_PROFILE env (default on). Skips if enriched recently.
    """
    auto = os.getenv("AUTO_ENRICH_PROFILE", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    if not auto and not force:
        return False

    links = load_links()
    ok, err = validate_linkedin_required(links)
    if not ok:
        logger.warning(f"Auto-enrich skipped: {err}")
        return False

    if not force and ENRICH_STAMP_PATH.exists():
        try:
            import time
            age_days = (time.time() - ENRICH_STAMP_PATH.stat().st_mtime) / 86400
            if age_days < ENRICH_MAX_AGE_DAYS:
                logger.info(
                    f"Profile enriched {age_days:.1f}d ago — skipping (max age {ENRICH_MAX_AGE_DAYS}d)"
                )
                return False
        except Exception:
            pass

    if not resume_path:
        resume_path = get_default_resume_path()

    logger.info("Auto-enriching profile from LinkedIn, links, and resume...")
    try:
        import multiprocessing as _mp

        ctx = _mp.get_context("spawn")
        queue = ctx.Queue(maxsize=1)
        proc = ctx.Process(
            target=_run_profile_enrich_child,
            args=(queue, links, resume_path, headless),
            daemon=True,
        )
        proc.start()
        proc.join(AUTO_ENRICH_TIMEOUT_SECONDS)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            logger.warning(
                "Auto-enrich timed out after %ss - skipping",
                AUTO_ENRICH_TIMEOUT_SECONDS,
            )
            return False
        if queue.empty():
            logger.warning("Auto-enrich exited without a result - skipping")
            return False
        payload = queue.get()
        if not payload.get("ok"):
            logger.warning("Auto-enrich skipped: %s", payload.get("error", "unknown error"))
            return False
        result = payload["result"]
        if result["ok"]:
            logger.info(result["message"])
            return True
        logger.warning("Auto-enrich skipped: %s", result["message"])
        return False
    except Exception as e:
        logger.error(f"Auto-enrich failed: {e}")
        return False


def reload_runtime_candidate_profile() -> None:
    """Refresh CANDIDATE_PROFILE after enrich or profile save."""
    try:
        from config.config import reload_candidate_profile
        reload_candidate_profile()
    except Exception as e:
        logger.debug(f"Runtime profile reload: {e}")


def apply_skill_answers(answers: dict[str, str]) -> None:
    """Append confirmed skills to profile ## Skills & tools section."""
    body = load_profile_body()
    additions = "\n".join(f"- {k}: {v}" for k, v in answers.items() if v.strip())
    if "## Skills & tools" in body:
        body = body.replace("## Skills & tools", f"## Skills & tools\n\n{additions}\n", 1)
    else:
        body += f"\n\n## Skills & tools (user confirmed)\n{additions}\n"
    save_profile_body(body, load_links())

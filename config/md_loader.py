"""
Load applicant_profile.md and applicant_requirements.md from data/.

Any user can copy the .template.md files, fill them in, and JobHuntrr will use them.
Optional YAML frontmatter in requirements .md sets score thresholds.
"""

from pathlib import Path
import os
import re

_DATA = Path(__file__).parent.parent / "data"
ENHANCED_DIR = _DATA / "enhanced"

PROFILE_PATH = _DATA / "applicant_profile.md"
REQUIREMENTS_PATH = _DATA / "applicant_requirements.md"
CUSTOM_SCORING_PATH = _DATA / "custom_scoring_prompt.md"
PROFILE_ENHANCED_PATH = ENHANCED_DIR / "applicant_profile_enhanced.md"
REQUIREMENTS_ENHANCED_PATH = ENHANCED_DIR / "applicant_requirements_enhanced.md"

# Legacy path (migrated to .md)
_LEGACY_PROFILE_PATH = _DATA / "candidate_profile.txt"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse --- yaml --- body. Values: int if numeric else str."""
    text = text.strip()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta_raw, body = parts[1].strip(), parts[2].strip()
    meta: dict = {}
    for line in meta_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.isdigit():
            meta[key] = int(val)
        else:
            try:
                meta[key] = float(val)
            except ValueError:
                meta[key] = val.strip('"').strip("'")
    return meta, body


def load_applicant_profile() -> str:
    """Full profile markdown body (for scoring & form-fill prompts)."""
    for path in (PROFILE_PATH, _LEGACY_PROFILE_PATH):
        if path.exists():
            raw = path.read_text(encoding="utf-8").strip()
            _, body = _parse_frontmatter(raw)
            return body or raw
    return ""


_SECTION_HEADERS = (
    "Custom scoring prompt",
    "Search queries",
    "Custom search prompt",
)


def _split_requirements_sections(body: str) -> dict[str, str]:
    """Split requirements body into main + named sections."""
    parts = {h: "" for h in _SECTION_HEADERS}
    pattern = r"(?m)^##\s+(" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")\s*$"
    splits = re.split(pattern, body)
    parts["_main"] = splits[0].strip() if splits else body.strip()
    i = 1
    while i + 1 < len(splits):
        header = splits[i].strip()
        content = splits[i + 1].strip()
        if header in parts:
            parts[header] = content
        i += 2
    return parts


def _build_requirements_body(sections: dict[str, str]) -> str:
    main = (sections.get("_main") or "").strip()
    blocks = [main] if main else []
    for header in _SECTION_HEADERS:
        content = (sections.get(header) or "").strip()
        if content:
            blocks.append(f"## {header}\n\n{content}")
    return "\n\n".join(blocks).strip() + "\n"


def load_applicant_requirements() -> str:
    """Main requirements body only (excludes custom prompt sections)."""
    if not REQUIREMENTS_PATH.exists():
        return ""
    raw = REQUIREMENTS_PATH.read_text(encoding="utf-8").strip()
    _, body = _parse_frontmatter(raw)
    return _split_requirements_sections(body or raw).get("_main", body or raw)


def load_requirements_raw() -> tuple[dict, str]:
    """Frontmatter + full body."""
    if not REQUIREMENTS_PATH.exists():
        return {}, ""
    raw = REQUIREMENTS_PATH.read_text(encoding="utf-8").strip()
    meta, body = _parse_frontmatter(raw)
    return meta, body or raw


def load_requirements_sections() -> dict[str, str]:
    if not REQUIREMENTS_PATH.exists():
        return {"_main": "", **{h: "" for h in _SECTION_HEADERS}}
    _, body = _parse_frontmatter(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    return _split_requirements_sections(body)


def load_custom_scoring_prompt() -> str:
    """Prefer data/custom_scoring_prompt.md; fall back to Requirements → Scoring prompt tab."""
    if CUSTOM_SCORING_PATH.exists():
        text = CUSTOM_SCORING_PATH.read_text(encoding="utf-8").strip()
        if text and not text.startswith("<!--"):
            return text
    return load_requirements_sections().get("Custom scoring prompt", "").strip()


def load_custom_search_prompt() -> str:
    return load_requirements_sections().get("Custom search prompt", "").strip()


def _is_search_query_line(line: str) -> bool:
    """Ignore prose, bullets, and section headers masquerading as queries."""
    if not line or line.startswith("#"):
        return False
    if line.startswith("-") or line.startswith("*"):
        return False
    if line.endswith(":"):
        return False
    if len(line) > 90:
        return False
    low = line.lower()
    stop_prefixes = (
        "search for ",
        "priority locations",
        "include ",
        "primary search queries",
        "target industries",
        "target companies",
        "prioritize",
        "auto-apply",
        "save borderline",
        "skip low-signal",
    )
    if any(low.startswith(p) for p in stop_prefixes):
        return False
    if "posted" in low and "ago" in low:
        return False
    # Long prose sentences (not job-title keywords)
    if "|" not in line and "." in line and len(line) > 45:
        return False
    return True


def load_search_queries_from_requirements() -> list[dict]:
    """
    Parse ## Search queries section. One query per line:
      job title keywords | location
    Lines starting with # are ignored. Prose and metadata lines are skipped.
    """
    import re

    text = load_requirements_sections().get("Search queries", "").strip()
    if not text:
        return []
    queries = []
    in_primary = False
    stop_section = re.compile(
        r"^(target industries|target companies|prioritize)\s*:?\s*$",
        re.I,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^primary search queries\s*:?\s*$", line, re.I):
            in_primary = True
            continue
        if stop_section.match(line):
            break
        if not in_primary:
            if "|" not in line:
                continue
        elif not _is_search_query_line(line):
            continue
        if "|" in line:
            term, _, loc = line.partition("|")
            term, loc = term.strip(), loc.strip()
            if term and loc:
                queries.append({"term": term, "location": loc})
        elif _is_search_query_line(line):
            queries.append({"term": line, "location": "UAE"})
    return queries


def _build_frontmatter(meta: dict) -> str:
    if not meta:
        return ""
    lines = ["---"]
    for k, v in meta.items():
        if v is not None and str(v).strip() != "":
            lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def save_requirements_sections(sections: dict[str, str]) -> None:
    meta = {}
    if REQUIREMENTS_PATH.exists():
        meta, _ = _parse_frontmatter(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    body = _build_requirements_body(sections)
    REQUIREMENTS_PATH.write_text(_build_frontmatter(meta) + body, encoding="utf-8")


def load_requirements_config() -> dict:
    """
    Machine-readable settings from requirements frontmatter + defaults.
    """
    defaults = {
        "auto_apply": 75,
        "manual_review": 60,
        "max_years_hard_skip": 7,
        "min_requirements_match_pct": 50,
        "linkedin_hours_fresh": 48,
        "ats_days_fresh": 7,
        "min_salary_aed_monthly": 12000,
        "sps_immediate_action": 85,
        "sps_apply_network": 70,
        "sps_network_only": 50,
        "ips_inmail_threshold": 75,
        "easy_apply_max_hours": 24,
        "easy_apply_max_applicants": 50,
        "warm_lead_referral_threshold": 70,
    }
    if not REQUIREMENTS_PATH.exists():
        return defaults
    raw = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    meta, _ = _parse_frontmatter(raw)
    for k, v in meta.items():
        if k in defaults:
            defaults[k] = v
        elif k in (
            "min_salary_aed_monthly",
            "sps_immediate_action", "sps_apply_network", "sps_network_only",
            "ips_inmail_threshold", "easy_apply_max_hours", "easy_apply_max_applicants",
            "warm_lead_referral_threshold",
        ):
            try:
                defaults[k] = int(v)
            except (TypeError, ValueError):
                defaults[k] = v
    return defaults


def score_thresholds_from_md() -> dict:
    c = load_requirements_config()
    return {
        "auto_apply": int(c["auto_apply"]),
        "manual_review": int(c["manual_review"]),
        "skip": 0,
    }


def load_profile_links() -> dict:
    """LinkedIn (required), GitHub, website, other — from profile_manager."""
    from agents.profile_manager import load_links
    return load_links()


def use_dual_layer() -> bool:
    """When True: source files are yours; enrich writes data/enhanced/* only."""
    return os.getenv("PROFILE_DUAL_LAYER", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def load_profile_enhanced() -> str:
    if not PROFILE_ENHANCED_PATH.exists():
        return ""
    raw = PROFILE_ENHANCED_PATH.read_text(encoding="utf-8").strip()
    _, body = _parse_frontmatter(raw)
    return body or raw


def load_requirements_enhanced() -> str:
    if not REQUIREMENTS_ENHANCED_PATH.exists():
        return ""
    raw = REQUIREMENTS_ENHANCED_PATH.read_text(encoding="utf-8").strip()
    _, body = _parse_frontmatter(raw)
    return body or raw


def save_profile_enhanced(body: str) -> None:
    ENHANCED_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_ENHANCED_PATH.write_text(body.strip() + "\n", encoding="utf-8")


def save_requirements_enhanced(body: str) -> None:
    ENHANCED_DIR.mkdir(parents=True, exist_ok=True)
    REQUIREMENTS_ENHANCED_PATH.write_text(body.strip() + "\n", encoding="utf-8")


def get_candidate_profile_for_prompt(
    max_source: int = 8000,
    max_enhanced: int = 4000,
) -> str:
    """
    Profile text for scoring and applications.
    Dual layer: source (applicant_profile.md) + enhanced (auto from links/resume).
    """
    source = load_applicant_profile()
    if not source:
        return ""
    if not use_dual_layer():
        return source
    enhanced = load_profile_enhanced()
    if not enhanced.strip():
        return source
    return (
        "## Source profile (your content — authoritative)\n"
        f"{source[:max_source]}\n\n"
        "## Enhanced profile (auto-generated — supplemental only)\n"
        f"{enhanced[:max_enhanced]}"
    )


def get_requirements_for_scorer(
    max_main: int = 3500,
    max_enhanced: int = 2000,
    max_custom_scoring: int = 5500,
) -> str:
    """Requirements text for the scorer (main + optional enhanced + custom scoring)."""
    main = load_applicant_requirements()
    custom = load_custom_scoring_prompt()
    parts = []
    if main:
        label = "Source requirements" if use_dual_layer() else "Applicant requirements"
        parts.append(f"## {label}\n{main[:max_main]}")
    if use_dual_layer():
        enhanced = load_requirements_enhanced()
        if enhanced.strip():
            parts.append(
                "## Enhanced requirements (auto-generated — supplemental)\n"
                f"{enhanced[:max_enhanced]}"
            )
    if custom:
        parts.append(
            "## Custom scoring instructions (highest priority)\n"
            f"{custom[:max_custom_scoring]}"
        )
    return "\n\n".join(parts)

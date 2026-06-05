#!/usr/bin/env python3
"""
Read-only: compare enrich output to jobhuntr_prompt_pack_rashed.md sections #2 and #4.
Does not run enrich or modify any prompts.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parent
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from sandbox_paths import (
    OUTPUT_PROFILE_ENHANCED,
    OUTPUT_REQUIREMENTS_ENHANCED,
    PROMPT_PACK,
    REPO_ROOT,
    REPORTS,
    ensure_dirs,
)

# Keywords from your perfected Enhanced Profile Layer (section #2)
PROFILE_KEYWORDS = [
    ("ADIA", "ADIA private equity"),
    ("ADIC", "ADIC attribution"),
    ("70%", "ADIC ~70% variance"),
    ("MIT", "MIT Zero Robotics"),
    ("MBRSC", "MBRSC / space"),
    ("30%", "MIT ~30% traversal"),
    ("NYUAD", "NYUAD quantum"),
    ("40%", "quantum ~40% compute"),
    ("3-qubit", "XXZ / 3-qubit"),
    ("Polygon Technical Infrastructures", "Polygon company name"),
    ("RECtify", "RECtify / climate"),
    ("Emirati", "Emirati nationality"),
    ("Arabic", "Arabic language"),
    ("DIBA", "DIBA project"),
    ("QuantConnect", "QuantConnect (optional in sources)"),
    ("mathematics", "math foundation"),
    ("Python", "Python"),
    ("MATLAB", "MATLAB"),
]

REQUIREMENTS_KEYWORDS = [
    ("quant", "quant roles"),
    ("sovereign", "sovereign wealth"),
    ("G42", "G42"),
    ("Mubadala", "Mubadala"),
    ("hedge", "hedge funds"),
    ("ADNOC", "ADNOC"),
    ("space", "space sector"),
    ("35", "compensation band"),
    ("AED", "AED comp"),
]

JUNK_PATTERNS = [
    r"here are the extracted",
    r"\*\*LinkedIn \(required\)\*\*:\s*https?://",
    r"^-\s*\*\*GitHub:\*\*\s*https?://",
]


def extract_pack_section(pack_text: str, section_num: int) -> str:
    """Extract '# N. ...' until next '# N+1.' or '# N. ' at same level."""
    start_pat = rf"^# {section_num}\. "
    next_pat = rf"^# {section_num + 1}\. "
    start = None
    for i, line in enumerate(pack_text.splitlines()):
        if re.match(start_pat, line):
            start = i
            break
    if start is None:
        return ""
    lines = pack_text.splitlines()
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(next_pat, lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def keyword_report(text: str, keywords: list[tuple[str, str]]) -> list[str]:
    low = text.lower()
    rows = []
    for needle, label in keywords:
        hit = needle.lower() in low
        rows.append(f"| {label} | `{needle}` | {'yes' if hit else '**no**'} |")
    return rows


def junk_report(text: str) -> list[str]:
    issues = []
    for pat in JUNK_PATTERNS:
        if re.search(pat, text, re.I | re.M):
            issues.append(f"- Matches junk pattern: `{pat}`")
    return issues


def overlap_ratio(a: str, b: str) -> float:
    """Rough token overlap (words length>=5)."""
    def tokens(s: str) -> set[str]:
        return {w.lower() for w in re.findall(r"[a-zA-Z]{5,}", s)}

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--production-output",
        action="store_true",
        help="Also compare data/enhanced/ (read-only; does not run enrich)",
    )
    args = parser.parse_args()

    ensure_dirs()
    if not PROMPT_PACK.exists():
        print(f"Missing prompt pack: {PROMPT_PACK}")
        return 1

    pack = PROMPT_PACK.read_text(encoding="utf-8")
    pack_profile = extract_pack_section(pack, 2)
    pack_requirements = extract_pack_section(pack, 4)

    targets: list[tuple[str, Path]] = [("sandbox_profile", OUTPUT_PROFILE_ENHANCED)]
    if OUTPUT_REQUIREMENTS_ENHANCED.exists():
        targets.append(("sandbox_requirements", OUTPUT_REQUIREMENTS_ENHANCED))
    if args.production_output:
        prod_p = REPO_ROOT / "data" / "enhanced" / "applicant_profile_enhanced.md"
        prod_r = REPO_ROOT / "data" / "enhanced" / "applicant_requirements_enhanced.md"
        if prod_p.exists():
            targets.append(("production_profile", prod_p))
        if prod_r.exists():
            targets.append(("production_requirements", prod_r))

    lines = [
        "# Enrich vs prompt pack (read-only report)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Reference: `{PROMPT_PACK.name}` sections **#2** and **#4**",
        "",
        "This report does **not** change prompts or run enrich.",
        "",
    ]

    for label, path in targets:
        lines.append(f"## {label}: `{path.relative_to(REPO_ROOT)}`")
        if not path.exists():
            lines.append("*File not found — run `run_sandbox_enrich.py` first (sandbox only).*")
            lines.append("")
            continue
        text = path.read_text(encoding="utf-8")
        lines.append(f"- Characters: {len(text)}")
        lines.append(f"- Token overlap with pack §2 profile: **{overlap_ratio(text, pack_profile):.1%}**")
        if "requirements" in label:
            lines.append(
                f"- Token overlap with pack §4 requirements: **{overlap_ratio(text, pack_requirements):.1%}**"
            )
        else:
            lines.append(
                f"- Token overlap with pack §2 (target spec): **{overlap_ratio(text, pack_profile):.1%}**"
            )

        junk = junk_report(text)
        if junk:
            lines.append("")
            lines.append("### Quality issues")
            lines.extend(junk)

        lines.append("")
        lines.append("### Keyword coverage (profile pack §2)")
        lines.append("| Topic | Keyword | Found |")
        lines.append("|-------|---------|-------|")
        lines.extend(keyword_report(text, PROFILE_KEYWORDS))
        lines.append("")

    lines.append("## What “similar or better” means")
    lines.append("")
    lines.append("Your pack **§2** uses structured blocks: Verified Experience per role, Technical Evidence, Role Fit Ranking, Employer Boost.")
    lines.append("Current enrich code outputs **per-source** sections (`## From Resume`, `## From LinkedIn`, …).")
    lines.append("High keyword coverage + low junk + rising overlap % = closer to your pack; structure may still differ until enrich prompts are retuned (outside this sandbox).")
    lines.append("")

    report_path = REPORTS / "comparison_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Run profile enrich in isolation. Writes only under sandbox/enrich_eval/output/.

Does NOT modify jobhuntr_prompt_pack_rashed.md or data/applicant_*.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parent
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

from sandbox_paths import (
    OUTPUT_FETCH_LOG,
    OUTPUT_PROFILE_ENHANCED,
    OUTPUT_REQUIREMENTS_ENHANCED,
    REPO_ROOT,
    apply_sandbox_patches,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox enrich (isolated I/O)")
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Fetch sources only; no Ollama; no enhanced file writes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch sources and print plan; do not write enhanced files",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="LinkedIn browser headless (default: visible, easier auth)",
    )
    args = parser.parse_args()

    apply_sandbox_patches()

    from agents.profile_manager import (
        collect_all_sources,
        get_default_resume_path,
        load_links,
        run_profile_enrich,
        validate_linkedin_required,
    )

    links = load_links()
    ok, err = validate_linkedin_required(links)
    if not ok:
        print(f"ERROR: {err}")
        print("Add fixtures/profile_settings.json with linkedin + resume_path.")
        return 1

    resume = get_default_resume_path()
    print(f"Sandbox enrich — repo: {REPO_ROOT}")
    print(f"Resume: {resume or '(not set)'}")
    print(f"Output profile: {OUTPUT_PROFILE_ENHANCED}")
    print(f"Output requirements: {OUTPUT_REQUIREMENTS_ENHANCED}")

    try:
        sources = collect_all_sources(links, resume_path=resume, headless=args.headless)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    fetch_summary = {k: len(v) for k, v in sources.items()}
    print("Fetched:", fetch_summary)
    OUTPUT_FETCH_LOG.write_text(
        json.dumps({k: {"chars": len(v), "preview": v[:200]} for k, v in sources.items()}, indent=2),
        encoding="utf-8",
    )
    print(f"Fetch log: {OUTPUT_FETCH_LOG}")

    if args.fetch_only:
        print("(--fetch-only) Stopping before LLM / enhanced writes.")
        return 0

    if args.dry_run:
        print("(--dry-run) Would run run_profile_enrich() next. No enhanced files written.")
        return 0

    result = run_profile_enrich(
        links,
        resume_path=resume,
        dual_layer=True,
        headless=args.headless,
        enrich_requirements=True,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "sources"}, indent=2))
    print("Sources:", result.get("sources"))
    if OUTPUT_PROFILE_ENHANCED.exists():
        print(f"\n--- {OUTPUT_PROFILE_ENHANCED.name} (first 120 lines) ---")
        lines = OUTPUT_PROFILE_ENHANCED.read_text(encoding="utf-8").splitlines()
        for line in lines[:120]:
            print(line)
        if len(lines) > 120:
            print(f"... ({len(lines) - 120} more lines)")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Path isolation for enrich sandbox. Patches module-level paths before profile_manager / md_loader load.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SANDBOX_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SANDBOX_ROOT.parent.parent
FIXTURES = SANDBOX_ROOT / "fixtures"
OUTPUT = SANDBOX_ROOT / "output"
REPORTS = SANDBOX_ROOT / "reports"
PROMPT_PACK = REPO_ROOT / "jobhuntr_prompt_pack_rashed.md"

SOURCE_PROFILE = FIXTURES / "source_profile_stub.md"
SOURCE_REQUIREMENTS = FIXTURES / "source_requirements_stub.md"
SANDBOX_PROFILE_SETTINGS = FIXTURES / "profile_settings.json"
OUTPUT_PROFILE_ENHANCED = OUTPUT / "applicant_profile_enhanced.md"
OUTPUT_REQUIREMENTS_ENHANCED = OUTPUT / "applicant_requirements_enhanced.md"
OUTPUT_FETCH_LOG = OUTPUT / "last_fetch_sources.json"


def ensure_dirs() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)


def apply_sandbox_patches() -> None:
    """Redirect all enrich I/O to sandbox/fixtures and sandbox/output."""
    ensure_dirs()
    os.environ["JOBHUNTRR_SANDBOX"] = "1"
    os.environ.setdefault("PROFILE_DUAL_LAYER", "1")
    os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    os.environ.setdefault("OLLAMA_MODEL", "qwen3:8b")

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import config.md_loader as md_loader
    import agents.profile_manager as pm
    from config import env_settings as es

    md_loader._DATA = SANDBOX_ROOT / "_data_mirror"
    md_loader.ENHANCED_DIR = OUTPUT
    md_loader.PROFILE_PATH = SOURCE_PROFILE
    md_loader.REQUIREMENTS_PATH = SOURCE_REQUIREMENTS
    md_loader.PROFILE_ENHANCED_PATH = OUTPUT_PROFILE_ENHANCED
    md_loader.REQUIREMENTS_ENHANCED_PATH = OUTPUT_REQUIREMENTS_ENHANCED

    pm.ROOT = REPO_ROOT
    pm.PROFILE_PATH = SOURCE_PROFILE
    pm.REQUIREMENTS_PATH = SOURCE_REQUIREMENTS
    pm.PROFILE_META_PATH = FIXTURES / "profile_links.json"
    pm.BACKUP_DIR = OUTPUT / "profile_backups"
    pm.ENRICH_STAMP_PATH = OUTPUT / ".last_profile_enrich"
    pm.ENRICH_REQ_STAMP_PATH = OUTPUT / ".last_requirements_enrich"

    es.ROOT = REPO_ROOT
    es.PROFILE_SETTINGS_PATH = (
        SANDBOX_PROFILE_SETTINGS
        if SANDBOX_PROFILE_SETTINGS.exists()
        else FIXTURES / "profile_settings.template.json"
    )

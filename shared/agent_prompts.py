from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_PROMPTS_DIR = REPO_ROOT / "agent-system-prompts"


def resolve_agent_prompts_dir(base_dir: Path | str | None = None) -> Path:
    if base_dir is not None:
        candidate = Path(base_dir)
        if candidate.name == AGENT_PROMPTS_DIR.name and candidate.is_dir():
            return candidate
        nested = candidate / AGENT_PROMPTS_DIR.name
        if nested.is_dir():
            return nested
        if candidate.is_dir() and any(candidate.glob("*.md")):
            return candidate

    return AGENT_PROMPTS_DIR

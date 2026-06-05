"""
Persistent instructions saved from Chat (and /remember).
Injected into Chat + apply-time LLM prompts.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("chat_saved_prompts")

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_FILE = ROOT / "data" / "chat_saved_prompts.md"
SECTION_TITLE = "Chat saved prompts"

_AUTO_SAVE_PATTERNS = (
    re.compile(r"^/remember\s+", re.I),
    re.compile(r"^remember\s*:", re.I),
    re.compile(r"^save\s*(this|instruction)\s*:", re.I),
    re.compile(r"\bfrom now on\b", re.I),
    re.compile(r"\bfor this run\b", re.I),
    re.compile(r"\balways (avoid|use|mention|include|exclude)\b", re.I),
    re.compile(r"\b(never|don'?t|do not) mention\b", re.I),
    re.compile(r"\bavoid mentioning\b", re.I),
    re.compile(r"\bdo not (apply|use|include|mention)\b", re.I),
)


def _ensure_file() -> None:
    PROMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROMPTS_FILE.exists():
        PROMPTS_FILE.write_text(
            f"# {SECTION_TITLE}\n\n"
            "Instructions you save here are used by **Chat** and **Apply** "
            "(form answers, cover letters, etc.).\n\n"
            "## Active instructions\n\n",
            encoding="utf-8",
        )


def load_prompts() -> list[str]:
    """Bullet lines under ## Active instructions."""
    _ensure_file()
    text = PROMPTS_FILE.read_text(encoding="utf-8")
    m = re.search(
        r"## Active instructions\s*(.*?)(?=\n## |\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    items: list[str] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.startswith("* "):
            items.append(line[2:].strip())
    return items


def _strip_saved_suffix(text: str) -> str:
    return re.sub(r"\s*_\(saved[^)]*\)_\s*$", "", (text or "").strip())


def prompts_block_for_agent() -> str:
    items = [_strip_saved_suffix(p) for p in load_prompts()]
    items = [p for p in items if p]
    if not items:
        return ""
    lines = "\n".join(f"- {p}" for p in items)
    return (
        f"CHAT SAVED PROMPTS (user instructions — follow for this session/run):\n"
        f"{lines}"
    )


def _normalize_instruction(text: str) -> str:
    text = (text or "").strip()
    for pat in (_AUTO_SAVE_PATTERNS[:3]):
        m = pat.match(text)
        if m:
            text = pat.sub("", text, count=1).strip()
            break
    if text.lower().startswith("/remember"):
        text = text[9:].strip()
    return text


def should_auto_save_instruction(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 8:
        return False
    if t.lower().startswith("/remember"):
        return True
    return any(p.search(t) for p in _AUTO_SAVE_PATTERNS)


def append_prompt(text: str) -> bool:
    """Append instruction if not duplicate. Returns True if added."""
    text = _normalize_instruction(text)
    if not text or len(text) < 4:
        return False
    existing = [p.lower() for p in load_prompts()]
    if text.lower() in existing:
        return False

    _ensure_file()
    body = PROMPTS_FILE.read_text(encoding="utf-8")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {text} _(saved {ts})_\n"
    if "## Active instructions" in body:
        body = re.sub(
            r"(## Active instructions\s*)",
            r"\1\n" + entry,
            body,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        body += f"\n## Active instructions\n\n{entry}"
    PROMPTS_FILE.write_text(body, encoding="utf-8")
    logger.info("Chat saved prompt: %s", text[:80])
    return True


def remove_prompt(index: int) -> bool:
    items = load_prompts()
    if index < 0 or index >= len(items):
        return False
    del items[index]
    _write_prompts_list(items)
    return True


def clear_prompts() -> None:
    _write_prompts_list([])


def _write_prompts_list(items: list[str]) -> None:
    _ensure_file()
    body = PROMPTS_FILE.read_text(encoding="utf-8")
    block = "## Active instructions\n\n"
    if items:
        block += "\n".join(f"- {p}" for p in items) + "\n"
    if re.search(r"## Active instructions", body, re.I):
        body = re.sub(
            r"## Active instructions\s*.*?(?=\n## |\Z)",
            block.rstrip() + "\n",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )
    else:
        body += "\n" + block
    PROMPTS_FILE.write_text(body, encoding="utf-8")

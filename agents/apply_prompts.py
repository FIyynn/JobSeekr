"""Non-blocking apply-time decisions for AFK operation."""

from __future__ import annotations

import logging
import os
from typing import Callable

logger = logging.getLogger("apply_prompts")

# Kept as exported flags for backwards compatibility with older call sites.
UNATTENDED_APPLY = True
INTERACTIVE_APPLY = False


def register_prompt_handler(handler: Callable) -> None:
    """Compatibility no-op: apply automation never opens dialogs."""
    logger.debug("Ignoring apply prompt handler registration in unattended mode")


def clear_prompt_handler() -> None:
    """Compatibility no-op."""


def prompt_text(title: str, message: str, default: str = "") -> str:
    logger.info("Unattended apply - using default for: %s", title)
    return default


def prompt_confirm(title: str, message: str) -> bool:
    logger.info("Unattended apply - auto-confirming: %s", title)
    return True


def prompt_user_action(message: str, url: str = "", timeout_sec: int = 300) -> bool:
    """Never block on CAPTCHA, MFA, email verification, or signup walls."""
    logger.warning("Unattended apply - manual action deferred: %s", message)
    return False


def prompt_skill_gaps(missing: list[str]) -> dict[str, str]:
    """Do not invent missing skills or interrupt the user."""
    if missing:
        logger.info("Unattended apply - leaving %d profile gap(s) unanswered", len(missing))
    return {}


def prompt_tailor_resume_approval(job: dict) -> bool:
    """Use the configured standard resume without asking."""
    return False


def maybe_confirm_answer(question: str, ai_answer: str, qa: dict) -> str:
    """Use grounded generated answers without opening a confirmation dialog."""
    return ai_answer

"""Local Ollama chat helper for the JobHuntrr GUI."""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("ollama_chat")


class OllamaModelNotFoundError(RuntimeError):
    """Raised when the configured model is not installed locally."""

    def __init__(self, model: str, installed: list[str]):
        self.model = model
        self.installed = installed
        hint = f"ollama pull {model}"
        if installed:
            hint += f"\n\nInstalled models: {', '.join(installed[:8])}"
            if len(installed) > 8:
                hint += ", …"
        super().__init__(
            f"Model '{model}' is not installed in Ollama.\n"
            f"Open a terminal and run:\n  {hint}\n\n"
            f"Then retry Chat (or restart JobHuntrr if the pull was already running)."
        )


def list_local_models(base_url: str, timeout: int = 10) -> list[str]:
    """Return model names from `ollama list` via the HTTP API."""
    url = base_url.rstrip("/")
    try:
        r = requests.get(f"{url}/api/tags", timeout=timeout)
        r.raise_for_status()
        names: list[str] = []
        for m in r.json().get("models") or []:
            name = (m.get("name") or "").strip()
            if name:
                names.append(name)
        return names
    except Exception as e:
        logger.debug("list_local_models failed: %s", e)
        return []


def _model_is_installed(model: str, installed: list[str]) -> bool:
    if not model:
        return False
    base = model.split(":")[0]
    for name in installed:
        if name == model or name.startswith(f"{model}:") or name.startswith(f"{base}:"):
            return True
        if name.split(":")[0] == base:
            return True
    return False


def ensure_model_available(model: str, base_url: str) -> None:
    installed = list_local_models(base_url)
    if installed and not _model_is_installed(model, installed):
        raise OllamaModelNotFoundError(model, installed)


def _agent_rules() -> str:
    try:
        from config.apply_agent_rules import rules_block
        return rules_block()
    except Exception:
        return ""


def _resume_path_line() -> str:
    try:
        from config.apply_agent_rules import get_resume_path
        p = get_resume_path()
        return f"The apply agent uploads this resume file (from Profile Settings): {p}"
    except Exception:
        return "Resume path is set in Profile Settings → Resume PDF path."


CHAT_CAPABILITIES = """
JOBHUNTRR CHAT — CAPABILITIES (read carefully):

You are the in-app Chat assistant only. You CANNOT:
- Apply to jobs, open browsers, click Workday/Greenhouse buttons, or upload files yourself
- Change settings unless the user uses Profile Settings or saves a Chat instruction
- Ask for Google Drive/Dropbox links — the resume is already a local file on disk

To test queued applications without submitting, use Jobs tab -> "Test queued form fill (NO SUBMIT)".
To submit queued applications, use "Apply queued jobs now (LIVE)".
To search, score, and submit eligible jobs in one run, use "Search + apply now (LIVE)".
That automation uses Playwright to click "Autofill with resume" (Workday) or "Autofill with Resume" (Greenhouse)
and attaches the PDF from Profile Settings — not LinkedIn autofill.

When the user gives a standing instruction (e.g. "avoid mentioning ADIA", "for this run don't say X"):
- Tell them it was or can be saved under "Chat saved prompts" (used by Chat and Apply).
- Do not claim you already changed the browser or submitted an application.

Resume for applications:
{resume_line}

If the user wants a different resume file, tell them to use Profile Settings → Resume PDF path → Browse/Upload → Save all settings.
""".strip()


DEFAULT_SYSTEM = (
    "You are a helpful assistant inside JobHuntrr, a local UAE job-search agent. "
    "Answer clearly and concisely. "
    "When drafting application or interview answers, use first person (\"I\", \"my\") — "
    "never third person (\"Rashed is…\"). "
    "Use ONLY facts from the applicant profile and grounding rules below. "
    "Never invent employers, projects, internships, or metrics."
)


def _is_application_practice(messages: list[dict]) -> bool:
    """True when the user is practicing application / behavioral answers."""
    text = " ".join(
        (m.get("content") or "")
        for m in messages
        if (m.get("role") or "").lower() == "user"
    ).lower()
    hints = (
        "application", "interview", "behavioral", "cover letter", "why us",
        "tell me about a project", "critical thinking", "problem solving",
        "difficulty", "challenge you faced", "form question", "how would you answer",
        "assume you scrolled", "screening question",
    )
    return any(h in text for h in hints)


def _raise_for_ollama_http(err: requests.HTTPError, model: str, base_url: str) -> None:
    """Turn Ollama 404 (missing model) into a clear user-facing error."""
    resp = err.response
    if resp is not None and resp.status_code == 404:
        installed = list_local_models(base_url)
        if not _model_is_installed(model, installed):
            raise OllamaModelNotFoundError(model, installed) from err
    raise err


def chat_completion(
    messages: list[dict],
    model: str,
    base_url: str,
    *,
    timeout: int = 180,
) -> str:
    """
    Send a conversation to Ollama. Uses /api/chat; falls back to /api/generate.
    messages: [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
    """
    url = base_url.rstrip("/")
    ensure_model_available(model, base_url)
    temp = 0.25 if _is_application_practice(messages) else 0.4
    # Disable chain-of-thought thinking for qwen3 (speeds up responses significantly)
    if "qwen3" in model.lower():
        messages = [
            {**m, "content": m["content"].rstrip() + "\n/no_think"}
            if m.get("role") == "user" else m
            for m in messages
        ]
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temp, "num_predict": 2048},
    }
    try:
        r = requests.post(f"{url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        content = (r.json().get("message") or {}).get("content", "")
        if content.strip():
            return content.strip()
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            "Cannot reach Ollama. Start it with: ollama serve"
        ) from e
    except requests.HTTPError as e:
        _raise_for_ollama_http(e, model, base_url)
    except OllamaModelNotFoundError:
        raise
    except Exception as e:
        logger.warning("Ollama /api/chat failed (%s), trying /api/generate", e)

    user_text = ""
    system_text = DEFAULT_SYSTEM
    for m in messages:
        role = (m.get("role") or "").lower()
        if role == "system":
            system_text = m.get("content") or system_text
        elif role == "user":
            user_text = m.get("content") or user_text

    prompt = f"{system_text}\n\nUser: {user_text}\n\nAssistant:"
    try:
        r = requests.post(
            f"{url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temp, "num_predict": 2048},
            },
            timeout=timeout,
        )
        r.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            "Cannot reach Ollama. Start it with: ollama serve"
        ) from e
    except requests.HTTPError as e:
        _raise_for_ollama_http(e, model, base_url)
    return (r.json().get("response") or "").strip()


def build_system_prompt(
    *,
    include_profile: bool = False,
    include_requirements: bool = False,
    selected_job: Optional[dict] = None,
    include_saved_prompts: bool = True,
    last_user_text: str = "",
) -> str:
    caps = CHAT_CAPABILITIES.format(resume_line=_resume_path_line())
    rules = _agent_rules()
    parts = [DEFAULT_SYSTEM, caps]
    if rules:
        parts.append(rules)
    # Compact facts so Chat stays grounded even if full profile toggle is off
    try:
        from config.config import APPLICATION_QA
        from config.profile_grounding import anchors_reference_block, format_applicant_facts
        facts = format_applicant_facts(APPLICATION_QA)
        if facts.strip():
            parts.append("\n--- APPLICANT FACT SHEET ---\n" + facts)
        parts.append("\n--- " + anchors_reference_block())
    except Exception:
        pass
    if include_saved_prompts:
        try:
            from agents.chat_saved_prompts import prompts_block_for_agent
            block = prompts_block_for_agent()
            if block:
                parts.append(block)
        except Exception:
            pass
    try:
        from config.profile_grounding import get_profile_excerpt, pick_anchor_for_question
        excerpt = get_profile_excerpt(6000)
        if excerpt.strip():
            parts.append("\n\n--- APPLICANT PROFILE ---\n" + excerpt)
        if last_user_text.strip() and _is_application_practice(
            [{"role": "user", "content": last_user_text}]
        ):
            anchor = pick_anchor_for_question(last_user_text)
            parts.append(
                f"\n--- TOPIC FOR THIS ANSWER (use only this experience) ---\n"
                f"{anchor['title']}: {anchor['summary']}"
            )
    except Exception as e:
        logger.warning("Profile context unavailable: %s", e)
    if include_requirements:
        try:
            from config.md_loader import load_requirements_sections
            sec = load_requirements_sections()
            main = (sec.get("_main") or "")[:4000]
            if main.strip():
                parts.append("\n\n--- JOB REQUIREMENTS ---\n" + main)
        except Exception as e:
            logger.warning("Requirements context unavailable: %s", e)
    if selected_job:
        parts.append(
            "\n\n--- SELECTED JOB ---\n"
            f"Title: {selected_job.get('title', '')}\n"
            f"Company: {selected_job.get('company', '')}\n"
            f"Location: {selected_job.get('location', '')}\n"
            f"Score: {selected_job.get('score', '')}\n"
            f"Fit: {(selected_job.get('fit_reason') or '')[:1500]}\n"
            f"Description excerpt: {(selected_job.get('description') or '')[:2000]}"
        )
    return "\n".join(parts)

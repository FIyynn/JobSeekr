from __future__ import annotations

import html as _html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.linkedin import linkedin
from services.web import webagent
import services.web.markdown as markdown_tools
from shared.agent_prompts import resolve_agent_prompts_dir


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[2]

FINAL_RESPONSE_TAG = "final_response"
CMD_TAG = "cmd"

WEBUSE_TOOLS = {
    "webagent_fetch_page",
    "webagent_click",
    "webagent_type",
    "webagent_clear_text",
}

LINKEDIN_TOOLS = {
    "linkedin.fetch_job_listings",
    "linkedin.fetch_listings_description",
}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "webagent_fetch_page": {"required": {"url"}, "optional": set()},
    "webagent_click": {"required": {"target_id"}, "optional": set()},
    "webagent_type": {"required": {"target_id", "text"}, "optional": {"click_enter"}},
    "webagent_clear_text": {"required": {"target_id"}, "optional": set()},
    "linkedin.fetch_job_listings": {
        "required": {"keyword", "location"},
        "optional": {"filter_by", "filters"},
    },
    "linkedin.fetch_listings_description": {
        "required": {"listing_id"},
        "optional": {"listing_id"},
    },
}

DEFAULT_COLORS = {
    "user": "#111827",
    "plan": "#2563eb",
    "llm": "#16a34a",
    "cmd": "#ca8a04",
    "output": "#a16207",
    "final": "#db2777",
    "error": "#dc2626",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_instruction_bundle(base_dir: Path | None = None) -> dict[str, str]:
    root = resolve_agent_prompts_dir(base_dir)
    return {
        "webuse": read_text(root / "webuse_tool_instructions.md"),
        "loop": read_text(root / "webuse_loop.md"),
        "plan": read_text(root / "webuse_plan.md"),
        "reflection": read_text(root / "webuse_reflection.md"),
        "finalize": read_text(root / "webuse_finalize.md"),
        "tool": read_text(root / "webuse_tool_instructions.md"),
    }


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def _escape(value: Any) -> str:
    return _html.escape(str(value or ""), quote=False)


def render_colored_block(label: str, text: Any, kind: str = "user") -> str:
    accent = DEFAULT_COLORS.get(kind, "#111827")
    title = _escape(label)
    body = _escape(text)
    return (
        f'<div style="background:transparent; padding:8px 12px; margin:6px 0; '
        f'border-left:4px solid {accent}; white-space:pre-wrap; '
        f'font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; '
        f'font-size:13px; line-height:1.45;">'
        f'<div style="font-weight:700; margin-bottom:4px; color:{accent};">{title}</div>'
        f'<div>{body}</div>'
        f"</div>"
    )


def display_block(label: str, text: Any, kind: str = "user") -> None:
    try:
        from IPython.display import HTML, display
    except Exception:
        print(f"{label}\n{text}", flush=True)
        return
    display(HTML(render_colored_block(label, text, kind=kind)))


def display_json(label: str, value: Any, kind: str = "user") -> None:
    display_block(label, pretty_json(value), kind=kind)


def display_markdown_block(label: str, text: Any, kind: str = "final") -> None:
    try:
        from IPython.display import HTML, Markdown, display
    except Exception:
        print(f"{label}\n{text}", flush=True)
        return
    accent = DEFAULT_COLORS.get(kind, "#111827")
    display(HTML(
        f'<div style="margin:6px 0 2px 0; font-weight:700; color:{accent};">{_escape(label)}</div>'
    ))
    display(Markdown(str(text or "")))


def stop_requested() -> bool:
    if os.name != "nt":
        return False
    try:
        import msvcrt

        if msvcrt.kbhit():
            key = msvcrt.getwch()
            if key == "]":
                return True
    except Exception:
        return False
    return False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _ensure_runtime_memory(runtime: dict[str, Any] | None) -> dict[str, Any]:
    runtime = runtime or {}
    memory = runtime.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        runtime["memory"] = memory
    memory.setdefault("task", _clean(runtime.get("task", "")))
    memory.setdefault("phase", _clean(runtime.get("phase", "planning")) or "planning")
    memory.setdefault("plan", _clean(runtime.get("last_plan", "")))
    memory.setdefault("last_tool", _clean(runtime.get("last_tool", "")))
    memory.setdefault("last_result", "")
    memory.setdefault("last_target_id", "")
    memory.setdefault("findings", [])
    memory.setdefault("errors", [])
    memory.setdefault("current_web_state", {})
    memory.setdefault("session_state", {})
    memory.setdefault("last_reflection", "")
    memory.setdefault("next_action", "")
    return memory


def _runtime_memory_lines(runtime: dict[str, Any] | None) -> list[str]:
    if not runtime:
        return []
    memory = _ensure_runtime_memory(runtime)
    lines: list[str] = []
    task = _clean(memory.get("task", ""))
    if task:
        lines.append(f"task: {task}")
    phase = _clean(memory.get("phase", ""))
    if phase:
        lines.append(f"phase: {phase}")
    plan = _clean(memory.get("plan", "") or runtime.get("last_plan", ""))
    if plan:
        lines.append(f"plan: {plan[:1000]}")
    last_tool = _clean(memory.get("last_tool", "") or runtime.get("last_tool", ""))
    if last_tool:
        lines.append(f"last tool: {last_tool}")
    last_result = memory.get("last_result") or runtime.get("last_tool_result")
    if isinstance(last_result, dict):
        last_result = format_tool_result_for_llm(last_result, runtime)
    last_result_text = _clean(last_result)
    if last_result_text:
        lines.append(f"last result: {last_result_text[:1000]}")
    last_target_id = _clean(memory.get("last_target_id", ""))
    if last_target_id:
        lines.append(f"last target id: {last_target_id}")
    last_reflection = _clean(memory.get("last_reflection", ""))
    if last_reflection:
        lines.append(f"last reflection: {last_reflection[:800]}")
    next_action = _clean(memory.get("next_action", ""))
    if next_action:
        lines.append(f"next action: {next_action[:400]}")
    current_web_state = memory.get("current_web_state") or runtime.get("current_web_state")
    if isinstance(current_web_state, dict):
        current_url = _clean(current_web_state.get("current_url", ""))
        title = _clean(current_web_state.get("title", ""))
        summary = _clean(current_web_state.get("summary", ""))
        counts = current_web_state.get("counts")
        if current_url:
            lines.append(f"page url: {current_url}")
        if title:
            lines.append(f"page title: {title}")
        if summary:
            lines.append(f"page summary: {summary[:160]}")
        if isinstance(counts, dict) and counts:
            rendered_counts = ", ".join(f"{_clean(k)}={counts[k]}" for k in counts if _clean(k))
            if rendered_counts:
                lines.append(f"page counts: {rendered_counts}")
    findings = memory.get("findings")
    if isinstance(findings, list) and findings:
        rendered = "; ".join(_clean(item) for item in findings[-5:] if _clean(item))
        if rendered:
            lines.append(f"findings: {rendered[:1000]}")
    errors = memory.get("errors")
    if isinstance(errors, list) and errors:
        rendered = "; ".join(_clean(item) for item in errors[-3:] if _clean(item))
        if rendered:
            lines.append(f"errors: {rendered[:1000]}")
    return lines


def _sync_runtime_memory(
    runtime: dict[str, Any] | None,
    *,
    phase: str | None = None,
    plan_text: str | None = None,
    tool_name: str | None = None,
    tool_result: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    if runtime is None:
        return
    memory = _ensure_runtime_memory(runtime)
    if phase:
        memory["phase"] = _clean(phase) or memory.get("phase", "planning")
    if plan_text is not None:
        memory["plan"] = _clean(plan_text)
        runtime["last_plan"] = _clean(plan_text)
    if tool_name is not None:
        memory["last_tool"] = _clean(tool_name)
        runtime["last_tool"] = _clean(tool_name)
    if tool_result is not None:
        memory["last_result"] = tool_result
        runtime["last_tool_result"] = tool_result
        target_id = _clean(tool_result.get("target_id", "") or tool_result.get("requested_target_id", ""))
        if target_id:
            memory["last_target_id"] = target_id
        session_state = tool_result.get("session_state")
        if isinstance(session_state, dict):
            memory["session_state"] = session_state
        current_web_state = tool_result.get("current_web_state")
        if isinstance(current_web_state, dict):
            memory["current_web_state"] = current_web_state
    if note:
        note_text = _clean(note)
        memory["last_reflection"] = note_text
        match = re.search(r"(?im)^\s*next action:\s*(.+)$", note_text)
        if match:
            memory["next_action"] = _clean(match.group(1))
        findings = memory.setdefault("findings", [])
        if isinstance(findings, list):
            findings.append(note_text)


def _reset_task_runtime(runtime: dict[str, Any], task: str) -> None:
    runtime["task"] = task
    runtime["phase"] = "planning"
    runtime["last_plan"] = ""
    runtime["last_tool"] = ""
    runtime["last_tool_result"] = None
    runtime["stuck_counts"] = {}
    runtime["current_web_state"] = {}
    runtime["session_state"] = {}
    runtime["session_outputs"] = []
    memory = _ensure_runtime_memory(runtime)
    memory["task"] = task
    memory["phase"] = "planning"
    memory["plan"] = ""
    memory["last_tool"] = ""
    memory["last_result"] = ""
    memory["last_target_id"] = ""
    memory["findings"] = []
    memory["errors"] = []
    memory["current_web_state"] = {}
    memory["session_state"] = {}
    memory["last_reflection"] = ""
    memory["next_action"] = ""


def _runtime_recent_lines(runtime: dict[str, Any] | None, limit: int = 8, width: int = 1200) -> list[str]:
    if not runtime:
        return []
    session_outputs = runtime.get("session_outputs") or []
    lines: list[str] = []
    for entry in session_outputs[-limit:]:
        if not isinstance(entry, dict):
            continue
        kind = _clean(entry.get("kind", "")) or "item"
        label = _clean(entry.get("label", ""))
        text = entry.get("text", "")
        rendered = text if isinstance(text, str) else pretty_json(text)
        rendered = _clean(rendered)
        if rendered:
            prefix = f"- {kind}"
            if label:
                prefix += f" {label}"
            lines.append(f"{prefix}: {rendered[:width]}")
    return lines


def _runtime_state_lines(runtime: dict[str, Any] | None) -> list[str]:
    if not runtime:
        return []
    lines: list[str] = []
    session_state = runtime.get("session_state")
    if isinstance(session_state, dict):
        current_url = _clean(session_state.get("current_url", ""))
        if current_url:
            lines.append(f"url: {current_url}")
        title = _clean(session_state.get("title", ""))
        if title:
            lines.append(f"title: {title}")
        target_id = _clean(session_state.get("target_id", ""))
        if target_id:
            lines.append(f"target: {target_id}")
        action = _clean(session_state.get("action", ""))
        if action:
            lines.append(f"action: {action}")
    current_web_state = runtime.get("current_web_state")
    if isinstance(current_web_state, dict):
        for key in ("current_url", "title", "summary"):
            value = _clean(current_web_state.get(key, ""))
            if value:
                lines.append(f"{key.replace('_', ' ')}: {value}")
        counts = current_web_state.get("counts")
        if isinstance(counts, dict) and counts:
            rendered_counts = ", ".join(f"{_clean(k)}={counts[k]}" for k in counts if _clean(k))
            if rendered_counts:
                lines.append(f"counts: {rendered_counts}")
    markdown_text = _clean(runtime.get("markdown_text", ""))
    if markdown_text:
        first_nonempty = next((line.strip() for line in markdown_text.splitlines() if line.strip()), "")
        if first_nonempty:
            lines.append(f"page: {first_nonempty[:160]}")
    return lines


def _phase_system_messages(bundle: dict[str, str], phase: str) -> list[str]:
    phase_name = _clean(phase).casefold() or "planning"
    if phase_name == "planning":
        block = _clean(bundle.get("plan", ""))
    elif phase_name == "reflection":
        block = _clean(bundle.get("reflection", ""))
    elif phase_name == "finalize":
        block = _clean(bundle.get("finalize", ""))
    elif phase_name == "tool":
        block = "\n\n".join(
            part
            for part in (
                _clean(bundle.get("tool", "")),
                _clean(bundle.get("loop", "")),
            )
            if part
        )
    else:
        block = _clean(bundle.get("loop", "")) or _clean(bundle.get("webuse", ""))
    return [block] if block else []


def _phase_user_prompt(task: str, phase: str, runtime: dict[str, Any] | None = None) -> str:
    phase_name = _clean(phase).casefold() or "planning"
    runtime = runtime or {}
    memory_lines = _runtime_memory_lines(runtime)
    task_text = _clean(task)
    last_plan = _clean(runtime.get("last_plan", ""))
    last_tool = _clean(runtime.get("last_tool", ""))
    last_result = runtime.get("last_tool_result")
    last_result_text = ""
    if isinstance(last_result, dict):
        last_result_text = format_tool_result_for_llm(last_result, runtime)
    elif last_result:
        last_result_text = _clean(last_result)
    recent_lines = _runtime_recent_lines(runtime)
    state_lines = _runtime_state_lines(runtime)
    if memory_lines:
        state_lines = list(state_lines) + ["", "Persistent runtime memory:", *memory_lines]

    if phase_name == "planning":
        lines = [
            "Task:",
            task_text,
            "",
            "Create the first action plan only.",
            "Do not use tools yet.",
            "Do not write command syntax or tool names.",
            "Focus on the next page or control to inspect, what evidence matters, and why.",
        ]
        if state_lines:
            lines.extend(["", "Current state:", *state_lines])
        return "\n".join(lines)

    if phase_name == "reflection":
        lines = [
            "Task:",
            task_text,
            "",
            "Current plan:",
            last_plan or "none yet",
            "",
            "Latest tool:",
            last_tool or "none yet",
            "",
            "Latest result:",
            last_result_text or "none yet",
        ]
        if state_lines:
            lines.extend(["", "Current state:", *state_lines])
        if recent_lines:
            lines.extend(["", "Recent context:", *recent_lines])
        lines.extend(
            [
                "",
                "This is the reflection phase immediately after a tool call.",
                "Reflect on what changed, what was completed, what is still missing, and whether the task is complete.",
                "Do not rewrite the plan from scratch.",
                "Start with exactly one line: Decision: complete or Decision: continue.",
                "If continuing, keep it short and give the next action clearly as one tool-worthy step using the exact phrase 'Next action: click target_id ...' or 'Next action: type target_id ...'.",
            ]
        )
        return "\n".join(lines)

    if phase_name == "finalize":
        lines = [
            "Task:",
            task_text,
            "",
            "Write only the final response.",
            "Do not mention planning or tool syntax.",
        ]
        if state_lines:
            lines.extend(["", "Current state:", *state_lines])
        if recent_lines:
            lines.extend(["", "Recent context:", *recent_lines])
        return "\n".join(lines)

    lines = [
        "Task:",
        task_text,
        "",
        "Use one tool call at a time.",
        "Return exactly one <cmd>...</cmd> or one <final_response>...</final_response>.",
        "If the previous reflection included a 'Next action:' line, follow it exactly, including the same target_id.",
    ]
    if state_lines:
        lines.extend(["", "Current state:", *state_lines])
    if recent_lines:
        lines.extend(["", "Recent context:", *recent_lines])
    return "\n".join(lines)


def _extract_first_tag(text: str, tag: str) -> str | None:
    pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.S | re.I)
    match = pattern.search(text or "")
    if not match:
        return None
    return match.group(1).strip()


def _first_unclosed_protocol_tag(raw: str) -> str | None:
    for tag in (CMD_TAG, "output", FINAL_RESPONSE_TAG):
        opens = len(re.findall(rf"<{tag}>", raw, re.S | re.I))
        closes = len(re.findall(rf"</{tag}>", raw, re.S | re.I))
        if opens > closes:
            return tag
    return None


def parse_model_output(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {"kind": "empty", "text": ""}

    unclosed_tag = _first_unclosed_protocol_tag(raw)
    if unclosed_tag:
        return {
            "kind": "error",
            "message": f"Unclosed <{unclosed_tag}> tag. Use the matching </{unclosed_tag}> closing tag.",
            "text": raw,
        }

    final_text = _extract_first_tag(raw, FINAL_RESPONSE_TAG)
    if final_text is not None:
        return {"kind": "final_response", "text": final_text}

    cmd_matches = list(re.finditer(r"<cmd>(.*?)</cmd>", raw, re.S | re.I))
    if len(cmd_matches) > 1:
        return {"kind": "error", "message": "Multiple <cmd> blocks found.", "text": raw}
    if len(cmd_matches) == 1:
        return parse_command_block(cmd_matches[0].group(1).strip())

    return {"kind": "text", "text": raw}


def parse_command_block(block: str) -> dict[str, Any]:
    candidate = (block or "").strip()
    match = re.fullmatch(r"(?P<tool>[A-Za-z_][\w\.]*)\((?P<args>.*)\)", candidate, re.S)
    if not match:
        return {"kind": "error", "message": "Malformed command block.", "raw": candidate}

    tool_name = match.group("tool").strip()
    args_text = match.group("args").strip()
    if not args_text:
        args: dict[str, Any] = {}
    else:
        try:
            args_obj = json.loads(args_text)
        except Exception as exc:
            return {"kind": "error", "message": f"Invalid JSON args: {exc}", "tool": tool_name, "raw": candidate}
        if not isinstance(args_obj, dict):
            return {"kind": "error", "message": "Tool args must be a JSON object.", "tool": tool_name, "raw": candidate}
        args = args_obj

    ok, error = validate_tool_args(tool_name, args)
    if not ok:
        return {"kind": "error", "message": error, "tool": tool_name, "args": args}
    return {"kind": "command", "tool": tool_name, "args": args, "raw": candidate}


def validate_tool_args(tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        return False, f"Unknown tool: {tool_name}"

    required = set(schema.get("required", set()))
    optional = set(schema.get("optional", set()))
    required_any = schema.get("required_any") or []
    allowed = set(required) | set(optional)
    for group in required_any:
        allowed |= set(group)

    for key in args:
        if key not in allowed:
            return False, f"Unexpected argument for {tool_name}: {key}"

    missing = sorted(key for key in required if key not in args)
    if missing:
        return False, f"Missing required argument(s) for {tool_name}: {', '.join(missing)}"

    if required_any:
        if not any(any(key in args for key in group) for group in required_any):
            rendered = " or ".join(" / ".join(sorted(group)) for group in required_any)
            return False, f"Missing required argument(s) for {tool_name}: {rendered}"
    return True, ""


def _read_api_key(api_key_path: str | Path | None) -> str:
    if not api_key_path:
        env_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if env_key:
            return env_key
        raise ValueError("Missing OpenAI API key path and OPENAI_API_KEY environment variable.")
    path = Path(api_key_path)
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError(f"OpenAI API key file is empty: {path}")
    return key


def _extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("No choices returned from OpenAI.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, dict):
                    parts.append(str(text.get("value") or ""))
                else:
                    parts.append(str(text or ""))
        if parts:
            return "".join(parts)
    return str(message.get("content") or "")


def call_openai_chat(
    api_key_path: str | Path,
    model: str,
    messages: list[dict[str, str]],
    reasoning_effort: str = "low",
    max_completion_tokens: int = 1024,
    timeout: int = 120,
    base_url: str = "https://api.openai.com/v1/chat/completions",
) -> str:
    api_key = _read_api_key(api_key_path)
    payload = {
        "model": model,
        "messages": messages,
        "reasoning_effort": reasoning_effort,
        "max_completion_tokens": max_completion_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"OpenAI API error: {exc.code} {exc.reason} {body}".strip()) from exc
    data = json.loads(raw)
    return _extract_chat_completion_text(data)


def call_llama_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout: int = 120,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"llama server error: {exc.code} {exc.reason} {body}".strip()) from exc
    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("No choices returned from llama server.")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def call_model_chat(
    backend: str,
    *,
    messages: list[dict[str, str]],
    api_key_path: str | Path | None = None,
    openai_model: str = "gpt-5.4-mini",
    openai_base_url: str = "https://api.openai.com/v1/chat/completions",
    openai_reasoning_effort: str = "low",
    llama_model: str = "qwen3.5-9b",
    llama_base_url: str = "http://127.0.0.1:8080/v1/chat/completions",
    llama_temperature: float = 0.2,
    max_completion_tokens: int = 1024,
    timeout: int = 120,
) -> str:
    backend_name = str(backend or "").strip().casefold()
    if backend_name in {"local", "llama", "llama.cpp", "llamacpp"}:
        return call_llama_chat(
            llama_base_url,
            llama_model,
            messages,
            temperature=llama_temperature,
            max_tokens=max_completion_tokens,
            timeout=timeout,
        )
    if backend_name in {"openai", "api"}:
        if api_key_path is None:
            raise ValueError("api_key_path is required for the OpenAI backend.")
        return call_openai_chat(
            api_key_path,
            openai_model,
            messages,
            reasoning_effort=openai_reasoning_effort,
            max_completion_tokens=max_completion_tokens,
            timeout=timeout,
            base_url=openai_base_url,
        )
    raise ValueError(f"Unknown backend: {backend}")


def call_model_chat_with_retry(
    backend: str,
    *,
    messages: list[dict[str, str]],
    api_key_path: str | Path | None = None,
    openai_model: str = "gpt-5.4-mini",
    openai_base_url: str = "https://api.openai.com/v1/chat/completions",
    openai_reasoning_effort: str = "low",
    llama_model: str = "qwen3.5-9b",
    llama_base_url: str = "http://127.0.0.1:8080/v1/chat/completions",
    llama_temperature: float = 0.2,
    max_completion_tokens: int = 1024,
    timeout: int = 120,
    retries: int = 2,
) -> str:
    attempt_tokens = max_completion_tokens
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return call_model_chat(
                backend,
                messages=messages,
                api_key_path=api_key_path,
                openai_model=openai_model,
                openai_base_url=openai_base_url,
                openai_reasoning_effort=openai_reasoning_effort,
                llama_model=llama_model,
                llama_base_url=llama_base_url,
                llama_temperature=llama_temperature,
                max_completion_tokens=attempt_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            attempt_tokens = max(128, attempt_tokens // 2)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Model call failed without a captured error.")


def _refresh_snapshot(runtime: dict[str, Any]) -> None:
    driver = runtime.get("driver")
    if driver is None:
        return
    try:
        markdown_text, dev = markdown_tools.output_markdown(driver)
    except Exception as exc:
        runtime["snapshot_error"] = str(exc)
        return
    runtime["markdown_text"] = markdown_text
    runtime["interactables"] = dev
    runtime["last_dev"] = dev
    counts = dev.get("counts", {}) if isinstance(dev, dict) else {}
    runtime["current_web_state"] = {
        "current_url": getattr(driver, "current_url", "") or "",
        "title": getattr(driver, "title", "") or "",
        "summary": next((line.strip() for line in (markdown_text or "").splitlines() if line.strip()), ""),
        "counts": counts if isinstance(counts, dict) else {},
    }


def execute_tool_call(tool_name: str, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    driver = runtime.get("driver")
    if driver is None:
        return {"status": "error", "message": "Missing driver in runtime."}

    verbose = bool(runtime.get("verbose", True))
    log_path = runtime.get("log_path")
    store = runtime.get("store")
    delays = runtime.get("delays") or {}
    default_delay = 2

    if tool_name == "webagent_fetch_page":
        result = webagent.webagent_fetch_page(
            driver,
            args["url"],
            log_path=log_path,
            store=store,
            verbose=verbose,
            wait_seconds=runtime.get("wait_seconds", 0),
        )
        runtime["markdown_text"] = result.get("markdown", "")
        runtime["interactables"] = result.get("dev", {})
        runtime["session_state"] = result.get("session_state", {})
        runtime["current_web_state"] = {
            "current_url": result.get("session_state", {}).get("current_url", "") if isinstance(result.get("session_state"), dict) else "",
            "title": getattr(driver, "title", "") or "",
            "summary": next((line.strip() for line in (result.get("markdown", "") or "").splitlines() if line.strip()), ""),
            "counts": result.get("dev", {}).get("counts", {}) if isinstance(result.get("dev"), dict) else {},
        }
        return result

    if tool_name == "webagent_click":
        result = webagent.webagent_click(
            driver,
            runtime.get("markdown_text", ""),
            runtime.get("interactables", {}),
            args["target_id"],
            delay_seconds=delays.get("webagent_click", default_delay),
            log_path=log_path,
            store=store,
            verbose=verbose,
        )
        _refresh_snapshot(runtime)
        return result

    if tool_name == "webagent_type":
        try:
            result = webagent.webagent_type(
                driver,
                runtime.get("markdown_text", ""),
                runtime.get("interactables", {}),
                args["target_id"],
                args["text"],
                click_enter=bool(args.get("click_enter", False)),
                delay_seconds=delays.get("webagent_type", default_delay),
                log_path=log_path,
                store=store,
                verbose=verbose,
            )
        except TypeError:
            result = webagent.webagent_type(
                driver,
                runtime.get("markdown_text", ""),
                runtime.get("interactables", {}),
                args["target_id"],
                args["text"],
                delay_seconds=delays.get("webagent_type", default_delay),
                log_path=log_path,
                store=store,
                verbose=verbose,
            )
        _refresh_snapshot(runtime)
        return result

    if tool_name == "webagent_clear_text":
        result = webagent.webagent_clear_text(
            driver,
            runtime.get("markdown_text", ""),
            runtime.get("interactables", {}),
            args["target_id"],
            delay_seconds=delays.get("webagent_clear_text", default_delay),
            log_path=log_path,
            store=store,
            verbose=verbose,
        )
        _refresh_snapshot(runtime)
        return result

    if tool_name == "linkedin.fetch_job_listings":
        result = linkedin.fetch_job_listings(
            driver,
            keyword=args["keyword"],
            location=args["location"],
            filters=args.get("filters"),
            filter_by=args.get("filter_by", "Jobs"),
            delays=delays,
            log_path=log_path,
            verbose=verbose,
        )
        return result

    if tool_name == "linkedin.fetch_listings_description":
        result = linkedin.fetch_listings_description(
            driver,
            runtime.get("last_listing_payload", runtime.get("listings_json", {})),
            listing_id=args.get("listing_id"),
            delays=delays,
            log_path=log_path,
            verbose=verbose,
        )
        return result

    return {"status": "error", "message": f"Unsupported tool: {tool_name}"}


def build_runtime_prompt(task: str, bundle: dict[str, str], phase: str = "planning", runtime: dict[str, Any] | None = None) -> str:
    return _phase_user_prompt(task, phase, runtime or {})


def build_messages(
    task: str,
    bundle: dict[str, str],
    phase: str = "planning",
    runtime: dict[str, Any] | None = None,
    include_plan: bool | None = None,
) -> list[dict[str, str]]:
    runtime = runtime or {}
    _ensure_runtime_memory(runtime)
    messages: list[dict[str, str]] = []
    for block in _phase_system_messages(bundle, phase):
        if block:
            messages.append({"role": "system", "content": block})
    messages.append({"role": "user", "content": _phase_user_prompt(task, phase, runtime or {})})
    return messages


def append_transcript_text(runtime: dict[str, Any], kind: str, label: str, text: Any) -> None:
    transcript = runtime.setdefault("session_outputs", [])
    entry = {"kind": kind, "label": label, "text": text if isinstance(text, str) else pretty_json(text)}
    transcript.append(entry)


def _format_diffs_plain(diffs: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    changed = diffs.get("changed") or []
    added = diffs.get("added") or []
    deleted_count = diffs.get("deleted_element_count")
    if changed:
        lines.append("changed:")
        lines.extend(f"- {item}" for item in changed)
    if added:
        lines.append("added:")
        lines.extend(f"- {item}" for item in added)
    if deleted_count is not None:
        lines.append(f"deleted_element_count: {deleted_count}")
    return lines


def _tool_target_text(result: dict[str, Any]) -> str:
    target = result.get("target")
    if isinstance(target, dict):
        for key in ("text", "anchor_text", "aria_label"):
            value = str(target.get(key, "") or "").strip()
            if value:
                return value
    return str(result.get("target_id", "") or "").strip()


def format_tool_result_for_llm(result: dict[str, Any], runtime: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    status = str(result.get("status", "")).strip()
    message = str(result.get("message", "")).strip()
    if status:
        lines.append(f"status: {status}")
    if message:
        lines.append(f"message: {message}")

    tool_name = str(result.get("interaction_type") or result.get("kind") or "").strip().lower()
    is_interaction = tool_name in {"click", "input_text", "clear", "select_option", "toggle", "hover", "open", "attach"}

    if not is_interaction:
        markdown = ""
        if isinstance(result.get("markdown"), str):
            markdown = result.get("markdown", "").strip()
        elif runtime and isinstance(runtime.get("markdown_text"), str):
            markdown = str(runtime.get("markdown_text"),).strip()
        if markdown:
            lines.append("markdown:")
            lines.append(markdown)
    elif runtime:
        state_lines = _runtime_state_lines(runtime)
        if state_lines:
            lines.append("state:")
            lines.extend(state_lines)
        if result.get("page_changed") and isinstance(result.get("markdown"), str):
            markdown = result.get("markdown", "").strip()
            if markdown:
                lines.append("markdown:")
                lines.append(markdown)

    diffs = result.get("diffs")
    if isinstance(diffs, dict) and diffs:
        lines.append("diffs:")
        lines.extend(_format_diffs_plain(diffs))
        changed = diffs.get("changed") or []
        added = diffs.get("added") or []
        deleted_count = diffs.get("deleted_element_count")
        if not changed and not added and (deleted_count == 0 or deleted_count is None):
            lines.append("note: likely redundant interaction; no visible page change.")

    if not lines:
        lines.append("status: unknown")
    return "\n".join(lines)


def format_tool_result_for_display(result: dict[str, Any], runtime: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    target_text = _tool_target_text(result)
    if target_text:
        lines.append(f"target: {target_text}")
    message = str(result.get("message", "")).strip()
    if message:
        lines.append(f"message: {message}")
    tool_name = str(result.get("interaction_type") or result.get("kind") or "").strip().lower()
    is_interaction = tool_name in {"click", "input_text", "clear", "select_option", "toggle", "hover", "open", "attach"}
    if not is_interaction:
        formatted = format_tool_result_for_llm(result, runtime=runtime)
        return formatted
    if result.get("page_changed") and isinstance(result.get("markdown"), str):
        markdown = result.get("markdown", "").strip()
        if markdown:
            lines.append("markdown:")
            lines.append(markdown)
    diffs = result.get("diffs")
    if isinstance(diffs, dict) and diffs:
        lines.append("diffs:")
        lines.extend(_format_diffs_plain(diffs))
    if not lines:
        lines.append("status: unknown")
    return "\n".join(lines)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, sort_keys=False)
    return max(1, len(text) // 4 + 8)


def trim_messages_to_budget(
    messages: list[dict[str, Any]],
    max_context_tokens: int = 20000,
    response_reserve_tokens: int = 2048,
    keep_head: int = 5,
) -> list[dict[str, Any]]:
    if not messages:
        return messages
    if len(messages) <= keep_head:
        return messages

    budget = max(1, max_context_tokens - response_reserve_tokens)
    head = list(messages[:keep_head])
    tail = list(messages[keep_head:])

    head_tokens = sum(estimate_message_tokens(message) for message in head)
    if head_tokens >= budget:
        return head

    tail_budget = budget - head_tokens
    kept_tail_reversed: list[dict[str, Any]] = []
    used_tail_tokens = 0
    for message in reversed(tail):
        tokens = estimate_message_tokens(message)
        if kept_tail_reversed and used_tail_tokens + tokens > tail_budget:
            break
        if not kept_tail_reversed and tokens > tail_budget and tail_budget > 0:
            kept_tail_reversed.append(message)
            break
        if used_tail_tokens + tokens > tail_budget:
            break
        kept_tail_reversed.append(message)
        used_tail_tokens += tokens

    return head + list(reversed(kept_tail_reversed))


def should_continue(loop_count: int, max_steps: int) -> bool:
    return loop_count < max_steps


@dataclass
class LoopState:
    task: str
    messages: list[dict[str, str]] = field(default_factory=list)
    seen_errors: dict[str, int] = field(default_factory=dict)
    steps: int = 0
    halted: bool = False

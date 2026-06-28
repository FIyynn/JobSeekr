from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services.linkedin import linkedin
from shared.agent_prompts import resolve_agent_prompts_dir

try:
    from tests.llm_test.llm_runtime import (
        call_model_chat_with_retry,
        display_block,
        display_json,
        display_markdown_block,
        pretty_json,
        stop_requested,
        trim_messages_to_budget,
    )
except ImportError:
    from tests.llm_test.llm_runtime import (
        call_model_chat_with_retry,
        display_block,
        display_json,
        pretty_json,
        stop_requested,
        trim_messages_to_budget,
    )

    def display_markdown_block(label: str, text: Any, kind: str = "final") -> None:
        display_block(label, text, kind=kind)


BASE_DIR = Path(__file__).resolve().parent
CMD_TAG = "cmd"
FINAL_RESPONSE_TAG = "final_response"
DEFAULT_CONTEXT_TOKENS = 20000
DEFAULT_RESPONSE_RESERVE_TOKENS = 2048

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "linkedin.fetch_job_listings": {
        "required": {"keyword", "location"},
        "optional": {"filter_by", "filters", "pages"},
    },
    "linkedin.resume_search_task": {
        "required": {"search_task_id", "pages"},
        "optional": set(),
    },
    "linkedin.fetch_listings_description": {
        "required": {"listing_id"},
        "optional": {"listing_id"},
    },
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_instruction_bundle(base_dir: Path | None = None) -> dict[str, str]:
    root = resolve_agent_prompts_dir(base_dir)
    return {
        "loop": read_text(root / "linkedin_loop.md"),
        "plan": read_text(root / "linkedin_plan.md"),
        "reflection": read_text(root / "linkedin_reflection.md"),
        "finish": read_text(root / "linkedin_finish.md"),
        "linkedin": read_text(root / "linkedin_tool_instructions.md"),
        "tool": read_text(root / "linkedin_tool_instructions.md"),
        "examples": read_text(root / "linkedin_task_examples.md"),
    }


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

    args = _coerce_tool_args(tool_name, args)

    ok, error = validate_tool_args(tool_name, args)
    if not ok:
        return {"kind": "error", "message": error, "tool": tool_name, "args": args}
    return {"kind": "command", "tool": tool_name, "args": args, "raw": candidate}


def _coerce_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(args)
    if tool_name != "linkedin.fetch_job_listings":
        return cleaned

    legacy_filter_keys = {
        "experience_level",
        "date_posted",
        "job_type",
        "remote",
        "easy_apply",
        "has_verifications",
        "location_filter",
        "industry",
        "job_function",
        "title",
        "under_10_applicants",
        "in_your_network",
        "fair_chance_employer",
        "benefits",
        "commitments",
    }

    legacy_filters = {
        key: cleaned.pop(key)
        for key in list(cleaned.keys())
        if key in legacy_filter_keys
    }
    if not legacy_filters:
        return cleaned

    existing_filters = cleaned.get("filters")
    if isinstance(existing_filters, dict):
        merged_filters = dict(existing_filters)
        merged_filters.update(legacy_filters)
        cleaned["filters"] = merged_filters
        return cleaned
    if existing_filters is None:
        cleaned["filters"] = legacy_filters
        return cleaned

    return cleaned


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

    if required_any and not any(any(key in args for key in group) for group in required_any):
        rendered = " or ".join(" / ".join(sorted(group)) for group in required_any)
        return False, f"Missing required argument(s) for {tool_name}: {rendered}"
    return True, ""


def _as_listings_payload(listings_json: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    if isinstance(listings_json, dict):
        return listings_json
    if isinstance(listings_json, list):
        return {"listings": listings_json}
    return {"listings": []}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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
    memory.setdefault("search_task", runtime.get("search_task", {}))
    memory.setdefault("current_linkedin_state", runtime.get("current_linkedin_state", {}))
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
    search_task = memory.get("search_task") or runtime.get("search_task")
    if isinstance(search_task, dict) and search_task:
        task_id = _clean(search_task.get("id", ""))
        pages = search_task.get("pages_fetched") or search_task.get("pages") or []
        if task_id:
            lines.append(f"search task: {task_id}")
        if pages:
            lines.append(f"search pages: {pages}")
    linkedin_state = memory.get("current_linkedin_state") or runtime.get("current_linkedin_state")
    if isinstance(linkedin_state, dict):
        current_url = _clean(linkedin_state.get("current_url", ""))
        title = _clean(linkedin_state.get("title", ""))
        summary = _clean(linkedin_state.get("summary", ""))
        if current_url:
            lines.append(f"page url: {current_url}")
        if title:
            lines.append(f"page title: {title}")
        if summary:
            lines.append(f"page summary: {summary[:160]}")
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
        search_task = tool_result.get("search_task")
        if isinstance(search_task, dict):
            memory["search_task"] = search_task
            runtime["search_task"] = search_task
        current_linkedin_state = tool_result.get("session_state")
        if isinstance(current_linkedin_state, dict):
            memory["current_linkedin_state"] = current_linkedin_state
            runtime["current_linkedin_state"] = current_linkedin_state
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
    runtime["search_task"] = {}
    runtime["last_listing_payload"] = {}
    runtime["listings_json"] = {}
    runtime["last_detail_payload"] = {}
    runtime["current_linkedin_state"] = {}
    runtime["inspected_listing_ids"] = []
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
    memory["search_task"] = {}
    memory["current_linkedin_state"] = {}
    memory["last_reflection"] = ""
    memory["next_action"] = ""


def _safe_get(mapping: dict[str, Any], key: str, default: str = "") -> str:
    value = mapping.get(key, default)
    return _clean(value)


def _format_listing_line(listing: dict[str, Any], index: int) -> str:
    title = _safe_get(listing, "title", "Untitled listing")
    company = _safe_get(listing, "company")
    location = _safe_get(listing, "location")
    job_id = _safe_get(listing, "job_id")
    listed_on = _safe_get(listing, "listed_on")
    flags: list[str] = []
    if listing.get("easy_apply"):
        flags.append("easy apply")
    if listing.get("promoted"):
        flags.append("promoted")
    extras = [part for part in [listed_on, ", ".join(flags) if flags else "", f"job_id={job_id}" if job_id else ""] if part]
    tail = " | ".join(extras)
    middle = " — ".join(part for part in [company, location] if part)
    base = f"[{index}] {title}"
    if middle:
        base += f" — {middle}"
    if tail:
        base += f" — {tail}"
    return base


def _format_candidate_shortlist(result: dict[str, Any], limit: int | None = None) -> list[str]:
    listings = result.get("listings") or []
    if not isinstance(listings, list) or not listings:
        return []
    lines = ["candidate shortlist:"]
    visible_listings = [item for item in listings if isinstance(item, dict)]
    preview_count = len(visible_listings) if limit is None else max(1, int(limit))
    for index, listing in enumerate(visible_listings[:preview_count]):
        title = _safe_get(listing, "title", "Untitled listing")
        company = _safe_get(listing, "company")
        location = _safe_get(listing, "location")
        job_id = _safe_get(listing, "job_id")
        details = " ? ".join(part for part in [company, location] if part)
        tail = f" | job_id={job_id}" if job_id else ""
        lines.append(f"- [{index}] {title}{f' ? {details}' if details else ''}{tail}")
    remaining = len(visible_listings) - preview_count
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    return lines


def _format_job_listings_result(result: dict[str, Any], runtime: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    status = _clean(result.get("status", ""))
    if status:
        lines.append(f"status: {status}")

    keyword = _clean(result.get("keyword", ""))
    location = _clean(result.get("location", ""))
    filter_by = _clean(result.get("filter_by", ""))
    if keyword or location:
        lines.append(f"query: {keyword} | {location}".strip(" |"))
    if filter_by:
        lines.append(f"filter_by: {filter_by}")

    listings = result.get("listings") or []
    if listings:
        lines.append("listings:")
        visible_listings = [listing for listing in listings if isinstance(listing, dict)]
        preview_limit = None
        if isinstance(runtime, dict):
            preview_limit = runtime.get("listing_preview_limit")
        if preview_limit is None:
            preview_count = len(visible_listings)
        else:
            try:
                preview_count = max(1, int(preview_limit))
            except Exception:
                preview_count = len(visible_listings)
        for index, listing in enumerate(visible_listings[:preview_count]):
            lines.append(f"- {_format_listing_line(listing, index)}")
        if len(visible_listings) > preview_count:
            lines.append(f"- ... and {len(visible_listings) - preview_count} more")
    else:
        lines.append("listings: none")

    pagination = result.get("pagination") or {}
    if isinstance(pagination, dict) and pagination:
        pages = pagination.get("pages") or []
        current_page = _clean(pagination.get("current_page", ""))
        page_text = ", ".join(
            f"{page.get('text')}{'*' if page.get('current') else ''}"
            for page in pages
            if isinstance(page, dict)
        )
        summary = []
        if current_page:
            summary.append(f"current={current_page}")
        if page_text:
            summary.append(f"visible={page_text}")
        if summary:
            lines.append("pagination: " + " | ".join(summary))

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("warnings:")
        for warning in warnings:
            lines.append(f"- {_clean(warning)}")
    return "\n".join(lines)


def _format_company_profile(profile: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in ("name", "followers", "industry", "size", "linkedin_employee_count", "url"):
        value = _safe_get(profile, key)
        if value:
            parts.append(f"{key}: {value}")
    description = _safe_get(profile, "description")
    if description:
        parts.append(f"description: {description}")
    office = _safe_get(profile, "office")
    if office:
        parts.append(f"office: {office}")
    return parts


def _format_listing_detail_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    status = _clean(result.get("status", ""))
    if status:
        lines.append(f"status: {status}")

    items = result.get("ai") or []
    if not items:
        lines.append("details: none")
        return "\n".join(lines)

    lines.append("details:")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        listing = item.get("listing") if isinstance(item.get("listing"), dict) else {}
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        company_profile = item.get("company_profile") if isinstance(item.get("company_profile"), dict) else {}
        title = _safe_get(listing, "title")
        company = _safe_get(listing, "company")
        location = _safe_get(listing, "location")
        job_id = _safe_get(detail, "job_id") or _safe_get(listing, "job_id")
        listed_on = _safe_get(detail, "listed_on") or _safe_get(listing, "listed_on")
        apply_activity = _safe_get(detail, "apply_activity")
        posted_at = _safe_get(detail, "posted_at")
        description = _safe_get(detail, "job_description")
        lines.append(f"- [{index}] {title or 'Untitled'} — {company or 'Unknown company'} — {location or 'Unknown location'}")
        if job_id:
            lines.append(f"  job_id: {job_id}")
        if listed_on:
            lines.append(f"  listed_on: {listed_on}")
        if posted_at and posted_at != listed_on:
            lines.append(f"  posted_at: {posted_at}")
        if apply_activity:
            lines.append(f"  apply_activity: {apply_activity}")
        if description:
            lines.append(f"  description: {description}")
        if company_profile:
            lines.append("  company_profile:")
            for part in _format_company_profile(company_profile):
                lines.append(f"    - {part}")
    return "\n".join(lines)


def _format_page_list(values: list[int]) -> str:
    cleaned: list[str] = []
    for value in values:
        try:
            number = int(value)
        except Exception:
            continue
        if number > 0 and str(number) not in cleaned:
            cleaned.append(str(number))
    return ", ".join(cleaned) if cleaned else "none"


def _format_search_task_result(result: dict[str, Any]) -> list[str]:
    task = result.get("search_task")
    if not isinstance(task, dict):
        task = result.get("dev", {}).get("search_task") if isinstance(result.get("dev"), dict) else {}
    if not isinstance(task, dict) or not task:
        return []

    lines = ["search task:"]
    task_id = _clean(task.get("id", ""))
    query = _clean(task.get("keyword", "")) or _clean(result.get("keyword", ""))
    location = _clean(task.get("location", "")) or _clean(result.get("location", ""))
    filter_by = _clean(task.get("filter_by", "")) or _clean(result.get("filter_by", ""))
    filters = task.get("filters", result.get("filters", []))
    pages_requested = task.get("pages_requested", result.get("pages_requested", []))
    pages_fetched = task.get("pages_fetched", [])
    listing_count = task.get("listing_count", len(result.get("listings", []) or []))
    visible_unfetched_pages = task.get("visible_unfetched_pages", [])
    inspected_ids = result.get("inspected_listing_ids")

    if task_id:
        lines.append(f"- search_task_id: {task_id}")
    if query:
        lines.append(f"- search query: {query}")
    if location:
        lines.append(f"- location: {location}")
    if filter_by:
        lines.append(f"- filter_by: {filter_by}")
    if filters:
        lines.append(f"- filters: {pretty_json(filters)}")
    if pages_requested:
        lines.append(f"- pages requested: {_format_page_list([page for page in pages_requested if str(page).isdigit()])}")
    if pages_fetched:
        lines.append(f"- pages fetched: {_format_page_list([page for page in pages_fetched if str(page).isdigit()])}")
    lines.append(f"- listings accumulated: {listing_count}")
    if visible_unfetched_pages:
        lines.append(f"- visible unfetched pages: {_format_page_list([page for page in visible_unfetched_pages if str(page).isdigit()])}")
    if inspected_ids:
        lines.append(f"- inspected listing ids: {', '.join(str(item) for item in inspected_ids if str(item).strip())}")
    warnings = task.get("warnings", result.get("warnings", []))
    if warnings:
        lines.append(f"- warnings: {pretty_json(warnings)}")
    return lines


def _format_selected_listing_ids(runtime: dict[str, Any]) -> list[str]:
    selected = runtime.get("inspected_listing_ids") or []
    if not isinstance(selected, list) or not selected:
        return []
    unique: list[str] = []
    for item in selected:
        value = _clean(item)
        if value and value not in unique:
            unique.append(value)
    if not unique:
        return []
    return [f"Already inspected listing ids: {', '.join(unique)}"]


def _phase_system_messages(bundle: dict[str, str], phase: str) -> list[str]:
    phase_name = _clean(phase).casefold()
    if phase_name == "planning":
        return [bundle["plan"]]
    if phase_name == "reflection":
        return [bundle["reflection"]]
    if phase_name == "finalize":
        return [bundle["finish"]]
    if phase_name == "tool":
        return [bundle["tool"], bundle["loop"], bundle["linkedin"], bundle.get("examples", "")]
    return [bundle["loop"], bundle["linkedin"], bundle.get("examples", "")]


def _phase_user_prompt(task: str, phase: str, runtime: dict[str, Any]) -> str:
    phase_name = _clean(phase).casefold()
    plan_text = _clean(runtime.get("last_plan", ""))
    last_tool = _clean(runtime.get("last_tool", ""))
    last_result = runtime.get("last_tool_result")
    last_result_text = ""
    if isinstance(last_result, dict):
        last_result_text = format_tool_result_for_llm(last_result, runtime)
    elif last_result:
        last_result_text = _clean(last_result)
    active_search_task = runtime.get("search_task")
    search_task_lines = _format_search_task_result({"search_task": active_search_task}) if isinstance(active_search_task, dict) else []
    candidate_shortlist = _format_candidate_shortlist(runtime.get("last_listing_payload") or runtime.get("listings_json") or {}, limit=runtime.get("candidate_shortlist_limit"))
    inspected_listing_lines = _format_selected_listing_ids(runtime)
    session_outputs = runtime.get("session_outputs") or []
    recent_lines: list[str] = []
    for entry in session_outputs[-12:]:
        if not isinstance(entry, dict):
            continue
        kind = _clean(entry.get("kind", ""))
        label = _clean(entry.get("label", ""))
        text = entry.get("text", "")
        rendered_text = text if isinstance(text, str) else pretty_json(text)
        rendered_text = _clean(rendered_text)
        if rendered_text:
            recent_lines.append(f"- {kind or 'item'} {label or ''}: {rendered_text[:1200]}".strip())
    recent_context = "\n".join(recent_lines)
    memory_lines = _runtime_memory_lines(runtime)

    if phase_name == "planning":
        lines = [
            "Task:",
            task.strip(),
            "",
            "Create the first action plan only.",
            "Do not use tools yet.",
            "Do not write command syntax or tool names.",
            "Focus on the search strategy, key filters, and what evidence will decide the next step.",
        ]
        if memory_lines:
            lines.extend(["", "Persistent runtime memory:", *memory_lines])
        return "\n".join(lines)

    if phase_name == "reflection":
        lines = [
            "Task:",
            task.strip(),
            "",
            "Current plan:",
            plan_text or "none yet",
            "",
            "Latest tool:",
            last_tool or "none yet",
            "",
            "Latest result:",
            last_result_text or "none yet",
            "",
            "Active search task:",
        ]
        lines.extend(search_task_lines or ["none yet"])
        if candidate_shortlist:
            lines.extend(["", *candidate_shortlist])
        if inspected_listing_lines:
            lines.extend(["", *inspected_listing_lines])
        if memory_lines:
            lines.extend(["", "Persistent runtime memory:", *memory_lines])
        lines.extend([
            "",
            "Recent context:",
            recent_context or "none yet",
            "",
            "This is the reflection phase immediately after a tool call.",
            "Reflect on what has already been done before deciding whether to continue.",
            "Reflect on what changed, what was completed, what is still missing, and whether the task is complete.",
            "Do not rewrite the plan from scratch.",
            "Start with exactly one line: Decision: complete or Decision: continue.",
            "If continuing, explain the next action clearly and keep it to one tool-worthy step using the exact phrase 'Next action: click target_id ...' or 'Next action: type target_id ...'.",
        ])
        return "\n".join(lines)

    if phase_name == "finalize":
        lines = [
            "Task:",
            task.strip(),
            "",
            "Current plan:",
            plan_text or "none yet",
            "",
            "Latest tool:",
            last_tool or "none yet",
            "",
            "Latest result:",
            last_result_text or "none yet",
            "",
            "Active search task:",
        ]
        lines.extend(search_task_lines or ["none yet"])
        if candidate_shortlist:
            lines.extend(["", *candidate_shortlist])
        if inspected_listing_lines:
            lines.extend(["", *inspected_listing_lines])
        if memory_lines:
            lines.extend(["", "Persistent runtime memory:", *memory_lines])
        lines.extend([
            "",
            "Recent context:",
            recent_context or "none yet",
            "",
            "Write the final answer now.",
            "Return exactly one <final_response>...</final_response> block.",
        ])
        return "\n".join(lines)

    lines = [
        "Task:",
        task.strip(),
        "",
        "Current plan:",
        plan_text or "none yet",
        "",
        "Latest tool:",
        last_tool or "none yet",
        "",
        "Latest result:",
        last_result_text or "none yet",
        "",
        "Active search task:",
    ]
    lines.extend(search_task_lines or ["none yet"])
    if candidate_shortlist:
        lines.extend(["", *candidate_shortlist])
    if inspected_listing_lines:
        lines.extend(["", *inspected_listing_lines])
    if memory_lines:
        lines.extend(["", "Persistent runtime memory:", *memory_lines])
    lines.extend([
        "",
        "Recent context:",
        recent_context or "none yet",
        "",
        "Last reflection constraint:",
        _clean(runtime.get("memory", {}).get("last_reflection", "")) or "none yet",
        "Required next action:",
        _clean(runtime.get("memory", {}).get("next_action", "")) or "none yet",
        "",
        "Use the tool instructions to perform the next step from the current plan.",
        "If the task is complete, the next reflection should say Decision: complete.",
        "When selecting a listing for details, use a distinct listing_id that has not already been inspected.",
        "If the candidate shortlist is weak, refine the search or inspect a different candidate instead of repeating the same id.",
        "If the last reflection named a Next action, follow it exactly including the same target_id.",
        "Otherwise return exactly one <cmd>...</cmd> tool call.",
    ])
    return "\n".join(lines)


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
        block = _clean(block)
        if block:
            messages.append({"role": "system", "content": block})
    messages.append({"role": "user", "content": _phase_user_prompt(task, phase, runtime)})
    return messages


def format_tool_result_for_llm(result: dict[str, Any], runtime: dict[str, Any] | None = None) -> str:
    tool_name = _clean(result.get("tool", "") or result.get("kind", "") or result.get("interaction_type", ""))
    if tool_name == "linkedin.fetch_job_listings":
        lines = _format_job_listings_result(result, runtime=runtime).splitlines()
        lines.extend(_format_search_task_result(result))
        return "\n".join(line for line in lines if line)
    if tool_name == "linkedin.resume_search_task":
        lines = _format_job_listings_result(result, runtime=runtime).splitlines()
        lines.extend(_format_search_task_result(result))
        return "\n".join(line for line in lines if line)
    if tool_name == "linkedin.fetch_listings_description":
        return _format_listing_detail_result(result)

    lines: list[str] = []
    status = _clean(result.get("status", ""))
    message = _clean(result.get("message", ""))
    if status:
        lines.append(f"status: {status}")
    if message:
        lines.append(f"message: {message}")
    if not lines:
        lines.append("status: unknown")
    return "\n".join(lines)


def format_tool_result_for_display(result: dict[str, Any], runtime: dict[str, Any] | None = None) -> str:
    return format_tool_result_for_llm(result, runtime=runtime)


def append_transcript_text(runtime: dict[str, Any], kind: str, label: str, text: Any) -> None:
    transcript = runtime.setdefault("session_outputs", [])
    entry = {"kind": kind, "label": label, "text": text if isinstance(text, str) else pretty_json(text)}
    transcript.append(entry)


def trim_linkedin_messages_to_budget(
    messages: list[dict[str, Any]],
    *,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    response_reserve_tokens: int = DEFAULT_RESPONSE_RESERVE_TOKENS,
    keep_head: int = 3,
) -> list[dict[str, Any]]:
    return trim_messages_to_budget(
        messages,
        max_context_tokens=context_tokens,
        response_reserve_tokens=response_reserve_tokens,
        keep_head=keep_head,
    )


def execute_tool_call(tool_name: str, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    driver = runtime.get("driver")
    if driver is None:
        return {"status": "error", "message": "Missing driver in runtime.", "tool": tool_name}

    verbose = bool(runtime.get("verbose", False))
    log_path = runtime.get("log_path")
    store = runtime.get("store")
    delays = runtime.get("delays") or {}
    now = runtime.get("now")
    search_tasks = runtime.setdefault("search_tasks", {})

    if tool_name == "linkedin.fetch_job_listings":
        result = linkedin.fetch_job_listings(
            driver,
            keyword=args["keyword"],
            location=args["location"],
            filters=args.get("filters"),
            filter_by=args.get("filter_by", "Jobs"),
            pages=args.get("pages", 1),
            delays=delays,
            log_path=log_path,
            verbose=verbose,
            now=now,
        )
        result["tool"] = tool_name
        runtime["last_listing_payload"] = result
        runtime["listings_json"] = result
        runtime["page_cache"] = result.get("dev", {}).get("page_cache", [])
        runtime["search_task"] = result.get("search_task", {})
        search_task_id = result.get("search_task", {}).get("id", "")
        if search_task_id:
            search_tasks[str(search_task_id)] = result
        runtime["current_linkedin_state"] = result.get("session_state", {})
        return result

    if tool_name == "linkedin.resume_search_task":
        result = linkedin.resume_search_task(
            driver,
            search_task_id=args["search_task_id"],
            pages=args.get("pages", 1),
            search_tasks=search_tasks,
            delays=delays,
            log_path=log_path,
            verbose=verbose,
            now=now,
        )
        result["tool"] = tool_name
        runtime["last_listing_payload"] = result
        runtime["listings_json"] = result
        runtime["page_cache"] = result.get("dev", {}).get("page_cache", [])
        runtime["search_task"] = result.get("search_task", {})
        search_task_id = result.get("search_task", {}).get("id", args.get("search_task_id", ""))
        if search_task_id:
            search_tasks[str(search_task_id)] = result
        runtime["current_linkedin_state"] = result.get("session_state", {})
        return result

    if tool_name == "linkedin.fetch_listings_description":
        listings_payload = runtime.get("last_listing_payload") or runtime.get("listings_json") or {}
        inspected_listing_ids = runtime.setdefault("inspected_listing_ids", [])
        listing_id = str(args.get("listing_id", "")).strip()
        repeated_listing_id = bool(listing_id and listing_id in inspected_listing_ids)
        if listing_id and listing_id not in inspected_listing_ids:
            inspected_listing_ids.append(listing_id)
        result = linkedin.fetch_listings_description(
            driver,
            listings_payload,
            listing_id=listing_id,
            delays=delays,
            log_path=log_path,
            verbose=verbose,
            now=now,
        )
        result["tool"] = tool_name
        if repeated_listing_id:
            result.setdefault("dev", {}).setdefault("warnings", []).append(f"Listing id already inspected: {listing_id}")
        runtime["last_detail_payload"] = result
        runtime["current_linkedin_state"] = result.get("session_state", {})
        return result

    return {"status": "error", "message": f"Unsupported tool: {tool_name}", "tool": tool_name}


def run_agent(
    task: str,
    runtime: dict[str, Any],
    bundle: dict[str, str],
    backend: str = "local",
    max_steps: int = 20,
) -> dict[str, Any]:
    _reset_task_runtime(runtime, task)
    runtime["session_outputs"] = runtime.get("session_outputs", [])
    phase = "planning"

    display_block("User task", task, kind="user")
    context_tokens = int(runtime.get("context_tokens", DEFAULT_CONTEXT_TOKENS))
    response_reserve_tokens = int(runtime.get("response_reserve_tokens", DEFAULT_RESPONSE_RESERVE_TOKENS))

    for step in range(max_steps):
        if stop_requested():
            final_text = "Loop halted by ] key."
            display_block("Final response", final_text, kind="final")
            runtime["last_result"] = {"kind": "halted", "text": final_text}
            return runtime["last_result"]

        messages = build_messages(task, bundle, phase=phase, runtime=runtime)
        messages = trim_linkedin_messages_to_budget(
            messages,
            context_tokens=context_tokens,
            response_reserve_tokens=response_reserve_tokens,
            keep_head=len(messages),
        )

        try:
            llm_text = call_model_chat_with_retry(
                backend,
                messages=messages,
                api_key_path=runtime.get("api_key_path"),
                openai_model=runtime.get("openai_model", "gpt-5.4-mini"),
                openai_base_url=runtime.get("openai_base_url", "https://api.openai.com/v1/chat/completions"),
                openai_reasoning_effort=runtime.get("openai_reasoning_effort", "low"),
                llama_model=runtime.get("llama_model", "qwen3.5-9b"),
                llama_base_url=runtime.get("llama_base_url", "http://127.0.0.1:8080/v1/chat/completions"),
                max_completion_tokens=runtime.get("max_completion_tokens", 1024),
                timeout=runtime.get("timeout", 300),
                retries=runtime.get("retries", 2),
            )
        except Exception as exc:
            error_text = str(exc)
            display_block(f"LLM error {step + 1}", error_text, kind="error")
            runtime["last_result"] = {"kind": "error", "message": error_text}
            return runtime["last_result"]

        display_block(f"LLM response {step + 1}", llm_text, kind="llm")
        append_transcript_text(runtime, "llm", f"step-{step + 1}", llm_text)

        parsed = parse_model_output(llm_text)
        if parsed["kind"] == "final_response":
            display_markdown_block("Final response", parsed["text"], kind="final")
            runtime["last_result"] = parsed
            _sync_runtime_memory(runtime, phase="finalize", note="final response reached")
            append_transcript_text(runtime, "final", "response", parsed["text"])
            return parsed

        if parsed["kind"] == "error":
            display_json("Parsed error", parsed, kind="error")
            key = parsed.get("message", "parse-error")
            runtime["stuck_counts"][key] = runtime["stuck_counts"].get(key, 0) + 1
            if runtime["stuck_counts"][key] > 4:
                final_text = f"Stopped after repeated parse errors: {key}"
                display_markdown_block("Final response", final_text, kind="final")
                runtime["last_result"] = {"kind": "stopped", "text": final_text}
                return runtime["last_result"]
            phase = "tool"
            runtime["phase"] = phase
            continue

        if parsed["kind"] == "text":
            if phase == "planning":
                display_block(f"Plan {step + 1}", parsed["text"], kind="plan")
                append_transcript_text(runtime, "plan", f"step-{step + 1}", parsed["text"])
                runtime["last_plan"] = parsed["text"]
                _sync_runtime_memory(runtime, phase="tool", plan_text=parsed["text"])
                phase = "tool"
                runtime["phase"] = phase
                continue
            if phase == "reflection":
                display_block(f"Reflection {step + 1}", parsed["text"], kind="plan")
                append_transcript_text(runtime, "reflection", f"step-{step + 1}", parsed["text"])
                decision_text = _clean(parsed["text"]).casefold()
                if decision_text.startswith("decision: complete"):
                    phase = "finalize"
                else:
                    phase = "tool"
                _sync_runtime_memory(runtime, phase=phase)
                runtime["phase"] = phase
                continue
            display_block(f"Unexpected text {step + 1}", parsed["text"], kind="error")
            runtime["phase"] = "tool"
            messages.append(
                {
                    "role": "user",
                    "content": "The previous response was invalid. Return exactly one <cmd>...</cmd> or one <final_response>...</final_response>. Do not use plain English for a tool step.",
                }
            )
            continue

        if parsed["kind"] == "command":
            cmd_text = f'<cmd>{parsed["raw"]}</cmd>'
            display_block(f"Parsed cmd {step + 1}", cmd_text, kind="cmd")
            result = execute_tool_call(parsed["tool"], parsed["args"], runtime)
            runtime["last_tool"] = parsed["tool"]
            runtime["last_tool_result"] = result
            _sync_runtime_memory(runtime, phase="reflection", tool_name=parsed["tool"], tool_result=result)
            phase = "reflection"
            runtime["phase"] = phase
            output_text = format_tool_result_for_llm(result, runtime)
            display_block(f"Tool output {step + 1}", format_tool_result_for_display(result, runtime), kind="output")
            append_transcript_text(runtime, "cmd", f"step-{step + 1}", cmd_text)
            append_transcript_text(runtime, "output", f"step-{step + 1}", output_text)

            stuck_key = f'{parsed["tool"]}:{result.get("status", "unknown")}:{result.get("message", "")}'
            runtime["stuck_counts"][stuck_key] = runtime["stuck_counts"].get(stuck_key, 0) + 1
            if runtime["stuck_counts"][stuck_key] > 4:
                final_text = f"Stopped after repeating the same tool state: {stuck_key}"
                display_markdown_block("Final response", final_text, kind="final")
                runtime["last_result"] = {"kind": "stopped", "text": final_text, "last_tool": parsed["tool"]}
                _sync_runtime_memory(runtime, phase="finalize", note=final_text)
                return runtime["last_result"]
            continue

        display_json("Unexpected parse result", parsed, kind="error")
        phase = "tool"
        runtime["phase"] = phase

    final_text = f"Stopped after max_steps={max_steps}."
    display_markdown_block("Final response", final_text, kind="final")
    runtime["last_result"] = {"kind": "stopped", "text": final_text}
    return runtime["last_result"]

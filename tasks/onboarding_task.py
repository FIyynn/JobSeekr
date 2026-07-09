from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import load_app_config
from shared.agent_prompts import resolve_agent_prompts_dir
from tests.llm_test.llm_runtime import call_model_chat_with_retry


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = REPO_ROOT / "runtime" / "task_states" / "onboarding_task_state.json"
DEFAULT_HEARTBEAT_SECONDS = 2.0
DEFAULT_STEP_DELAY_SECONDS = 0.8
ONBOARDING_PROMPTS_DIR = REPO_ROOT / "agent-system-prompts" / "onboarding"

REQUIRED_PROFILE_FIELDS = (
    "full_name",
    "email",
    "location",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in update.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def default_state_path() -> Path:
    return DEFAULT_STATE_PATH


def clear_task_state(state_path: str | Path | None = None) -> None:
    path = Path(state_path or DEFAULT_STATE_PATH)
    if path.exists():
        path.unlink()


def read_task_state(state_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(state_path or DEFAULT_STATE_PATH)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


class TaskStateWriter:
    def __init__(self, state_path: str | Path, heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS, verbose: bool = True):
        self.state_path = Path(state_path)
        self.heartbeat_seconds = max(0.5, float(heartbeat_seconds or 0))
        self.verbose = verbose
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def _persist_locked(self) -> None:
        _write_json(self.state_path, self._state)

    def set(self, **patch: Any) -> dict[str, Any]:
        with self._lock:
            self._state = _deep_merge(self._state, patch)
            self._state["updated_at"] = _now_iso()
            if "heartbeat_at" not in self._state:
                self._state["heartbeat_at"] = self._state["updated_at"]
            self._persist_locked()
            return deepcopy(self._state)

    def initialize(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._state = deepcopy(payload)
            self._state.setdefault("updated_at", _now_iso())
            self._state.setdefault("heartbeat_at", self._state["updated_at"])
            self._persist_locked()
            return deepcopy(self._state)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _beat() -> None:
            while not self._stop.wait(self.heartbeat_seconds):
                with self._lock:
                    status = str(self._state.get("status", "")).strip()
                    if status not in {"queued", "running", "partial", "waiting_for_user"}:
                        continue
                    self._state["heartbeat_at"] = _now_iso()
                    self._state["updated_at"] = self._state["heartbeat_at"]
                    self._persist_locked()

        self._thread = threading.Thread(target=_beat, name="onboarding-task-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.heartbeat_seconds + 1.0)
        with self._lock:
            self._state["heartbeat_at"] = _now_iso()
            self._state["updated_at"] = self._state["heartbeat_at"]
            self._persist_locked()


def _coerce_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[;,|]\s*", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            text = _coerce_text(item)
            if text:
                items.append(text)
        return items
    text = _coerce_text(value)
    return [text] if text else []


def _document_text(document: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "content", "body", "value", "markdown"):
        raw_value = document.get(key)
        if raw_value is None:
            continue
        text = str(raw_value).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _load_document_path(path_text: str) -> list[dict[str, Any]]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        return [{"name": path.name or "document", "type": "missing", "text": ""}]
    if path.is_file():
        return [
            {
                "name": path.name,
                "type": path.suffix.lstrip(".") or "file",
                "text": path.read_text(encoding="utf-8", errors="ignore"),
                "path": str(path),
            }
        ]
    loaded: list[dict[str, Any]] = []
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        if file_path.name.startswith("."):
            continue
        loaded.append(
            {
                "name": file_path.name,
                "type": file_path.suffix.lstrip(".") or "file",
                "text": file_path.read_text(encoding="utf-8", errors="ignore"),
                "path": str(file_path),
            }
        )
    return loaded


def _normalize_documents(documents: Any) -> list[dict[str, Any]]:
    if not isinstance(documents, list):
        if isinstance(documents, (str, Path)):
            return _load_document_path(str(documents))
        return []
    normalized: list[dict[str, Any]] = []
    for index, document in enumerate(documents):
        if isinstance(document, (str, Path)):
            normalized.extend(_load_document_path(str(document)))
            continue
        if not isinstance(document, dict):
            normalized.append({"name": f"document-{index + 1}", "type": "unknown", "text": _coerce_text(document)})
            continue
        source_path = _coerce_text(document.get("path") or document.get("source") or document.get("file"))
        if source_path:
            loaded_documents = _load_document_path(source_path)
            if loaded_documents:
                normalized.extend(loaded_documents)
                continue
        normalized.append(
            {
                "name": _coerce_text(document.get("name") or document.get("filename") or f"document-{index + 1}"),
                "type": _coerce_text(document.get("type") or document.get("document_type") or "document"),
                "text": _document_text(document),
                "path": source_path or "",
            }
        )
    return normalized


def _normalize_markdown_text(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        stripped = line.strip()
        if stripped in {"#", "---"}:
            continue
        if line.startswith("# "):
            line = line[2:]
            line = re.sub(r"\\(?=[#*\-&])", "", line)
        line = line.replace("â€“", "-").replace("â€”", "-")
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _split_markdown_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_path: list[tuple[int, str]] = []
    current_key = "root"
    sections[current_key] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            while current_path and current_path[-1][0] >= level:
                current_path.pop()
            current_path.append((level, title))
            current_key = " > ".join(item[1] for item in current_path)
            sections.setdefault(current_key, [])
            continue
        sections.setdefault(current_key, []).append(line)
    return sections


def _section_lines(sections: dict[str, list[str]], title: str) -> list[str]:
    title_key = title.casefold()
    collected: list[str] = []
    for key, lines in sections.items():
        key_lower = key.casefold()
        if key_lower == title_key or key_lower.startswith(f"{title_key} > ") or key_lower.endswith(f" > {title_key}"):
            if key_lower != title_key:
                collected.append(f"### {key.split(' > ')[-1].strip()}")
            collected.extend(lines)
    return collected


def _section_text(sections: dict[str, list[str]], title: str) -> str:
    return "\n".join(line for line in _section_lines(sections, title) if line.strip() not in {"#", "---"}).strip()


def _collect_bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def _collect_text_blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                blocks.append(" ".join(current).strip())
                current = []
            continue
        if stripped:
            current.append(stripped)
    if current:
        blocks.append(" ".join(current).strip())
    return blocks


def _split_items_and_notes(items: Any) -> tuple[list[str], list[str]]:
    concrete: list[str] = []
    notes: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        lowered = item.casefold()
        if any(
            token in lowered
            for token in (
                "matter more than",
                "prefer",
                "especially attractive",
                "only interesting if",
                "can still work when",
                "if the role has",
                "do not",
                "shortlist",
                "growth",
                "impact",
                "mentorship",
                "training path",
                "supportive manager",
                "documented",
                "clear",
            )
        ) and len(item.split()) > 4:
            notes.append(item)
        else:
            concrete.append(item)
    return _dedupe_keep_order(concrete), _dedupe_keep_order(notes)


def _split_numeric_value_and_note(item: str) -> tuple[str, str]:
    text = _coerce_text(item)
    if not text:
        return "", ""
    if not re.match(r"^(?:AED|USD|EUR|GBP|SAR|QAR|KWD|OMR|BHD)?\s*\d", text, re.I):
        return text, ""
    marker = re.search(r"\b(?:for|when|if|with|because|as long as|so long as)\b", text, re.I)
    if not marker:
        return text, ""
    core = text[: marker.start()].strip(" -:;,.")
    note = text[marker.start() :].strip()
    return (core or text), note


def _split_numeric_items_and_notes(items: Any) -> tuple[list[str], list[str]]:
    values: list[str] = []
    notes: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        value, note = _split_numeric_value_and_note(item)
        if value:
            if re.match(r"^(?:AED|USD|EUR|GBP|SAR|QAR|KWD|OMR|BHD)?\s*\d", value, re.I) or not values:
                values.append(value)
            else:
                notes.append(value)
        if note:
            notes.append(note)
    return _dedupe_keep_order(values), _dedupe_keep_order(notes)


def _numeric_core_text(items: Any) -> str:
    values = [item for item in _coerce_text_list(items) if re.match(r"^(?:AED|USD|EUR|GBP|SAR|QAR|KWD|OMR|BHD)?\s*\d", item, re.I)]
    return " ".join(values).strip()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = _coerce_text(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _normalize_heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", _coerce_text(text).strip()).casefold()


def _parse_tiered_bullets(lines: list[str], heading_map: dict[str, str]) -> dict[str, list[str]]:
    tiers: dict[str, list[str]] = {value: [] for value in heading_map.values()}
    current_key = ""
    for line in lines:
        stripped = _coerce_text(line).strip()
        if not stripped or stripped in {"#", "---"}:
            continue
        heading_text = re.sub(r"^#{1,6}\s+", "", stripped).strip()
        heading = heading_map.get(_normalize_heading_key(heading_text))
        if heading:
            current_key = heading
            continue
        if stripped.startswith("#"):
            current_key = ""
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if current_key:
                tiers.setdefault(current_key, []).append(item)
    return tiers


def _parse_salary_range(text: str) -> dict[str, Any]:
    source = _coerce_text(text)
    if not source:
        return {"currency": "", "period": "", "min": None, "max": None, "text": ""}
    currency_match = re.search(r"\b(AED|USD|EUR|GBP|SAR|QAR|KWD|OMR|BHD)\b", source, re.I)
    currency = currency_match.group(1).upper() if currency_match else ""
    period = "monthly" if any(token in source.casefold() for token in ("month", "/month", "monthly", "mo")) else ""
    numbers = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", source)]
    minimum = min(numbers) if numbers else None
    maximum = max(numbers) if numbers else None
    return {"currency": currency, "period": period, "min": minimum, "max": maximum, "text": source}


def _infer_seniority(profile: dict[str, Any]) -> dict[str, Any]:
    summary = _coerce_text(profile.get("summary")).casefold()
    education = profile.get("education", []) if isinstance(profile.get("education"), list) else []
    experience = profile.get("experience", []) if isinstance(profile.get("experience"), list) else []
    evidence: list[str] = []
    if not experience:
        evidence.append("no_work_experience_listed")
    if education:
        evidence.append("has_education")
    if any(token in summary for token in ("graduate", "new grad", "entry level", "entry-level", "junior", "fresh graduate")):
        evidence.append("summary_signals_entry_level")
    if any(_coerce_text(item).casefold().find("graduate") >= 0 for item in education):
        evidence.append("education_signals_entry_level")

    level = "unknown"
    if "summary_signals_entry_level" in evidence or "education_signals_entry_level" in evidence or (not experience and education):
        level = "entry_level"
    elif experience:
        level = "experienced"

    return {
        "level": level,
        "years_min": 0 if level == "entry_level" else None,
        "years_max": 2 if level == "entry_level" else None,
        "evidence": evidence,
    }


def _split_profile_role_hints(items: Any) -> tuple[list[str], list[str]]:
    roles: list[str] = []
    seniority_hints: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        lowered = item.casefold()
        if any(
            marker in lowered
            for marker in (
                "entry-level / recent-graduate profile",
                "entry level / recent graduate profile",
                "recent-graduate profile",
                "recent graduate profile",
                "entry-level candidate",
                "entry level candidate",
                "fresh graduate profile",
                "recent graduate",
                "entry-level",
                "entry level",
            )
        ):
            seniority_hints.append(item)
            continue
        roles.append(item)
    return _dedupe_keep_order(roles), _dedupe_keep_order(seniority_hints)


def _normalize_work_arrangement(items: Any) -> list[str]:
    normalized: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        lowered = item.casefold()
        if "hybrid" in lowered:
            normalized.append("Hybrid")
        if any(token in lowered for token in ("on-site", "onsite", "in-office", "in office")):
            normalized.append("On-site")
        if any(token in lowered for token in ("remote", "work from home", "wfh")):
            normalized.append("Remote")
    return _dedupe_keep_order(normalized)


def _parse_application_policy(items: Any) -> dict[str, Any]:
    texts = _dedupe_keep_order(_coerce_text_list(items))
    blob = " | ".join(texts).casefold()
    auto_apply = None
    default_action = ""
    if any(token in blob for token in ("do not auto-apply", "don't auto-apply", "shortlist for review first", "shortlist for review")):
        auto_apply = False
        default_action = "shortlist_for_review"
    elif any(token in blob for token in ("auto-apply by default", "automatically apply", "auto apply")):
        auto_apply = True
        default_action = "auto_apply"
    return {
        "auto_apply": auto_apply,
        "default_action": default_action,
        "notes": [],
    }


def _parse_eligibility(additional_information: Any, hard_constraints: Any, preferences: dict[str, Any]) -> dict[str, Any]:
    return {
        "right_to_work": {},
        "driving_license": {},
        "availability": {},
        "work_arrangement": {
            "ideal": [],
            "acceptable": [],
            "notes": [],
        },
    }


def _merge_eligibility_sources(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    if override.get("right_to_work"):
        result["right_to_work"] = deepcopy(override.get("right_to_work"))
    if override.get("driving_license"):
        result["driving_license"] = deepcopy(override.get("driving_license"))
    if override.get("availability"):
        result["availability"] = deepcopy(override.get("availability"))
    if override.get("work_arrangement"):
        arrangement = override.get("work_arrangement") if isinstance(override.get("work_arrangement"), dict) else {}
        base_arrangement = result.get("work_arrangement") if isinstance(result.get("work_arrangement"), dict) else {}
        result["work_arrangement"] = {
            "ideal": _dedupe_keep_order(_coerce_text_list(arrangement.get("ideal")) or _coerce_text_list(base_arrangement.get("ideal"))),
            "acceptable": _dedupe_keep_order(_coerce_text_list(arrangement.get("acceptable")) or _coerce_text_list(base_arrangement.get("acceptable"))),
            "notes": _dedupe_keep_order(_coerce_text_list(arrangement.get("notes")) or _coerce_text_list(base_arrangement.get("notes"))),
        }
    return result


def _merge_application_policy(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    for key in ("auto_apply", "default_action", "notes"):
        value = override.get(key)
        if value is not None and value != "":
            result[key] = deepcopy(value)
    return result


def _merge_seniority(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    for key in ("level", "recent_graduate", "years_min", "years_max", "evidence", "hints"):
        value = override.get(key)
        if value is not None and value != "":
            result[key] = deepcopy(value)
    return result


def _split_hard_constraints(items: Any) -> tuple[list[str], list[str], list[str]]:
    negative_markers = (
        "unpaid",
        "commission-only",
        "commission only",
        "illegal",
        "unsafe",
        "fraud",
        "vague talent-pool",
        "vague talent pool",
    )
    explicit_hard_language = (
        "skip",
        "avoid",
        "do not",
        "don't",
        "never",
        "must not",
        "shall not",
        "no ",
        "without",
        "not acceptable",
        "not allowed",
        "always",
    )
    soft_negative_language = (
        "poor fit",
        "not worth moving forward",
        "unclear",
        "clear standard employment setup",
    )
    hard_yes: list[str] = []
    hard_no: list[str] = []
    notes: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        lowered = item.casefold()
        if any(marker in lowered for marker in ("unpaid", "commission-only", "commission only", "illegal", "unsafe", "fraud")):
            if "unpaid" in lowered:
                hard_no.append("unpaid roles")
            if "commission" in lowered:
                hard_no.append("commission-only roles")
            if "illegal" in lowered:
                hard_no.append("illegal roles")
            if "unsafe" in lowered:
                hard_no.append("unsafe roles")
            if "fraud" in lowered:
                hard_no.append("fraudulent roles")
            if "talent pool" in lowered or "talent-pool" in lowered:
                hard_no.append("vague talent-pool roles")
            continue
        if any(marker in lowered for marker in soft_negative_language):
            summary, note = _compact_constraint_rule_and_note(item)
            if summary:
                notes.append(summary)
            if note:
                notes.append(note)
            continue
        if any(marker in lowered for marker in explicit_hard_language) and any(token in lowered for token in ("skip", "avoid", "do not", "don't", "never", "must not", "shall not", "no ")):
            summary, note = _compact_constraint_rule_and_note(item)
            hard_no.append(summary or _coerce_text(item))
            if note:
                notes.append(note)
        else:
            summary, note = _compact_constraint_rule_and_note(item)
            hard_yes.append(summary or _coerce_text(item))
            if note:
                notes.append(note)
    return _dedupe_keep_order(hard_yes), _dedupe_keep_order(hard_no), _dedupe_keep_order(notes)


def _compact_constraint_rule_and_note(text: str) -> tuple[str, str]:
    value = _coerce_text(text)
    if not value:
        return "", ""
    match = re.match(r"^(?:roles?|jobs?)\s+without\s+a\s+clear\s+(.+?)(?:\s+are\s+a\s+poor\s+fit.*)?$", value, re.I)
    if match:
        return _coerce_text(f"no clear {match.group(1)}"), ""
    if lowered.startswith("if "):
        match = re.match(r"^if\s+(.+?),\s*(?:it\s+is\s+)?(?:probably\s+)?(?:not\s+)?worth.*$", value, re.I)
        if match:
            condition = _coerce_text(match.group(1))
            note = _compact_note_tail(value)
            return condition, note
    for pattern in (
        r"^(.*?)(?:\s+is\s+a\s+poor\s+fit.*)$",
        r"^(.*?)(?:\s+is\s+probably\s+not\s+worth.*)$",
        r"^(.*?)(?:\s+is\s+not\s+worth.*)$",
        r"^(.*?)(?:\s+should\s+be\s+skipped.*)$",
        r"^(.*?)(?:\s+is\s+not\s+acceptable.*)$",
    ):
        match = re.match(pattern, value, re.I)
        if match:
            return _coerce_text(match.group(1)), _compact_note_tail(value)
    return value, ""


def _compact_note_tail(text: str, *, fallback: str = "") -> str:
    value = _coerce_text(text)
    if not value:
        return fallback
    lowered = value.casefold()
    if lowered.startswith("if ") and "," in value:
        tail = value.split(",", 1)[1].strip()
        if not tail:
            return fallback
        if any(phrase in tail.casefold() for phrase in ("not worth moving forward", "poor fit", "unclear", "acceptable")):
            return ""
        return tail
    for prefix in ("because ", "since ", "due to ", "as "):
        if lowered.startswith(prefix):
            tail = value[len(prefix):].strip(" -:;,.\n\t")
            return tail or fallback
    if "because " in lowered:
        return value.split("because ", 1)[1].strip(" -:;,.\n\t") or fallback
    if "since " in lowered:
        return value.split("since ", 1)[1].strip(" -:;,.\n\t") or fallback
    if "due to " in lowered:
        return value.split("due to ", 1)[1].strip(" -:;,.\n\t") or fallback
    return ""


def _compact_policy_note(text: str) -> str:
    _ = text
    return ""


def _compact_arrangement_note(text: str) -> str:
    value = _coerce_text(text)
    if not value:
        return ""
    lowered = value.casefold()
    if lowered.startswith("open to "):
        value = value[8:].strip()
    value = re.sub(r"\s+opportunities?$", "", value, flags=re.I).strip(" -:;,.")
    return value or _coerce_text(text)


def _build_scoring_preferences(pref_sections: dict[str, list[str]], flat_preferences: dict[str, list[str]]) -> dict[str, Any]:
    industries = _parse_tiered_bullets(
        _section_lines(pref_sections, "Industries"),
        {
            "highly interested": "high_priority",
            "also interested": "also_interested",
        },
    )
    work_style = _parse_tiered_bullets(
        _section_lines(pref_sections, "Preferred Work Style"),
        {
            "ideal": "ideal",
            "also acceptable": "acceptable",
        },
    )
    compensation = _parse_tiered_bullets(
        _section_lines(pref_sections, "Compensation Preferences"),
        {
            "ideal": "ideal",
            "comfortable": "comfortable",
            "would consider lower if": "lower_if",
        },
    )
    commute = _parse_tiered_bullets(
        _section_lines(pref_sections, "Commute"),
        {
            "preferred": "preferred",
            "comfortable": "comfortable",
            "would relocate": "would_relocate",
        },
    )
    company_size = _parse_tiered_bullets(
        _section_lines(pref_sections, "Company Size"),
        {
            "preferred": "preferred",
            "also interested": "also_interested",
        },
    )
    trade_offs = _parse_tiered_bullets(
        _section_lines(pref_sections, "Trade-offs"),
        {
            "i would trade salary for": "salary",
            "i would trade remote work for": "remote_work",
            "i would trade job title for": "job_title",
            "i would trade company prestige for": "prestige",
        },
    )
    hard_constraints = _coerce_text_list(flat_preferences.get("hard_constraints"))
    hard_yes, hard_no, hard_constraint_notes = _split_hard_constraints(hard_constraints)
    hard_yes = _dedupe_keep_order(_coerce_text_list(flat_preferences.get("must_have")) + hard_yes)
    hard_no = _dedupe_keep_order(_coerce_text_list(flat_preferences.get("hard_no")) + hard_no)
    high_priority, industry_notes = _split_items_and_notes(industries.get("high_priority"))
    also_interested, industry_also_notes = _split_items_and_notes(industries.get("also_interested"))
    industry_notes = _dedupe_keep_order(industry_notes + industry_also_notes)
    ideal_work_style, work_style_notes = _split_items_and_notes(work_style.get("ideal"))
    acceptable_work_style, acceptable_work_notes = _split_items_and_notes(work_style.get("acceptable"))
    work_style_notes = _dedupe_keep_order(work_style_notes + acceptable_work_notes)
    preferred_compensation, compensation_notes = _split_numeric_items_and_notes(compensation.get("ideal"))
    comfortable_compensation, comfortable_notes = _split_numeric_items_and_notes(compensation.get("comfortable"))
    lower_if_values, lower_if_notes = _split_items_and_notes(compensation.get("lower_if"))
    compensation_notes = _dedupe_keep_order(compensation_notes + comfortable_notes + lower_if_notes)
    preferred_commute, commute_notes = _split_items_and_notes(commute.get("preferred"))
    comfortable_commute, commute_comfortable_notes = _split_items_and_notes(commute.get("comfortable"))
    commute_notes = _dedupe_keep_order(commute_notes + commute_comfortable_notes)
    preferred_company_size, company_notes = _split_items_and_notes(company_size.get("preferred"))
    also_company_size, company_also_notes = _split_items_and_notes(company_size.get("also_interested"))
    company_notes = _dedupe_keep_order(company_notes + company_also_notes)

    return {
        "preferred_roles": _coerce_text_list(flat_preferences.get("preferred_roles")),
        "industries": {
            "high_priority": high_priority,
            "also_interested": also_interested,
            "notes": industry_notes,
        },
        "work_style": {
            "ideal": ideal_work_style,
            "acceptable": acceptable_work_style,
            "notes": work_style_notes,
        },
        "compensation": {
            "ideal": preferred_compensation,
            "comfortable": comfortable_compensation,
            "lower_if": lower_if_values,
            "notes": compensation_notes,
        },
        "commute": {
            "preferred": preferred_commute,
            "comfortable": comfortable_commute,
            "would_relocate": _dedupe_keep_order(_coerce_text_list(commute.get("would_relocate"))),
            "notes": commute_notes,
        },
        "company_size": {
            "preferred": preferred_company_size,
            "also_interested": also_company_size,
            "notes": company_notes,
        },
        "trade_offs": trade_offs,
        "nice_to_haves": _coerce_text_list(flat_preferences.get("nice_to_haves")),
        "hard_constraints": hard_constraints,
        "hard_yes": hard_yes,
        "hard_no": hard_no,
        "notes": hard_constraint_notes,
        "must_have": hard_yes,
    }


def _build_digitized_user(profile: dict[str, Any], documents: list[dict[str, Any]], *, confidence_score: int, confidence_flags: dict[str, bool]) -> dict[str, Any]:
    personal = profile.get("personal", {}) if isinstance(profile.get("personal"), dict) else {}
    preferences = profile.get("preferences", {}) if isinstance(profile.get("preferences"), dict) else {}
    scoring_preferences = profile.get("scoring_preferences", {}) if isinstance(profile.get("scoring_preferences"), dict) else {}
    eligibility = profile.get("eligibility", {}) if isinstance(profile.get("eligibility"), dict) else {}
    seniority = profile.get("seniority", {}) if isinstance(profile.get("seniority"), dict) else {}
    application_policy = profile.get("application_policy", {}) if isinstance(profile.get("application_policy"), dict) else {}
    field_sources = profile.get("field_sources", {}) if isinstance(profile.get("field_sources"), dict) else {}
    completeness_notes: list[str] = []
    if not confidence_flags.get("has_summary"):
        completeness_notes.append("summary")
    if not confidence_flags.get("has_skills"):
        completeness_notes.append("skills")
    if not confidence_flags.get("has_education"):
        completeness_notes.append("education")
    if not confidence_flags.get("has_projects"):
        completeness_notes.append("projects")
    if not confidence_flags.get("has_preferred_roles"):
        completeness_notes.append("preferred_roles")
    if not confidence_flags.get("has_trade_offs"):
        completeness_notes.append("trade_offs")
    if not confidence_flags.get("has_eligibility"):
        completeness_notes.append("eligibility")
    if not confidence_flags.get("has_seniority"):
        completeness_notes.append("seniority")
    if not confidence_flags.get("has_application_policy"):
        completeness_notes.append("application_policy")
    if not confidence_flags.get("has_scoring_preferences"):
        completeness_notes.append("structured_preferences")

    required_complete = not profile.get("missing_fields")
    ready_for_scoring = required_complete and confidence_score >= 80 and confidence_flags.get("has_eligibility") and confidence_flags.get("has_seniority")

    compensation_ideal = _coerce_text_list(scoring_preferences.get("compensation", {}).get("ideal"))
    compensation_comfortable = _coerce_text_list(scoring_preferences.get("compensation", {}).get("comfortable"))
    compensation_lower_if = _coerce_text_list(scoring_preferences.get("compensation", {}).get("lower_if"))
    eligibility_source = "task_input" if any(field_sources.get(key) == "task_input" for key in ("eligibility.right_to_work", "eligibility.driving_license", "eligibility.availability", "eligibility.work_arrangement")) else "documents"
    seniority_source = "task_input" if any(field_sources.get(key) == "task_input" for key in ("seniority.level", "seniority.recent_graduate")) else "documents"
    application_policy_source = "task_input" if any(field_sources.get(key) == "task_input" for key in ("application_policy.auto_apply", "application_policy.default_action")) else "documents"

    return {
        "identity": {
            "full_name": _coerce_text(personal.get("full_name")),
            "headline": _coerce_text(personal.get("headline")),
        },
        "contact": {
            "email": _coerce_text(personal.get("email")),
            "phone": _coerce_text(personal.get("phone")),
            "location": _coerce_text(personal.get("location")),
        },
        "links": {
            "linkedin_url": _coerce_text(personal.get("linkedin_url")),
            "github_url": _coerce_text(personal.get("github_url")),
            "website_url": _coerce_text(personal.get("website_url")),
        },
        "summary": _coerce_text(profile.get("summary")),
        "education": deepcopy(profile.get("education") if isinstance(profile.get("education"), list) else []),
        "experience": deepcopy(profile.get("experience") if isinstance(profile.get("experience"), list) else []),
        "projects": deepcopy(profile.get("projects") if isinstance(profile.get("projects"), list) else []),
        "skills": _dedupe_keep_order(_coerce_text_list(profile.get("skills"))),
        "languages": _dedupe_keep_order(_coerce_text_list(profile.get("languages"))),
        "certifications": _dedupe_keep_order(_coerce_text_list(profile.get("certifications"))),
        "eligibility": {
            "right_to_work": deepcopy(eligibility.get("right_to_work", {})) if isinstance(eligibility.get("right_to_work"), dict) else {},
            "driving_license": deepcopy(eligibility.get("driving_license", {})) if isinstance(eligibility.get("driving_license"), dict) else {},
            "availability": deepcopy(eligibility.get("availability", {})) if isinstance(eligibility.get("availability"), dict) else {},
            "work_arrangement": {
                "ideal": _dedupe_keep_order(_coerce_text_list(eligibility.get("work_arrangement", {}).get("ideal"))),
                "acceptable": _dedupe_keep_order(_coerce_text_list(eligibility.get("work_arrangement", {}).get("acceptable"))),
                "notes": _dedupe_keep_order(_coerce_text_list(eligibility.get("work_arrangement", {}).get("notes"))),
            },
        },
        "seniority": {
            "level": _coerce_text(seniority.get("level")) or "unknown",
            "recent_graduate": bool(seniority.get("recent_graduate")) or _coerce_text(seniority.get("level")).casefold() == "entry_level",
            "years_min": seniority.get("years_min"),
            "years_max": seniority.get("years_max"),
            "evidence": _dedupe_keep_order(_coerce_text_list(seniority.get("evidence"))),
            "hints": _dedupe_keep_order(_coerce_text_list(seniority.get("hints"))),
        },
        "application_policy": {
            "auto_apply": application_policy.get("auto_apply"),
            "default_action": _coerce_text(application_policy.get("default_action")),
            "notes": _dedupe_keep_order(_coerce_text_list(application_policy.get("notes"))),
        },
        "preferences": {
            "roles": _dedupe_keep_order(_coerce_text_list(preferences.get("preferred_roles"))),
            "industries": {
                "high_priority": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("industries", {}).get("high_priority"))),
                "also_interested": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("industries", {}).get("also_interested"))),
            },
            "work_style": {
                "ideal": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("work_style", {}).get("ideal"))),
                "acceptable": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("work_style", {}).get("acceptable"))),
            },
            "work_arrangement": {
                "ideal": _dedupe_keep_order(_coerce_text_list(eligibility.get("work_arrangement", {}).get("ideal"))),
                "acceptable": _dedupe_keep_order(_coerce_text_list(eligibility.get("work_arrangement", {}).get("acceptable"))),
            },
            "compensation": {
                "ideal": compensation_ideal,
                "comfortable": compensation_comfortable,
                "lower_if": compensation_lower_if,
                "notes": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("compensation", {}).get("notes"))),
                "ranges": {
                    "ideal": _parse_salary_range(_numeric_core_text(compensation_ideal)),
                    "comfortable": _parse_salary_range(_numeric_core_text(compensation_comfortable)),
                },
            },
            "commute": {
                "preferred": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("commute", {}).get("preferred"))),
                "comfortable": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("commute", {}).get("comfortable"))),
                "would_relocate": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("commute", {}).get("would_relocate"))),
            },
            "company_size": {
                "preferred": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("company_size", {}).get("preferred"))),
                "also_interested": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("company_size", {}).get("also_interested"))),
            },
            "trade_offs": {
                "salary": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("trade_offs", {}).get("salary"))),
                "remote_work": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("trade_offs", {}).get("remote_work"))),
                "job_title": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("trade_offs", {}).get("job_title"))),
                "prestige": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("trade_offs", {}).get("prestige"))),
            },
        },
        "constraints": {
            "hard_yes": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("hard_yes"))),
            "hard_no": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("hard_no"))),
            "must_have": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("hard_yes"))),
            "nice_to_haves": _dedupe_keep_order(_coerce_text_list(preferences.get("nice_to_haves"))),
            "notes": _dedupe_keep_order(_coerce_text_list(scoring_preferences.get("notes"))),
        },
        "source_coverage": {
            "field_sources": deepcopy(field_sources),
            "documents": _compact_document_summaries(documents),
            "section_sources": {
                "eligibility": eligibility_source,
                "seniority": seniority_source,
                "application_policy": application_policy_source,
            },
        },
        "completeness": {
            "required_complete": required_complete,
            "ready_for_scoring": ready_for_scoring,
            "missing_fields": list(profile.get("missing_fields", [])),
            "notes": completeness_notes,
            "confidence_score": confidence_score,
        },
    }


def _parse_profile_documents(documents: list[dict[str, Any]]) -> dict[str, Any]:
    combined_lines: list[str] = []
    by_name: dict[str, list[str]] = {}
    for document in documents:
        raw_text = str(document.get("text") or "")
        lines = _normalize_markdown_text(raw_text)
        combined_lines.extend(lines)
        by_name[_coerce_text(document.get("name")).casefold()] = lines

    cv_lines = by_name.get("cv.md", combined_lines)
    pref_lines = by_name.get("preferences.md", combined_lines)
    cv_sections = _split_markdown_sections(cv_lines)
    pref_sections = _split_markdown_sections(pref_lines)

    name = cv_lines[0].strip() if cv_lines else ""
    headline = ""
    top_block: list[str] = []
    for line in cv_lines:
        if line.startswith("## "):
            break
        top_block.append(line)
        if not headline:
            match = re.match(r"^\*\*(.+?)\*\*$", line)
            if match:
                headline = match.group(1).strip()

    summary = _section_text(cv_sections, "Professional Summary")
    education_lines = _section_lines(cv_sections, "Education")
    certifications_lines = _section_lines(cv_sections, "Certifications")
    projects_lines = _section_lines(cv_sections, "Academic Projects")
    soft_skills_lines = _section_lines(cv_sections, "Soft Skills")
    languages_lines = _section_lines(cv_sections, "Languages")
    additional_lines = _section_lines(cv_sections, "Additional Information")
    technical_lines = _section_lines(cv_sections, "Technical Skills")

    education: list[dict[str, Any]] = []
    current_entry: dict[str, Any] | None = None
    current_bucket: list[str] = []
    for line in education_lines:
        if line.startswith("### "):
            if current_entry:
                if current_bucket:
                    current_entry["details"] = current_bucket[:]
                education.append(current_entry)
            current_entry = {"title": line[4:].strip(), "details": []}
            current_bucket = []
            continue
        if line.startswith("- "):
            current_bucket.append(line[2:].strip())
        elif current_entry and line:
            current_bucket.append(line)
    if current_entry:
        if current_bucket:
            current_entry["details"] = current_bucket[:]
        education.append(current_entry)

    technical_skills: list[str] = []
    current_group = ""
    for line in technical_lines:
        if line.startswith("### "):
            current_group = line[4:].strip()
            continue
        if line.startswith("- "):
            item = line[2:].strip()
            technical_skills.append(f"{current_group}: {item}" if current_group else item)

    preferences_sections = {
        "preferred_roles": _collect_bullets(_section_lines(pref_sections, "Preferred Roles (Highest Priority)")),
        "industries": _collect_bullets(_section_lines(pref_sections, "Industries")),
        "work_style": _collect_bullets(_section_lines(pref_sections, "Preferred Work Style")),
        "technologies": _collect_bullets(_section_lines(pref_sections, "Technologies I'd Like to Work With")),
        "compensation": _collect_bullets(_section_lines(pref_sections, "Compensation Preferences")),
        "commute": _collect_bullets(_section_lines(pref_sections, "Commute")),
        "company_size": _collect_bullets(_section_lines(pref_sections, "Company Size")),
        "team_preferences": _collect_bullets(_section_lines(pref_sections, "Team Preferences")),
        "career_goals": _collect_bullets(_section_lines(pref_sections, "Career Goals")),
        "trade_offs": _collect_bullets(_section_lines(pref_sections, "Trade-offs")),
        "nice_to_haves": _collect_bullets(_section_lines(pref_sections, "Nice-to-Haves")),
        "hard_constraints": _collect_bullets(_section_lines(pref_sections, "Hard Constraints")),
    }
    scoring_preferences = _build_scoring_preferences(pref_sections, preferences_sections)

    top_text = "\n".join(top_block)
    personal = {
        "full_name": name,
        "headline": headline,
        "email": "",
        "phone": "",
        "location": "",
        "linkedin_url": "",
        "github_url": "",
        "website_url": "",
    }
    for line in top_block:
        cleaned = line.strip()
        if not personal["email"]:
            personal["email"] = _extract_contact_pattern(cleaned, r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}")
        if not personal["phone"]:
            personal["phone"] = _extract_contact_pattern(cleaned, r"(?:\+?\d[\d\s().-]{7,}\d)")
        if not personal["location"] and any(token in cleaned.casefold() for token in ("uae", "dubai", "abu dhabi", "united arab emirates")):
            if "@" not in cleaned:
                personal["location"] = cleaned.replace("  ", " ").strip()
        if not personal["linkedin_url"] and "linkedin.com" in cleaned.casefold():
            personal["linkedin_url"] = _extract_contact_pattern(cleaned, r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s)]+|linkedin\.com/[^\s)]+")
        if not personal["github_url"] and "github.com" in cleaned.casefold():
            personal["github_url"] = _extract_contact_pattern(cleaned, r"(?:https?://)?(?:www\.)?github\.com/[^\s)]+|github\.com/[^\s)]+")
        if not personal["website_url"] and "portfolio:" in cleaned.casefold():
            personal["website_url"] = cleaned.split(":", 1)[1].strip()
    if not personal["website_url"]:
        for line in reversed(top_block):
            candidate = line.strip()
            if candidate and "@" not in candidate and "." in candidate and not any(token in candidate.casefold() for token in ("linkedin.com", "github.com")):
                personal["website_url"] = candidate
                break

    skills: list[str] = []
    skills.extend(technical_skills)
    skills.extend(_collect_bullets(soft_skills_lines))
    languages = _collect_bullets(languages_lines)
    certifications = _collect_bullets(certifications_lines)
    projects = []
    current_project: dict[str, Any] | None = None
    for line in projects_lines:
        if line.startswith("### "):
            if current_project:
                projects.append(current_project)
            current_project = {"name": line[4:].strip(), "details": []}
            continue
        if line.startswith("- "):
            if current_project is None:
                current_project = {"name": "", "details": []}
            current_project["details"].append(line[2:].strip())
    if current_project:
        projects.append(current_project)

    return {
        "personal": personal,
        "summary": summary,
        "education": education,
        "skills": skills,
        "languages": languages,
        "certifications": certifications,
        "projects": projects,
        "additional_information": _collect_bullets(additional_lines),
        "preferences": preferences_sections,
        "scoring_preferences": scoring_preferences,
        "source_lines": combined_lines,
    }


def _build_source_text(documents: list[dict[str, Any]], task_input: dict[str, Any]) -> str:
    pieces: list[str] = []
    resume_text = str(task_input.get("resume_text") or "").strip()
    if resume_text:
        pieces.append(resume_text)
    for document in documents:
        text = str(document.get("text") or "").strip()
        if text:
            pieces.append(text)
    profile = task_input.get("profile")
    if isinstance(profile, dict):
        for key in ("summary", "headline", "bio"):
            text = str(profile.get(key) or "").strip()
            if text:
                pieces.append(text)
    return "\n\n".join(piece for piece in pieces if piece).strip()


def _infer_name(source_text: str) -> str:
    lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    if not lines:
        return ""
    first = lines[0]
    if len(first.split()) <= 6 and "resume" not in first.lower():
        return first
    return ""


def _extract_contact_pattern(source_text: str, pattern: str) -> str:
    match = re.search(pattern, source_text, re.I)
    return match.group(0).strip() if match else ""


def _infer_location(source_text: str) -> str:
    patterns = [
        r"[A-Z][A-Za-zÀ-ÿ'’\- ]+,\s*[A-Z][A-Za-zÀ-ÿ'’\- ]+(?:,\s*[A-Z][A-Za-zÀ-ÿ'’\- ]+)?",
        r"Dubai,\s*United Arab Emirates",
        r"Abu Dhabi,\s*United Arab Emirates",
        r"United Arab Emirates",
    ]
    for pattern in patterns:
        match = re.search(pattern, source_text, re.I)
        if match:
            return _coerce_text(match.group(0))
    return ""


def _compact_document_summaries(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for document in documents:
        text = _coerce_text(document.get("text"))
        preview = " ".join(text.splitlines()[:8]).strip()
        if len(preview) > 500:
            preview = preview[:497].rstrip() + "..."
        summaries.append(
            {
                "name": _coerce_text(document.get("name")),
                "type": _coerce_text(document.get("type")),
                "path": _coerce_text(document.get("path")),
                "preview": preview,
            }
        )
    return summaries


def _pick_profile_value(task_profile: dict[str, Any], parsed_profile: dict[str, Any], field: str) -> tuple[str, str]:
    task_value = _coerce_text(task_profile.get(field))
    if task_value:
        return task_value, "task_input"
    parsed_value = _coerce_text(parsed_profile.get(field))
    if parsed_value:
        return parsed_value, "documents"
    return "", "missing"


def _profile_confidence(profile: dict[str, Any]) -> tuple[int, dict[str, bool]]:
    personal = profile.get("personal", {}) if isinstance(profile.get("personal"), dict) else {}
    preferences = profile.get("preferences", {}) if isinstance(profile.get("preferences"), dict) else {}
    scoring_preferences = profile.get("scoring_preferences", {}) if isinstance(profile.get("scoring_preferences"), dict) else {}
    eligibility = profile.get("eligibility", {}) if isinstance(profile.get("eligibility"), dict) else {}
    seniority = profile.get("seniority", {}) if isinstance(profile.get("seniority"), dict) else {}
    application_policy = profile.get("application_policy", {}) if isinstance(profile.get("application_policy"), dict) else {}

    flags = {
        "has_full_name": bool(_coerce_text(personal.get("full_name"))),
        "has_email": bool(_coerce_text(personal.get("email"))),
        "has_location": bool(_coerce_text(personal.get("location"))),
        "has_summary": bool(_coerce_text(profile.get("summary"))),
        "has_skills": len(profile.get("skills", [])) > 0,
        "has_education": len(profile.get("education", [])) > 0,
        "has_projects": len(profile.get("projects", [])) > 0,
        "has_preferred_roles": len(preferences.get("preferred_roles", [])) > 0,
        "has_trade_offs": len(preferences.get("trade_offs", [])) > 0,
        "has_hard_constraints": len(preferences.get("hard_constraints", [])) > 0,
        "has_eligibility": any(
            eligibility.get(key)
            for key in ("right_to_work", "driving_license", "availability", "work_arrangement")
        ),
        "has_seniority": bool(_coerce_text(seniority.get("level"))) or any(
            _coerce_text(item) for item in seniority.get("evidence", [])
        ) or any(
            _coerce_text(item) for item in seniority.get("hints", [])
        ),
        "has_application_policy": bool(_coerce_text(application_policy.get("default_action"))) or application_policy.get("auto_apply") is not None,
        "has_structured_compensation": any(scoring_preferences.get("compensation", {}).get(key, []) for key in ("ideal", "comfortable", "lower_if")),
        "has_structured_industries": any(scoring_preferences.get("industries", {}).get(key, []) for key in ("high_priority", "also_interested")),
        "has_structured_work_style": any(scoring_preferences.get("work_style", {}).get(key, []) for key in ("ideal", "acceptable")),
        "has_structured_trade_offs": any(scoring_preferences.get("trade_offs", {}).get(key, []) for key in ("salary", "remote_work", "job_title", "prestige")),
        "has_scoring_preferences": any(
            flags_value
            for flags_value in (
                any(scoring_preferences.get("compensation", {}).get(key, []) for key in ("ideal", "comfortable", "lower_if")),
                any(scoring_preferences.get("industries", {}).get(key, []) for key in ("high_priority", "also_interested")),
                any(scoring_preferences.get("work_style", {}).get(key, []) for key in ("ideal", "acceptable")),
                any(scoring_preferences.get("trade_offs", {}).get(key, []) for key in ("salary", "remote_work", "job_title", "prestige")),
            )
        ),
    }

    score = 0
    score += 10 if flags["has_full_name"] else 0
    score += 10 if flags["has_email"] else 0
    score += 10 if flags["has_location"] else 0
    score += 10 if flags["has_summary"] else 0
    score += min(15, max(0, len(profile.get("skills", []))) * 2)
    score += 10 if flags["has_education"] else 0
    score += 15 if flags["has_projects"] else 0
    score += 8 if flags["has_preferred_roles"] else 0
    score += 4 if flags["has_trade_offs"] else 0
    score += 3 if flags["has_hard_constraints"] else 0
    score += 5 if flags["has_eligibility"] else 0
    score += 5 if flags["has_seniority"] else 0
    score += 2 if flags["has_application_policy"] else 0
    score += 4 if flags["has_structured_compensation"] else 0
    score += 4 if flags["has_structured_industries"] else 0
    score += 4 if flags["has_structured_work_style"] else 0
    score += 4 if flags["has_structured_trade_offs"] else 0
    score += 4 if flags["has_scoring_preferences"] else 0

    return min(100, score), flags


def _merge_profile_sources(task_input: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    parsed_documents = _parse_profile_documents(documents)
    input_profile = task_input.get("profile") if isinstance(task_input.get("profile"), dict) else {}
    input_preferences = task_input.get("preferences") if isinstance(task_input.get("preferences"), dict) else {}

    parsed_personal = parsed_documents.get("personal", {}) if isinstance(parsed_documents.get("personal"), dict) else {}
    parsed_preferences = parsed_documents.get("preferences", {}) if isinstance(parsed_documents.get("preferences"), dict) else {}
    parsed_scoring_preferences = parsed_documents.get("scoring_preferences", {}) if isinstance(parsed_documents.get("scoring_preferences"), dict) else {}
    field_sources: dict[str, str] = {}

    personal: dict[str, str] = {}
    for field in ("full_name", "headline", "email", "phone", "location", "linkedin_url", "github_url", "website_url"):
        value, source = _pick_profile_value(input_profile, parsed_personal, field)
        personal[field] = value
        field_sources[field] = source

    experience = deepcopy(input_profile.get("experience")) if isinstance(input_profile.get("experience"), list) else deepcopy(parsed_documents.get("experience") or [])
    education = deepcopy(input_profile.get("education")) if isinstance(input_profile.get("education"), list) else deepcopy(parsed_documents.get("education") or [])
    projects = deepcopy(input_profile.get("projects")) if isinstance(input_profile.get("projects"), list) else deepcopy(parsed_documents.get("projects") or [])
    skills = _coerce_text_list(input_profile.get("skills")) or _coerce_text_list(parsed_documents.get("skills"))
    languages = _coerce_text_list(input_profile.get("languages")) or _coerce_text_list(parsed_documents.get("languages"))
    certifications = _coerce_text_list(input_profile.get("certifications")) or _coerce_text_list(parsed_documents.get("certifications"))
    additional_information = _coerce_text_list(parsed_documents.get("additional_information"))

    merged_preferences = deepcopy(parsed_preferences)
    if isinstance(input_preferences, dict):
        for key, value in input_preferences.items():
            if _coerce_text(value):
                merged_preferences[key] = value
    scoring_preferences = deepcopy(parsed_scoring_preferences)

    experience_profile = _infer_seniority(
        {
            "summary": _coerce_text(input_profile.get("summary") or parsed_documents.get("summary")),
            "education": education,
            "experience": experience,
        }
    )

    preferred_roles, seniority_hints = _split_profile_role_hints(merged_preferences.get("preferred_roles"))
    merged_preferences["preferred_roles"] = preferred_roles

    skills_profile = {
        "verified_skills": _dedupe_keep_order(skills),
        "desired_technologies": _dedupe_keep_order(_coerce_text_list(merged_preferences.get("technologies"))),
    }

    structured_trade_offs = scoring_preferences.get("trade_offs", {})
    flat_trade_offs = _dedupe_keep_order(
        [
            *(_coerce_text_list(merged_preferences.get("trade_offs"))),
            *(_coerce_text_list(structured_trade_offs.get("salary"))),
            *(_coerce_text_list(structured_trade_offs.get("remote_work"))),
            *(_coerce_text_list(structured_trade_offs.get("job_title"))),
            *(_coerce_text_list(structured_trade_offs.get("prestige"))),
        ]
    )
    if not flat_trade_offs and isinstance(merged_preferences.get("trade_offs"), list):
        flat_trade_offs = _coerce_text_list(merged_preferences.get("trade_offs"))
    merged_preferences["trade_offs"] = flat_trade_offs

    eligibility = _parse_eligibility(
        parsed_documents.get("additional_information"),
        merged_preferences.get("hard_constraints"),
        {
            "work_style": {
                "ideal": _coerce_text_list(scoring_preferences.get("work_style", {}).get("ideal")),
                "acceptable": _coerce_text_list(scoring_preferences.get("work_style", {}).get("acceptable")),
            }
        },
    )
    eligibility_override = input_profile.get("eligibility") if isinstance(input_profile.get("eligibility"), dict) else None
    eligibility = _merge_eligibility_sources(eligibility, eligibility_override)
    application_policy = _parse_application_policy(_coerce_text_list(merged_preferences.get("team_preferences")))
    application_policy_override = input_profile.get("application_policy") if isinstance(input_profile.get("application_policy"), dict) else None
    application_policy = _merge_application_policy(application_policy, application_policy_override)
    seniority_override = input_profile.get("seniority") if isinstance(input_profile.get("seniority"), dict) else None
    if isinstance(seniority_override, dict) and seniority_override:
        merged_seniority = _merge_seniority(
            {
                **experience_profile,
                "recent_graduate": experience_profile.get("level") == "entry_level",
                "hints": seniority_hints,
            },
            seniority_override,
        )
        if not merged_seniority.get("hints"):
            merged_seniority["hints"] = seniority_hints
        experience_profile = merged_seniority

    eligibility_source = "task_input" if isinstance(eligibility_override, dict) and any(eligibility_override.get(key) for key in ("right_to_work", "driving_license", "availability", "work_arrangement")) else "documents"
    seniority_source = "task_input" if isinstance(seniority_override, dict) and any(seniority_override.get(key) is not None for key in ("level", "recent_graduate", "years_min", "years_max", "evidence", "hints")) else "documents"
    application_policy_source = "task_input" if isinstance(application_policy_override, dict) and any(application_policy_override.get(key) is not None for key in ("auto_apply", "default_action", "notes")) else "documents"

    field_sources.update(
        {
            "eligibility.right_to_work": eligibility_source,
            "eligibility.driving_license": eligibility_source,
            "eligibility.availability": eligibility_source,
            "eligibility.work_arrangement": eligibility_source,
            "seniority.level": seniority_source,
            "seniority.recent_graduate": seniority_source,
            "application_policy.auto_apply": application_policy_source,
            "application_policy.default_action": application_policy_source,
        }
    )

    scoring_profile = {
        "boosts": [
            *_coerce_text_list(merged_preferences.get("preferred_roles")),
            *(_coerce_text_list(parsed_scoring_preferences.get("industries", {}).get("high_priority"))),
            *(_coerce_text_list(parsed_scoring_preferences.get("work_style", {}).get("ideal"))),
            *(_coerce_text_list(parsed_scoring_preferences.get("compensation", {}).get("ideal"))),
            *(_coerce_text_list(parsed_scoring_preferences.get("commute", {}).get("preferred"))),
            *(_coerce_text_list(parsed_scoring_preferences.get("company_size", {}).get("preferred"))),
            *(_coerce_text_list(merged_preferences.get("nice_to_haves"))),
            *(_coerce_text_list(merged_preferences.get("career_goals"))),
        ],
        "penalties": [
            "Unpaid roles",
            "Commission-only roles",
            "Vague talent-pool postings",
        ],
        "hard_yes": _dedupe_keep_order(_coerce_text_list(parsed_scoring_preferences.get("hard_yes"))),
        "hard_no": _dedupe_keep_order(_coerce_text_list(parsed_scoring_preferences.get("hard_no"))),
        "hard_constraints": _coerce_text_list(merged_preferences.get("hard_constraints")),
        "experience": experience_profile,
        "compensation": {
            **parsed_scoring_preferences.get("compensation", {}),
            "parsed": _parse_salary_range(" ".join(_coerce_text_list(parsed_scoring_preferences.get("compensation", {}).get("ideal")))),
        },
        "industries": parsed_scoring_preferences.get("industries", {}),
        "work_style": parsed_scoring_preferences.get("work_style", {}),
        "trade_offs": structured_trade_offs,
        "auto_apply_threshold": 80,
        "manual_review_threshold": 60,
    }

    scoring_profile["boosts"] = _dedupe_keep_order([item for item in scoring_profile["boosts"] if item])
    scoring_profile["penalties"] = _dedupe_keep_order([item for item in scoring_profile["penalties"] if item])

    missing_fields = [field for field in REQUIRED_PROFILE_FIELDS if not personal.get(field)]
    confidence_score, confidence_flags = _profile_confidence(
        {
            "personal": personal,
            "summary": _coerce_text(input_profile.get("summary") or parsed_documents.get("summary")),
            "education": education,
            "skills": skills,
            "projects": projects,
            "preferences": merged_preferences,
            "scoring_preferences": parsed_scoring_preferences,
            "eligibility": eligibility,
            "seniority": {
                **experience_profile,
                "hints": seniority_hints,
            },
            "application_policy": application_policy,
        }
    )
    return {
        "personal": personal,
        "summary": _coerce_text(input_profile.get("summary") or parsed_documents.get("summary")),
        "education": education,
        "experience": experience,
        "projects": projects,
        "skills": skills,
        "skills_profile": skills_profile,
        "experience_profile": experience_profile,
        "languages": languages,
        "certifications": certifications,
        "preferences": merged_preferences,
        "scoring_preferences": parsed_scoring_preferences,
        "scoring_profile": scoring_profile,
        "eligibility": eligibility,
        "seniority": {
            **experience_profile,
            "recent_graduate": experience_profile.get("level") == "entry_level",
            "hints": seniority_hints,
        },
        "application_policy": application_policy,
        "additional_information": additional_information,
        "missing_fields": missing_fields,
        "confidence_score": confidence_score,
        "confidence_flags": confidence_flags,
        "field_sources": field_sources,
    }


def _build_resume_markdown(profile: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    personal = profile.get("personal", {}) if isinstance(profile.get("personal"), dict) else {}
    lines = ["# Confirmed Profile"]
    for key in ("full_name", "email", "phone", "location", "linkedin_url", "github_url", "website_url"):
        value = _coerce_text(personal.get(key))
        if value:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")

    for label in ("education", "experience", "projects", "skills", "languages", "certifications"):
        value = profile.get(label, [])
        if not value:
            continue
        if isinstance(value, list):
            lines.append(f"- {label.replace('_', ' ').title()}:")
            for item in value:
                if isinstance(item, dict):
                    title = _coerce_text(item.get("title") or item.get("name"))
                    details = item.get("details") if isinstance(item.get("details"), list) else []
                    if title:
                        lines.append(f"  - {title}")
                    for detail in details:
                        text = _coerce_text(detail)
                        if text:
                            lines.append(f"    - {text}")
                    if not title and not details:
                        text = _coerce_text(item)
                        if text:
                            lines.append(f"  - {text}")
                else:
                    text = _coerce_text(item)
                    if text:
                        lines.append(f"  - {text}")

    if documents:
        lines.append("")
        lines.append("# Source Documents")
        for document in documents:
            preview = _coerce_text(document.get("preview"))
            suffix = f": {preview}" if preview else ""
            lines.append(f"- {_coerce_text(document.get('name'))} ({_coerce_text(document.get('type'))}){suffix}")

    return "\n".join(line for line in lines if line is not None).strip()


def _build_prompt_pack(profile: dict[str, Any], task_input: dict[str, Any], source_text: str) -> dict[str, Any]:
    profile_preferences = profile.get("preferences") if isinstance(profile.get("preferences"), dict) else {}
    scoring_preferences = profile.get("scoring_preferences") if isinstance(profile.get("scoring_preferences"), dict) else {}
    scoring_profile = profile.get("scoring_profile") if isinstance(profile.get("scoring_profile"), dict) else {}
    experience_profile = scoring_profile.get("experience") if isinstance(scoring_profile.get("experience"), dict) else {}
    task_preferences = task_input.get("preferences") if isinstance(task_input.get("preferences"), dict) else {}
    preferences = deepcopy(profile_preferences)
    if isinstance(task_preferences, dict):
        for key, value in task_preferences.items():
            if _coerce_text(value):
                preferences[key] = value
    target_titles = _coerce_text_list(
        preferences.get("target_titles")
        or preferences.get("titles")
        or preferences.get("preferred_roles")
        or preferences.get("preferred_titles")
    )
    target_locations = _coerce_text_list(preferences.get("target_locations") or preferences.get("locations"))
    industries = _coerce_text_list(preferences.get("industries") or preferences.get("target_industries"))
    high_priority_industries = _coerce_text_list(scoring_preferences.get("industries", {}).get("high_priority"))
    also_interested_industries = _coerce_text_list(scoring_preferences.get("industries", {}).get("also_interested"))
    work_mode = _coerce_text(preferences.get("work_mode") or preferences.get("remote_hybrid_onsite"))
    salary = _coerce_text(preferences.get("salary") or preferences.get("salary_min"))
    skills = _coerce_text_list(profile.get("skills"))

    requirements_lines = [
        "Use only confirmed user facts.",
        "Do not invent experience, education, employers, or skills.",
    ]
    if target_titles:
        requirements_lines.append(f"Target titles: {', '.join(target_titles)}")
    if target_locations:
        requirements_lines.append(f"Target locations: {', '.join(target_locations)}")
    if high_priority_industries or industries:
        requirements_lines.append(
            f"Target industries: {', '.join(high_priority_industries or industries)}"
        )
    if also_interested_industries:
        requirements_lines.append(f"Also interested industries: {', '.join(also_interested_industries)}")
    if work_mode:
        requirements_lines.append(f"Preferred work mode: {work_mode}")
    if salary:
        requirements_lines.append(f"Salary preference: {salary}")
    if experience_profile.get("level"):
        requirements_lines.append(f"Seniority: {experience_profile.get('level')}")

    search_terms = target_titles[:3] or ["relevant roles"]
    search_locations = target_locations[:3] or [profile.get("personal", {}).get("location", "") or "any relevant location"]
    search_prompt = " | ".join(
        part for part in [
            ", ".join(search_terms),
            ", ".join(search_locations),
            ", ".join(industries[:3]) if industries else "",
        ]
        if part
    )

    scoring_lines = [
        "Score jobs using: role fit, skills fit, location fit, compensation fit, and growth fit.",
        "Prefer roles that align with confirmed skills and target titles.",
    ]
    if skills:
        scoring_lines.append(f"Known skills: {', '.join(skills[:20])}")
    if scoring_profile.get("boosts"):
        scoring_lines.append(f"Boosts: {', '.join(_coerce_text_list(scoring_profile.get('boosts'))[:20])}")
    if scoring_profile.get("penalties"):
        scoring_lines.append(f"Penalties: {', '.join(_coerce_text_list(scoring_profile.get('penalties'))[:20])}")
    if scoring_profile.get("hard_yes"):
        scoring_lines.append(f"Hard yes constraints: {', '.join(_coerce_text_list(scoring_profile.get('hard_yes'))[:20])}")
    if scoring_profile.get("hard_no"):
        scoring_lines.append(f"Hard no constraints: {', '.join(_coerce_text_list(scoring_profile.get('hard_no'))[:20])}")
    if scoring_profile.get("hard_constraints"):
        scoring_lines.append(f"Hard constraints: {', '.join(_coerce_text_list(scoring_profile.get('hard_constraints'))[:20])}")
    if scoring_profile.get("compensation", {}).get("parsed", {}).get("text"):
        scoring_lines.append(f"Compensation target: {scoring_profile['compensation']['parsed']['text']}")

    return {
        "requirements_prompt": "\n".join(requirements_lines).strip(),
        "search_prompt": search_prompt.strip(),
        "scoring_prompt": "\n".join(scoring_lines).strip(),
        "resume_markdown": _build_resume_markdown(profile, _normalize_documents(task_input.get("documents"))),
    }


def _load_onboarding_prompt_text() -> str:
    prompt_dir = resolve_agent_prompts_dir(ONBOARDING_PROMPTS_DIR)
    prompt_file = prompt_dir / "onboarding_task.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Missing onboarding prompt file: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned = _coerce_text(text)
    if not cleaned:
        raise ValueError("LLM returned an empty onboarding response.")
    candidate_texts = [cleaned]
    if cleaned.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned.strip(), flags=re.I | re.S)
        if stripped:
            candidate_texts.insert(0, stripped.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate_texts.insert(0, cleaned[start : end + 1].strip())
    last_error: Exception | None = None
    for candidate in candidate_texts:
        try:
            payload = json.loads(candidate)
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError(f"Could not parse onboarding JSON: {last_error}")


def _default_llm_profile() -> dict[str, Any]:
    return {
        "personal": {
            "full_name": "",
            "headline": "",
            "email": "",
            "phone": "",
            "location": "",
            "linkedin_url": "",
            "github_url": "",
            "website_url": "",
        },
        "summary": "",
        "education": [],
        "experience": [],
        "projects": [],
        "skills": [],
        "languages": [],
        "certifications": [],
        "preferences": {
            "preferred_roles": [],
            "industries": [],
            "work_style": [],
            "technologies": [],
            "compensation": [],
            "commute": [],
            "company_size": [],
            "team_preferences": [],
            "career_goals": [],
            "trade_offs": [],
            "nice_to_haves": [],
            "hard_constraints": [],
        },
        "scoring_preferences": {
            "industries": {"high_priority": [], "also_interested": []},
            "work_style": {"ideal": [], "acceptable": []},
            "compensation": {"ideal": [], "comfortable": [], "lower_if": [], "notes": []},
            "commute": {"preferred": [], "comfortable": [], "would_relocate": []},
            "company_size": {"preferred": [], "also_interested": []},
            "trade_offs": {"salary": [], "remote_work": [], "job_title": [], "prestige": []},
            "hard_yes": [],
            "hard_no": [],
            "hard_constraints": [],
            "notes": [],
        },
        "eligibility": {
            "right_to_work": {},
            "driving_license": {},
            "availability": {},
            "work_arrangement": {"ideal": [], "acceptable": [], "notes": []},
        },
        "seniority": {
            "level": "",
            "recent_graduate": False,
            "years_min": None,
            "years_max": None,
            "evidence": [],
            "hints": [],
        },
        "application_policy": {
            "auto_apply": None,
            "default_action": "",
            "notes": [],
        },
        "missing_fields": [],
        "confidence_score": 0,
        "confidence_flags": {},
        "field_sources": {},
    }


def _merge_profile_override(base_profile: dict[str, Any], override_profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(override_profile, dict) or not override_profile:
        return deepcopy(base_profile)
    return _deep_merge(base_profile, override_profile)


def _compact_profile_to_internal(profile: dict[str, Any]) -> dict[str, Any]:
    raw = profile if isinstance(profile, dict) else {}
    compact_preferences = raw.get("preferences") if isinstance(raw.get("preferences"), dict) else {}
    compact_scoring = raw.get("scoring_preferences") if isinstance(raw.get("scoring_preferences"), dict) else {}
    compact_eligibility = raw.get("eligibility") if isinstance(raw.get("eligibility"), dict) else {}
    internal = _default_llm_profile()
    internal["personal"] = _merge_profile_override(internal["personal"], raw.get("personal") if isinstance(raw.get("personal"), dict) else {})
    internal["summary"] = _coerce_text(raw.get("summary"))
    internal["education"] = deepcopy(raw.get("education") if isinstance(raw.get("education"), list) else [])
    internal["experience"] = deepcopy(raw.get("experience") if isinstance(raw.get("experience"), list) else [])
    internal["projects"] = deepcopy(raw.get("projects") if isinstance(raw.get("projects"), list) else [])
    internal["skills"] = _dedupe_keep_order(_coerce_text_list(raw.get("skills")))
    internal["technologies"] = _dedupe_keep_order(_coerce_text_list(raw.get("technologies")))
    internal["languages"] = _dedupe_keep_order(_coerce_text_list(raw.get("languages")))
    internal["certifications"] = _dedupe_keep_order(_coerce_text_list(raw.get("certifications")))

    roles = _dedupe_keep_order(
        _coerce_text_list(
            raw.get("preferred_roles")
            or compact_preferences.get("roles")
            or compact_preferences.get("preferred_roles")
        )
    )
    industries = compact_preferences.get("industries") if isinstance(compact_preferences.get("industries"), dict) else {}
    work_style = compact_preferences.get("work_style") if isinstance(compact_preferences.get("work_style"), dict) else {}
    work_arrangement = compact_preferences.get("work_arrangement") if isinstance(compact_preferences.get("work_arrangement"), dict) else {}
    compensation = compact_preferences.get("compensation") if isinstance(compact_preferences.get("compensation"), dict) else {}
    commute = compact_preferences.get("commute") if isinstance(compact_preferences.get("commute"), dict) else {}
    company_size = compact_preferences.get("company_size") if isinstance(compact_preferences.get("company_size"), dict) else {}
    trade_offs = compact_preferences.get("trade_offs") if isinstance(compact_preferences.get("trade_offs"), dict) else {}
    hard_yes = _dedupe_keep_order(_coerce_text_list(compact_preferences.get("hard_yes") or raw.get("hard_yes")))
    hard_no = _dedupe_keep_order(_coerce_text_list(compact_preferences.get("hard_no") or raw.get("hard_no")))
    notes = _dedupe_keep_order(_coerce_text_list(compact_preferences.get("notes") or raw.get("notes")))

    industries_high_priority = _dedupe_keep_order(
        _coerce_text_list(
            raw.get("industries_high_priority")
            or _coerce_text_list(industries.get("high_priority"))
            or compact_scoring.get("industries", {}).get("high_priority")
        )
    )
    industries_also_interested = _dedupe_keep_order(
        _coerce_text_list(
            raw.get("industries_also_interested")
            or _coerce_text_list(industries.get("also_interested"))
            or compact_scoring.get("industries", {}).get("also_interested")
        )
    )
    work_style_ideal = _dedupe_keep_order(
        _coerce_text_list(raw.get("work_style_ideal") or _coerce_text_list(work_style.get("ideal")) or compact_scoring.get("work_style", {}).get("ideal"))
    )
    work_style_acceptable = _dedupe_keep_order(
        _coerce_text_list(raw.get("work_style_acceptable") or _coerce_text_list(work_style.get("acceptable")) or compact_scoring.get("work_style", {}).get("acceptable"))
    )
    work_arrangement_ideal = _dedupe_keep_order(
        _coerce_text_list(raw.get("work_arrangement_ideal") or _coerce_text_list(work_arrangement.get("ideal")))
    )
    work_arrangement_acceptable = _dedupe_keep_order(
        _coerce_text_list(raw.get("work_arrangement_acceptable") or _coerce_text_list(work_arrangement.get("acceptable")))
    )
    work_arrangement_notes = _dedupe_keep_order(
        _coerce_text_list(raw.get("eligibility_work_arrangement_notes") or _coerce_text_list(work_arrangement.get("notes")))
    )
    compensation_ideal = _dedupe_keep_order(
        _coerce_text_list(raw.get("compensation_ideal") or _coerce_text_list(compensation.get("ideal")) or compact_scoring.get("compensation", {}).get("ideal"))
    )
    compensation_comfortable = _dedupe_keep_order(
        _coerce_text_list(raw.get("compensation_comfortable") or _coerce_text_list(compensation.get("comfortable")) or compact_scoring.get("compensation", {}).get("comfortable"))
    )
    compensation_lower_if = _dedupe_keep_order(
        _coerce_text_list(raw.get("compensation_lower_if") or _coerce_text_list(compensation.get("lower_if")) or compact_scoring.get("compensation", {}).get("lower_if"))
    )
    commute_preferred = _dedupe_keep_order(_coerce_text_list(raw.get("commute_preferred") or _coerce_text_list(commute.get("preferred"))))
    commute_comfortable = _dedupe_keep_order(_coerce_text_list(raw.get("commute_comfortable") or _coerce_text_list(commute.get("comfortable"))))
    commute_would_relocate = _dedupe_keep_order(_coerce_text_list(raw.get("commute_would_relocate") or _coerce_text_list(commute.get("would_relocate"))))
    company_size_preferred = _dedupe_keep_order(_coerce_text_list(raw.get("company_size_preferred") or _coerce_text_list(company_size.get("preferred"))))
    company_size_also_interested = _dedupe_keep_order(_coerce_text_list(raw.get("company_size_also_interested") or _coerce_text_list(company_size.get("also_interested"))))
    trade_off_salary = _dedupe_keep_order(_coerce_text_list(raw.get("trade_off_salary") or _coerce_text_list(trade_offs.get("salary"))))
    trade_off_remote_work = _dedupe_keep_order(_coerce_text_list(raw.get("trade_off_remote_work") or _coerce_text_list(trade_offs.get("remote_work"))))
    trade_off_job_title = _dedupe_keep_order(_coerce_text_list(raw.get("trade_off_job_title") or _coerce_text_list(trade_offs.get("job_title"))))
    trade_off_prestige = _dedupe_keep_order(_coerce_text_list(raw.get("trade_off_prestige") or _coerce_text_list(trade_offs.get("prestige"))))

    eligibility_right_to_work = raw.get("eligibility_right_to_work") if isinstance(raw.get("eligibility_right_to_work"), dict) else {}
    eligibility_driving_license = raw.get("eligibility_driving_license") if isinstance(raw.get("eligibility_driving_license"), dict) else {}
    eligibility_availability = raw.get("eligibility_availability") if isinstance(raw.get("eligibility_availability"), dict) else {}
    eligibility_work_arrangement_ideal = _dedupe_keep_order(
        _coerce_text_list(raw.get("eligibility_work_arrangement_ideal") or _coerce_text_list(compact_eligibility.get("work_arrangement", {}).get("ideal")))
    )
    eligibility_work_arrangement_acceptable = _dedupe_keep_order(
        _coerce_text_list(raw.get("eligibility_work_arrangement_acceptable") or _coerce_text_list(compact_eligibility.get("work_arrangement", {}).get("acceptable")))
    )
    eligibility_work_arrangement_notes = _dedupe_keep_order(
        _coerce_text_list(raw.get("eligibility_work_arrangement_notes") or _coerce_text_list(compact_eligibility.get("work_arrangement", {}).get("notes")))
    )
    seniority_level = _coerce_text(raw.get("seniority_level") or compact_scoring.get("experience", {}).get("level") or raw.get("seniority", {}).get("level") if isinstance(raw.get("seniority"), dict) else "")
    seniority_recent_graduate = bool(raw.get("seniority_recent_graduate"))
    seniority_years_min = raw.get("seniority_years_min")
    seniority_years_max = raw.get("seniority_years_max")
    seniority_evidence = _dedupe_keep_order(_coerce_text_list(raw.get("seniority_evidence")))
    seniority_hints = _dedupe_keep_order(_coerce_text_list(raw.get("seniority_hints")))
    application_auto_apply = raw.get("application_auto_apply")
    application_default_action = _coerce_text(raw.get("application_default_action") or raw.get("application_policy", {}).get("default_action") if isinstance(raw.get("application_policy"), dict) else "")
    application_notes = _dedupe_keep_order(_coerce_text_list(raw.get("application_notes")))

    internal_preferences = {
        "preferred_roles": roles,
        "industries": _dedupe_keep_order([*industries_high_priority, *industries_also_interested]),
        "work_style": _dedupe_keep_order([*work_style_ideal, *work_style_acceptable]),
        "technologies": _dedupe_keep_order(_coerce_text_list(raw.get("technologies"))),
        "compensation": _dedupe_keep_order([*compensation_ideal, *compensation_comfortable, *compensation_lower_if]),
        "commute": _dedupe_keep_order([*commute_preferred, *commute_comfortable, *commute_would_relocate]),
        "company_size": _dedupe_keep_order([*company_size_preferred, *company_size_also_interested]),
        "team_preferences": [],
        "career_goals": [],
        "trade_offs": _dedupe_keep_order([*trade_off_salary, *trade_off_remote_work, *trade_off_job_title, *trade_off_prestige]),
        "nice_to_haves": _dedupe_keep_order(_coerce_text_list(raw.get("nice_to_haves"))),
        "hard_constraints": _dedupe_keep_order([*hard_yes, *hard_no, *_coerce_text_list(raw.get("hard_constraints"))]),
        "notes": notes,
        "hard_yes": hard_yes,
        "hard_no": hard_no,
        "must_have": hard_yes,
    }
    internal_scoring_preferences = {
        "industries": {
            "high_priority": industries_high_priority,
            "also_interested": industries_also_interested,
        },
        "work_style": {
            "ideal": work_style_ideal,
            "acceptable": work_style_acceptable,
        },
        "compensation": {
            "ideal": compensation_ideal,
            "comfortable": compensation_comfortable,
            "lower_if": compensation_lower_if,
            "notes": [],
        },
        "commute": {
            "preferred": commute_preferred,
            "comfortable": commute_comfortable,
            "would_relocate": commute_would_relocate,
        },
        "company_size": {
            "preferred": company_size_preferred,
            "also_interested": company_size_also_interested,
        },
        "trade_offs": {
            "salary": trade_off_salary,
            "remote_work": trade_off_remote_work,
            "job_title": trade_off_job_title,
            "prestige": trade_off_prestige,
        },
        "hard_yes": hard_yes,
        "hard_no": hard_no,
        "hard_constraints": _dedupe_keep_order([*hard_yes, *hard_no]),
        "notes": notes,
    }
    internal["preferences"] = internal_preferences
    internal["scoring_preferences"] = internal_scoring_preferences
    internal["eligibility"] = {
        "right_to_work": deepcopy(eligibility_right_to_work),
        "driving_license": deepcopy(eligibility_driving_license),
        "availability": deepcopy(eligibility_availability),
        "work_arrangement": {
            "ideal": eligibility_work_arrangement_ideal or work_arrangement_ideal,
            "acceptable": eligibility_work_arrangement_acceptable or work_arrangement_acceptable,
            "notes": eligibility_work_arrangement_notes or work_arrangement_notes,
        },
    }
    internal["seniority"] = {
        "level": seniority_level,
        "recent_graduate": seniority_recent_graduate or seniority_level.casefold() == "entry_level",
        "years_min": seniority_years_min,
        "years_max": seniority_years_max,
        "evidence": seniority_evidence,
        "hints": seniority_hints,
    }
    internal["application_policy"] = {
        "auto_apply": application_auto_apply,
        "default_action": application_default_action,
        "notes": application_notes,
    }
    return internal


def _build_onboarding_messages(task_input: dict[str, Any], documents: list[dict[str, Any]], prompt_text: str, *, focus: str = "core") -> list[dict[str, str]]:
    compact_documents = [
        {
            "name": _coerce_text(document.get("name")),
            "type": _coerce_text(document.get("type")),
            "text": _coerce_text(document.get("text")),
        }
        for document in documents
    ]
    payload = {
        "task_name": _coerce_text(task_input.get("task_name") or "onboarding_profile_digitization"),
        "task_id": _coerce_text(task_input.get("task_id") or ""),
        "focus": _coerce_text(focus or "core"),
        "documents": compact_documents,
    }
    return [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": json.dumps(payload, indent=2, ensure_ascii=False)},
    ]


def _call_onboarding_llm(task_input: dict[str, Any], documents: list[dict[str, Any]], *, focus: str = "core") -> dict[str, Any]:
    app_config = load_app_config(verbose=False)
    onboarding_llm = app_config.get("agent_onboarding", {}).get("llm") if isinstance(app_config.get("agent_onboarding"), dict) else {}
    llm_backend = app_config.get("llm_backend", {})
    backend_name = _coerce_text((onboarding_llm or {}).get("provider") or llm_backend.get("backend") or app_config.get("llm", {}).get("provider") or "local")
    backend_settings = onboarding_llm if isinstance(onboarding_llm, dict) and onboarding_llm else {}
    if not backend_settings:
        backend_settings = llm_backend.get(backend_name) if isinstance(llm_backend.get(backend_name), dict) else {}
    if not backend_settings:
        backend_settings = app_config.get("llm", {}) if isinstance(app_config.get("llm"), dict) else {}
    prompt_text = _load_onboarding_prompt_text()
    messages = _build_onboarding_messages(task_input, documents, prompt_text, focus=focus)
    response_text = call_model_chat_with_retry(
        backend_name,
        messages=messages,
        api_key_path=backend_settings.get("api_key_path"),
        openai_model=backend_settings.get("model", "gpt-5.4-mini"),
        openai_base_url=backend_settings.get("base_url", "https://api.openai.com/v1/chat/completions"),
        openai_reasoning_effort=backend_settings.get("reasoning_effort", "low"),
        llama_model=backend_settings.get("model", "qwen3.5-9b"),
        llama_base_url=backend_settings.get("base_url", "http://127.0.0.1:8080/v1/chat/completions"),
        llama_temperature=float(backend_settings.get("temperature", 0.0) or 0.0),
        max_completion_tokens=int(backend_settings.get("max_output_tokens", 8192) or 8192),
        timeout=int(backend_settings.get("timeout_seconds", 120) or 120),
        retries=2,
    )
    payload = _extract_json_payload(response_text)
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("Onboarding LLM response did not include a profile object.")
    return {
        "profile": profile,
        "raw_response": response_text,
    }


def _build_questions(missing_fields: list[str], asked_fields: list[str] | None = None) -> list[dict[str, str]]:
    seen = {field.strip().casefold() for field in (asked_fields or []) if field}
    questions: list[dict[str, str]] = []
    for field in missing_fields:
        if field.strip().casefold() in seen:
            continue
        label = field.replace("_", " ").strip().title()
        questions.append(
            {
                "field": field,
                "question": f"What is your {label.lower()}?",
            }
        )
    return questions


def _build_scoring_questions(profile: dict[str, Any], asked_fields: list[str] | None = None) -> list[dict[str, str]]:
    seen = {field.strip().casefold() for field in (asked_fields or []) if field}
    scoring_profile = profile.get("scoring_profile") if isinstance(profile.get("scoring_profile"), dict) else {}
    experience_profile = scoring_profile.get("experience") if isinstance(scoring_profile.get("experience"), dict) else {}
    questions: list[dict[str, str]] = []

    scoring_pairs = [
        ("minimum_monthly_salary_aed", "What is your absolute minimum monthly salary in AED?"),
        ("auto_apply_policy", "Can the agent auto-apply, or should it only shortlist jobs for review?"),
        ("hard_no_roles", "Any hard-no roles, industries, company types, or job types?"),
        ("abu_dhabi_relocation_policy", "For Abu Dhabi or other UAE relocation, is that generally acceptable or only for exceptional roles?"),
        ("skip_unpaid_commission_only", "Should unpaid, commission-only, and vague talent-pool postings always be skipped?"),
        ("entry_level_exception_policy", "Are roles asking for 1–2 years experience okay if the skills match?"),
        ("target_companies", "Any target companies you want boosted?"),
    ]
    for field, question in scoring_pairs:
        if field in seen:
            continue
        questions.append({"field": field, "question": question})

    if experience_profile.get("level") == "entry_level" and "entry_level_hint" not in seen:
        questions.append({"field": "entry_level_hint", "question": "Should the scorer treat you as a recent graduate / entry-level candidate by default?"})

    return questions


def _task_input_summary(task_input: dict[str, Any]) -> dict[str, Any]:
    documents = _normalize_documents(task_input.get("documents"))
    profile = task_input.get("profile") if isinstance(task_input.get("profile"), dict) else {}
    preferences = task_input.get("preferences") if isinstance(task_input.get("preferences"), dict) else {}
    return {
        "task_name": _coerce_text(task_input.get("task_name") or "onboarding_profile_digitization"),
        "document_count": len(documents),
        "profile_fields": sorted([key for key, value in profile.items() if _coerce_text(value)]),
        "preference_fields": sorted([key for key, value in preferences.items() if _coerce_text(value)]),
        "asked_fields": sorted(_coerce_text_list(task_input.get("asked_fields"))),
    }


def run_onboarding_task(
    task_input: dict[str, Any],
    *,
    state_path: str | Path | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    step_delay_seconds: float = DEFAULT_STEP_DELAY_SECONDS,
    verbose: bool = True,
) -> dict[str, Any]:
    input_payload = deepcopy(task_input or {})
    path = Path(state_path or DEFAULT_STATE_PATH)
    task_id = _coerce_text(input_payload.get("task_id") or uuid.uuid4().hex[:12])
    documents = _normalize_documents(input_payload.get("documents"))
    source_text = _build_source_text(documents, input_payload)
    state_writer = TaskStateWriter(path, heartbeat_seconds=heartbeat_seconds, verbose=verbose)

    state_writer.initialize(
        {
            "task_id": task_id,
            "task_type": "onboarding_profile_digitization",
            "status": "queued",
            "phase": "queued",
            "step": "queued",
            "message": "Task queued.",
            "progress": 0,
            "updated_at": _now_iso(),
            "heartbeat_at": _now_iso(),
            "state_path": str(path),
            "input": _task_input_summary(input_payload),
            "result": {},
            "warnings": [],
            "missing_fields": [],
        }
    )
    state_writer.start()

    result: dict[str, Any] = {
        "task_id": task_id,
        "task_type": "onboarding_profile_digitization",
        "status": "queued",
        "state_path": str(path),
        "input": _task_input_summary(input_payload),
        "digitized_user": {},
        "completeness": {},
        "result": {},
        "warnings": [],
        "missing_fields": [],
    }

    try:
        _vlog(verbose, "task: ingest input")
        state_writer.set(
            status="running",
            phase="ingest",
            step="load_input",
            message="Loading onboarding input.",
            progress=10,
        )
        time.sleep(max(0.0, step_delay_seconds))

        _vlog(verbose, f"task: documents={len(documents)}")
        state_writer.set(
            phase="extract",
            step="call_llm",
            message=f"Extracting digitized profile from {len(documents)} document(s).",
            progress=30,
        )
        time.sleep(max(0.0, step_delay_seconds))

        llm_results: dict[str, dict[str, Any]] = {}
        llm_warnings: list[str] = []
        for focus, progress in (("core", 30), ("preferences", 42)):
            state_writer.set(
                phase="extract",
                step=f"call_llm_{focus}",
                message=f"Extracting {focus} profile data from {len(documents)} document(s).",
                progress=progress,
            )
            time.sleep(max(0.0, step_delay_seconds))
            try:
                llm_results[focus] = _call_onboarding_llm(input_payload, documents, focus=focus)
            except Exception as exc:
                llm_warnings.append(f"{focus} extraction failed: {exc}")
                llm_results[focus] = {"profile": {}, "raw_response": ""}

        merged_llm_profile = _deep_merge(
            llm_results.get("core", {}).get("profile", {}) if isinstance(llm_results.get("core", {}).get("profile", {}), dict) else {},
            llm_results.get("preferences", {}).get("profile", {}) if isinstance(llm_results.get("preferences", {}).get("profile", {}), dict) else {},
        )
        profile = _compact_profile_to_internal(merged_llm_profile)
        missing_fields = [field for field in REQUIRED_PROFILE_FIELDS if not _coerce_text(profile.get("personal", {}).get(field))]
        confidence_score, confidence_flags = _profile_confidence(profile)
        profile["missing_fields"] = missing_fields
        profile["confidence_score"] = confidence_score
        profile["confidence_flags"] = confidence_flags
        warnings: list[str] = [*llm_warnings]
        if missing_fields:
            warnings.append(f"Missing required profile fields: {', '.join(missing_fields)}")
        incomplete_quality_sections = [
            label
            for label, flag in (
                ("summary", confidence_flags.get("has_summary")),
                ("skills", confidence_flags.get("has_skills")),
                ("education", confidence_flags.get("has_education")),
                ("projects", confidence_flags.get("has_projects")),
                ("preferred_roles", confidence_flags.get("has_preferred_roles")),
                ("trade_offs", confidence_flags.get("has_trade_offs")),
                ("structured_preferences", confidence_flags.get("has_scoring_preferences")),
            )
            if not flag
        ]
        if incomplete_quality_sections:
            warnings.append(f"Incomplete profile sections: {', '.join(incomplete_quality_sections)}")
        state_writer.set(
            phase="normalize",
            step="normalize_profile",
            message="Normalized extracted profile data.",
            progress=60,
            missing_fields=missing_fields,
            warnings=warnings,
            result={
                "raw_llm_response": {
                    key: value.get("raw_response", "")[:2000]
                    for key, value in llm_results.items()
                    if isinstance(value, dict)
                }
            },
        )
        time.sleep(max(0.0, step_delay_seconds))

        state_writer.set(
            phase="finalize",
            step="compose_output",
            message="Composing digitized user handoff.",
            progress=85,
        )
        time.sleep(max(0.0, step_delay_seconds))

        digitized_user = _build_digitized_user(
            profile,
            documents,
            confidence_score=confidence_score,
            confidence_flags=confidence_flags,
        )
        completeness = digitized_user["completeness"]
        status = "partial" if missing_fields else "success"
        final_result = {
            "digitized_user": digitized_user,
            "completeness": completeness,
        }

        result.update(
            {
                "status": status,
                "missing_fields": missing_fields,
                "warnings": warnings,
                "digitized_user": digitized_user,
                "completeness": completeness,
                "result": final_result,
                "raw_llm_response": {
                    key: value.get("raw_response", "")
                    for key, value in llm_results.items()
                    if isinstance(value, dict)
                },
            }
        )
        state_writer.set(
            status=status,
            phase="finalize",
            step="done",
            message="Onboarding task complete.",
            progress=100,
            result=final_result,
            missing_fields=missing_fields,
            warnings=warnings,
        )
        return deepcopy(result)
    except Exception as exc:
        message = str(exc)
        result.update(
            {
                "status": "failed",
                "warnings": [message],
                "digitized_user": {},
                "completeness": {},
                "result": {},
            }
        )
        state_writer.set(
            status="failed",
            phase="error",
            step="error",
            message=message,
            progress=100,
            warnings=[message],
        )
        return deepcopy(result)
    finally:
        state_writer.stop()

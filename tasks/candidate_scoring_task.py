from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.config import load_app_config
from agents_runtime.webagent_runtime import call_model_chat_with_retry


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = REPO_ROOT / "runtime" / "task_states" / "candidate_scoring_task_state.json"
DEFAULT_HEARTBEAT_SECONDS = 2.0
DEFAULT_STEP_DELAY_SECONDS = 0.2
DEFAULT_BATCH_SIZE = 15
DEFAULT_LLM_BACKEND = "local"
DEFAULT_LLM_MAX_COMPLETION_TOKENS = 1200
DEFAULT_LLM_TIMEOUT_SECONDS = 180

SENIOR_TERMS = (
    "senior",
    "sr ",
    "sr.",
    "lead",
    "principal",
    "staff",
    "manager",
    "director",
    "head",
    "architect",
    "expert",
)

ENTRY_TERMS = (
    "entry level",
    "entry-level",
    "junior",
    "associate",
    "graduate",
    "new grad",
    "new graduate",
    "trainee",
    "intern",
)

VAGUE_TERMS = (
    "talent pool",
    "open application",
    "general application",
    "multiple roles",
    "future opportunities",
    "opportunity",
    "vacancy",
    "evergreen",
    "pipeline",
)

ROLE_STRONG_HINTS = (
    "data engineer",
    "data analyst",
    "analytics engineer",
    "business intelligence analyst",
    "bi analyst",
    "junior ml engineer",
    "machine learning engineer",
    "ai/data solutions engineer",
    "data scientist",
    "data platform",
    "etl",
    "sql analyst",
    "reporting analyst",
    "power bi",
    "tableau",
    "spark",
    "kafka",
    "airflow",
    "dbt",
    "data warehouse",
    "data engineering",
    "data analytics",
    "business intelligence",
)

ROLE_PARTIAL_HINTS = (
    "it graduate",
    "technology graduate",
    "graduate trainee",
    "graduate program",
    "fresh graduate",
    "freshers",
    "fresher",
    "trainee",
    "intern",
    "digital analyst",
    "business analyst",
    "operations analyst",
    "product analyst",
    "product data",
    "cloud trainee",
    "software engineer",
    "software engineering",
    "software developer",
    "developer",
    "technical graduate",
    "engineering fresher",
)

ROLE_WEAK_HINTS = (
    "data entry",
    "admin assistant",
    "administrative assistant",
    "customer service",
    "customer support",
    "receptionist",
    "guest services",
    "guest service",
    "student affairs",
    "call center",
    "support officer",
    "hr assistant",
    "sales executive",
    "sales representative",
)

ROLE_NO_HINTS = (
    "waiter",
    "waitress",
    "bus person",
    "runner",
    "f&b attendant",
    "food and beverage",
    "food & beverage",
    "driver",
    "kids club",
    "real estate agent",
    "teacher",
    "sports organizer",
    "housekeeping",
    "cashier",
)

HARD_EXCLUDE_CODES = {
    "hard_constraint_conflict",
}


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


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return text


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in re.split(r"[,\n;|]+", value) if part.strip()]
        return items
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            text = _coerce_text(item)
            if text:
                items.extend([part.strip() for part in re.split(r"[,\n;|]+", text) if part.strip()])
        return items
    text = _coerce_text(value)
    return [text] if text else []


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


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

        self._stop.clear()
        self._thread = threading.Thread(target=_beat, name="candidate-scoring-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None


def _normalize_digitized_user(task_input: dict[str, Any]) -> dict[str, Any]:
    for key in ("digitized_user", "onboarding_result", "profile"):
        value = task_input.get(key)
        if isinstance(value, dict):
            if key == "profile" and not any(k in value for k in ("identity", "contact", "preferences", "constraints")):
                continue
            if key != "digitized_user":
                nested = value.get("digitized_user")
                if isinstance(nested, dict):
                    value = nested
                else:
                    value = value
            result = deepcopy(value)
            constraints = result.get("constraints") if isinstance(result.get("constraints"), dict) else {}
            if isinstance(constraints, dict):
                hard_yes = _dedupe_keep_order(_coerce_text_list(constraints.get("hard_yes")) + _coerce_text_list(constraints.get("must_have")))
                hard_no = _dedupe_keep_order(_coerce_text_list(constraints.get("hard_no")))
                if not hard_yes and not hard_no and constraints.get("hard_constraints") is not None:
                    split_yes, split_no = _split_hard_constraints(constraints.get("hard_constraints"))
                    hard_yes = split_yes
                    hard_no = split_no
                result.setdefault("constraints", {})
                result["constraints"]["hard_yes"] = hard_yes
                result["constraints"]["hard_no"] = hard_no
                if "must_have" in result["constraints"] or hard_yes:
                    result["constraints"]["must_have"] = hard_yes
            return result
    result = task_input.get("result")
    if isinstance(result, dict):
        nested = result.get("digitized_user")
        if isinstance(nested, dict):
            return deepcopy(nested)
    return {}


def _normalize_candidates(task_input: dict[str, Any]) -> list[dict[str, Any]]:
    source = task_input.get("candidates")
    if not isinstance(source, list):
        source = task_input.get("listings")
    if not isinstance(source, list):
        source = task_input.get("rows")
    if not isinstance(source, list):
        return []
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        row = deepcopy(item)
        listing_id = _coerce_text(row.get("listing_id") or row.get("job_id") or row.get("id"))
        if not listing_id:
            listing_id = f"candidate-{index + 1}"
        row["listing_id"] = listing_id
        row.setdefault("job_id", listing_id)
        row.setdefault("title", _coerce_text(row.get("title")))
        row.setdefault("company", _coerce_text(row.get("company")))
        row.setdefault("location", _coerce_text(row.get("location")))
        row.setdefault("link", _coerce_text(row.get("link")))
        candidates.append(row)
    return candidates


def _normalize_search_context(task_input: dict[str, Any]) -> dict[str, Any]:
    source = task_input.get("candidate_search_input")
    if not isinstance(source, dict):
        source = task_input.get("search_context")
    if not isinstance(source, dict):
        source = task_input.get("source_context")
    if not isinstance(source, dict):
        source = task_input.get("search")
    if not isinstance(source, dict):
        source = {}
    filters = source.get("filters") if isinstance(source.get("filters"), dict) else {}
    return {
        "keyword": _coerce_text(source.get("keyword") or source.get("query") or ""),
        "location": _coerce_text(source.get("location") or ""),
        "filters": _canonicalize(filters),
        "pages": source.get("pages"),
    }


def _split_hard_constraints(items: Any) -> tuple[list[str], list[str]]:
    negative_markers = (
        "skip",
        "avoid",
        "do not",
        "don't",
        "not ",
        "without",
        "unpaid",
        "commission-only",
        "commission only",
        "vague",
        "poor fit",
        "not worth",
        "unclear",
        "never",
        "no ",
    )
    hard_yes: list[str] = []
    hard_no: list[str] = []
    for item in _dedupe_keep_order(_coerce_text_list(items)):
        lowered = item.casefold()
        if any(marker in lowered for marker in negative_markers):
            hard_no.append(item)
        else:
            hard_yes.append(item)
    return _dedupe_keep_order(hard_yes), _dedupe_keep_order(hard_no)


def _normalize_llm_settings(task_input: dict[str, Any]) -> dict[str, Any]:
    try:
        app_config = load_app_config(verbose=False)
        llm_config = deepcopy(app_config.get("llm_backend") or {})
    except Exception:
        llm_config = {}
    overrides = task_input.get("llm")
    if isinstance(overrides, dict):
        llm_config = _deep_merge(llm_config, overrides)
    backend_override = _coerce_text(task_input.get("llm_backend"))
    if backend_override:
        llm_config["backend"] = backend_override
    for key in ("backend", "api_key_path", "openai_model", "openai_base_url", "openai_reasoning_effort", "llama_model", "llama_base_url", "llama_temperature", "max_completion_tokens", "timeout", "retries"):
        value = task_input.get(key)
        if value not in (None, ""):
            llm_config[key] = value
    llm_config.setdefault("backend", llm_config.get("backend") or DEFAULT_LLM_BACKEND)
    llm_config.setdefault("max_completion_tokens", DEFAULT_LLM_MAX_COMPLETION_TOKENS)
    llm_config.setdefault("timeout", DEFAULT_LLM_TIMEOUT_SECONDS)
    llm_config.setdefault("retries", 2)
    return llm_config


def _candidate_prompt_row(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": _coerce_text(candidate.get("title")),
        "listing_id": _coerce_text(candidate.get("listing_id")),
        "company": _coerce_text(candidate.get("company")),
        "location": _coerce_text(candidate.get("location")),
        "job_type": _coerce_text(candidate.get("job_type") or candidate.get("employment_type")),
        "easy_apply": bool(_coerce_text(candidate.get("easy_apply")).casefold() in {"true", "yes", "1"}),
        "promoted": bool(_coerce_text(candidate.get("promoted")).casefold() in {"true", "yes", "1"}),
        "listed_on": _coerce_text(candidate.get("listed_on")),
    }


def _is_low_confidence_rejection_reason(reason: str) -> bool:
    text = _coerce_text(reason).casefold()
    if not text:
        return False
    markers = (
        "unclear",
        "may be",
        "might be",
        "possibly",
        "vague",
        "empty job_type",
        "empty job type",
        "thin",
        "not enough",
        "could be",
        "maybe",
        "hard to tell",
        "seems like",
        "looks like",
    )
    return any(marker in text for marker in markers)


def _is_supported_exclusion_reason(reason: str, candidate: dict[str, Any], digitized_user: dict[str, Any]) -> bool:
    reason_text = _coerce_text(reason).casefold()
    candidate_blob = _candidate_blob(candidate).casefold()
    hard_yes, hard_no = _hard_buckets(digitized_user)

    explicit_markers = (
        "hard constraint conflict",
        "constraint conflict",
        "role irrelevance",
        "role mismatch",
        "unrelated role",
        "weakly related",
        "support/admin role",
        "hospitality/service role",
        "customer service role",
        "receptionist role",
        "data entry role",
        "real estate role",
        "education role",
        "outside uae",
        "outside the uae",
        "not in uae",
        "unpaid",
        "commission-only",
        "commission only",
        "talent pool",
        "open application",
        "general application",
        "future opportunities",
        "evergreen",
        "pipeline",
        "internship",
        "part-time",
        "part time",
        "temporary",
        "contract",
        "full-time",
        "full time",
    )
    if any(marker in reason_text or marker in candidate_blob for marker in explicit_markers):
        return True

    if any(_coerce_text(item).casefold() in candidate_blob for item in hard_no if _coerce_text(item)):
        return True

    if any(("full-time" in _coerce_text(item).casefold() or "full time" in _coerce_text(item).casefold()) for item in hard_yes):
        if any(needle in candidate_blob for needle in ("internship", "part-time", "part time", "temporary", "contract")):
            return True

    if any("uae" in _coerce_text(item).casefold() for item in hard_yes):
        if any(country in candidate_blob for country in ("united states", "uk", "india", "canada", "europe")):
            return True

    return False


def _build_batch_messages(
    digitized_user: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
    search_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    search_context = search_context or {}
    system = (
        "You are a candidate exclusion judge.\n"
        "Exclude only rows that should not go to detail fetch.\n"
        "Hard constraints are necessary, not sufficient.\n"
        "Apply role relevance too.\n"
        "Exclude weak/no-fit roles even if they are full-time, legal, UAE-based, or entry-level.\n"
        "Keep only rows with meaningful overlap to the target roles, relevant technical work, or a structured graduate/tech program.\n"
        "If unsure, keep the row.\n"
        "Use common sense from the title and company text.\n"
        "Typical title hints: role_function, seniority, specialization_tool_keywords, program_nationality_signals, location_work_mode.\n"
        "Return strict JSON only.\n"
        "Schema: {\"excluded\":[{\"company\":\"\",\"listing_id\":\"\",\"reason\":\"short reason\"}]}\n"
        "If nothing should be excluded, return exactly {\"excluded\":[]}.\n"
    )
    agent2_context = {
        "roles": _coerce_text_list((digitized_user.get("roles") or (digitized_user.get("preferences") or {}).get("roles"))),
        "skills": _coerce_text_list(digitized_user.get("skills")),
        "seniority": _canonicalize(digitized_user.get("seniority") or {}),
        "eligibility": {
            "right_to_work": _canonicalize(((digitized_user.get("eligibility") or {}).get("right_to_work") or {})),
            "work_arrangement": _canonicalize(((digitized_user.get("eligibility") or {}).get("work_arrangement") or {})),
        },
        "constraints": {
            "hard_yes": _coerce_text_list((digitized_user.get("constraints") or {}).get("hard_yes")),
            "hard_no": _coerce_text_list((digitized_user.get("constraints") or {}).get("hard_no")),
        },
        "application_policy": _canonicalize(digitized_user.get("application_policy") or {}),
        "title_hints": [
            "role_function",
            "seniority",
            "specialization_tool_keywords",
            "program_nationality_signals",
            "location_work_mode",
        ],
    }
    user = {
        "batch_index": batch_index,
        "batch_count": batch_count,
        "search_context": {
            "keyword": _coerce_text(search_context.get("keyword") or ""),
            "location": _coerce_text(search_context.get("location") or ""),
            "filters": _canonicalize(search_context.get("filters") or {}),
            "pages": search_context.get("pages"),
        },
        "agent2_context": agent2_context,
        "candidates": [_candidate_prompt_row(candidate) for candidate in batch],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]


def _strip_code_fences(text: str) -> str:
    raw = _coerce_text(text)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _repair_json_text(text: str) -> str:
    raw = _strip_code_fences(text)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    raw = raw.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return raw.strip()


def _parse_llm_exclusions(text: str) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    raw = _repair_json_text(text)
    if not raw:
        return [], ["Empty LLM response."]
    data: Any
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            warnings.append("LLM response did not contain JSON; treated as no exclusions.")
            return [], warnings
        try:
            data = json.loads(_repair_json_text(match.group(0)))
        except Exception:
            warnings.append("LLM response JSON could not be repaired; treated as no exclusions.")
            return [], warnings
    excluded = data.get("excluded")
    if excluded is None:
        excluded = data.get("excluded_listing_ids") or data.get("exclude") or []
    normalized: list[dict[str, str]] = []
    if isinstance(excluded, list):
        for item in excluded:
            if isinstance(item, str):
                listing_id = _coerce_text(item)
                if listing_id:
                    normalized.append(
                        {
                            "company": "",
                            "listing_id": listing_id,
                            "reason": "Excluded by LLM.",
                        }
                    )
                continue
            if isinstance(item, dict):
                listing_id = _coerce_text(item.get("listing_id") or item.get("job_id") or item.get("id"))
                if not listing_id:
                    continue
                normalized.append(
                    {
                        "company": _coerce_text(item.get("company") or ""),
                        "listing_id": listing_id,
                        "reason": _coerce_text(item.get("reason") or "Excluded by LLM."),
                    }
                )
    if not normalized and isinstance(data.get("kept"), list):
        warnings.append("LLM response used unexpected kept-list format; treated as no exclusions.")
    return normalized, warnings


def _judge_batch_with_llm(
    digitized_user: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
    llm_settings: dict[str, Any],
    search_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], str, list[str]]:
    messages = _build_batch_messages(
        digitized_user,
        batch,
        batch_index=batch_index,
        batch_count=batch_count,
        search_context=search_context,
    )
    backend = _coerce_text(llm_settings.get("backend") or DEFAULT_LLM_BACKEND)
    llm_text = call_model_chat_with_retry(
        backend,
        messages=messages,
        api_key_path=llm_settings.get("api_key_path"),
        openai_model=_coerce_text(llm_settings.get("openai_model") or "gpt-5.4-mini"),
        openai_base_url=_coerce_text(llm_settings.get("openai_base_url") or "https://api.openai.com/v1/chat/completions"),
        openai_reasoning_effort=_coerce_text(llm_settings.get("openai_reasoning_effort") or "low"),
        llama_model=_coerce_text(llm_settings.get("llama_model") or "qwen3.5-9b"),
        llama_base_url=_coerce_text(llm_settings.get("llama_base_url") or "http://127.0.0.1:8080/v1/chat/completions"),
        llama_temperature=float(llm_settings.get("llama_temperature") or 0.2),
        max_completion_tokens=int(llm_settings.get("max_completion_tokens") or DEFAULT_LLM_MAX_COMPLETION_TOKENS),
        timeout=int(llm_settings.get("timeout") or DEFAULT_LLM_TIMEOUT_SECONDS),
        retries=int(llm_settings.get("retries") or 2),
    )
    excluded, warnings = _parse_llm_exclusions(llm_text)
    return excluded, llm_text, warnings


def _split_batches(items: list[dict[str, Any]], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[dict[str, Any]]]:
    size = max(1, int(batch_size or DEFAULT_BATCH_SIZE))
    return [items[index : index + size] for index in range(0, len(items), size)]


def _candidate_blob(candidate: dict[str, Any]) -> str:
    parts = [
        _coerce_text(candidate.get("title")),
        _coerce_text(candidate.get("company")),
        _coerce_text(candidate.get("location")),
        _coerce_text(candidate.get("job_type") or candidate.get("employment_type")),
        _coerce_text(candidate.get("easy_apply")),
        _coerce_text(candidate.get("promoted")),
    ]
    return " | ".join(part for part in parts if part)


def _preferred_role_match(text: str, digitized_user: dict[str, Any]) -> str:
    roles = _coerce_text_list(((digitized_user.get("preferences") or {}).get("roles")))
    haystack = text.casefold()
    for role in roles:
        role_text = role.casefold().strip()
        if role_text and role_text in haystack:
            return role
    return ""


def _role_target_summary(digitized_user: dict[str, Any]) -> str:
    roles = _dedupe_keep_order(_coerce_text_list(((digitized_user.get("preferences") or {}).get("roles"))))
    if not roles:
        return "user target roles"
    if len(roles) == 1:
        return roles[0]
    grouped = []
    for role in roles[:3]:
        grouped.append(role)
    if len(roles) > 3:
        grouped.append("...")
    return " / ".join(grouped)


def _role_relevance_level(candidate_blob: str, digitized_user: dict[str, Any]) -> tuple[str, str, str]:
    haystack = candidate_blob.casefold()
    target_roles = _dedupe_keep_order(_coerce_text_list(((digitized_user.get("preferences") or {}).get("roles"))))
    target_terms: list[str] = []
    for role in target_roles:
        normalized = _coerce_text(role).casefold()
        if normalized:
            target_terms.append(normalized)
            target_terms.extend(token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 2)
    target_terms = _dedupe_keep_order(target_terms)

    if any(hint in haystack for hint in ROLE_NO_HINTS):
        current = "unrelated role"
        for hint in ROLE_NO_HINTS:
            if hint in haystack:
                if hint in {"waiter", "waitress", "bus person", "runner", "driver"}:
                    current = "hospitality/service role"
                elif "real estate" in hint:
                    current = "real estate role"
                elif "teacher" in hint:
                    current = "education role"
                elif "kids club" in hint:
                    current = "kids club role"
                break
        return "no", current, _role_target_summary(digitized_user)

    if any(hint in haystack for hint in ROLE_WEAK_HINTS):
        current = "support/admin role"
        if "data entry" in haystack:
            current = "data entry role"
        elif "customer service" in haystack or "customer support" in haystack:
            current = "customer service role"
        elif "receptionist" in haystack:
            current = "receptionist role"
        elif "sales" in haystack:
            current = "sales role"
        return "weak", current, _role_target_summary(digitized_user)

    preferred_match = _preferred_role_match(candidate_blob, digitized_user)
    if preferred_match:
        return "strong", _coerce_text(preferred_match), _role_target_summary(digitized_user)

    strong_hit = any(hint in haystack for hint in ROLE_STRONG_HINTS)
    partial_hit = any(hint in haystack for hint in ROLE_PARTIAL_HINTS)
    target_overlap = any(term in haystack for term in target_terms if term)

    if strong_hit and target_overlap:
        return "strong", "role family overlap", _role_target_summary(digitized_user)
    if strong_hit:
        return "partial", "adjacent technical role", _role_target_summary(digitized_user)
    if partial_hit:
        if any(term in haystack for term in ("graduate program", "graduate trainee", "graduate", "trainee", "intern", "fresher", "fresh graduate", "freshers")):
            return "partial", "graduate or trainee role", _role_target_summary(digitized_user)
        return "partial", "adjacent role", _role_target_summary(digitized_user)
    if any(term in haystack for term in target_terms if term):
        return "strong", "preferred role match", _role_target_summary(digitized_user)
    return "no", "unrelated role", _role_target_summary(digitized_user)


def _location_matches(candidate_location: str, digitized_user: dict[str, Any]) -> bool:
    _ = candidate_location
    _ = digitized_user
    return True


def _hard_buckets(digitized_user: dict[str, Any]) -> tuple[list[str], list[str]]:
    constraints = digitized_user.get("constraints") if isinstance(digitized_user.get("constraints"), dict) else {}
    hard_yes = _dedupe_keep_order(_coerce_text_list(constraints.get("hard_yes")) + _coerce_text_list(constraints.get("must_have")))
    hard_no = _dedupe_keep_order(_coerce_text_list(constraints.get("hard_no")))
    if not hard_yes and not hard_no and constraints.get("hard_constraints") is not None:
        hard_yes, hard_no = _split_hard_constraints(constraints.get("hard_constraints"))
    return hard_yes, hard_no


def _has_hard_no(candidate_blob: str, digitized_user: dict[str, Any]) -> str:
    _, hard_no = _hard_buckets(digitized_user)
    haystack = candidate_blob.casefold()
    for item in hard_no:
        term = item.casefold().strip()
        if term and term in haystack:
            return item
    return ""


def _has_hard_yes_conflict(candidate_blob: str, digitized_user: dict[str, Any]) -> str:
    hard_yes, _ = _hard_buckets(digitized_user)
    haystack = candidate_blob.casefold()
    contradiction_terms = ("internship", "part-time", "part time", "temporary", "contract", "commission-only", "commission only")
    for item in hard_yes:
        term = item.casefold().strip()
        if not term:
            continue
        if "full-time" in term or "full time" in term:
            if any(needle in haystack for needle in contradiction_terms):
                return item
        if "uae" in term and "uae" not in haystack and any(needle in haystack for needle in ("dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah", "fujairah", "umm al quwain", "al ain")) is False:
            # Conservative: only flag a clear mismatch when the listing looks explicitly outside the UAE context.
            if any(country in haystack for country in ("united states", "uk", "india", "canada", "europe", "remote")):
                return item
    return ""


def _looks_senior(text: str) -> bool:
    haystack = text.casefold()
    return any(term in haystack for term in SENIOR_TERMS)


def _looks_entry_level(text: str) -> bool:
    haystack = text.casefold()
    return any(term in haystack for term in ENTRY_TERMS)


def _looks_vague(text: str) -> bool:
    haystack = text.casefold()
    return any(term in haystack for term in VAGUE_TERMS) or not text.strip()


def _shorten_reason_target(text: str) -> str:
    value = _coerce_text(text)
    if not value:
        return ""
    lowered = value.casefold()
    if "full-time" in lowered or "full time" in lowered:
        return "full-time only"
    if "part-time" in lowered or "part time" in lowered:
        return "part-time only"
    if "internship" in lowered:
        return "no internship roles"
    if "temporary" in lowered:
        return "no temporary roles"
    if "contract" in lowered:
        return "no contract roles"
    if "commission" in lowered:
        return "no commission-only roles"
    if "uae" in lowered:
        return "uae-only roles"
    return value


def _exclusion_reason_parts(candidate_blob: str, hard_no_match: str, hard_yes_conflict: str) -> dict[str, str]:
    haystack = candidate_blob.casefold()
    current = ""
    target = ""
    if hard_no_match:
        target = _shorten_reason_target(hard_no_match) or hard_no_match
        if _looks_vague(candidate_blob):
            current = "vague posting"
        elif "hr" in hard_no_match.casefold() and "hr" in haystack:
            current = "HR role"
        elif "finance" in hard_no_match.casefold() and "finance" in haystack:
            current = "finance role"
        elif "data" in hard_no_match.casefold() and "data" in haystack:
            current = "data role"
        elif "technical" in hard_no_match.casefold():
            current = "non-technical role"
        else:
            current = f"matches {hard_no_match}".strip()
    elif hard_yes_conflict:
        target = _shorten_reason_target(hard_yes_conflict) or hard_yes_conflict
        if "full-time" in hard_yes_conflict.casefold() or "full time" in hard_yes_conflict.casefold():
            if "internship" in haystack:
                current = "internship role"
            elif "part-time" in haystack or "part time" in haystack:
                current = "part-time role"
            elif "temporary" in haystack:
                current = "temporary role"
            elif "contract" in haystack:
                current = "contract role"
            elif "commission" in haystack:
                current = "commission-only role"
            else:
                current = "not full-time"
        elif "uae" in hard_yes_conflict.casefold():
            current = "outside UAE"
        else:
            current = "requirement mismatch"
    return {
        "current": _coerce_text(current),
        "target": _coerce_text(target),
    }


def _role_relevance_reason_parts(candidate_blob: str, digitized_user: dict[str, Any], level: str, current_hint: str = "") -> dict[str, str]:
    target = _role_target_summary(digitized_user)
    current = _coerce_text(current_hint)
    if not current:
        haystack = candidate_blob.casefold()
        if any(hint in haystack for hint in ROLE_NO_HINTS):
            current = "unrelated role"
        elif any(hint in haystack for hint in ROLE_WEAK_HINTS):
            current = "weakly related support role"
        elif "graduate" in haystack or "trainee" in haystack or "intern" in haystack:
            current = "graduate/trainee role"
        elif any(hint in haystack for hint in ROLE_PARTIAL_HINTS):
            current = "adjacent technical role"
        else:
            current = "unrelated role"
    return {
        "current": current,
        "target": target,
    }


def _score_candidate(candidate: dict[str, Any], digitized_user: dict[str, Any]) -> dict[str, Any]:
    text_blob = _candidate_blob(candidate)
    relevance_level, relevance_current, relevance_target = _role_relevance_level(text_blob, digitized_user)
    hard_no_match = ""
    hard_yes_conflict = ""
    score = 0
    decision = "keep"
    reason_code = ""
    reason_text = ""
    reason_parts = {"current": "", "target": ""}
    if relevance_level in {"no", "weak"}:
        decision = "exclude"
        reason_code = "role_irrelevance"
        reason_parts = _role_relevance_reason_parts(text_blob, digitized_user, relevance_level, relevance_current)
        reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")
        score = 0
    elif relevance_level == "partial":
        score = 70
    else:
        score = 100

    if decision != "exclude":
        hard_no_match = _has_hard_no(text_blob, digitized_user)
        hard_yes_conflict = _has_hard_yes_conflict(text_blob, digitized_user)
        if hard_no_match:
            decision = "exclude"
            reason_code = "constraint_conflict"
            reason_parts = _exclusion_reason_parts(text_blob, hard_no_match, "")
            reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")
            score = 0
        elif hard_yes_conflict:
            decision = "exclude"
            reason_code = "constraint_conflict"
            reason_parts = _exclusion_reason_parts(text_blob, "", hard_yes_conflict)
            reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")
            score = 0

    if decision == "exclude" and not reason_parts.get("current") and relevance_level in {"no", "weak"}:
        reason_parts = _role_relevance_reason_parts(text_blob, digitized_user, relevance_level, relevance_current)
        reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")

    if decision == "exclude" and reason_code == "constraint_conflict" and hard_yes_conflict:
        decision = "exclude"
        if not reason_parts.get("current") or not reason_parts.get("target"):
            reason_parts = _exclusion_reason_parts(text_blob, hard_no_match, hard_yes_conflict)
            reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")

    if decision == "exclude":
        if not reason_parts.get("current"):
            reason_parts["current"] = _coerce_text(candidate.get("title") or candidate.get("company") or candidate.get("location") or "listing mismatch")
        if not reason_parts.get("target"):
            if reason_code == "role_irrelevance":
                reason_parts["target"] = _role_target_summary(digitized_user)
            else:
                reason_parts["target"] = _shorten_reason_target(hard_no_match or hard_yes_conflict) or _coerce_text(hard_no_match or hard_yes_conflict or "user constraints")
        if not reason_text:
            reason_text = f"{reason_parts['current']} -> {reason_parts['target']}".strip(" ->")

    scored_row = deepcopy(candidate)
    scored_row.update(
        {
            "decision": decision,
            "score": score,
            "exclude_reason_code": reason_code,
            "exclude_reason_text": reason_text,
            "exclude_reason": reason_parts,
            "exclude_reason_current": reason_parts.get("current", ""),
            "exclude_reason_target": reason_parts.get("target", ""),
        }
    )
    return scored_row


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    excluded = [row for row in rows if row.get("decision") == "exclude"]
    kept = [row for row in rows if row.get("decision") == "keep"]
    reasons: dict[str, int] = {}
    for row in excluded:
        code = _coerce_text(row.get("exclude_reason_code")) or "unknown"
        reasons[code] = reasons.get(code, 0) + 1
    return {
        "total_candidates": len(rows),
        "kept_count": len(kept),
        "excluded_count": len(excluded),
        "reason_histogram": reasons,
    }


def _task_input_summary(task_input: dict[str, Any]) -> dict[str, Any]:
    candidates = _normalize_candidates(task_input)
    digitized_user = _normalize_digitized_user(task_input)
    llm_settings = _normalize_llm_settings(task_input)
    search_context = _normalize_search_context(task_input)
    return {
        "task_name": _coerce_text(task_input.get("task_name") or "candidate_listing_scoring"),
        "task_id": _coerce_text(task_input.get("task_id") or ""),
        "candidate_count": len(candidates),
        "batch_size": int(task_input.get("batch_size") or DEFAULT_BATCH_SIZE),
        "digitized_user_present": bool(digitized_user),
        "llm_backend": _coerce_text(llm_settings.get("backend") or DEFAULT_LLM_BACKEND),
        "search_context": search_context,
    }


def run_candidate_scoring_task(
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
    digitized_user = _normalize_digitized_user(input_payload)
    candidates = _normalize_candidates(input_payload)
    batch_size = max(1, int(input_payload.get("batch_size") or DEFAULT_BATCH_SIZE))
    llm_settings = _normalize_llm_settings(input_payload)
    search_context = _normalize_search_context(input_payload)
    state_writer = TaskStateWriter(path, heartbeat_seconds=heartbeat_seconds, verbose=verbose)

    missing_fields = list(
        (digitized_user.get("completeness", {}) if isinstance(digitized_user.get("completeness"), dict) else {}).get("missing_fields", [])
    )
    if not missing_fields and not digitized_user:
        missing_fields = ["digitized_user"]

    state_writer.initialize(
        {
            "task_id": task_id,
            "task_type": "candidate_listing_scoring",
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
            "missing_fields": missing_fields,
        }
    )
    state_writer.start()

    result: dict[str, Any] = {
        "task_id": task_id,
        "task_type": "candidate_listing_scoring",
        "status": "queued",
        "state_path": str(path),
        "input": _task_input_summary(input_payload),
        "search_context": search_context,
        "digitized_user": digitized_user,
        "summary": {},
        "scored_candidates": [],
        "kept_candidates": [],
        "next_stage_candidates": [],
        "excluded_candidates": [],
        "batches": [],
        "result": {},
        "warnings": [],
        "missing_fields": missing_fields,
    }

    try:
        if not digitized_user:
            warning = "Missing digitized_user handoff payload."
            result["warnings"].append(warning)
            state_writer.set(
                status="partial",
                phase="finalize",
                step="missing_digitized_user",
                message=warning,
                progress=100,
                warnings=result["warnings"],
                missing_fields=result["missing_fields"],
            )
            result["status"] = "partial"
            result["result"] = {
                "digitized_user": {},
                "summary": _summarize_rows([]),
                "scored_candidates": [],
                "kept_candidates": [],
                "next_stage_candidates": [],
                "excluded_candidates": [],
            }
            return deepcopy(result)

        _vlog(verbose, "task: ingest candidate input")
        state_writer.set(
            status="running",
            phase="ingest",
            step="load_input",
            message=f"Loading {len(candidates)} candidate(s).",
            progress=10,
            warnings=[],
            missing_fields=missing_fields,
        )
        time.sleep(max(0.0, step_delay_seconds))

        batches = _split_batches(candidates, batch_size=batch_size)
        scored_candidates: list[dict[str, Any]] = []
        batch_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        if not candidates:
            warnings.append("No candidates provided.")

        def _judge_single_batch(batch_index: int, batch: list[dict[str, Any]]) -> dict[str, Any]:
            llm_text = ""
            llm_warnings: list[str] = []
            llm_error = ""
            try:
                excluded_rows, llm_text, llm_warnings = _judge_batch_with_llm(
                    digitized_user,
                    batch,
                    batch_index=batch_index,
                    batch_count=len(batches),
                    llm_settings=llm_settings,
                    search_context=search_context,
                )
                if not _coerce_text(llm_text):
                    llm_error = "Empty LLM response."
                    llm_warnings = [*llm_warnings, llm_error]
            except Exception as exc:
                llm_error = f"LLM judge failed for batch {batch_index}: {exc}"
                llm_warnings = [llm_error]
                excluded_rows = []
                for candidate in batch:
                    fallback = _score_candidate(candidate, digitized_user)
                    if fallback.get("decision") == "exclude":
                        excluded_rows.append(
                            {
                                "listing_id": _coerce_text(fallback.get("listing_id")),
                                "reason_code": _coerce_text(fallback.get("exclude_reason_code") or "excluded_by_llm") or "excluded_by_llm",
                                "reason": _coerce_text(fallback.get("exclude_reason_text") or "Excluded by fallback heuristic."),
                                "score": "0",
                            }
                        )
            return {
                "batch_index": batch_index,
                "batch": batch,
                "excluded_rows": excluded_rows,
                "llm_text": llm_text,
                "llm_error": llm_error,
                "llm_warnings": llm_warnings,
            }

        state_writer.set(
            phase="split",
            step="batch_candidates",
            message=f"Split into {len(batches)} batch(es).",
            progress=20 if batches else 100,
            warnings=warnings,
            missing_fields=missing_fields,
        )
        time.sleep(max(0.0, step_delay_seconds))

        if len(batches) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(batches))) as executor:
                batch_runs = list(executor.map(lambda pair: _judge_single_batch(pair[0], pair[1]), list(enumerate(batches, start=1))))
        else:
            batch_runs = [_judge_single_batch(batch_index, batch) for batch_index, batch in enumerate(batches, start=1)]

        for batch_run in batch_runs:
            batch_index = batch_run["batch_index"]
            batch = batch_run["batch"]
            _vlog(verbose, f"task: judge batch {batch_index}/{len(batches)} size={len(batch)}")
            state_writer.set(
                phase="judge",
                step=f"judge_batch_{batch_index}",
                message=f"Judging batch {batch_index} of {len(batches)}.",
                progress=min(95, 20 + int((batch_index / max(1, len(batches))) * 70)),
                warnings=warnings,
                missing_fields=missing_fields,
            )
            time.sleep(max(0.0, step_delay_seconds))

            excluded_rows = batch_run["excluded_rows"]
            llm_text = batch_run["llm_text"]
            llm_error = batch_run["llm_error"]
            warnings.extend(batch_run["llm_warnings"])

            _vlog(verbose, f"task: judge batch {batch_index}/{len(batches)} size={len(batch)}")
            excluded_by_id = {}
            llm_excluded_by_id: dict[str, dict[str, str]] = {}
            for row in excluded_rows:
                listing_id = _coerce_text(row.get("listing_id"))
                if not listing_id:
                    continue
                llm_excluded_by_id[listing_id] = {
                    "reason_code": _coerce_text(row.get("reason_code") or row.get("exclude_reason_code") or "constraint_conflict") or "constraint_conflict",
                    "reason": _coerce_text(row.get("reason") or row.get("reason_text") or "Excluded by LLM."),
                }

            for candidate in batch:
                listing_id = _coerce_text(candidate.get("listing_id"))
                if not listing_id:
                    continue
                deterministic_reason = _score_candidate(candidate, digitized_user)
                deterministic_exclude = _coerce_text(deterministic_reason.get("decision")) == "exclude"
                if deterministic_exclude:
                    excluded_by_id[listing_id] = {
                        "reason_code": _coerce_text(deterministic_reason.get("exclude_reason_code") or "constraint_conflict") or "constraint_conflict",
                        "reason": _coerce_text(deterministic_reason.get("exclude_reason_text") or "Excluded by deterministic gate."),
                        "current": _coerce_text(deterministic_reason.get("exclude_reason_current") or candidate.get("title") or candidate.get("company") or candidate.get("location") or "listing mismatch"),
                        "target": _coerce_text(deterministic_reason.get("exclude_reason_target") or (_role_target_summary(digitized_user) if _coerce_text(deterministic_reason.get("exclude_reason_code")) == "role_irrelevance" else "user constraints")),
                    }
                    continue

                llm_exclusion = llm_excluded_by_id.get(listing_id)
                if not llm_exclusion:
                    continue
                reason_text = _coerce_text(llm_exclusion.get("reason") or "Excluded by LLM.")
                if _is_low_confidence_rejection_reason(reason_text) or not _is_supported_exclusion_reason(reason_text, candidate, digitized_user):
                    warnings.append(f"Skipped low-confidence exclusion for listing_id {listing_id}.")
                    continue
                reason_parts = {
                    "current": _coerce_text(candidate.get("title") or candidate.get("company") or candidate.get("location") or "listing mismatch"),
                    "target": _coerce_text(reason_text or "user constraints"),
                }
                excluded_by_id[listing_id] = {
                    "reason_code": _coerce_text(llm_exclusion.get("reason_code") or "constraint_conflict") or "constraint_conflict",
                    "reason": reason_text,
                    "current": reason_parts.get("current", ""),
                    "target": reason_parts.get("target", ""),
                }
            scored_batch: list[dict[str, Any]] = []
            for row_index, candidate in enumerate(batch, start=1):
                listing_id = _coerce_text(candidate.get("listing_id"))
                scored_row = {
                    "title": _coerce_text(candidate.get("title")),
                    "listing_id": listing_id,
                    "company": _coerce_text(candidate.get("company")),
                    "location": _coerce_text(candidate.get("location")),
                    "link": _coerce_text(candidate.get("link")),
                    "listed_on": _coerce_text(candidate.get("listed_on")),
                    "easy_apply": candidate.get("easy_apply"),
                    "promoted": candidate.get("promoted"),
                }
                for key, value in candidate.items():
                    if key not in scored_row:
                        scored_row[key] = deepcopy(value)
                exclusion = excluded_by_id.get(listing_id)
                if exclusion:
                    scored_row.update(
                        {
                            "decision": "exclude",
                            "score": 0,
                            "exclude_reason_code": exclusion["reason_code"],
                            "exclude_reason_text": exclusion["reason"],
                            "exclude_reason": {
                                "current": exclusion.get("current", ""),
                                "target": exclusion.get("target", ""),
                            },
                            "exclude_reason_current": exclusion.get("current", ""),
                            "exclude_reason_target": exclusion.get("target", ""),
                        }
                    )
                else:
                    scored_row.update(
                        {
                            "decision": "keep",
                            "score": 100,
                            "exclude_reason_code": "",
                            "exclude_reason_text": "",
                            "exclude_reason": {"current": "", "target": ""},
                            "exclude_reason_current": "",
                            "exclude_reason_target": "",
                        }
                    )
                scored_row["batch_index"] = batch_index
                scored_row["batch_row_index"] = row_index
                scored_batch.append(scored_row)
                scored_candidates.append(scored_row)

            batch_summary = _summarize_rows(scored_batch)
            batch_results.append(
                {
                    "batch_index": batch_index,
                    "candidate_count": len(batch),
                    "kept_count": batch_summary["kept_count"],
                    "excluded_count": batch_summary["excluded_count"],
                    "reason_histogram": batch_summary["reason_histogram"],
                    "rows": scored_batch,
                    "llm_response": llm_text,
                    "llm_error": llm_error,
                }
            )

        kept_candidates = [row for row in scored_candidates if row.get("decision") == "keep"]
        excluded_candidates = [row for row in scored_candidates if row.get("decision") == "exclude"]
        summary = _summarize_rows(scored_candidates)
        summary.update(
            {
                "batch_count": len(batches),
                "batch_size": batch_size,
                "llm_backend": _coerce_text(llm_settings.get("backend") or DEFAULT_LLM_BACKEND),
            }
        )
        result_payload = {
            "digitized_user": digitized_user,
            "summary": summary,
            "scored_candidates": scored_candidates,
            "kept_candidates": kept_candidates,
            "next_stage_candidates": kept_candidates,
            "excluded_candidates": excluded_candidates,
            "batches": batch_results,
        }

        status = "partial" if missing_fields else "success"
        result.update(
            {
                "status": status,
                "warnings": warnings,
                "missing_fields": missing_fields,
                "summary": summary,
                "scored_candidates": scored_candidates,
                "kept_candidates": kept_candidates,
                "next_stage_candidates": kept_candidates,
                "excluded_candidates": excluded_candidates,
                "batches": batch_results,
                "result": result_payload,
            }
        )

        state_writer.set(
            status=status,
            phase="finalize",
            step="done",
            message="Candidate scoring complete.",
            progress=100,
            warnings=warnings,
            missing_fields=missing_fields,
            result={
                "summary": summary,
                "kept_count": len(kept_candidates),
                "excluded_count": len(excluded_candidates),
            },
        )
        return deepcopy(result)
    except Exception as exc:
        message = str(exc)
        result.update(
            {
                "status": "failed",
                "warnings": [message],
                "summary": {},
                "scored_candidates": [],
                "kept_candidates": [],
                "next_stage_candidates": [],
                "excluded_candidates": [],
                "batches": [],
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
            missing_fields=missing_fields,
        )
        return deepcopy(result)
    finally:
        state_writer.stop()

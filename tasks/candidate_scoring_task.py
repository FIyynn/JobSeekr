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
from worker.webagent_runtime import call_model_chat_with_retry


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
                    return deepcopy(nested)
            return deepcopy(value)
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


def _build_batch_messages(
    digitized_user: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
) -> list[dict[str, str]]:
    system = (
        "You are a candidate-listing exclusion judge.\n"
        "Goal: decide which rows should be excluded from detail fetch using ONLY explicit constraints.\n"
        "Important rules:\n"
        "- Use ONLY the current batch and the digitized_user.\n"
        "- Each batch is independent. Do not use any previous batch context.\n"
        "- Ignore all soft preferences, rankings, and fit judgments.\n"
        "- Only exclude when the candidate clearly conflicts with explicit constraints.\n"
        "- The only source of truth is digitized_user.constraints.\n"
        "- Use hard_no and must_have only.\n"
        "- If you are unsure, keep the row.\n"
        "- If nothing should be excluded, return exactly {\"excluded\": []}.\n"
        "- Return strict JSON only. No markdown, no code fences.\n"
        "Return schema:\n"
        "{\n"
        '  \"excluded\": [\n'
        "    {\n"
        '      \"company\": \"string\",\n'
        '      \"listing_id\": \"string\",\n'
        '      \"reason\": \"short plain-English reason\"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Any listing_id not listed in excluded is kept.\n"
        "Do not return kept rows.\n"
        "Do not return an empty string.\n"
    )
    user = {
        "batch_index": batch_index,
        "batch_count": batch_count,
        "digitized_user": {
            "constraints": digitized_user.get("constraints", {}),
        },
        "candidates": [_candidate_prompt_row(candidate) for candidate in batch],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
    ]


def _strip_code_fences(text: str) -> str:
    raw = _coerce_text(text)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_llm_exclusions(text: str) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    raw = _strip_code_fences(text)
    if not raw:
        return [], ["Empty LLM response."]
    data: Any
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise ValueError("LLM response did not contain JSON.")
        data = json.loads(match.group(0))
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
) -> tuple[list[dict[str, str]], str, list[str]]:
    messages = _build_batch_messages(digitized_user, batch, batch_index=batch_index, batch_count=batch_count)
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


def _location_matches(candidate_location: str, digitized_user: dict[str, Any]) -> bool:
    _ = candidate_location
    _ = digitized_user
    return True


def _has_hard_no(candidate_blob: str, digitized_user: dict[str, Any]) -> str:
    constraints = digitized_user.get("constraints") if isinstance(digitized_user.get("constraints"), dict) else {}
    hard_no = _dedupe_keep_order(_coerce_text_list(constraints.get("hard_no")) + _coerce_text_list(constraints.get("must_have")))
    haystack = candidate_blob.casefold()
    for item in hard_no:
        term = item.casefold().strip()
        if term and term in haystack:
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


def _score_candidate(candidate: dict[str, Any], digitized_user: dict[str, Any]) -> dict[str, Any]:
    text_blob = _candidate_blob(candidate)
    hard_no_match = _has_hard_no(text_blob, digitized_user)
    score = 0
    decision = "keep"
    reason_code = ""
    reason_text = ""
    if hard_no_match:
        decision = "exclude"
        reason_code = "hard_constraint_conflict"
        reason_text = f"Matches a hard-no preference: {hard_no_match}."
        score = 100

    scored_row = deepcopy(candidate)
    scored_row.update(
        {
            "decision": decision,
            "score": score,
            "exclude_reason_code": reason_code,
            "exclude_reason_text": reason_text,
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
    return {
        "task_name": _coerce_text(task_input.get("task_name") or "candidate_listing_scoring"),
        "task_id": _coerce_text(task_input.get("task_id") or ""),
        "candidate_count": len(candidates),
        "batch_size": int(task_input.get("batch_size") or DEFAULT_BATCH_SIZE),
        "digitized_user_present": bool(digitized_user),
        "llm_backend": _coerce_text(llm_settings.get("backend") or DEFAULT_LLM_BACKEND),
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

        state_writer.set(
            phase="split",
            step="batch_candidates",
            message=f"Split into {len(batches)} batch(es).",
            progress=20 if batches else 100,
            warnings=warnings,
            missing_fields=missing_fields,
        )
        time.sleep(max(0.0, step_delay_seconds))

        for batch_index, batch in enumerate(batches, start=1):
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
                )
                warnings.extend(llm_warnings)
                if not _coerce_text(llm_text):
                    llm_error = "Empty LLM response."
                    warnings.append(llm_error)
            except Exception as exc:
                llm_error = f"LLM judge failed for batch {batch_index}: {exc}"
                warnings.append(llm_error)
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

            excluded_by_id = {
                _coerce_text(row.get("listing_id")): {
                    "reason_code": "constraint_conflict",
                    "reason": _coerce_text(row.get("reason") or "Excluded by LLM."),
                }
                for row in excluded_rows
                if _coerce_text(row.get("listing_id"))
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
                        }
                    )
                else:
                    scored_row.update(
                        {
                            "decision": "keep",
                            "score": 100,
                            "exclude_reason_code": "",
                            "exclude_reason_text": "",
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

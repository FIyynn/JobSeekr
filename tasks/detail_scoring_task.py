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
DEFAULT_STATE_PATH = REPO_ROOT / "runtime" / "task_states" / "detail_scoring_task_state.json"
DEFAULT_HEARTBEAT_SECONDS = 2.0
DEFAULT_STEP_DELAY_SECONDS = 0.3
DEFAULT_BATCH_SIZE = 10
DEFAULT_LLM_BACKEND = "local"
DEFAULT_LLM_MAX_COMPLETION_TOKENS = 1800
DEFAULT_LLM_TIMEOUT_SECONDS = 240

SECTION_WEIGHTS = {
    "compensation": 20,
    "progression": 20,
    "work_style": 15,
    "relevance": 20,
    "company_signal": 15,
    "risks": 10,
}

SECTION_ORDER = tuple(SECTION_WEIGHTS.keys())

POSITIVE_RESULT = {"yes": 1.0, "partial": 0.5, "no": 0.0}

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

GROWTH_TERMS = (
    "growth",
    "progression",
    "promotion",
    "mentor",
    "mentorship",
    "training",
    "development",
    "learning",
    "rotation",
    "rotational",
    "graduate program",
)

RISK_TERMS = (
    "talent pool",
    "open application",
    "general application",
    "future opportunities",
    "evergreen",
    "pipeline",
    "commission-only",
    "commission only",
    "unpaid",
    "temporary",
    "contract",
    "vague",
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


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\s+", " ", text)


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
        self._thread = threading.Thread(target=_beat, name="detail-scoring-heartbeat", daemon=True)
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
            nested = value.get("digitized_user")
            if isinstance(nested, dict):
                return deepcopy(nested)
            if key != "profile" or any(k in value for k in ("identity", "contact", "preferences", "constraints")):
                return deepcopy(value)
    result = task_input.get("result")
    if isinstance(result, dict):
        nested = result.get("digitized_user")
        if isinstance(nested, dict):
            return deepcopy(nested)
    return {}


def _extract_detail_payload(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    if isinstance(row.get("detail"), dict):
        return deepcopy(row["detail"])
    if isinstance(row.get("listing"), dict):
        return deepcopy(row.get("detail") or {})
    if isinstance(row.get("ai"), dict):
        return deepcopy(row["ai"])
    return deepcopy(row)


def _extract_listing_payload(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    if isinstance(row.get("listing"), dict):
        return deepcopy(row["listing"])
    listing_keys = ("title", "company", "location", "listing_id", "job_id", "link", "easy_apply", "promoted", "listed_on")
    listing = {key: deepcopy(row.get(key)) for key in listing_keys if key in row}
    return listing


def _extract_detail_rows(task_input: dict[str, Any]) -> list[dict[str, Any]]:
    source = task_input.get("detail_rows")
    if not isinstance(source, list):
        source = task_input.get("rows")
    if not isinstance(source, list):
        source = task_input.get("listings")
    if not isinstance(source, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        listing = _extract_listing_payload(item)
        detail = _extract_detail_payload(item)
        row = deepcopy(item)
        listing_id = _coerce_text(
            row.get("listing_id")
            or row.get("job_id")
            or listing.get("listing_id")
            or listing.get("job_id")
            or detail.get("job_id")
            or detail.get("listing_id")
        )
        if not listing_id:
            listing_id = f"detail-{index + 1}"
        row["listing_id"] = listing_id
        listing.setdefault("listing_id", listing_id)
        listing.setdefault("job_id", listing_id)
        row["listing"] = listing
        row["detail"] = detail
        rows.append(row)
    return rows


def _normalize_llm_settings(task_input: dict[str, Any]) -> dict[str, Any]:
    try:
        app_config = load_app_config(verbose=False)
        llm_config = deepcopy(app_config.get("llm_backend") or {})
    except Exception:
        llm_config = {}
    backend_override = _coerce_text(task_input.get("llm_backend"))
    if backend_override:
        llm_config["backend"] = backend_override
    for key in ("backend", "api_key_path", "openai_model", "openai_base_url", "openai_reasoning_effort", "llama_model", "llama_base_url", "llama_temperature", "max_completion_tokens", "timeout", "retries"):
        if key in task_input and task_input.get(key) is not None:
            llm_config[key] = task_input.get(key)
    llm_config.setdefault("backend", llm_config.get("backend") or DEFAULT_LLM_BACKEND)
    return llm_config


def _split_batches(items: list[dict[str, Any]], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[dict[str, Any]]]:
    size = max(1, int(batch_size or DEFAULT_BATCH_SIZE))
    return [items[index : index + size] for index in range(0, len(items), size)]


def _task_input_summary(task_input: dict[str, Any]) -> dict[str, Any]:
    detail_rows = _extract_detail_rows(task_input)
    digitized_user = _normalize_digitized_user(task_input)
    llm_settings = _normalize_llm_settings(task_input)
    return {
        "task_name": _coerce_text(task_input.get("task_name") or "detail_listing_scoring"),
        "task_id": _coerce_text(task_input.get("task_id") or ""),
        "row_count": len(detail_rows),
        "batch_size": int(task_input.get("batch_size") or DEFAULT_BATCH_SIZE),
        "digitized_user_present": bool(digitized_user),
        "llm_backend": _coerce_text(llm_settings.get("backend") or DEFAULT_LLM_BACKEND),
    }


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
                    normalized.append({"company": "", "listing_id": listing_id, "reason": "Excluded by LLM."})
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


def _detail_row_summary(row: dict[str, Any]) -> dict[str, Any]:
    listing = row.get("listing") if isinstance(row.get("listing"), dict) else {}
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    company_profile = detail.get("company_profile") if isinstance(detail.get("company_profile"), dict) else {}
    job_description = detail.get("job_description") if isinstance(detail.get("job_description"), dict) else {}
    if isinstance(job_description, dict):
        job_description = job_description.get("raw_text", "")
    detail_text = " | ".join(
        part
        for part in (
            _coerce_text(listing.get("title")),
            _coerce_text(listing.get("company")),
            _coerce_text(listing.get("location")),
            _coerce_text(detail.get("posted_at")),
            _coerce_text(detail.get("apply_activity")),
            _coerce_text(detail.get("promotion_status")),
            _coerce_text(detail.get("application_management")),
            _coerce_text(detail.get("response_insights")),
            _coerce_text(job_description),
            _coerce_text(company_profile.get("description")),
            _coerce_text(company_profile.get("industry")),
            _coerce_text(company_profile.get("size")),
            _coerce_text(company_profile.get("linkedin_employee_count")),
        )
        if part
    )
    return {
        "listing_id": _coerce_text(row.get("listing_id") or listing.get("listing_id") or listing.get("job_id") or detail.get("job_id")),
        "title": _coerce_text(listing.get("title") or detail.get("title")),
        "company": _coerce_text(listing.get("company") or detail.get("company") or company_profile.get("name")),
        "location": _coerce_text(listing.get("location") or detail.get("location")),
        "detail_text": detail_text,
        "listing": deepcopy(listing),
        "detail": deepcopy(detail),
    }


def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> str:
    haystack = text.casefold()
    for term in terms:
        needle = _coerce_text(term).casefold()
        if needle and needle in haystack:
            return _coerce_text(term)
    return ""


def _normalize_location_hint(text: str) -> str:
    cleaned = _coerce_text(text)
    cleaned = re.sub(r"\s*\(.*\)\s*$", "", cleaned)
    return cleaned


def _score_result_to_float(result: str) -> float:
    return POSITIVE_RESULT.get(result, 0.0)


def _section_payload(result: str, weight: int, notes: list[str], signals: list[str]) -> dict[str, Any]:
    return {
        "result": result,
        "weight": weight,
        "notes": _dedupe_keep_order([note for note in notes if _coerce_text(note)]),
        "signals": _dedupe_keep_order([signal for signal in signals if _coerce_text(signal)]),
    }


def _compute_sections(row: dict[str, Any], digitized_user: dict[str, Any]) -> tuple[dict[str, Any], float]:
    listing = row.get("listing") if isinstance(row.get("listing"), dict) else {}
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    company_profile = detail.get("company_profile") if isinstance(detail.get("company_profile"), dict) else {}
    job_description = detail.get("job_description") if isinstance(detail.get("job_description"), dict) else {}
    if isinstance(job_description, dict):
        job_description = job_description.get("raw_text", "")
    text_parts = [
        _coerce_text(listing.get("title")),
        _coerce_text(listing.get("company")),
        _coerce_text(listing.get("location")),
        _coerce_text(detail.get("posted_at")),
        _coerce_text(detail.get("apply_activity")),
        _coerce_text(detail.get("promotion_status")),
        _coerce_text(detail.get("application_management")),
        _coerce_text(detail.get("response_insights")),
        _coerce_text(job_description),
        _coerce_text(company_profile.get("description")),
        _coerce_text(company_profile.get("industry")),
        _coerce_text(company_profile.get("size")),
        _coerce_text(company_profile.get("linkedin_employee_count")),
        " ".join(_coerce_text_list(detail.get("listing_preferences"))),
    ]
    text_blob = " \n".join(part for part in text_parts if part)
    blob_lower = text_blob.casefold()

    user_preferences = digitized_user.get("preferences") if isinstance(digitized_user.get("preferences"), dict) else {}
    user_constraints = digitized_user.get("constraints") if isinstance(digitized_user.get("constraints"), dict) else {}
    user_contact = digitized_user.get("contact") if isinstance(digitized_user.get("contact"), dict) else {}

    preferred_roles = _coerce_text_list(user_preferences.get("roles"))
    preferred_industries = _coerce_text_list((user_preferences.get("industries") or {}).get("high_priority"))
    also_industries = _coerce_text_list((user_preferences.get("industries") or {}).get("also_interested"))
    ideal_work_style = _coerce_text_list((user_preferences.get("work_style") or {}).get("ideal"))
    acceptable_work_style = _coerce_text_list((user_preferences.get("work_style") or {}).get("acceptable"))
    ideal_comp = _coerce_text_list((user_preferences.get("compensation") or {}).get("ideal"))
    comfortable_comp = _coerce_text_list((user_preferences.get("compensation") or {}).get("comfortable"))
    lower_if_comp = _coerce_text_list((user_preferences.get("compensation") or {}).get("lower_if"))
    preferred_commute = _coerce_text_list((user_preferences.get("commute") or {}).get("preferred"))
    comfortable_commute = _coerce_text_list((user_preferences.get("commute") or {}).get("comfortable"))
    would_relocate = _coerce_text_list((user_preferences.get("commute") or {}).get("would_relocate"))
    preferred_company_size = _coerce_text_list((user_preferences.get("company_size") or {}).get("preferred"))
    company_also_interested = _coerce_text_list((user_preferences.get("company_size") or {}).get("also_interested"))
    hard_no = _coerce_text_list(user_constraints.get("hard_no"))
    must_have = _coerce_text_list(user_constraints.get("must_have"))

    title = _coerce_text(listing.get("title") or detail.get("title"))
    company = _coerce_text(listing.get("company") or detail.get("company") or company_profile.get("name"))
    location = _normalize_location_hint(_coerce_text(listing.get("location") or detail.get("location")))
    location_lower = location.casefold()
    title_lower = title.casefold()
    company_lower = company.casefold()

    # Compensation
    comp_signals: list[str] = []
    comp_notes: list[str] = []
    if _contains_any(blob_lower, ("salary", "compensation", "pay", "benefits", "bonus", "aed", "$", "hourly")):
        comp_signals.append("Compensation signal found in listing detail.")
    if _contains_any(blob_lower, ("unpaid", "commission only", "commission-only")):
        comp_signals.append("Negative compensation signal found.")
    if ideal_comp or comfortable_comp:
        comp_notes.append("User provided compensation preferences.")
    if comp_signals and not _contains_any(blob_lower, ("unpaid", "commission only", "commission-only")):
        comp_result = "yes"
    elif _contains_any(blob_lower, ("unpaid", "commission only", "commission-only")):
        comp_result = "no"
    elif _contains_any(blob_lower, ("salary", "compensation", "pay", "aed", "$")):
        comp_result = "partial"
    else:
        comp_result = "partial"

    # Progression
    prog_signals: list[str] = []
    prog_notes: list[str] = []
    growth_hits = [term for term in GROWTH_TERMS if term in blob_lower]
    if growth_hits:
        prog_signals.extend(f"Growth term: {term}" for term in growth_hits[:4])
    if _contains_any(blob_lower, ENTRY_TERMS):
        prog_signals.append("Entry-level language found.")
    if preferred_roles:
        prog_notes.append("User has target roles.")
    if growth_hits and _contains_any(blob_lower, ENTRY_TERMS):
        prog_result = "yes"
    elif growth_hits or _contains_any(blob_lower, ENTRY_TERMS):
        prog_result = "partial"
    else:
        prog_result = "no"

    # Work style
    work_signals: list[str] = []
    work_notes: list[str] = []
    work_mode_hit = ""
    for term in ideal_work_style + acceptable_work_style:
        if term.casefold() in blob_lower:
            work_mode_hit = term
            break
    if work_mode_hit:
        work_signals.append(f"Work style mention matched: {work_mode_hit}")
    commute_match = ""
    for term in preferred_commute + comfortable_commute + would_relocate:
        if term.casefold() and term.casefold() in location_lower:
            commute_match = term
            break
    if commute_match:
        work_signals.append(f"Location matched user commute preference: {commute_match}")
    if "remote" in blob_lower or "hybrid" in blob_lower or "on-site" in blob_lower:
        work_signals.append("Work arrangement mentioned in listing detail.")
    if work_mode_hit or commute_match:
        work_result = "yes"
    elif _coerce_text(location):
        work_result = "partial"
    else:
        work_result = "no"

    # Relevance
    relevance_signals: list[str] = []
    relevance_notes: list[str] = []
    role_hit = _contains_any(blob_lower, preferred_roles)
    skill_hits = [skill for skill in _coerce_text_list(digitized_user.get("skills")) if skill.casefold() in blob_lower]
    project_hits = [project.get("name", "") for project in digitized_user.get("projects", []) if isinstance(project, dict) and _coerce_text(project.get("name")).casefold() in blob_lower]
    if role_hit:
        relevance_signals.append(f"Preferred role matched: {role_hit}")
    if skill_hits:
        relevance_signals.extend(f"Skill matched: {skill}" for skill in skill_hits[:4])
    if project_hits:
        relevance_signals.extend(f"Project signal: {name}" for name in project_hits[:3])
    if role_hit and (skill_hits or project_hits):
        relevance_result = "yes"
    elif role_hit or skill_hits or project_hits:
        relevance_result = "partial"
    else:
        relevance_result = "no"

    # Company signal
    company_signals: list[str] = []
    company_notes: list[str] = []
    if company and company.casefold() in " ".join(_coerce_text_list((digitized_user.get("source_coverage") or {}).get("target_companies", []))).casefold():
        company_signals.append("Company matches explicit target company.")
    if _contains_any(company_lower + " " + blob_lower, preferred_industries):
        company_signals.append("High-priority industry matched.")
    if _contains_any(company_lower + " " + blob_lower, also_industries):
        company_signals.append("Also-interested industry matched.")
    if _contains_any(blob_lower, _coerce_text_list((digitized_user.get("source_coverage") or {}).get("documents", []))):
        company_notes.append("Company signal appears in user source coverage.")
    if company_signals:
        company_result = "yes"
    elif company or company_profile:
        company_result = "partial"
    else:
        company_result = "no"

    # Risks
    risk_signals: list[str] = []
    risk_notes: list[str] = []
    risk_hits = [term for term in RISK_TERMS if term in blob_lower]
    if risk_hits:
        risk_signals.extend(f"Risk term: {term}" for term in risk_hits[:5])
    if _contains_any(blob_lower, hard_no + must_have):
        risk_signals.append("User hard constraint mention found.")
    if not _coerce_text(detail.get("job_description", {}).get("raw_text") if isinstance(detail.get("job_description"), dict) else detail.get("job_description")):
        risk_signals.append("Missing or thin job description.")
    if risk_hits or _contains_any(blob_lower, hard_no + must_have):
        risk_result = "no"
    elif _coerce_text(detail.get("job_description", {}).get("raw_text") if isinstance(detail.get("job_description"), dict) else detail.get("job_description")):
        risk_result = "yes"
    else:
        risk_result = "partial"

    sections = {
        "compensation": _section_payload(comp_result, SECTION_WEIGHTS["compensation"], comp_notes, comp_signals),
        "progression": _section_payload(prog_result, SECTION_WEIGHTS["progression"], prog_notes, prog_signals),
        "work_style": _section_payload(work_result, SECTION_WEIGHTS["work_style"], work_notes, work_signals),
        "relevance": _section_payload(relevance_result, SECTION_WEIGHTS["relevance"], relevance_notes, relevance_signals),
        "company_signal": _section_payload(company_result, SECTION_WEIGHTS["company_signal"], company_notes, company_signals),
        "risks": _section_payload(risk_result, SECTION_WEIGHTS["risks"], risk_notes, risk_signals),
    }
    total = 0.0
    for section_name in SECTION_ORDER:
        section = sections[section_name]
        total += section["weight"] * _score_result_to_float(section["result"])
    return sections, round(total, 2)


def _build_batch_messages(
    digitized_user: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
) -> list[dict[str, str]]:
    system = (
        "You judge full job-detail fit for a job seeker.\n"
        "Return JSON only.\n"
        "Output schema:\n"
        '{\n'
        '  "excluded": [\n'
        '    {"company": "string", "listing_id": "string", "reason": "short plain-English reason"}\n'
        "  ]\n"
        "}\n"
        "Exclude only when the full detail clearly conflicts with the user's profile, constraints, or stated preferences.\n"
        "Keep borderline or uncertain rows.\n"
        "Do not return kept rows.\n"
        "Do not return an empty string.\n"
    )
    user = {
        "batch_index": batch_index,
        "batch_count": batch_count,
        "digitized_user": digitized_user,
        "detail_rows": [_detail_row_summary(row) for row in batch],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
    ]


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


def _hard_fallback_exclusions(batch: list[dict[str, Any]], digitized_user: dict[str, Any]) -> list[dict[str, str]]:
    excluded: list[dict[str, str]] = []
    hard_no = _coerce_text_list((digitized_user.get("constraints") if isinstance(digitized_user.get("constraints"), dict) else {}).get("hard_no"))
    for row in batch:
        summary = _detail_row_summary(row)
        blob = " | ".join([summary["title"], summary["company"], summary["location"], summary["detail_text"]]).casefold()
        conflict = _contains_any(blob, tuple(hard_no))
        if conflict:
            excluded.append(
                {
                    "company": summary["company"],
                    "listing_id": summary["listing_id"],
                    "reason": f"Hard constraint conflict: {conflict}.",
                }
            )
            continue
        if _contains_any(blob, RISK_TERMS) and not summary["detail_text"]:
            excluded.append(
                {
                    "company": summary["company"],
                    "listing_id": summary["listing_id"],
                    "reason": "Thin or vague posting.",
                }
            )
    return excluded


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    excluded = [row for row in rows if row.get("decision") == "exclude"]
    kept = [row for row in rows if row.get("decision") == "keep"]
    reasons: dict[str, int] = {}
    section_histogram: dict[str, dict[str, int]] = {name: {"yes": 0, "partial": 0, "no": 0} for name in SECTION_ORDER}
    for row in rows:
        sections = row.get("sections") if isinstance(row.get("sections"), dict) else {}
        for section_name in SECTION_ORDER:
            result = _coerce_text((sections.get(section_name) or {}).get("result") if isinstance(sections.get(section_name), dict) else "")
            if result in {"yes", "partial", "no"}:
                section_histogram[section_name][result] += 1
    for row in excluded:
        code = _coerce_text(row.get("exclude_reason_code")) or "unknown"
        reasons[code] = reasons.get(code, 0) + 1
    return {
        "total_rows": len(rows),
        "kept_count": len(kept),
        "excluded_count": len(excluded),
        "reason_histogram": reasons,
        "section_histogram": section_histogram,
    }


def run_detail_scoring_task(
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
    detail_rows = _extract_detail_rows(input_payload)
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
            "task_type": "detail_listing_scoring",
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
        "task_type": "detail_listing_scoring",
        "status": "queued",
        "state_path": str(path),
        "input": _task_input_summary(input_payload),
        "digitized_user": digitized_user,
        "summary": {},
        "scored_detail_rows": [],
        "kept_detail_rows": [],
        "next_stage_rows": [],
        "excluded_detail_rows": [],
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
                "scored_detail_rows": [],
                "kept_detail_rows": [],
                "next_stage_rows": [],
                "excluded_detail_rows": [],
            }
            return deepcopy(result)

        _vlog(verbose, "task: ingest detail input")
        state_writer.set(
            status="running",
            phase="ingest",
            step="load_input",
            message=f"Loading {len(detail_rows)} detail row(s).",
            progress=10,
            warnings=[],
            missing_fields=missing_fields,
        )
        time.sleep(max(0.0, step_delay_seconds))

        batches = _split_batches(detail_rows, batch_size=batch_size)
        scored_rows: list[dict[str, Any]] = []
        batch_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        if not detail_rows:
            warnings.append("No detail rows provided.")

        state_writer.set(
            phase="split",
            step="batch_detail_rows",
            message=f"Split into {len(batches)} batch(es).",
            progress=20 if batches else 100,
            warnings=warnings,
            missing_fields=missing_fields,
        )
        time.sleep(max(0.0, step_delay_seconds))

        for batch_index, batch in enumerate(batches, start=1):
            _vlog(verbose, f"task: judge detail batch {batch_index}/{len(batches)} size={len(batch)}")
            state_writer.set(
                phase="judge",
                step=f"judge_detail_batch_{batch_index}",
                message=f"Judging detail batch {batch_index} of {len(batches)}.",
                progress=min(95, 20 + int((batch_index / max(1, len(batches))) * 70)),
                warnings=warnings,
                missing_fields=missing_fields,
            )
            time.sleep(max(0.0, step_delay_seconds))

            llm_text = ""
            llm_warnings: list[str] = []
            llm_error = ""
            excluded_rows: list[dict[str, str]] = []
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
                excluded_rows = _hard_fallback_exclusions(batch, digitized_user)

            excluded_by_id = {
                _coerce_text(row.get("listing_id")): {
                    "reason_code": "detail_conflict",
                    "reason": _coerce_text(row.get("reason") or "Excluded by LLM."),
                }
                for row in excluded_rows
                if _coerce_text(row.get("listing_id"))
            }
            for row in batch:
                summary = _detail_row_summary(row)
                sections, section_score = _compute_sections(row, digitized_user)
                exclusion = excluded_by_id.get(summary["listing_id"])
                scored_row = {
                    "listing_id": summary["listing_id"],
                    "company": summary["company"],
                    "title": summary["title"],
                    "location": summary["location"],
                    "decision": "exclude" if exclusion else "keep",
                    "score": 0 if exclusion else 100,
                    "exclude_reason_code": exclusion["reason_code"] if exclusion else "",
                    "exclude_reason_text": exclusion["reason"] if exclusion else "",
                    "fit_score": section_score,
                    "sections": sections,
                    "batch_index": batch_index,
                }
                if isinstance(row.get("listing"), dict):
                    scored_row["listing"] = deepcopy(row["listing"])
                if isinstance(row.get("detail"), dict):
                    scored_row["detail"] = deepcopy(row["detail"])
                for key, value in row.items():
                    if key not in scored_row:
                        scored_row[key] = deepcopy(value)
                scored_rows.append(scored_row)

            batch_summary = _summarize_rows(scored_rows[-len(batch) :])
            batch_results.append(
                {
                    "batch_index": batch_index,
                    "detail_count": len(batch),
                    "kept_count": batch_summary["kept_count"],
                    "excluded_count": batch_summary["excluded_count"],
                    "reason_histogram": batch_summary["reason_histogram"],
                    "section_histogram": batch_summary["section_histogram"],
                    "rows": deepcopy(scored_rows[-len(batch) :]),
                    "llm_response": llm_text,
                    "llm_error": llm_error,
                }
            )

        kept_rows = [row for row in scored_rows if row.get("decision") == "keep"]
        excluded_rows = [row for row in scored_rows if row.get("decision") == "exclude"]
        summary = _summarize_rows(scored_rows)
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
            "scored_detail_rows": scored_rows,
            "kept_detail_rows": kept_rows,
            "next_stage_rows": kept_rows,
            "excluded_detail_rows": excluded_rows,
            "batches": batch_results,
        }

        status = "partial" if missing_fields else "success"
        result.update(
            {
                "status": status,
                "warnings": warnings,
                "missing_fields": missing_fields,
                "summary": summary,
                "scored_detail_rows": scored_rows,
                "kept_detail_rows": kept_rows,
                "next_stage_rows": kept_rows,
                "excluded_detail_rows": excluded_rows,
                "batches": batch_results,
                "result": result_payload,
            }
        )

        state_writer.set(
            status=status,
            phase="finalize",
            step="done",
            message="Detail scoring complete.",
            progress=100,
            warnings=warnings,
            missing_fields=missing_fields,
            result={
                "summary": summary,
                "kept_count": len(kept_rows),
                "excluded_count": len(excluded_rows),
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
                "scored_detail_rows": [],
                "kept_detail_rows": [],
                "next_stage_rows": [],
                "excluded_detail_rows": [],
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


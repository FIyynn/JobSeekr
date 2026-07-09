from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from browser import linkedin as browser_linkedin
from shared.agent_prompts import resolve_agent_prompts_dir
from tests.llm_test.llm_runtime import call_model_chat_with_retry, pretty_json


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
DEFAULT_CONTEXT_TOKENS = 20000
DEFAULT_RESPONSE_RESERVE_TOKENS = 2048


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_planner_bundle(base_dir: Path | None = None) -> dict[str, str]:
    root = resolve_agent_prompts_dir(base_dir)
    return {
        "plan": read_text(root / "linkedin_plan.md"),
        "loop": read_text(root / "linkedin_loop.md"),
        "reflection": read_text(root / "linkedin_reflection.md"),
        "finish": read_text(root / "linkedin_finish.md"),
        "tool": read_text(root / "linkedin_tool_instructions.md"),
        "examples": read_text(root / "linkedin_task_examples.md"),
    }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


def _strip_code_fences(text: str) -> str:
    raw = _clean(text)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _sanitize_json_text(text: str) -> str:
    raw = _strip_code_fences(text)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    return raw.strip()


def _extract_balanced_json_object(text: str) -> str | None:
    raw = _sanitize_json_text(text)
    start = raw.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = _sanitize_json_text(text)
    if not raw:
        raise ValueError("Empty query planner response.")
    try:
        data = json.loads(raw)
    except Exception:
        balanced = _extract_balanced_json_object(raw)
        if balanced is None:
            raise
        data = json.loads(_sanitize_json_text(balanced))
    if not isinstance(data, dict):
        raise ValueError("Query planner response must be a JSON object.")
    return data


def _flatten_queries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    clusters = plan.get("role_queries")
    if not isinstance(clusters, list):
        clusters = plan.get("query_clusters")
    if not isinstance(clusters, list):
        clusters = []
    flattened: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters, start=1):
        if not isinstance(cluster, dict):
            continue
        cluster_name = _clean(
            cluster.get("role")
            or cluster.get("name")
            or cluster.get("cluster")
            or f"role-{cluster_index}"
        )
        queries = cluster.get("queries")
        if isinstance(queries, str):
            queries = [queries]
        if not isinstance(queries, list):
            queries = []
        for query_index, query in enumerate(queries, start=1):
            query_text = _clean(query)
            if not query_text:
                continue
            flattened.append(
                {
                    "cluster_name": cluster_name,
                    "query": query_text,
                    "query_index": query_index,
                    "cluster_index": cluster_index,
                }
            )
    return flattened


def _page_spec(max_page_count: int) -> str:
    page_count = max(1, int(max_page_count or 1))
    return "1" if page_count == 1 else f"1-{page_count}"


def _query_plan_signature(payload: dict[str, Any]) -> str:
    raw = json.dumps(_canonicalize(payload), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _artifact_path(root: Path, signature: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{signature}.json"


def _text_blob(value: Any) -> str:
    if isinstance(value, dict):
        parts = [_text_blob(item) for item in value.values()]
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        parts = [_text_blob(item) for item in value]
        return " ".join(part for part in parts if part)
    return _clean(value)


def _fallback_query_plan(task: str, digitized_user: dict[str, Any]) -> dict[str, Any]:
    roles = _text_blob(digitized_user.get("roles") or digitized_user.get("preferences", {}).get("roles") or "")
    skills = _text_blob(digitized_user.get("skills") or "")
    location_value = (
        digitized_user.get("contact", {}).get("location")
        or digitized_user.get("preferences", {}).get("locations", {}).get("ideal", "")
    )
    if isinstance(location_value, list):
        location = _clean(location_value[0] if location_value else "")
    else:
        location = _clean(location_value)
    if not location:
        location = "United Arab Emirates"

    role_text = " ".join(
        part
        for part in (
            _text_blob(task),
            _text_blob(digitized_user.get("summary", "")),
            _text_blob(digitized_user.get("experience", "")),
            _text_blob(digitized_user.get("education", "")),
            _text_blob(digitized_user.get("projects", "")),
            roles,
            skills,
        )
        if part
    ).casefold()
    clusters: list[dict[str, Any]] = []
    if any(term in role_text for term in ("data engineer", "data engineering", "etl", "spark", "kafka", "airflow", "dbt", "warehouse")):
        clusters.append(
            {
                "role": "data engineer",
                "queries": ["data engineer", "etl engineer", "analytics engineer"],
                "notes": "Role-driven data engineering coverage",
            }
        )
    if any(term in role_text for term in ("data analyst", "analytics", "bi", "business intelligence", "power bi", "tableau", "reporting")):
        clusters.append(
            {
                "role": "data analyst",
                "queries": ["data analyst", "business intelligence analyst", "reporting analyst"],
                "notes": "Role-driven analytics coverage",
            }
        )
    if any(term in role_text for term in ("ml", "machine learning", "ai", "data scientist")):
        clusters.append(
            {
                "role": "machine learning engineer",
                "queries": ["machine learning engineer", "ai engineer", "data scientist"],
                "notes": "Role-driven AI / ML coverage",
            }
        )
    if any(term in role_text for term in ("graduate", "trainee", "fresher", "junior", "entry")):
        clusters.append(
            {
                "role": "graduate trainee",
                "queries": ["graduate trainee", "entry level data analyst", "junior data engineer"],
                "notes": "Graduate-friendly technical roles",
            }
        )
    if any(term in role_text for term in ("cloud", "azure", "aws", "gcp", "platform")):
        clusters.append(
            {
                "role": "cloud data engineer",
                "queries": ["cloud analyst", "data platform engineer", "cloud data engineer"],
                "notes": "Role-driven cloud/platform coverage",
            }
        )
    if not clusters:
        clusters = [
            {
                "role": "data analyst",
                "queries": ["data analyst", "data engineer", "business intelligence analyst"],
                "notes": "General high-yield technical search",
            }
        ]

    keyword = ", ".join(cluster.get("role", "") for cluster in clusters if cluster.get("role")) or _clean(task)

    return {
        "task_name": "linkedin_query_planner",
        "search_context": {
            "keyword": keyword,
            "location": location,
            "filter_by": "Jobs",
            "filters": {
                "experience_level": "Entry level",
            },
        },
        "role_queries": clusters,
        "notes": ["fallback planner used because LLM output could not be parsed"],
    }


def build_query_plan(
    task: str,
    digitized_user: dict[str, Any],
    *,
    bundle: dict[str, str],
    backend: str | None = None,
    api_key_path: str | Path | None,
    openai_model: str | None = None,
    openai_base_url: str | None = None,
    openai_reasoning_effort: str | None = None,
    llama_model: str | None = None,
    llama_base_url: str | None = None,
    llama_temperature: float = 0.2,
    max_completion_tokens: int = 800,
    timeout: int = 180,
    llm_config: dict[str, Any] | None = None,
    response_reserve_tokens: int = DEFAULT_RESPONSE_RESERVE_TOKENS,
) -> dict[str, Any]:
    llm_config = llm_config if isinstance(llm_config, dict) else {}
    backend = _clean(llm_config.get("backend") or backend) or "local"
    api_key_path = llm_config.get("api_key_path", api_key_path)
    openai_model = _clean(llm_config.get("openai_model") or openai_model) or "gpt-5.4-mini"
    openai_base_url = _clean(llm_config.get("openai_base_url") or openai_base_url) or "https://api.openai.com/v1/chat/completions"
    openai_reasoning_effort = _clean(llm_config.get("openai_reasoning_effort") or openai_reasoning_effort) or "low"
    llama_model = _clean(llm_config.get("llama_model") or llama_model) or "qwen3.5-9b"
    llama_base_url = _clean(llm_config.get("llama_base_url") or llama_base_url) or "http://127.0.0.1:8080/v1/chat/completions"
    llama_temperature = float(llm_config.get("llama_temperature", llama_temperature) or llama_temperature)
    max_completion_tokens = int(llm_config.get("max_completion_tokens", max_completion_tokens) or max_completion_tokens)
    timeout = int(llm_config.get("timeout", timeout) or timeout)
    messages = [
        {"role": "system", "content": bundle["plan"]},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": _clean(task),
                    "digitized_user": _canonicalize(digitized_user),
                    "title_hints": [
                        "role_function",
                        "seniority",
                        "specialization_tool_keywords",
                        "program_nationality_signals",
                        "location_work_mode",
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    llm_text = call_model_chat_with_retry(
        backend,
            messages=messages,
            api_key_path=api_key_path,
            openai_model=openai_model,
            openai_base_url=openai_base_url,
            openai_reasoning_effort=openai_reasoning_effort,
            llama_model=llama_model,
            llama_base_url=llama_base_url,
            llama_temperature=llama_temperature,
            max_completion_tokens=max_completion_tokens,
            timeout=timeout,
        )
    try:
        plan = parse_query_plan(llm_text)
        if not plan.get("role_queries"):
            return _fallback_query_plan(task, digitized_user)
        return plan
    except Exception:
        return _fallback_query_plan(task, digitized_user)


def parse_query_plan(text: str) -> dict[str, Any]:
    data = _extract_json_object(text)
    clusters = data.get("role_queries") or data.get("query_clusters") or data.get("clusters") or []
    if isinstance(clusters, dict):
        clusters = [clusters]
    if not isinstance(clusters, list):
        clusters = []
    normalized_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        queries = cluster.get("queries") or cluster.get("query") or []
        if isinstance(queries, str):
            queries = [queries]
        if not isinstance(queries, list):
            queries = []
        normalized_queries = []
        for query in queries:
            query_text = _clean(query)
            if query_text:
                normalized_queries.append(query_text)
        if not normalized_queries:
            continue
        normalized_clusters.append(
            {
                "role": _clean(cluster.get("role") or cluster.get("name") or cluster.get("cluster") or "role"),
                "queries": normalized_queries,
                "notes": _clean(cluster.get("notes") or cluster.get("why") or ""),
            }
        )

    search_context = data.get("search_context") if isinstance(data.get("search_context"), dict) else {}
    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    normalized = {
        "task_name": _clean(data.get("task_name") or "linkedin_query_planner"),
        "search_context": {
            "keyword": _clean(search_context.get("keyword") or search_context.get("query") or ""),
            "location": _clean(search_context.get("location") or ""),
            "filter_by": _clean(search_context.get("filter_by") or "Jobs"),
            "filters": _canonicalize(search_context.get("filters") or {}),
        },
        "role_queries": normalized_clusters,
        "notes": data.get("notes", []),
    }
    return normalized


def load_or_fetch_query_plan(
    *,
    task: str,
    digitized_user: dict[str, Any],
    artifact_root: Path,
    bundle: dict[str, str],
    backend: str | None = None,
    api_key_path: str | Path | None = None,
    openai_model: str | None = None,
    openai_base_url: str | None = None,
    openai_reasoning_effort: str | None = None,
    llama_model: str | None = None,
    llama_base_url: str | None = None,
    llama_temperature: float = 0.2,
    max_completion_tokens: int = 800,
    timeout: int = 180,
    llm_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, Path]:
    llm_config = llm_config if isinstance(llm_config, dict) else {}
    backend = _clean(llm_config.get("backend") or backend) or "local"
    openai_model = _clean(llm_config.get("openai_model") or openai_model) or "gpt-5.4-mini"
    openai_base_url = _clean(llm_config.get("openai_base_url") or openai_base_url) or "https://api.openai.com/v1/chat/completions"
    openai_reasoning_effort = _clean(llm_config.get("openai_reasoning_effort") or openai_reasoning_effort) or "low"
    llama_model = _clean(llm_config.get("llama_model") or llama_model) or "qwen3.5-9b"
    llama_base_url = _clean(llm_config.get("llama_base_url") or llama_base_url) or "http://127.0.0.1:8080/v1/chat/completions"
    payload = {
        "task": _clean(task),
        "digitized_user": _canonicalize(digitized_user),
        "backend": backend,
        "openai_model": openai_model,
        "openai_base_url": openai_base_url,
        "openai_reasoning_effort": openai_reasoning_effort,
        "llama_model": llama_model,
        "llama_base_url": llama_base_url,
        "llama_temperature": float(llm_config.get("llama_temperature", llama_temperature) or llama_temperature),
        "max_completion_tokens": int(llm_config.get("max_completion_tokens", max_completion_tokens) or max_completion_tokens),
        "timeout": int(llm_config.get("timeout", timeout) or timeout),
    }
    signature = _query_plan_signature(payload)
    path = _artifact_path(artifact_root, signature)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        cached_result = cached.get("query_plan_result", {})
        if isinstance(cached_result, dict) and cached_result.get("role_queries"):
            return cached_result, True, path
    query_plan = build_query_plan(
        task,
        digitized_user,
        bundle=bundle,
        backend=backend,
        api_key_path=api_key_path,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        openai_reasoning_effort=openai_reasoning_effort,
        llama_model=llama_model,
        llama_base_url=llama_base_url,
        llama_temperature=llama_temperature,
        max_completion_tokens=max_completion_tokens,
        timeout=timeout,
        llm_config=llm_config,
    )
    result = {
        "task": _clean(task),
        "query_plan": query_plan,
        "role_queries": query_plan.get("role_queries", query_plan.get("query_clusters", [])),
        "search_context": query_plan.get("search_context", {}),
        "notes": query_plan.get("notes", []),
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "query_plan_input": payload,
                "query_plan_result": result,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    return result, False, path


def run_query_plan_search(
    *,
    driver,
    query_plan_result: dict[str, Any],
    artifact_root: Path,
    linkedin_module=browser_linkedin,
    search_config: dict[str, Any] | None = None,
    delays: dict[str, Any] | None = None,
    verbose: bool = False,
    max_query_count: int = 4,
    max_page_count: int = 3,
    max_listing_count: int = 60,
) -> tuple[dict[str, Any], bool, Path]:
    search_config = search_config if isinstance(search_config, dict) else {}
    search_context = query_plan_result.get("search_context") if isinstance(query_plan_result.get("search_context"), dict) else {}
    query_clusters = query_plan_result.get("role_queries") if isinstance(query_plan_result.get("role_queries"), list) else query_plan_result.get("query_clusters") if isinstance(query_plan_result.get("query_clusters"), list) else []
    if isinstance(search_config.get("max_query_count"), int):
        max_query_count = int(search_config.get("max_query_count") or max_query_count)
    if isinstance(search_config.get("max_page_count"), int):
        max_page_count = int(search_config.get("max_page_count") or max_page_count)
    if isinstance(search_config.get("max_listing_count"), int):
        max_listing_count = int(search_config.get("max_listing_count") or max_listing_count)
    search_delays = search_config.get("delays") if isinstance(search_config.get("delays"), dict) else {}
    if isinstance(search_config.get("verbose"), bool):
        verbose = bool(search_config.get("verbose"))
    merged_delays = dict(delays or {})
    merged_delays.update(search_delays)
    payload = {
        "search_context": _canonicalize(search_context),
        "query_clusters": _canonicalize(query_clusters),
        "max_query_count": int(max_query_count or 1),
        "max_page_count": int(max_page_count or 1),
        "max_listing_count": int(max_listing_count or 1),
    }
    signature = _query_plan_signature(payload)
    path = _artifact_path(artifact_root, signature)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        return cached.get("search_result", {}), True, path

    flattened = _flatten_queries(query_plan_result)
    query_items = flattened[: max(1, int(max_query_count or 1))]
    location = _clean(search_context.get("location") or "")
    filter_by = _clean(search_context.get("filter_by") or "Jobs") or "Jobs"
    filters = search_context.get("filters") if isinstance(search_context.get("filters"), list) else []
    page_spec = _page_spec(max_page_count)
    combined: list[dict[str, Any]] = []
    by_listing_id: dict[str, dict[str, Any]] = {}
    query_runs: list[dict[str, Any]] = []
    warnings: list[str] = []
    search_task: dict[str, Any] = {}

    for index, query_item in enumerate(query_items, start=1):
        query = query_item["query"]
        result = linkedin_module.fetch_job_listings(
            driver,
            keyword=query,
            location=location or "United States",
            filters=filters,
            filter_by=filter_by,
            pages=page_spec,
            delays=merged_delays,
            verbose=verbose,
        )
        search_task = result.get("search_task", search_task) or search_task
        warnings.extend(result.get("warnings", []))
        listings = result.get("listings", [])
        query_runs.append(
            {
                "query_index": index,
                "cluster_name": query_item.get("cluster_name", ""),
                "query": query,
                "listing_count": len(listings),
                "pages_requested": result.get("pages_requested", []),
                "pages_fetched": result.get("search_task", {}).get("pages_fetched", []),
                "search_task_id": result.get("search_task", {}).get("id", ""),
            }
        )
        for row in listings:
            if not isinstance(row, dict):
                continue
            listing_id = _clean(row.get("listing_id") or row.get("job_id"))
            if not listing_id:
                continue
            merged = deepcopy(row)
            provenance = merged.setdefault("search_provenance", [])
            if not isinstance(provenance, list):
                provenance = []
                merged["search_provenance"] = provenance
            provenance.append(
                {
                    "query": query,
                    "cluster_name": query_item.get("cluster_name", ""),
                    "query_index": index,
                    "page_spec": page_spec,
                }
            )
            existing = by_listing_id.get(listing_id)
            if existing is None:
                by_listing_id[listing_id] = merged
                combined.append(merged)
            else:
                existing_provenance = existing.setdefault("search_provenance", [])
                if isinstance(existing_provenance, list):
                    existing_provenance.extend(provenance)
        if len(combined) >= max_listing_count:
            break

    combined = combined[: max_listing_count]
    result = {
        "status": "success",
        "query_plan": query_plan_result,
        "search_context": search_context,
        "query_count": len(query_items),
        "max_query_count": int(max_query_count or 1),
        "max_page_count": int(max_page_count or 1),
        "max_listing_count": int(max_listing_count or 1),
        "query_runs": query_runs,
        "listings": combined,
        "search_task": search_task,
        "warnings": warnings,
        "artifact": {
            "path": str(path),
            "reused": False,
        },
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "search_plan_input": payload,
                "search_result": result,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    return result, False, path


def compact_query_plan(query_plan_result: dict[str, Any]) -> str:
    plan = query_plan_result.get("query_plan", query_plan_result)
    payload = {
        "search_context": plan.get("search_context", {}),
        "clusters": [
            {
                "role": cluster.get("role"),
                "queries": cluster.get("queries", []),
            }
            for cluster in plan.get("role_queries", plan.get("query_clusters", []))
            if isinstance(cluster, dict)
        ],
    }
    return pretty_json(payload)

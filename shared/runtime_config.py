from __future__ import annotations

from pathlib import Path
from typing import Any


def build_pipeline_runtime_config(app_config: dict[str, Any]) -> dict[str, Any]:
    llm_backend_config = app_config["llm_backend"]
    llm_backend_name = llm_backend_config.get("backend", "local")
    llm_backend_settings = llm_backend_config.get(llm_backend_name, llm_backend_config.get("local", {}))
    llm_api_key_path = Path(llm_backend_settings.get("api_key_path", "")) if llm_backend_settings.get("api_key_path") else None
    llm_temperature = float(llm_backend_settings.get("temperature", 0.2) or 0.2)
    search_agent = app_config["agent_search_query"]
    onboarding_agent = app_config["agent_onboarding"]
    candidate_agent = app_config["agent_candidate_scoring"]
    detail_fetch_agent = app_config["task_fetch_listing_details"]
    detail_score_agent = app_config["agent_score_detailed_listings"]
    return {
        "onboarding": {
            "heartbeat_seconds": onboarding_agent.get("heartbeat_seconds", 1.0),
            "step_delay_seconds": onboarding_agent.get("step_delay_seconds", 0.05),
            "verbose": False,
        },
        "query_generation": {
            "llm": {
                "backend": llm_backend_name,
                "api_key_path": str(llm_api_key_path) if llm_api_key_path else None,
                "openai_model": llm_backend_settings.get("model", "gpt-5.4-mini"),
                "openai_base_url": llm_backend_settings.get("base_url", "https://api.openai.com/v1/chat/completions"),
                "openai_reasoning_effort": llm_backend_settings.get("reasoning_effort", "low"),
                "llama_model": llm_backend_settings.get("model", "qwen3.5-9b"),
                "llama_base_url": llm_backend_settings.get("base_url", "http://127.0.0.1:8080/v1/chat/completions"),
                "llama_temperature": llm_temperature,
                "max_completion_tokens": 384,
                "timeout": 180,
            },
            "verbose": False,
        },
        "input_limits": {
            "query_context_chars": 4000,
            "max_query_notes_chars": 400,
            "max_preview_rows": 20,
        },
        "dev": {
            "show_raw_llm": False,
            "show_artifacts": True,
            "show_compact_outputs": True,
        },
        "linkedin_search": {
            "max_query_count": search_agent.get("max_query_count", 4),
            "max_page_count": search_agent.get("max_page_count", 3),
            "max_listing_count": search_agent.get("max_listing_count", 60),
            "verbose": search_agent.get("verbose", False),
            "delays": search_agent.get("delays", {}),
        },
        "candidate_scoring": {
            "batch_size": candidate_agent.get("batch_size", 15),
            "verbose": candidate_agent.get("verbose", False),
            "heartbeat_seconds": 1.0,
            "step_delay_seconds": 0.2,
        },
        "detail_fetch": {
            "verbose": detail_fetch_agent.get("verbose", False),
            "delays": detail_fetch_agent.get("delays", {}),
        },
        "detail_scoring": {
            "batch_size": detail_score_agent.get("batch_size", 10),
            "verbose": detail_score_agent.get("verbose", False),
            "heartbeat_seconds": 1.0,
            "step_delay_seconds": 0.2,
        },
    }


def build_candidate_pipeline_context(root: Path, app_config: dict[str, Any]) -> dict[str, Any]:
    llm_backend_config = app_config["llm_backend"]
    llm_backend_name = llm_backend_config.get("backend", "local")
    llm_backend_settings = llm_backend_config.get(llm_backend_name, llm_backend_config.get("local", {}))
    llm_api_key_path = Path(llm_backend_settings.get("api_key_path", "")) if llm_backend_settings.get("api_key_path") else None
    llm_model = llm_backend_settings.get("model", "")
    llm_base_url = llm_backend_settings.get("base_url", "")
    llm_reasoning_effort = llm_backend_settings.get("reasoning_effort", "low")
    llm_temperature = float(llm_backend_settings.get("temperature", 0.2) or 0.2)
    task_config = build_pipeline_runtime_config(app_config)
    paths = app_config["paths"]
    artifact_root = root / paths["notebook_artifact_root"] / "candidate_pipeline"
    candidate_artifact_dir = artifact_root / "candidates"
    detail_artifact_dir = artifact_root / "details"
    query_plan_artifact_dir = artifact_root / "query_plans"
    query_search_artifact_dir = artifact_root / "query_searches"
    return {
        "browser_cfg": dict(app_config["chrome"]),
        "llm_backend_name": llm_backend_name,
        "llm_backend_settings": llm_backend_settings,
        "llm_api_key_path": llm_api_key_path,
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
        "llm_reasoning_effort": llm_reasoning_effort,
        "llm_temperature": llm_temperature,
        "task_config": task_config,
        "onboarding_state_path": root / paths["task_state_root"] / "onboarding_task_state.json",
        "candidate_scoring_state_path": root / paths["task_state_root"] / "candidate_scoring_task_state.json",
        "detail_scoring_state_path": root / paths["task_state_root"] / "detail_scoring_task_state.json",
        "artifact_root": artifact_root,
        "candidate_artifact_dir": candidate_artifact_dir,
        "detail_artifact_dir": detail_artifact_dir,
        "query_plan_artifact_dir": query_plan_artifact_dir,
        "query_search_artifact_dir": query_search_artifact_dir,
    }

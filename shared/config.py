from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIRS = (
    ROOT / "10 - config",
    ROOT / "config",
)
APP_CONFIG_FILE = "app_config.json"


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def load_json(path: Path, verbose: bool = True) -> dict[str, Any]:
    _vlog(verbose, f"config: load {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def merge_dicts(base: dict[str, Any], override: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value, verbose=verbose)
        else:
            result[key] = deepcopy(value)
    return result


def _build_compat_profile(app_config: dict[str, Any]) -> dict[str, Any]:
    chrome = deepcopy(app_config.get("chrome") or {})
    paths = app_config.get("paths") or {}
    profile = {
        "browser": chrome,
        "mongo_file": paths.get("mongo_file", "runtime/mongo_store.json"),
        "data_dir": paths.get("runtime_root", "runtime"),
    }
    if "profile_dir" in chrome and "user_data_dir" not in profile["browser"]:
        profile["browser"]["user_data_dir"] = chrome["profile_dir"]
    return profile


def _build_compat_llm_backend(app_config: dict[str, Any]) -> dict[str, Any]:
    llm = deepcopy(app_config.get("llm") or {})
    provider = str(llm.get("provider") or "local")
    model = llm.get("model", "")
    base_url = llm.get("base_url", "")
    api_key_path = llm.get("api_key_path", "")
    temperature = llm.get("temperature", 0.2)
    max_output_tokens = llm.get("max_output_tokens", 2048)
    timeout_seconds = llm.get("timeout_seconds", 120)
    context_window = llm.get("context_window", 20000)
    batch_size = llm.get("batch_size", 1)
    local_payload = {
        "model": model,
        "base_url": base_url,
        "api_key_path": api_key_path,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "context_window": context_window,
        "timeout_seconds": timeout_seconds,
        "batch_size": batch_size,
    }
    openai_payload = {
        "model": model,
        "base_url": base_url,
        "api_key_path": api_key_path,
        "reasoning_effort": "low",
        "max_output_tokens": max_output_tokens,
        "context_window": context_window,
        "timeout_seconds": timeout_seconds,
        "batch_size": batch_size,
    }
    openrouter_payload = {
        "model": model,
        "base_url": base_url,
        "api_key_path": api_key_path,
        "max_output_tokens": max_output_tokens,
        "context_window": context_window,
        "timeout_seconds": timeout_seconds,
        "batch_size": batch_size,
    }
    return {
        "backend": provider,
        "local": local_payload,
        "openai": openai_payload,
        "openrouter": openrouter_payload,
    }


def load_app_config(config_dir: str | Path | None = None, verbose: bool = True) -> dict[str, Any]:
    if config_dir:
        base_dir = Path(config_dir)
    else:
        base_dir = next((candidate for candidate in CONFIG_DIRS if candidate.exists()), CONFIG_DIRS[0])
    _vlog(verbose, f"config: dir {base_dir}")
    app_config_path = base_dir / APP_CONFIG_FILE
    if app_config_path.exists():
        app_config = load_json(app_config_path, verbose=verbose)
        legacy_extract = load_json(base_dir / "extract.json", verbose=verbose) if (base_dir / "extract.json").exists() else {}
        legacy_presets = load_json(base_dir / "presets.json", verbose=verbose) if (base_dir / "presets.json").exists() else {}
        result = deepcopy(app_config)
        result.setdefault("profile", _build_compat_profile(result))
        result.setdefault("llm_backend", _build_compat_llm_backend(result))
        result.setdefault("extract", legacy_extract)
        result.setdefault("presets", legacy_presets)
        return result
    return {
        "profile": load_json(base_dir / "profile.json", verbose=verbose),
        "extract": load_json(base_dir / "extract.json", verbose=verbose),
        "presets": load_json(base_dir / "presets.json", verbose=verbose),
        "llm_backend": load_json(base_dir / "llm_backend.json", verbose=verbose) if (base_dir / "llm_backend.json").exists() else {},
    }


def resolve_extract_request(
    app_config: dict[str, Any],
    preset_name: str | None = None,
    overrides: dict[str, Any] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    request = deepcopy(app_config["extract"])
    if preset_name:
        _vlog(verbose, f"config: preset {preset_name}")
        preset = app_config["presets"].get(preset_name)
        if preset is None:
            raise KeyError(f"Unknown preset: {preset_name}")
        request = merge_dicts(request, preset, verbose=verbose)
    if overrides:
        _vlog(verbose, "config: overrides")
        request = merge_dicts(request, overrides, verbose=verbose)
    return request

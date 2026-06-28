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


def load_app_config(config_dir: str | Path | None = None, verbose: bool = True) -> dict[str, Any]:
    if config_dir:
        base_dir = Path(config_dir)
    else:
        base_dir = next((candidate for candidate in CONFIG_DIRS if candidate.exists()), CONFIG_DIRS[0])
    _vlog(verbose, f"config: dir {base_dir}")
    return {
        "profile": load_json(base_dir / "profile.json", verbose=verbose),
        "extract": load_json(base_dir / "extract.json", verbose=verbose),
        "presets": load_json(base_dir / "presets.json", verbose=verbose),
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

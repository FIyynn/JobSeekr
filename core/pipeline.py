from __future__ import annotations

from typing import Any

from stages.extract import run_extract_stage


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def run_pipeline(payload: dict[str, Any], context: dict[str, Any], verbose: bool = True) -> dict[str, Any]:
    stage = payload.get("stage", "extract")
    _vlog(verbose, f"pipeline: {stage}")
    if stage != "extract":
        raise NotImplementedError(f"Unsupported stage: {stage}")
    return run_extract_stage(payload, context, verbose=verbose)

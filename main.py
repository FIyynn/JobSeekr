from __future__ import annotations

import argparse
import json
from pathlib import Path

from browser.driver import build_driver, close_driver
from core.config import load_app_config, resolve_extract_request
from core.pipeline import run_pipeline
from storage.embedded_mongo import EmbeddedMongoStore


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def parse_args(verbose: bool = True) -> argparse.Namespace:
    _vlog(verbose, "main: args")
    parser = argparse.ArgumentParser(description="JobSeekr Fresh extract runner")
    parser.add_argument("--preset", default=None, help="Preset name from config/presets.json")
    parser.add_argument("--keyword", default=None, help="Keyword to search")
    parser.add_argument("--location", default=None, help="Location to search")
    parser.add_argument("--dry-run", action="store_true", help="Load config and print the planned request only")
    return parser.parse_args()


def main(verbose: bool = True) -> int:
    _vlog(verbose, "main: start")
    args = parse_args(verbose=verbose)
    app_config = load_app_config(verbose=verbose)
    request = resolve_extract_request(
        app_config,
        preset_name=args.preset,
        overrides={k: v for k, v in {"keyword": args.keyword, "location": args.location}.items() if v is not None},
        verbose=verbose,
    )

    if args.dry_run or not request.get("keyword") or not request.get("location"):
        print(json.dumps({"status": "ready", "request": request}, indent=2, ensure_ascii=True))
        return 0

    browser_cfg = app_config["profile"]["browser"]
    data_file = Path(app_config["profile"]["mongo_file"])
    store = EmbeddedMongoStore(data_file, verbose=verbose)
    driver = None
    try:
        driver = build_driver(browser_cfg, verbose=verbose)
        context = {
            "driver": driver,
            "profile": app_config["profile"],
            "request": request,
            "store": store,
        }
        result = run_pipeline(request | {"stage": "extract"}, context, verbose=verbose)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    finally:
        close_driver(driver, verbose=verbose)


if __name__ == "__main__":
    raise SystemExit(main())

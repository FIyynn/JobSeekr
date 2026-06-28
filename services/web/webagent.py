from __future__ import annotations

from datetime import datetime
from typing import Any

from services.web.interact import interact as _interact
from services.web.markdown import output_markdown
from core.logging import TreeLogger


DEFAULT_LOG_ROOT = "mock://mongodb/webagent"


def _vlog(verbose: bool, message: str) -> None:
    if verbose:
        print(message, flush=True)


def _mock_log_path(base: str | None, suffix: str) -> str:
    root = (base or DEFAULT_LOG_ROOT).rstrip("/")
    return f"{root}/{suffix.strip('/')}"


def _maybe_save(store: Any | None, collection: str, document: dict[str, Any], verbose: bool = True) -> None:
    if store is None:
        return
    saver = getattr(store, "insert_one", None)
    if callable(saver):
        try:
            saver(collection, document, verbose=verbose)
        except Exception:
            pass


def webagent_fetch_page(
    driver,
    url: str,
    delays: dict[str, Any] | None = None,
    log_path: str | None = None,
    verbose: bool = False,
    store: Any | None = None,
    remove_json: bool = True,
    remove_links: bool = True,
    wait_seconds: float | int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    logger = TreeLogger("webagent_fetch_page", verbose=verbose)
    result: dict[str, Any] = {
        "status": "partial",
        "url": url,
        "markdown": "",
        "dev": {},
        "log_path": _mock_log_path(log_path, "fetch_page"),
        "session_state": {},
        "logs": None,
    }
    if driver is None:
        raise ValueError("webagent_fetch_page requires a browser driver")

    logger.event("Navigate", f"url={url}", verbose=verbose)
    driver.get(url)
    if wait_seconds and float(wait_seconds) > 0:
        _vlog(verbose, f"webagent: wait {wait_seconds}s")
        import time

        time.sleep(float(wait_seconds))

    markdown, dev = output_markdown(driver, remove_json=remove_json, remove_links=remove_links)
    result["markdown"] = markdown
    result["dev"] = dev
    result["session_state"] = {
        "current_url": getattr(driver, "current_url", "") or url,
        "timestamp": (now or datetime.utcnow()).isoformat(),
        "interactable_count": len((dev or {}).get("interactables", [])),
        "image_count": len((dev or {}).get("images", [])),
    }
    result["logs"] = logger.to_dict(verbose=verbose)
    result["status"] = "success"

    _maybe_save(
        store,
        "webagent_runs",
        {
            "kind": "fetch_page",
            "log_path": result["log_path"],
            "url": url,
            "status": result["status"],
            "session_state": result["session_state"],
            "markdown": markdown,
        },
        verbose=verbose,
    )
    return result


def _webagent_interact(
    action: str,
    driver,
    markdown_text: str,
    interactables: Any,
    target_id: str,
    delay_seconds: float | int = 0,
    log_path: str | None = None,
    verbose: bool = False,
    store: Any | None = None,
) -> dict[str, Any]:
    logger = TreeLogger(f"webagent_{action}", verbose=verbose)
    logger.event("Interact", f"action={action} target={target_id}", verbose=verbose)
    result = _interact(
        driver,
        markdown_text,
        interactables,
        action,
        target_id,
        delay_seconds=delay_seconds,
    )
    result["log_path"] = _mock_log_path(log_path, action)
    result["session_state"] = {
        "current_url": getattr(driver, "current_url", "") or "",
        "target_id": target_id,
        "action": action,
    }
    result["logs"] = logger.to_dict(verbose=verbose)

    _maybe_save(
        store,
        "webagent_actions",
        {
            "kind": action,
            "log_path": result["log_path"],
            "status": result.get("status", "unknown"),
            "target_id": target_id,
            "interaction": result,
        },
        verbose=verbose,
    )
    return result


def webagent_click(
    driver,
    markdown_text: str,
    interactables: Any,
    target_id: str,
    delay_seconds: float | int = 0,
    log_path: str | None = None,
    verbose: bool = False,
    store: Any | None = None,
) -> dict[str, Any]:
    return _webagent_interact(
        "click",
        driver,
        markdown_text,
        interactables,
        target_id,
        delay_seconds=delay_seconds,
        log_path=log_path,
        verbose=verbose,
        store=store,
    )


def webagent_type(
    driver,
    markdown_text: str,
    interactables: Any,
    target_id: str,
    text: str,
    click_enter: bool = False,
    delay_seconds: float | int = 0,
    log_path: str | None = None,
    verbose: bool = False,
    store: Any | None = None,
) -> dict[str, Any]:
    payload_target_id = f"{target_id}?value={text}"
    if click_enter:
        payload_target_id += "&click_enter=true"
    return _webagent_interact(
        "input_text",
        driver,
        markdown_text,
        interactables,
        payload_target_id,
        delay_seconds=delay_seconds,
        log_path=log_path,
        verbose=verbose,
        store=store,
    )


def webagent_clear_text(
    driver,
    markdown_text: str,
    interactables: Any,
    target_id: str,
    delay_seconds: float | int = 0,
    log_path: str | None = None,
    verbose: bool = False,
    store: Any | None = None,
) -> dict[str, Any]:
    return _webagent_interact(
        "clear",
        driver,
        markdown_text,
        interactables,
        target_id,
        delay_seconds=delay_seconds,
        log_path=log_path,
        verbose=verbose,
        store=store,
    )

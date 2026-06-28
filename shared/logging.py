from __future__ import annotations

from copy import deepcopy
from typing import Any


class TreeLogger:
    def __init__(self, stage: str, verbose: bool = True):
        self.stage = stage
        self.verbose = verbose
        self._events: list[dict[str, Any]] = []

    def event(
        self,
        message: str,
        details: str | list[str] | None = None,
        level: str = "info",
        verbose: bool = True,
    ) -> dict[str, Any]:
        if verbose and self.verbose:
            print(f"log: {message}", flush=True)
        event: dict[str, Any] = {"level": level, "message": message}
        if details:
            event["details"] = details if isinstance(details, list) else [details]
        event["children"] = []
        self._events.append(event)
        return event

    def child(
        self,
        parent: dict[str, Any],
        message: str,
        details: str | list[str] | None = None,
        level: str = "info",
        verbose: bool = True,
    ) -> dict[str, Any]:
        if verbose and self.verbose:
            print(f"log: {message}", flush=True)
        child_event: dict[str, Any] = {"level": level, "message": message}
        if details:
            child_event["details"] = details if isinstance(details, list) else [details]
        child_event["children"] = []
        parent.setdefault("children", []).append(child_event)
        return child_event

    def to_dict(self, verbose: bool = True) -> dict[str, Any]:
        return {"stage": self.stage, "events": deepcopy(self._events)}

    def flatten(self, verbose: bool = True) -> list[str]:
        lines: list[str] = []

        def walk(events: list[dict[str, Any]], indent: int = 0) -> None:
            pad = "  " * indent
            for event in events:
                lines.append(f"{pad}- {event['message']}")
                for detail in event.get("details", []):
                    lines.append(f"{pad}  {detail}")
                walk(event.get("children", []), indent + 1)

        walk(self._events)
        return lines

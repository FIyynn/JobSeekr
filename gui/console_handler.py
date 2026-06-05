"""Bridge Python logging to the JobHuntrr GUI console tab."""

from __future__ import annotations

import logging

PIPELINE_LOGGERS = (
    "orchestrator",
    "discovery",
    "scorer",
    "form_filler",
    "profile_manager",
    "search_planner",
    "job_logger",
    "notion_logger",
    "notion_sync",
    "job_profile",
    "salary_filter",
    "job_fit",
    "linkedin_outreach",
)


class GuiConsoleHandler(logging.Handler):
    """Stream log records to a thread-safe GUI append callback."""

    def __init__(self, append_fn) -> None:
        super().__init__()
        self._append = append_fn
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._append(self.format(record))
        except Exception:
            self.handleError(record)


def attach_gui_console(append_fn) -> GuiConsoleHandler:
    """Attach once on the root logger (child loggers propagate — avoids duplicate lines)."""
    handler = GuiConsoleHandler(append_fn)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    for name in PIPELINE_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
    return handler


def detach_gui_console(handler: GuiConsoleHandler | None) -> None:
    if not handler:
        return
    root = logging.getLogger()
    try:
        root.removeHandler(handler)
    except ValueError:
        pass

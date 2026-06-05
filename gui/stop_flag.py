"""
Cooperative stop flag — set from the GUI Stop button, polled by pipeline loops.

Usage (in any long-running loop):
    from gui.stop_flag import should_stop, StopRequested
    if should_stop():
        raise StopRequested("User requested stop")
"""
import threading

_stop = threading.Event()


class StopRequested(Exception):
    """Raised inside background workers when the user presses Stop."""


def request_stop() -> None:
    """Called by the GUI Stop button."""
    _stop.set()


def clear() -> None:
    """Called at the start of every background task to reset the flag."""
    _stop.clear()


def should_stop() -> bool:
    """Lightweight poll — call this at the top of every major loop iteration."""
    return _stop.is_set()


def check_stop(msg: str = "Stop requested by user") -> None:
    """Raise StopRequested if the flag is set. Use inside loops."""
    if _stop.is_set():
        raise StopRequested(msg)

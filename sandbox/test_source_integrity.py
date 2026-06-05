"""Fail fast when source files are truncated, binary-corrupted, or missing APIs."""

from __future__ import annotations

import importlib
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_PARTS = {".git", ".venv", "venv", "__pycache__"}
REQUIRED_APIS = (
    ("orchestrator", "run_pipeline"),
    ("agents.form_filler", "apply_jobs_batch"),
    ("agents.web_signal_discovery", "discover_web_signals"),
    ("config.env_settings", "bootstrap_settings"),
    ("agents.job_logger", "update_after_apply"),
    ("apply_jobs", "run_apply_batch"),
    ("agents.account_signup", "clear_auth_wall"),
)


def main() -> int:
    failures: list[str] = []
    files = [
        path for path in ROOT.rglob("*.py")
        if not any(part in SKIP_PARTS for part in path.parts)
    ]
    for path in sorted(files):
        relative = path.relative_to(ROOT)
        data = path.read_bytes()
        if b"\0" in data:
            failures.append(f"{relative}: contains {data.count(b'\\0')} null byte(s)")
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{relative}: {exc.msg}")

    for module_name, api_name in REQUIRED_APIS:
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, api_name)
            if not callable(value):
                failures.append(f"{module_name}.{api_name}: exists but is not callable")
        except Exception as exc:
            failures.append(f"{module_name}.{api_name}: import failed: {exc}")

    if failures:
        print("SOURCE INTEGRITY FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(
        f"SOURCE INTEGRITY PASSED: {len(files)} Python files compiled, "
        f"{len(REQUIRED_APIS)} public APIs imported"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

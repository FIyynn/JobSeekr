"""
Per-portal ATS credential vault.

Secrets are encrypted with Windows DPAPI for the current Windows user. Portal
metadata is stored separately so known portals can prefer sign-in.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent.parent
VAULT_PATH = ROOT / "data" / "portal_credentials.dpapi.json"
REGISTRY_PATH = ROOT / "data" / "portal_registry.json"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def portal_key(url: str) -> str:
    """Return a tenant-level key; different Workday tenants stay separate."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _protect(data: bytes) -> str:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Portal credential vault requires Windows DPAPI")
    in_blob, keepalive = _blob(data)
    out_blob = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), "JobHuntrr portal credential", None, None, None, 0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return base64.b64encode(
            ctypes.string_at(out_blob.pbData, out_blob.cbData)
        ).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del keepalive


def _unprotect(encoded: str) -> bytes:
    in_blob, keepalive = _blob(base64.b64decode(encoded.encode("ascii")))
    out_blob = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del keepalive


def encrypt_secret(value: str) -> str:
    """Encrypt a single settings value for storage in a JSON file."""
    return f"dpapi:{_protect(value.encode('utf-8'))}" if value else ""


def decrypt_secret(value: str) -> str:
    """Decrypt a DPAPI settings value; preserve legacy plaintext values."""
    if not value or not value.startswith("dpapi:"):
        return value
    try:
        return _unprotect(value.removeprefix("dpapi:")).decode("utf-8")
    except Exception:
        return ""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_vault() -> dict:
    encoded = _read_json(VAULT_PATH).get("encrypted")
    if not encoded:
        return {}
    try:
        value = json.loads(_unprotect(encoded).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_vault(data: dict) -> None:
    _write_json(
        VAULT_PATH,
        {"version": 1, "encrypted": _protect(json.dumps(data).encode("utf-8"))},
    )


def get_portal_credential(url: str) -> dict:
    value = _read_vault().get(portal_key(url), {})
    return dict(value) if isinstance(value, dict) else {}


def save_portal_credential(url: str, email: str, password: str) -> None:
    key = portal_key(url)
    if not key or not email or not password:
        return
    data = _read_vault()
    data[key] = {"email": email, "password": password}
    _write_vault(data)


def get_portal_record(url: str) -> dict:
    value = _read_json(REGISTRY_PATH).get(portal_key(url), {})
    return dict(value) if isinstance(value, dict) else {}


def portal_has_account(url: str) -> bool:
    return get_portal_record(url).get("account_status") == "active"


def record_portal_result(url: str, *, platform: str = "", outcome: str) -> None:
    key = portal_key(url)
    if not key:
        return
    data = _read_json(REGISTRY_PATH)
    record = data.get(key, {})
    if not isinstance(record, dict):
        record = {}
    record["platform"] = platform or record.get("platform", "")
    record["last_outcome"] = outcome
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    if outcome in ("signed_in", "account_created", "manual_auth_completed"):
        record["account_status"] = "active"
    data[key] = record
    _write_json(REGISTRY_PATH, data)

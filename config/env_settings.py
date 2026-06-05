"""
Account credentials and runtime flags — stored in data/profile_settings.json only.

Edit via GUI Profile Settings → Account & credentials. No .env file required.
Legacy .env is imported once into profile_settings.json if present, then ignored.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
LEGACY_ENV_PATH = ROOT / ".env"
PROFILE_SETTINGS_PATH = ROOT / "data" / "profile_settings.json"

ENV_KEYS = (
    "STORAGE_BACKEND",
    "AUTO_ENRICH_PROFILE",
    "PROFILE_DUAL_LAYER",
    "UNATTENDED_APPLY",
    "INTERACTIVE_APPLY",
    "WEB_SIGNAL_SEARCH",
    "WEB_SIGNAL_MAX_RESULTS",
    "WEB_SIGNAL_MAX_QUERIES",
    "WEB_SIGNAL_RESULTS_PER_QUERY",
    "LINKEDIN_POST_SEARCH",
    "LINKEDIN_POST_MAX_RESULTS",
    "GOOGLE_JOBS_SEARCH",
    "BROWSER_GOOGLE_SEARCH",
    "SERPAPI_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_SEARCH_CX",
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID",
    "APPLICANT_EMAIL",
    "APPLICANT_PHONE",
    "APPLICANT_PHONE_LOCAL",
    "LINKEDIN_EMAIL",
    "LINKEDIN_PASSWORD",
)

DEFAULT_ENV = {
    "STORAGE_BACKEND": "local",
    "AUTO_ENRICH_PROFILE": "1",
    "PROFILE_DUAL_LAYER": "1",
    "UNATTENDED_APPLY": "1",
    "INTERACTIVE_APPLY": "0",
    "WEB_SIGNAL_SEARCH": "1",
    "WEB_SIGNAL_MAX_RESULTS": "15",
    "WEB_SIGNAL_MAX_QUERIES": "6",
    "WEB_SIGNAL_RESULTS_PER_QUERY": "4",
    "LINKEDIN_POST_SEARCH": "1",
    "LINKEDIN_POST_MAX_RESULTS": "8",
    "GOOGLE_JOBS_SEARCH": "1",
    "BROWSER_GOOGLE_SEARCH": "1",
    "SERPAPI_API_KEY": "",
    "GOOGLE_SEARCH_API_KEY": "",
    "GOOGLE_SEARCH_CX": "",
    "NOTION_TOKEN": "",
    "NOTION_DATABASE_ID": "",
    "APPLICANT_EMAIL": "",
    "APPLICANT_PHONE": "",
    "APPLICANT_PHONE_LOCAL": "",
    "LINKEDIN_EMAIL": "",
    "LINKEDIN_PASSWORD": "",
}

CORE_LINK_KEYS = ("linkedin", "github", "website", "other")
SECRET_ENV_KEYS = (
    "LINKEDIN_PASSWORD",
    "NOTION_TOKEN",
    "SERPAPI_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
)
_MIGRATED_FLAG = ROOT / "data" / ".env_migrated_to_profile_settings"

SIGNUP_DEFAULTS_KEYS = (
    "first_name",
    "last_name",
    "middle_name",
    "full_name",
    "gender",
    "email",
    "password",
    "address",
    "city",
    "state",
    "country",
    "postal_code",
    "location",
    "nationality",
    "date_of_birth",
)

_QA_TO_SIGNUP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "middle_name": "middle_name",
    "full_name": "full_name",
    "gender": "gender",
    "email": "email",
    "address": "address",
    "city": "city",
    "state": "state",
    "country": "country",
    "postal_code": "postal_code",
    "location": "location",
    "nationality": "nationality",
    "date_of_birth": "date_of_birth",
}


def _default_signup_defaults() -> dict:
    defaults = {k: "" for k in SIGNUP_DEFAULTS_KEYS}
    try:
        from config.config import APPLICATION_QA
        for signup_key, qa_key in _QA_TO_SIGNUP.items():
            val = APPLICATION_QA.get(qa_key)
            if val is not None and str(val).strip():
                defaults[signup_key] = str(val).strip()
    except Exception:
        pass
    return defaults


def _default_raw_settings() -> dict:
    return {
        "env": dict(DEFAULT_ENV),
        "signup_defaults": _default_signup_defaults(),
        "linkedin": "",
        "github": "",
        "website": "",
        "other": "",
        "resume_path": str(ROOT / "Rashed_Alneyadi_Resume.pdf"),
        "cover_letter_path": "",
        "extra_links": {},
    }


def _parse_legacy_dotenv(path: Path) -> dict[str, str]:
    """Read a legacy .env file (one-time migration only)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in ENV_KEYS:
            out[key] = val.strip()
    return out


def _read_raw_settings() -> dict:
    raw = _default_raw_settings()
    if not PROFILE_SETTINGS_PATH.exists():
        return raw
    try:
        data = json.loads(PROFILE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return raw
    if isinstance(data.get("env"), dict):
        for k in ENV_KEYS:
            if k in data["env"] and data["env"][k] is not None:
                value = str(data["env"][k])
                if k in SECRET_ENV_KEYS:
                    try:
                        from agents.credential_vault import decrypt_secret
                        value = decrypt_secret(value)
                    except Exception:
                        pass
                raw["env"][k] = value
    for k in CORE_LINK_KEYS:
        if data.get(k):
            raw[k] = data[k]
    if data.get("resume_path"):
        raw["resume_path"] = data["resume_path"]
    if data.get("cover_letter_path"):
        raw["cover_letter_path"] = data["cover_letter_path"]
    if isinstance(data.get("extra_links"), dict):
        raw["extra_links"] = data["extra_links"]
    if isinstance(data.get("signup_defaults"), dict):
        for k in SIGNUP_DEFAULTS_KEYS:
            if k in data["signup_defaults"] and data["signup_defaults"][k] is not None:
                value = str(data["signup_defaults"][k])
                if k == "password":
                    try:
                        from agents.credential_vault import decrypt_secret
                        value = decrypt_secret(value)
                    except Exception:
                        pass
                raw["signup_defaults"][k] = value
    return raw


def _normalize_signup_defaults(values: dict, env: dict | None = None) -> dict:
    """Fill derived signup fields (full_name, location, email) before save."""
    out = {k: str(values.get(k, "") or "").strip() for k in SIGNUP_DEFAULTS_KEYS}
    if not out["full_name"]:
        parts = [out["first_name"], out.get("middle_name", ""), out["last_name"]]
        out["full_name"] = " ".join(p for p in parts if p).strip()
    if not out["email"] and env:
        out["email"] = str(env.get("APPLICANT_EMAIL", "") or "").strip()
    if not out["location"]:
        loc_parts = [out["city"], out["state"], out["country"]]
        out["location"] = ", ".join(p for p in loc_parts if p).strip()
    return out


def _write_raw_settings(raw: dict) -> None:
    PROFILE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    signup = _normalize_signup_defaults(
        raw.get("signup_defaults") or {}, raw.get("env")
    )
    stored_signup = dict(signup)
    if stored_signup.get("password"):
        try:
            from agents.credential_vault import encrypt_secret
            stored_signup["password"] = encrypt_secret(stored_signup["password"])
        except Exception:
            pass
    stored_env = {k: str(raw["env"].get(k, DEFAULT_ENV[k])) for k in ENV_KEYS}
    for key in SECRET_ENV_KEYS:
        if stored_env.get(key):
            try:
                from agents.credential_vault import encrypt_secret
                stored_env[key] = encrypt_secret(stored_env[key])
            except Exception:
                pass
    payload = {
        "env": stored_env,
        "signup_defaults": stored_signup,
        "linkedin": raw.get("linkedin", ""),
        "github": raw.get("github", ""),
        "website": raw.get("website", ""),
        "other": raw.get("other", ""),
        "resume_path": raw.get("resume_path") or str(ROOT / "Rashed_Alneyadi_Resume.pdf"),
        "cover_letter_path": raw.get("cover_letter_path", ""),
        "extra_links": raw.get("extra_links") or {},
    }
    PROFILE_SETTINGS_PATH.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def migrate_legacy_dotenv_if_needed() -> bool:
    """
    Import legacy .env into profile_settings.json once.
    Returns True if migration ran.
    """
    if _MIGRATED_FLAG.exists():
        return False
    legacy = _parse_legacy_dotenv(LEGACY_ENV_PATH)
    if not legacy:
        _MIGRATED_FLAG.write_text("no_legacy_env\n", encoding="utf-8")
        return False
    raw = _read_raw_settings()
    for k, v in legacy.items():
        if v and not raw["env"].get(k):
            raw["env"][k] = v
    _write_raw_settings(raw)
    _MIGRATED_FLAG.write_text("migrated\n", encoding="utf-8")
    return True


def load_env_settings() -> dict[str, str]:
    """Account / Notion / LinkedIn / flags from profile_settings.json."""
    migrate_legacy_dotenv_if_needed()
    raw = _read_raw_settings()
    try:
        stored = json.loads(PROFILE_SETTINGS_PATH.read_text(encoding="utf-8"))
        stored_env = stored.get("env") or {}
        if any(
            stored_env.get(key) and not str(stored_env[key]).startswith("dpapi:")
            for key in SECRET_ENV_KEYS
        ):
            _write_raw_settings(raw)
    except Exception:
        pass
    return dict(raw["env"])


def load_signup_defaults() -> dict:
    """Personal identity fields used on signup and application forms."""
    migrate_legacy_dotenv_if_needed()
    raw = _read_raw_settings()
    try:
        stored = json.loads(PROFILE_SETTINGS_PATH.read_text(encoding="utf-8"))
        password = str((stored.get("signup_defaults") or {}).get("password") or "")
        if password and not password.startswith("dpapi:"):
            _write_raw_settings(raw)
    except Exception:
        pass
    return _normalize_signup_defaults(
        raw.get("signup_defaults") or {}, raw.get("env")
    )


def load_profile_settings() -> dict:
    """Links, resume path, extra URLs (for GUI and runtime)."""
    migrate_legacy_dotenv_if_needed()
    raw = _read_raw_settings()
    prof = {
        "resume_path": raw["resume_path"],
        "cover_letter_path": raw.get("cover_letter_path", ""),
        "extra_links": dict(raw.get("extra_links") or {}),
        "signup_defaults": load_signup_defaults(),
    }
    for k in CORE_LINK_KEYS:
        prof[k] = raw.get(k, "")
    # Fall back to profile markdown links if JSON empty
    if not prof.get("linkedin"):
        try:
            from agents.profile_manager import load_links
            links = load_links()
            for k in CORE_LINK_KEYS:
                if links.get(k):
                    prof[k] = links[k]
        except Exception:
            pass
    return prof


def load_all_settings() -> dict:
    return {"env": load_env_settings(), "profile": load_profile_settings()}


def save_env_settings(values: dict[str, str]) -> None:
    """Update env section only (preserves links/resume)."""
    raw = _read_raw_settings()
    for k in ENV_KEYS:
        if k in values:
            raw["env"][k] = str(values[k])
    _write_raw_settings(raw)


def save_profile_settings(
    links: dict,
    resume_path: str,
    extra_links: dict = None,
) -> None:
    """Update links/resume (preserves env section)."""
    raw = _read_raw_settings()
    for k in CORE_LINK_KEYS:
        raw[k] = (links.get(k) or "").strip()
    extra = extra_links or {
        k: links[k] for k in links if k not in CORE_LINK_KEYS and links.get(k)
    }
    raw["extra_links"] = extra
    raw["resume_path"] = (resume_path or "").strip() or raw["resume_path"]
    _write_raw_settings(raw)
    try:
        from agents.profile_manager import save_links
        save_links({**{k: raw[k] for k in CORE_LINK_KEYS}, **extra})
    except Exception:
        pass


def save_all_settings(
    env: dict[str, str],
    links: dict,
    resume_path: str,
    extra_links: dict = None,
    signup_defaults: dict = None,
    cover_letter_path: str = "",
) -> None:
    """Single save from GUI — env + links + resume → profile_settings.json."""
    raw = _read_raw_settings()
    for k in ENV_KEYS:
        if k in env:
            raw["env"][k] = str(env[k])
    if signup_defaults is not None:
        raw["signup_defaults"] = {
            k: str(signup_defaults.get(k, "") or "").strip()
            for k in SIGNUP_DEFAULTS_KEYS
        }
    for k in CORE_LINK_KEYS:
        raw[k] = (links.get(k) or "").strip()
    extra = extra_links or {
        k: links[k] for k in links if k not in CORE_LINK_KEYS and links.get(k)
    }
    raw["extra_links"] = extra
    raw["resume_path"] = (resume_path or "").strip() or raw["resume_path"]
    raw["cover_letter_path"] = (cover_letter_path or "").strip()
    _write_raw_settings(raw)
    try:
        from agents.profile_manager import save_links
        save_links({**{k: raw[k] for k in CORE_LINK_KEYS}, **extra})
    except Exception:
        pass


def update_env_keys(updates: dict[str, str]) -> None:
    """Partial env update (e.g. setup_linkedin.py)."""
    raw = _read_raw_settings()
    for k, v in updates.items():
        if k in ENV_KEYS:
            raw["env"][k] = str(v)
    _write_raw_settings(raw)


# Back-compat aliases (GUI used save_env_file)
def parse_env_file(path: Path = None) -> dict[str, str]:
    return load_env_settings()


def save_env_file(values: dict[str, str], path: Path = None) -> None:
    save_env_settings(values)


def apply_settings_to_runtime(env: dict, settings: dict) -> None:
    """Push saved values into os.environ and config for current process."""
    for k in ENV_KEYS:
        if k in env and env[k] is not None:
            os.environ[k] = str(env[k])
    try:
        from config import config as cfg
        signup = _normalize_signup_defaults(
            settings.get("signup_defaults") or load_signup_defaults(), env
        )
        cfg.APPLICATION_QA["email"] = (
            signup.get("email") or env.get("APPLICANT_EMAIL", "")
        )
        cfg.APPLICATION_QA["phone"] = env.get("APPLICANT_PHONE", "")
        cfg.APPLICATION_QA["phone_local"] = env.get("APPLICANT_PHONE_LOCAL", "")
        for key in SIGNUP_DEFAULTS_KEYS:
            if key == "email":
                continue
            val = signup.get(key, "")
            if val:
                cfg.APPLICATION_QA[key] = val
        if settings.get("linkedin"):
            cfg.APPLICATION_QA["linkedin"] = settings["linkedin"]
        rp = settings.get("resume_path")
        if rp and Path(rp).exists():
            cfg.APPLICATION_QA["resume_path"] = rp
            cfg.RESUME_PATH = rp
        clp = settings.get("cover_letter_path")
        if clp and Path(clp).exists():
            cfg.APPLICATION_QA["cover_letter_path"] = clp
        else:
            cfg.APPLICATION_QA.pop("cover_letter_path", None)
        cfg.reload_candidate_profile()
        from config.applicant_requirements import reload_applicant_requirements_text
        reload_applicant_requirements_text()
    except Exception:
        pass


def bootstrap_settings() -> dict:
    """Load saved settings and apply them to the current Python process."""
    settings = load_all_settings()
    apply_settings_to_runtime(settings["env"], settings["profile"])
    return settings

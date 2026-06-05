"""
Unified engine thresholds — loaded from data/applicant_requirements.md frontmatter.
"""

from config.md_loader import load_requirements_config

_ENGINE_DEFAULTS = {
    "sps_immediate_action": 85,
    "sps_apply_network": 70,
    "sps_network_only": 50,
    "ips_inmail_threshold": 75,
    "easy_apply_max_hours": 24,
    "easy_apply_max_applicants": 50,
    "warm_lead_referral_threshold": 70,
}


def load_engine_config() -> dict:
    cfg = load_requirements_config()
    out = dict(_ENGINE_DEFAULTS)
    for key in _ENGINE_DEFAULTS:
        if key in cfg:
            try:
                out[key] = int(cfg[key]) if isinstance(_ENGINE_DEFAULTS[key], int) else float(cfg[key])
            except (TypeError, ValueError):
                out[key] = cfg[key]
    return out


ENGINE_CONFIG = load_engine_config()


def reload_engine_config() -> dict:
    global ENGINE_CONFIG
    ENGINE_CONFIG = load_engine_config()
    return ENGINE_CONFIG

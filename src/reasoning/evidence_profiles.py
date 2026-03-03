"""
Reasoning evidence profile configuration (R0..R3).

Profiles control which evidence blocks are projected into reasoning_pack.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Tuple


ROUND_PROFILE_ORDER = ("R0", "R1", "R2", "R3")


_DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "R0": {
        "include_target_atb_summary": True,
        "include_target_atb_full": False,
        "include_atb_trends_self": False,
        "include_neighbor_summary": False,
        "include_neighbor_atb_stats_by_label": False,
        "include_neighbor_atb_stats": False,
        "include_neighbor_feature_rows": False,
        "include_literature_status": False,
        "include_experiment_status": False,
        "neighbor_topk": 0,
        "registry_max_items": 10,
    },
    "R1": {
        "include_target_atb_summary": True,
        "include_target_atb_full": False,
        "include_atb_trends_self": True,
        "include_neighbor_summary": False,
        "include_neighbor_atb_stats_by_label": False,
        "include_neighbor_atb_stats": False,
        "include_neighbor_feature_rows": False,
        "include_literature_status": False,
        "include_experiment_status": False,
        "neighbor_topk": 0,
        "registry_max_items": 12,
    },
    "R2": {
        "include_target_atb_summary": True,
        "include_target_atb_full": False,
        "include_atb_trends_self": True,
        "include_neighbor_summary": True,
        "include_neighbor_atb_stats_by_label": True,
        "include_neighbor_atb_stats": True,
        "include_neighbor_feature_rows": False,
        "include_literature_status": True,
        "include_experiment_status": True,
        "neighbor_topk": 5,
        "registry_max_items": 16,
    },
    "R3": {
        "include_target_atb_summary": True,
        "include_target_atb_full": False,
        "include_atb_trends_self": True,
        "include_neighbor_summary": True,
        "include_neighbor_atb_stats_by_label": True,
        "include_neighbor_atb_stats": True,
        "include_neighbor_feature_rows": False,
        "include_literature_status": True,
        "include_experiment_status": True,
        "neighbor_topk": 5,
        "registry_max_items": 20,
    },
}


def default_evidence_profiles() -> Dict[str, Any]:
    return {"active_profile": "R2", "profiles": deepcopy(_DEFAULT_PROFILES)}


def resolve_evidence_profiles(reasoning_config: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Resolve active evidence profile from reasoning_config.

    Returns:
        (active_profile, active_profile_cfg, normalized_profiles_cfg)
    """
    base = default_evidence_profiles()
    cfg = reasoning_config.get("evidence_profiles") if isinstance(reasoning_config, dict) else None
    if not isinstance(cfg, dict):
        active = str(base["active_profile"])
        return active, deepcopy(base["profiles"][active]), base

    profiles = deepcopy(base["profiles"])
    user_profiles = cfg.get("profiles")
    if isinstance(user_profiles, dict):
        for name, row in user_profiles.items():
            if name not in profiles or not isinstance(row, dict):
                continue
            profiles[name].update(row)

    active = str(cfg.get("active_profile") or base["active_profile"]).upper()
    if active not in profiles:
        active = str(base["active_profile"])

    normalized = {"active_profile": active, "profiles": profiles}
    return active, deepcopy(profiles[active]), normalized


def next_profile_name(current: str) -> str:
    cur = str(current or "R0").upper()
    if cur not in ROUND_PROFILE_ORDER:
        return "R1"
    idx = ROUND_PROFILE_ORDER.index(cur)
    if idx + 1 >= len(ROUND_PROFILE_ORDER):
        return "NONE"
    return ROUND_PROFILE_ORDER[idx + 1]

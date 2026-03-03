"""
Reasoning policy defaults for master reasoner.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_REASONING_POLICY: Dict[str, Any] = {
    "neighbor_support_min_sim": 0.55,
    "atb_dihedral_thresh_none": 8.0,
    "atb_dihedral_thresh_strong": 15.0,
    "atb_dihedral_flat_eps": 1.0e-6,
    "atb_gap_flat_eps": 0.05,
    "atb_gap_weak": 0.2,
    "atb_gap_strong": 0.6,
    "atb_vol_flat_eps": 0.1,
    "atb_vol_weak": 0.5,
    "atb_vol_strong": 2.0,
    # Legacy cap knobs (kept for backward compatibility; runtime now uses soft-penalty).
    "conf_cap_top1_sim_low": 0.45,
    "conf_cap_entropy_high": 0.50,
    "conf_cap_both": 0.42,
    "entropy_high": 0.75,
    "top1_sim_low": 0.50,
    # Soft-penalty knobs (v1).
    "confidence_soft_penalty_version": "v1",
    "confidence_base_stable": 0.62,
    "confidence_base_mixture": 0.52,
    "confidence_base_novelty": 0.45,
    "penalty_mode_conservative": 0.86,
    "penalty_sim_floor": 0.55,
    "penalty_ent_floor": 0.55,
    "separation_boost_strength": 0.22,
    "separation_center": 0.45,
    "penalty_sim_strength": 0.25,
    "penalty_entropy_strength": 0.25,
}

DEFAULT_ALLOWED_MECHANISM_LABELS: List[str] = [
    "TICT",
    "ESIPT",
    "ICT",
    "other",
    "unknown",
]


def build_allowed_mechanism_labels(overrides: Any = None) -> List[str]:
    labels: List[str] = []
    source = overrides if isinstance(overrides, list) else DEFAULT_ALLOWED_MECHANISM_LABELS
    for row in source:
        txt = str(row or "").strip()
        if not txt:
            continue
        if txt not in labels:
            labels.append(txt)
    if "unknown" not in labels:
        labels.append("unknown")
    if "other" not in labels:
        labels.append("other")
    return labels


def build_reasoning_policy(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    policy = deepcopy(DEFAULT_REASONING_POLICY)
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if k in policy and v is not None:
                policy[k] = v
    return policy

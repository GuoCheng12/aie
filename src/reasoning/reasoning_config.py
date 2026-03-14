"""
Reasoning policy defaults for master reasoner.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_REASONING_POLICY: Dict[str, Any] = {
    "evidence_axis_mode": "generic_v1",
    "ct_proxy_compat_alias": True,
    "allow_structure_prior_without_atb": True,
    "electronic_redistribution_requires_corroboration": True,
    "comparative_axis_can_only_adjust": True,
    "structure_prior_profile_enabled": True,
    "single_axis_confidence_cap": 0.38,
    "single_axis_penalty_factor": 0.80,
    "mixture_conflict_penalty": 0.92,
    "one_active_conflict_penalty": 0.90,
    "multi_active_conflict_penalty": 0.80,
    "unresolved_warning_penalty": 0.90,
    "comparative_only_support_penalty_factor": 0.85,
    "comparative_only_max_abs_conf_delta": 0.04,
    "final_adjudication_enabled": True,
    "final_adjudication_use_llm": True,
    "adjudication_candidate_set_mode": "master_primary_plus_competing",
    "standard_label_min_positive_axes": 2,
    "standard_label_requires_target_axis": True,
    "residual_other_min_standard_candidates": 2,
    "residual_other_min_conflicts": 2,
    "late_round_provisional_standard_confidence_cap": 0.32,
    "novelty_candidate_entropy_threshold": 0.75,
    "novelty_candidate_struct_threshold": 0.60,
    "allow_other_label": True,
    "other_residual_conflict_min": 2,
    "other_residual_min_standard_candidates": 2,
    "late_round_single_axis_standard_penalty_factor": 0.85,
    "late_round_single_axis_standard_confidence_cap": 0.32,
    "neighbor_support_min_sim": 0.55,
    "atb_dihedral_thresh_none": 8.0,
    "atb_dihedral_thresh_strong": 15.0,
    "atb_dihedral_flat_eps": 1.0e-6,
    "atb_gap_flat_eps": 0.05,
    "atb_gap_weak": 0.2,
    "atb_gap_strong": 0.6,
    "atb_dipole_flat_eps": 0.05,
    "atb_dipole_weak": 0.2,
    "atb_dipole_strong": 0.6,
    "charge_redis_total_abs_low": 0.2190,
    "charge_redis_total_abs_high": 0.4805,
    "charge_redis_top3_share_low": 0.1908,
    "charge_redis_top3_share_high": 0.3195,
    "charge_redis_hetero_share_low": 0.1046,
    "charge_redis_hetero_share_high": 0.2620,
    "atb_vol_flat_eps": 0.1,
    "atb_vol_weak": 0.5,
    "atb_vol_strong": 2.0,
    "atb_bonds_weak": 0.02,
    "atb_bonds_strong": 0.08,
    "atb_angles_weak": 0.2,
    "atb_angles_strong": 0.8,
    "atb_asymmetry_weak": 0.05,
    "atb_asymmetry_strong": 0.2,
    "atb_rotconst_rel_weak": 0.05,
    "atb_rotconst_rel_strong": 0.15,
    "entropy_high": 0.75,
    "top1_sim_low": 0.50,
    # Soft-penalty knobs (v1).
    "confidence_soft_penalty_version": "v1",
    "confidence_base_stable": 0.62,
    "confidence_base_mixture": 0.52,
    "confidence_base_novelty": 0.45,
    "global_confidence_cap": 0.95,
    "r0_penalty_factor": 0.90,
    "r0_candidate_confidence_cap": 0.30,
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
    "neutral aromatic",
    "other",
    "unknown",
]


def build_allowed_mechanism_labels(overrides: Any = None, *, include_other: bool | None = None) -> List[str]:
    labels: List[str] = []
    source = overrides if isinstance(overrides, list) else DEFAULT_ALLOWED_MECHANISM_LABELS
    for row in source:
        txt = str(row or "").strip()
        if not txt:
            continue
        if txt not in labels:
            labels.append(txt)
    if include_other is False:
        labels = [label for label in labels if label != "other"]
    if "unknown" not in labels:
        labels.append("unknown")
    if include_other is not False and "other" not in labels:
        labels.append("other")
    return labels


def resolve_allow_other_label(
    *,
    runtime: Dict[str, Any] | None = None,
    reference_index_root: str | None = None,
    source_ref: str | None = None,
) -> bool:
    rt = runtime if isinstance(runtime, dict) else {}
    explicit = rt.get("allow_other_label")
    if isinstance(explicit, bool):
        return explicit

    candidates = [
        str(rt.get("reference_index_root") or "").strip(),
        str(reference_index_root or "").strip(),
        str(source_ref or "").strip(),
    ]
    for raw in candidates:
        if not raw:
            continue
        try:
            path = Path(raw)
            parts = {part for part in path.parts if part}
        except Exception:
            parts = {raw}
        if "split_levels_v2" in parts:
            return False
        if "split_list" in parts:
            return False
    return True


def build_reasoning_policy(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    policy = deepcopy(DEFAULT_REASONING_POLICY)
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if k in policy and v is not None:
                policy[k] = v
    return policy

"""
Compact electronic redistribution profile for reasoning pack / LLM input.

This profile prefers atomwise charge-variation summaries when available. It is
phenomenological only and must not be treated as a true dipole-moment change.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

MAX_BYTES = 2048
DEFAULT_THRESHOLDS = {
    "atb_gap_flat_eps": 0.05,
    "atb_gap_weak": 0.2,
    "atb_gap_strong": 0.6,
    "charge_redis_total_abs_low": 0.2190,
    "charge_redis_total_abs_high": 0.4805,
    "charge_redis_top3_share_low": 0.1908,
    "charge_redis_top3_share_high": 0.3195,
    "charge_redis_hetero_share_low": 0.1046,
    "charge_redis_hetero_share_high": 0.2620,
    "atb_dipole_flat_eps": 0.05,
    "atb_dipole_weak": 0.2,
    "atb_dipole_strong": 0.6,
}


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _bucket_gap(value: Optional[float], weak: float, strong: float) -> str:
    if value is None:
        return "unknown"
    val = abs(float(value))
    if val < float(weak):
        return "low"
    if val < float(strong):
        return "mid"
    return "high"


def _direction(value: Optional[float], eps: float) -> str:
    if value is None:
        return "unknown"
    val = float(value)
    if abs(val) <= float(eps):
        return "flat"
    return "increase" if val > 0 else "decrease"


def _bucket_three_way(value: Optional[float], low_cut: float, high_cut: float, labels: tuple[str, str, str]) -> str:
    if value is None:
        return "unknown"
    val = float(value)
    if val < float(low_cut):
        return labels[0]
    if val < float(high_cut):
        return labels[1]
    return labels[2]


def _score(bucket: str) -> float:
    return {"low": 0.0, "mid": 1.0, "high": 2.0}.get(bucket, 0.0)


def _trim(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = list(out.get("notes") or [])[:1]
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out.pop("n_atoms_ge_0p02", None)
    out.pop("n_atoms_ge_0p01", None)
    return out


def _build_atomwise_profile(features_summary: Mapping[str, Any], cfg: Mapping[str, float]) -> Optional[Dict[str, Any]]:
    total_abs = _to_float(features_summary.get("charge_redis_total_abs"))
    max_abs = _to_float(features_summary.get("charge_redis_max_abs_atom"))
    top3_share = _to_float(features_summary.get("charge_redis_top3_abs_share"))
    hetero_share = _to_float(features_summary.get("charge_redis_heteroatom_abs_share"))
    if total_abs is None or max_abs is None or top3_share is None or hetero_share is None:
        return None

    delta_gap = _to_float(features_summary.get("delta_gap"))
    magnitude_bucket = _bucket_three_way(
        total_abs,
        cfg["charge_redis_total_abs_low"],
        cfg["charge_redis_total_abs_high"],
        ("low", "mid", "high"),
    )
    localization = _bucket_three_way(
        top3_share,
        cfg["charge_redis_top3_share_low"],
        cfg["charge_redis_top3_share_high"],
        ("distributed", "mixed", "localized"),
    )
    hetero_involvement = _bucket_three_way(
        hetero_share,
        cfg["charge_redis_hetero_share_low"],
        cfg["charge_redis_hetero_share_high"],
        ("low", "mid", "high"),
    )
    gap_bucket = _bucket_gap(delta_gap, cfg["atb_gap_weak"], cfg["atb_gap_strong"])
    raw_score = 0.7 * _score(magnitude_bucket) + 0.3 * _score(gap_bucket)
    if raw_score >= 1.5:
        redistribution_score = "high"
    elif raw_score >= 0.6:
        redistribution_score = "medium"
    else:
        redistribution_score = "low"
    reliability = "high" if delta_gap is not None else "medium"
    return {
        "version": "charge_redistribution_v2",
        "source": "atomwise_charge_variation",
        "total_abs_charge_variation": total_abs,
        "max_abs_atom_variation": max_abs,
        "top3_atom_abs_share": top3_share,
        "heteroatom_abs_share": hetero_share,
        "n_atoms_ge_0p01": int(_to_float(features_summary.get("charge_redis_n_atoms_ge_0p01")) or 0),
        "n_atoms_ge_0p02": int(_to_float(features_summary.get("charge_redis_n_atoms_ge_0p02")) or 0),
        "redistribution_magnitude_bucket": magnitude_bucket,
        "redistribution_localization": localization,
        "heteroatom_involvement": hetero_involvement,
        "delta_gap_abs": abs(delta_gap) if delta_gap is not None else None,
        "delta_gap_direction": _direction(delta_gap, cfg["atb_gap_flat_eps"]),
        "delta_gap_bucket": gap_bucket,
        "redistribution_score": redistribution_score,
        "reliability": reliability,
        "notes": [
            "Derived from atom-wise charge variation and gap change.",
            "This is an electronic redistribution cue, not a true dipole moment.",
        ],
    }


def _build_scalar_profile(features_summary: Mapping[str, Any], cfg: Mapping[str, float]) -> Optional[Dict[str, Any]]:
    delta_dipole = _to_float(features_summary.get("delta_dipole"))
    if delta_dipole is None:
        return None
    delta_gap = _to_float(features_summary.get("delta_gap"))
    dipole_bucket = _bucket_gap(delta_dipole, cfg["atb_dipole_weak"], cfg["atb_dipole_strong"])
    gap_bucket = _bucket_gap(delta_gap, cfg["atb_gap_weak"], cfg["atb_gap_strong"])
    raw_score = 0.7 * _score(dipole_bucket) + 0.3 * _score(gap_bucket)
    if raw_score >= 1.5:
        redistribution_score = "high"
    elif raw_score >= 0.6:
        redistribution_score = "medium"
    else:
        redistribution_score = "low"
    reliability = "medium" if delta_gap is not None else "low"
    return {
        "version": "charge_redistribution_v2",
        "source": "scalar_delta_dipole",
        "total_abs_charge_variation": None,
        "max_abs_atom_variation": abs(delta_dipole),
        "top3_atom_abs_share": None,
        "heteroatom_abs_share": None,
        "n_atoms_ge_0p01": None,
        "n_atoms_ge_0p02": None,
        "redistribution_magnitude_bucket": dipole_bucket,
        "redistribution_localization": "unknown",
        "heteroatom_involvement": "unknown",
        "delta_gap_abs": abs(delta_gap) if delta_gap is not None else None,
        "delta_gap_direction": _direction(delta_gap, cfg["atb_gap_flat_eps"]),
        "delta_gap_bucket": gap_bucket,
        "redistribution_score": redistribution_score,
        "reliability": reliability,
        "notes": [
            "Derived from legacy scalar redistribution summary and gap change.",
            "This is an electronic redistribution cue, not a true dipole moment.",
        ],
    }


def _build_gap_only_profile(features_summary: Mapping[str, Any], cfg: Mapping[str, float]) -> Optional[Dict[str, Any]]:
    delta_gap = _to_float(features_summary.get("delta_gap"))
    if delta_gap is None:
        return None
    gap_bucket = _bucket_gap(delta_gap, cfg["atb_gap_weak"], cfg["atb_gap_strong"])
    return {
        "version": "charge_redistribution_v2",
        "source": "gap_only",
        "total_abs_charge_variation": None,
        "max_abs_atom_variation": None,
        "top3_atom_abs_share": None,
        "heteroatom_abs_share": None,
        "n_atoms_ge_0p01": None,
        "n_atoms_ge_0p02": None,
        "redistribution_magnitude_bucket": "unknown",
        "redistribution_localization": "unknown",
        "heteroatom_involvement": "unknown",
        "delta_gap_abs": abs(delta_gap),
        "delta_gap_direction": _direction(delta_gap, cfg["atb_gap_flat_eps"]),
        "delta_gap_bucket": gap_bucket,
        "redistribution_score": "low" if gap_bucket == "low" else "medium",
        "reliability": "low",
        "notes": [
            "Derived from gap change only because no atom-wise or scalar redistribution summary was available.",
            "This is an electronic redistribution cue, not a true dipole moment.",
        ],
    }


def compute_charge_redistribution_profile(
    features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = dict(DEFAULT_THRESHOLDS)
    if isinstance(thresholds, Mapping):
        for key in cfg:
            if thresholds.get(key) is not None:
                cfg[key] = float(thresholds[key])

    summary = features_summary or {}
    profile = _build_atomwise_profile(summary, cfg)
    if profile is None:
        profile = _build_scalar_profile(summary, cfg)
    if profile is None:
        profile = _build_gap_only_profile(summary, cfg)
    if profile is None:
        profile = {
            "version": "charge_redistribution_v2",
            "source": "gap_only",
            "total_abs_charge_variation": None,
            "max_abs_atom_variation": None,
            "top3_atom_abs_share": None,
            "heteroatom_abs_share": None,
            "n_atoms_ge_0p01": None,
            "n_atoms_ge_0p02": None,
            "redistribution_magnitude_bucket": "unknown",
            "redistribution_localization": "unknown",
            "heteroatom_involvement": "unknown",
            "delta_gap_abs": None,
            "delta_gap_direction": "unknown",
            "delta_gap_bucket": "unknown",
            "redistribution_score": "low",
            "reliability": "low",
            "notes": [
                "Derived from limited redistribution inputs.",
                "This is an electronic redistribution cue, not a true dipole moment.",
            ],
        }
    return _trim(profile)


__all__ = ["compute_charge_redistribution_profile"]

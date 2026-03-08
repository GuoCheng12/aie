"""
Self-only aTB trend profile for master reasoning.

This module only uses target aTB features_summary (no neighbors).
Buckets are calibrated from current successful aTB sample quantiles.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional


# Calibrated constants from successful cache quantiles (locked for v1).
DIHEDRAL_P50 = 0.1280
DIHEDRAL_P95 = 1.2581
GAP_P25 = 0.3946
GAP_P75 = 0.9042
VOLUME_P25 = 0.8124
VOLUME_P75 = 3.8829

DIHEDRAL_FLAT_EPS = 0.05
GAP_FLAT_EPS = 0.05
VOLUME_FLAT_EPS = 0.1

MAX_BYTES = 2 * 1024


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _bucket_abs(value: Optional[float], *, low_cut: float, high_cut: float) -> str:
    if value is None:
        return "unknown"
    v = abs(float(value))
    if v < float(low_cut):
        return "low"
    if v < float(high_cut):
        return "mid"
    return "high"


def _direction(value: Optional[float], *, eps: float) -> str:
    if value is None:
        return "unknown"
    v = float(value)
    if abs(v) <= float(eps):
        return "flat"
    return "increase" if v > 0 else "decrease"


def _overall_motion_proxy(dihedral_bucket: str, volume_bucket: str) -> str:
    if dihedral_bucket == "high" or volume_bucket == "high":
        return "high"
    if dihedral_bucket == "low" and volume_bucket == "low":
        return "low"
    return "medium"


def _reliability(values: Dict[str, Optional[float]]) -> str:
    keys = ("delta_dihedral", "delta_gap", "delta_volume", "excitation_energy")
    numeric = sum(1 for k in keys if values.get(k) is not None)
    if numeric >= 4:
        return "high"
    if numeric >= 3:
        return "medium"
    return "low"


def _trim_to_budget(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)

    def _size() -> int:
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    if _size() <= MAX_BYTES:
        return out

    notes = out.get("notes")
    if isinstance(notes, list):
        out["notes"] = notes[:2]
    if _size() <= MAX_BYTES:
        return out

    out["notes"] = []
    return out


def compute_atb_trend_profile(features_summary: Mapping[str, Any]) -> Dict[str, Any]:
    values = {
        "delta_dihedral": _to_float((features_summary or {}).get("delta_dihedral")),
        "delta_gap": _to_float((features_summary or {}).get("delta_gap")),
        "delta_volume": _to_float((features_summary or {}).get("delta_volume")),
        "excitation_energy": _to_float((features_summary or {}).get("excitation_energy")),
    }

    dihedral = values["delta_dihedral"]
    gap = values["delta_gap"]
    volume = values["delta_volume"]

    dihedral_bucket = _bucket_abs(dihedral, low_cut=DIHEDRAL_P50, high_cut=DIHEDRAL_P95)
    gap_bucket = _bucket_abs(gap, low_cut=GAP_P25, high_cut=GAP_P75)
    volume_bucket = _bucket_abs(volume, low_cut=VOLUME_P25, high_cut=VOLUME_P75)

    profile = {
        "version": "atb_trend_v1",
        "abs_values": {
            "delta_dihedral": abs(dihedral) if dihedral is not None else None,
            "delta_gap": abs(gap) if gap is not None else None,
            "delta_volume": abs(volume) if volume is not None else None,
        },
        "buckets": {
            "delta_dihedral": dihedral_bucket,
            "delta_gap": gap_bucket,
            "delta_volume": volume_bucket,
        },
        "direction": {
            "delta_dihedral": _direction(dihedral, eps=DIHEDRAL_FLAT_EPS),
            "delta_gap": _direction(gap, eps=GAP_FLAT_EPS),
            "delta_volume": _direction(volume, eps=VOLUME_FLAT_EPS),
        },
        "overall_motion_proxy": _overall_motion_proxy(dihedral_bucket, volume_bucket),
        "ct_proxy": {"delta_gap_bucket": gap_bucket},
        "reliability": _reliability(values),
        "notes": [
            "Self-only profile from target aTB features_summary.",
            "Bucket strength uses fixed quantile-calibrated cutoffs.",
            "Directions use sign with flat epsilon and do not affect bucket.",
        ],
    }
    return _trim_to_budget(profile)


__all__ = ["compute_atb_trend_profile"]


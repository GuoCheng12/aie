"""
Compact target-only structural relaxation profile from existing aTB summary fields.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from src.reasoning.atb_trend_profile import DIHEDRAL_P50, DIHEDRAL_P95, VOLUME_P25, VOLUME_P75

MAX_BYTES = 2048
DEFAULT_THRESHOLDS = {
    "atb_bonds_weak": 0.02,
    "atb_bonds_strong": 0.08,
    "atb_angles_weak": 0.2,
    "atb_angles_strong": 0.8,
}


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _bucket_abs(value: Optional[float], weak: float, strong: float) -> str:
    if value is None:
        return "unknown"
    val = abs(float(value))
    if val < float(weak):
        return "low"
    if val < float(strong):
        return "mid"
    return "high"


def _score(bucket: str) -> float:
    return {"low": 0.0, "mid": 1.0, "high": 2.0}.get(bucket, 0.0)


def _relaxation_proxy(dihedral_bucket: str, bonds_bucket: str, angles_bucket: str, volume_bucket: str) -> str:
    score = 0.4 * _score(dihedral_bucket) + 0.2 * _score(bonds_bucket) + 0.2 * _score(angles_bucket) + 0.2 * _score(volume_bucket)
    if score >= 1.5:
        return "high"
    if score >= 0.7:
        return "medium"
    return "low"


def _reliability(values: Dict[str, Optional[float]]) -> str:
    count = sum(v is not None for v in values.values())
    if count >= 4:
        return "high"
    if count >= 2:
        return "medium"
    return "low"


def _trim(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = list(out.get("notes") or [])[:1]
    return out


def compute_atb_structural_relaxation_profile(
    features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = dict(DEFAULT_THRESHOLDS)
    if isinstance(thresholds, Mapping):
        for key in cfg:
            if thresholds.get(key) is not None:
                cfg[key] = float(thresholds[key])

    values = {
        "delta_dihedral": _to_float((features_summary or {}).get("delta_dihedral")),
        "delta_bonds": _to_float((features_summary or {}).get("delta_bonds")),
        "delta_angles": _to_float((features_summary or {}).get("delta_angles")),
        "delta_volume": _to_float((features_summary or {}).get("delta_volume")),
    }

    dihedral_bucket = _bucket_abs(values["delta_dihedral"], DIHEDRAL_P50, DIHEDRAL_P95)
    bonds_bucket = _bucket_abs(values["delta_bonds"], cfg["atb_bonds_weak"], cfg["atb_bonds_strong"])
    angles_bucket = _bucket_abs(values["delta_angles"], cfg["atb_angles_weak"], cfg["atb_angles_strong"])
    volume_bucket = _bucket_abs(values["delta_volume"], VOLUME_P25, VOLUME_P75)

    profile = {
        "version": "atb_structural_relaxation_v1",
        "abs_values": {
            "delta_dihedral": abs(values["delta_dihedral"]) if values["delta_dihedral"] is not None else None,
            "delta_bonds": abs(values["delta_bonds"]) if values["delta_bonds"] is not None else None,
            "delta_angles": abs(values["delta_angles"]) if values["delta_angles"] is not None else None,
            "delta_volume": abs(values["delta_volume"]) if values["delta_volume"] is not None else None,
        },
        "buckets": {
            "delta_dihedral": dihedral_bucket,
            "delta_bonds": bonds_bucket,
            "delta_angles": angles_bucket,
            "delta_volume": volume_bucket,
        },
        "relaxation_proxy_score": _relaxation_proxy(dihedral_bucket, bonds_bucket, angles_bucket, volume_bucket),
        "reliability": _reliability(values),
        "notes": [
            "Structural relaxation combines torsion, bond, angle, and volume responses.",
            "delta_dihedral is not treated as the only structural relaxation cue.",
        ],
    }
    return _trim(profile)


__all__ = ["compute_atb_structural_relaxation_profile"]

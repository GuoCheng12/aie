"""
Target-only aTB trend projection for reasoning pack (R1+).

This module computes compact, self-relative trend evidence from target aTB summary
without using neighbor distributions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional


_MAX_BYTES = 1024
_FIELDS_USED = ["delta_dihedral", "delta_gap", "delta_volume", "excitation_energy"]


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _direction(value: Optional[float], *, flat_eps: float, mixed_label: str = "flat") -> str:
    if value is None:
        return "unknown"
    if abs(value) <= max(0.0, float(flat_eps)):
        return mixed_label
    return "increase" if value > 0 else "decrease"


def _abs_bucket(value: Optional[float], *, weak: float, strong: float) -> str:
    if value is None:
        return "unknown"
    v = abs(float(value))
    weak_t = max(0.0, float(weak))
    strong_t = max(weak_t, float(strong))
    if v < weak_t:
        return "weak"
    if v < strong_t:
        return "moderate"
    return "strong"


def _dihedral_bucket(value: Optional[float], *, none_t: float, strong_t: float) -> str:
    if value is None:
        return "unknown"
    v = abs(float(value))
    none_v = max(0.0, float(none_t))
    strong_v = max(none_v, float(strong_t))
    if v < none_v:
        return "none"
    if v < strong_v:
        return "weak"
    return "strong"


def _overall_motion_proxy(dihedral_bucket: str, volume_bucket: str) -> str:
    if dihedral_bucket == "unknown" and volume_bucket == "unknown":
        return "unknown"
    if dihedral_bucket == "strong" or volume_bucket == "strong":
        return "high"
    if dihedral_bucket == "none" and volume_bucket in {"weak", "unknown"}:
        return "low"
    return "medium"


def _reliability(values: Dict[str, Optional[float]]) -> str:
    numeric = sum(1 for key in _FIELDS_USED if values.get(key) is not None)
    if numeric == len(_FIELDS_USED):
        return "high"
    if numeric >= 3:
        return "medium"
    return "low"


def _trim_to_budget(payload: Dict[str, Any], max_bytes: int = _MAX_BYTES) -> Dict[str, Any]:
    out = dict(payload)

    def _size() -> int:
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    if _size() <= max_bytes:
        return out

    notes = out.get("notes")
    if isinstance(notes, list):
        out["notes"] = [str(x)[:96] for x in notes[:2]]
    if _size() <= max_bytes:
        return out

    out["notes"] = []
    if _size() <= max_bytes:
        return out

    out["fields_used"] = ["delta_dihedral", "delta_gap", "delta_volume"]
    return out


def compute_atb_trends_self(
    target_atb_features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Compute target-only self-trend evidence for master reasoning.
    """
    summary = dict(target_atb_features_summary or {})
    th = dict(thresholds or {})

    dihedral = _to_float(summary.get("delta_dihedral"))
    gap = _to_float(summary.get("delta_gap"))
    volume = _to_float(summary.get("delta_volume"))
    excitation = _to_float(summary.get("excitation_energy"))

    values = {
        "delta_dihedral": dihedral,
        "delta_gap": gap,
        "delta_volume": volume,
        "excitation_energy": excitation,
    }

    dihedral_abs = abs(dihedral) if dihedral is not None else None
    dihedral_bucket = _dihedral_bucket(
        dihedral,
        none_t=float(th.get("atb_dihedral_thresh_none", 8.0)),
        strong_t=float(th.get("atb_dihedral_thresh_strong", 15.0)),
    )
    dihedral_direction = _direction(
        dihedral,
        flat_eps=float(th.get("atb_dihedral_flat_eps", 1e-6)),
        mixed_label="mixed",
    )

    gap_flat_eps = float(th.get("atb_gap_flat_eps", 0.05))
    gap_direction = _direction(gap, flat_eps=gap_flat_eps, mixed_label="flat")
    gap_bucket = _abs_bucket(
        gap,
        weak=float(th.get("atb_gap_weak", 0.2)),
        strong=float(th.get("atb_gap_strong", 0.6)),
    )

    vol_flat_eps = float(th.get("atb_vol_flat_eps", 0.1))
    vol_direction = _direction(volume, flat_eps=vol_flat_eps, mixed_label="flat")
    vol_bucket = _abs_bucket(
        volume,
        weak=float(th.get("atb_vol_weak", 0.5)),
        strong=float(th.get("atb_vol_strong", 2.0)),
    )

    reliability = _reliability(values)
    overall_motion = _overall_motion_proxy(dihedral_bucket, vol_bucket)
    notes: List[str] = []
    if dihedral_bucket != "unknown":
        notes.append(f"delta_dihedral bucket={dihedral_bucket} (abs={round(dihedral_abs or 0.0, 3)} deg).")
    if gap_direction != "unknown":
        notes.append(f"delta_gap shows {gap_direction} trend with {gap_bucket} magnitude.")
    if vol_direction != "unknown":
        notes.append(f"delta_volume shows {vol_direction} trend with {vol_bucket} magnitude.")
    notes.append(f"overall_motion_proxy={overall_motion}; reliability={reliability}.")

    out = {
        "enabled": bool(reliability in {"medium", "high"}),
        "fields_used": list(_FIELDS_USED),
        "delta_dihedral_abs_deg": dihedral_abs,
        "delta_dihedral_bucket": dihedral_bucket,
        "delta_dihedral_direction": dihedral_direction,
        "delta_gap_direction": gap_direction,
        "delta_gap_bucket": gap_bucket,
        "delta_volume_direction": vol_direction,
        "delta_volume_bucket": vol_bucket,
        "overall_motion_proxy": overall_motion,
        "reliability": reliability,
        "notes": notes[:4],
    }
    return _trim_to_budget(out, max_bytes=_MAX_BYTES)


__all__ = ["compute_atb_trends_self"]


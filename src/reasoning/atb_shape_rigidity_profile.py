"""
Compact target-only shape/rigidity profile from existing aTB summary fields.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

MAX_BYTES = 2048
DEFAULT_THRESHOLDS = {
    "atb_asymmetry_weak": 0.05,
    "atb_asymmetry_strong": 0.2,
    "atb_rotconst_rel_weak": 0.05,
    "atb_rotconst_rel_strong": 0.15,
}


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _rel_change(before: Optional[float], after: Optional[float]) -> Optional[float]:
    if before is None or after is None:
        return None
    denom = abs(before) if abs(before) > 1.0e-8 else 1.0
    return abs(after - before) / denom


def _bucket_small_change(value: Optional[float], weak: float, strong: float) -> str:
    if value is None:
        return "unknown"
    if value < float(weak):
        return "high"
    if value < float(strong):
        return "medium"
    return "low"


def _rigidity_proxy(asym_bucket: str, rot_bucket: str) -> str:
    if asym_bucket == "high" and rot_bucket == "high":
        return "high"
    if asym_bucket == "low" or rot_bucket == "low":
        return "low"
    return "medium"


def _reliability(asym_change: Optional[float], rot_changes: list[Optional[float]]) -> str:
    rot_count = sum(v is not None for v in rot_changes)
    if asym_change is not None and rot_count >= 2:
        return "high"
    if asym_change is not None or rot_count >= 1:
        return "medium"
    return "low"


def _trim(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = list(out.get("notes") or [])[:1]
    return out


def compute_atb_shape_rigidity_profile(
    features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = dict(DEFAULT_THRESHOLDS)
    if isinstance(thresholds, Mapping):
        for key in cfg:
            if thresholds.get(key) is not None:
                cfg[key] = float(thresholds[key])

    s0_asym = _to_float((features_summary or {}).get("s0_rays_asymmetry_parameter"))
    s1_asym = _to_float((features_summary or {}).get("s1_rays_asymmetry_parameter"))
    asym_change = abs(s1_asym - s0_asym) if (s0_asym is not None and s1_asym is not None) else None

    rot_changes = []
    for axis in ("a", "b", "c"):
        s0_val = _to_float((features_summary or {}).get(f"s0_rotational_constant_{axis}"))
        s1_val = _to_float((features_summary or {}).get(f"s1_rotational_constant_{axis}"))
        rot_changes.append(_rel_change(s0_val, s1_val))
    present_rot = [v for v in rot_changes if v is not None]
    rot_proxy = sum(present_rot) / len(present_rot) if present_rot else None

    asym_bucket = _bucket_small_change(asym_change, cfg["atb_asymmetry_weak"], cfg["atb_asymmetry_strong"])
    rot_bucket = _bucket_small_change(rot_proxy, cfg["atb_rotconst_rel_weak"], cfg["atb_rotconst_rel_strong"])

    profile = {
        "version": "atb_shape_rigidity_v1",
        "asymmetry_change_abs": asym_change,
        "asymmetry_bucket": asym_bucket,
        "rotational_constant_change_proxy": rot_proxy,
        "rotational_constant_bucket": rot_bucket,
        "rigidity_proxy": _rigidity_proxy(asym_bucket, rot_bucket),
        "reliability": _reliability(asym_change, rot_changes),
        "notes": [
            "Rigidity proxy treats smaller asymmetry and rotational-constant changes as higher rigidity.",
            "This profile is an auxiliary axis and is interpreted alongside the other evidence axes.",
        ],
    }
    return _trim(profile)


__all__ = ["compute_atb_shape_rigidity_profile"]

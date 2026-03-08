"""
Compact target-only CT proxy profile derived from existing aTB summary fields.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

MAX_BYTES = 2048
DEFAULT_THRESHOLDS = {
    "atb_dipole_flat_eps": 0.05,
    "atb_dipole_weak": 0.2,
    "atb_dipole_strong": 0.6,
    "atb_gap_flat_eps": 0.05,
    "atb_gap_weak": 0.2,
    "atb_gap_strong": 0.6,
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


def _direction(value: Optional[float], eps: float) -> str:
    if value is None:
        return "unknown"
    val = float(value)
    if abs(val) <= float(eps):
        return "flat"
    return "increase" if val > 0 else "decrease"


def _score(bucket: str) -> float:
    return {"low": 0.0, "mid": 1.0, "high": 2.0}.get(bucket, 0.0)


def _ct_proxy_score(dipole_bucket: str, gap_bucket: str) -> str:
    score = 0.7 * _score(dipole_bucket) + 0.3 * _score(gap_bucket)
    if score >= 1.5:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def _reliability(dipole: Optional[float], gap: Optional[float]) -> str:
    count = sum(v is not None for v in (dipole, gap))
    if count == 2:
        return "high"
    if count == 1:
        return "medium"
    return "low"


def _trim(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    raw = json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) <= MAX_BYTES:
        return out
    out["notes"] = list(out.get("notes") or [])[:1]
    return out


def compute_atb_ct_proxy_profile(
    features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = dict(DEFAULT_THRESHOLDS)
    if isinstance(thresholds, Mapping):
        for key in cfg:
            if thresholds.get(key) is not None:
                cfg[key] = float(thresholds[key])

    delta_dipole = _to_float((features_summary or {}).get("delta_dipole"))
    delta_gap = _to_float((features_summary or {}).get("delta_gap"))

    dipole_bucket = _bucket_abs(delta_dipole, cfg["atb_dipole_weak"], cfg["atb_dipole_strong"])
    gap_bucket = _bucket_abs(delta_gap, cfg["atb_gap_weak"], cfg["atb_gap_strong"])
    profile = {
        "version": "atb_ct_proxy_v1",
        "delta_dipole_abs": abs(delta_dipole) if delta_dipole is not None else None,
        "delta_dipole_direction": _direction(delta_dipole, cfg["atb_dipole_flat_eps"]),
        "delta_dipole_bucket": dipole_bucket,
        "delta_gap_abs": abs(delta_gap) if delta_gap is not None else None,
        "delta_gap_direction": _direction(delta_gap, cfg["atb_gap_flat_eps"]),
        "delta_gap_bucket": gap_bucket,
        "ct_proxy_score": _ct_proxy_score(dipole_bucket, gap_bucket),
        "reliability": _reliability(delta_dipole, delta_gap),
        "notes": [
            "CT proxy combines charge-separation change and gap change from target-only aTB.",
            "delta_dipole is primary; delta_gap is supporting context.",
        ],
    }
    return _trim(profile)


__all__ = ["compute_atb_ct_proxy_profile"]

"""
Robust in-domain/out-of-domain check for target aTB deltas against neighbor aTB deltas.

This module is runtime-oriented (release orchestrator path):
- retrieval remains structure-only (ECFP),
- result is written to case risk_scores only,
- no evidence-table writeback.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_FIELDS: Tuple[str, ...] = ("delta_gap", "delta_dihedral", "delta_volume")


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def _base_result(
    *,
    fields: Sequence[str],
    min_sample_size: int,
    z_max_threshold: float,
) -> Dict[str, Any]:
    return {
        "enabled": True,
        "sample_size": 0,
        "fields_used": list(fields),
        "median": {f: None for f in fields},
        "mad": {f: None for f in fields},
        "z_scores": {f: None for f in fields},
        "outlier_score_max": None,
        "outlier_score_rss": None,
        "outlier_dims": [],
        "flag": "target_missing",
        "reliability": "low",
        "thresholds": {"z_max": float(z_max_threshold), "min_sample_size": int(min_sample_size)},
        "warnings": [],
        "updated_at": _now_iso8601(),
    }


def _extract_target_vector(target_features: Optional[Dict[str, Any]], fields: Sequence[str]) -> Optional[Dict[str, float]]:
    if not isinstance(target_features, dict):
        return None
    out: Dict[str, float] = {}
    for field in fields:
        value = _as_float(target_features.get(field))
        if value is None:
            return None
        out[field] = value
    return out


def _extract_neighbor_rows(neighbor_features: Iterable[Dict[str, Any]], fields: Sequence[str]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for row in neighbor_features:
        if not isinstance(row, dict):
            continue
        if str(row.get("cache_status") or "").lower() != "success":
            continue
        parsed: Dict[str, float] = {}
        missing = False
        for field in fields:
            value = _as_float(row.get(field))
            if value is None:
                missing = True
                break
            parsed[field] = value
        if not missing:
            rows.append(parsed)
    return rows


def _compute_reliability(
    *,
    sample_size: int,
    valid_dims: int,
    has_mad_zero: bool,
) -> str:
    if sample_size >= 15 and valid_dims >= 3 and not has_mad_zero:
        return "high"
    if sample_size >= 8 and valid_dims >= 2 and not has_mad_zero:
        return "medium"
    return "low"


def compute_atb_neighbor_consistency(
    target_features: Optional[Dict[str, Any]],
    neighbor_features: Sequence[Dict[str, Any]],
    fields: Sequence[str] = DEFAULT_FIELDS,
    min_sample_size: int = 5,
    z_max_threshold: float = 3.5,
) -> Dict[str, Any]:
    """
    Compute robust outlier signal for target aTB delta vector.

    Inputs:
    - target_features: target feature dict containing required delta fields.
      If missing/incomplete -> flag=target_missing.
    - neighbor_features: list of neighbor feature dicts. Each row should include
      `cache_status` and delta fields. Only cache_status=success + complete fields are used.
    """

    fields = tuple(fields)
    result = _base_result(fields=fields, min_sample_size=min_sample_size, z_max_threshold=z_max_threshold)

    target_vec = _extract_target_vector(target_features, fields)
    if target_vec is None:
        return result

    neighbor_rows = _extract_neighbor_rows(neighbor_features, fields)
    sample_size = len(neighbor_rows)
    result["sample_size"] = sample_size
    if sample_size < int(min_sample_size):
        result["flag"] = "insufficient_neighbors"
        return result

    valid_z_values: List[float] = []
    outlier_dims: List[str] = []
    has_mad_zero = False

    for field in fields:
        values = [row[field] for row in neighbor_rows]
        median_v = float(statistics.median(values))
        mad_v = float(statistics.median([abs(v - median_v) for v in values]))
        result["median"][field] = median_v
        result["mad"][field] = mad_v

        if mad_v == 0.0:
            has_mad_zero = True
            if target_vec[field] == median_v:
                z = 0.0
            else:
                z = None
                result["warnings"].append(f"mad_zero:{field}")
        else:
            z = float((target_vec[field] - median_v) / (1.4826 * mad_v))

        result["z_scores"][field] = z
        if z is None:
            continue
        valid_z_values.append(z)
        if abs(z) >= float(z_max_threshold):
            outlier_dims.append(field)

    if valid_z_values:
        abs_vals = [abs(v) for v in valid_z_values]
        result["outlier_score_max"] = float(max(abs_vals))
        result["outlier_score_rss"] = float(math.sqrt(sum(v * v for v in valid_z_values) / len(valid_z_values)))
    else:
        result["warnings"].append("no_valid_dims")

    result["outlier_dims"] = outlier_dims
    result["reliability"] = _compute_reliability(
        sample_size=sample_size,
        valid_dims=len(valid_z_values),
        has_mad_zero=has_mad_zero,
    )

    score_max = result["outlier_score_max"]
    if score_max is not None and float(score_max) >= float(z_max_threshold):
        result["flag"] = "outlier"
    else:
        result["flag"] = "inlier"

    return result


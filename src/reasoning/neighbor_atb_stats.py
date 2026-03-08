"""
Compact neighbor aTB distribution stats for iterative reasoning (R2+).

Design goals:
- token-friendly output (small deterministic structure),
- directionally interpretable statistics,
- stable behavior under small sample counts.

Directional conventions:
- target_percentile in [0, 1] uses mid-rank:
    (count_lt + 0.5 * count_eq) / n
  lower percentile means target sits on the lower side of neighbor distribution.
- robust z-score sign is fixed:
    z_robust = (target - neighbors_median) / (1.4826 * MAD)
  positive means target is above the neighbor median, negative means below.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ATB_DELTA_FIELDS: Tuple[str, ...] = ("delta_gap", "delta_dihedral", "delta_volume")
_ATB_OPTIONAL_FIELDS: Tuple[str, ...] = ("excitation_energy",)
_MAX_STATS_BYTES = 3072


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    n = len(xs)
    m = n // 2
    if n % 2 == 1:
        return xs[m]
    return (xs[m - 1] + xs[m]) / 2.0


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _iqr(values: Sequence[float]) -> Optional[float]:
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    if q1 is None or q3 is None:
        return None
    return q3 - q1


def _midrank_percentile(values: Sequence[float], target: Optional[float]) -> Optional[float]:
    if not values or target is None:
        return None
    n = len(values)
    count_lt = 0
    count_eq = 0
    for v in values:
        if v < target:
            count_lt += 1
        elif v == target:
            count_eq += 1
    pct = (count_lt + 0.5 * count_eq) / float(n)
    if pct < 0.0:
        return 0.0
    if pct > 1.0:
        return 1.0
    return pct


def _robust_z(
    *,
    values: Sequence[float],
    target: Optional[float],
    sample_size: int,
    min_sample_size: int,
) -> Optional[float]:
    if target is None or not values:
        return None
    if sample_size < min_sample_size:
        return None
    med = _median(values)
    if med is None:
        return None
    abs_dev = [abs(v - med) for v in values]
    mad = _median(abs_dev)
    if mad is None:
        return None
    if mad == 0:
        if target == med:
            return 0.0
        return None
    return (target - med) / (1.4826 * mad)


def neighbor_row_features_summary(row: Mapping[str, Any]) -> Dict[str, Any]:
    fs = row.get("features_summary")
    if isinstance(fs, dict):
        out = dict(fs)
    else:
        out = {}
    for key in (*ATB_DELTA_FIELDS, *_ATB_OPTIONAL_FIELDS):
        if key in row and key not in out:
            out[key] = row.get(key)
    return out


def compact_neighbor_atb_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact = dict(row)
        compact.pop("features", None)
        fs = neighbor_row_features_summary(row)
        if fs:
            compact["features_summary"] = fs
        out.append(compact)
    return out


def _resolve_label(row: Mapping[str, Any], label_lookup: Optional[Mapping[str, str]]) -> Optional[str]:
    direct = row.get("neighbor_mechanism_label")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if not isinstance(label_lookup, Mapping):
        return None
    inchikey = row.get("neighbor_inchikey")
    if isinstance(inchikey, str) and inchikey in label_lookup:
        txt = str(label_lookup.get(inchikey) or "").strip()
        if txt:
            return txt
    rank = row.get("rank")
    rank_key = f"rank:{rank}"
    if rank_key in label_lookup:
        txt = str(label_lookup.get(rank_key) or "").strip()
        if txt:
            return txt
    return None


def _field_stats(
    *,
    target: Optional[float],
    neighbors: Sequence[float],
    sample_size: int,
    min_sample_size: int,
) -> Dict[str, Optional[float]]:
    med = _median(neighbors)
    return {
        "target": target,
        "neighbors_median": med,
        "neighbors_iqr": _iqr(neighbors),
        "target_percentile": _midrank_percentile(neighbors, target),
        "z_robust": _robust_z(
            values=neighbors,
            target=target,
            sample_size=sample_size,
            min_sample_size=min_sample_size,
        ),
    }


def _reliability(sample_size: int, z_values: Iterable[Optional[float]]) -> str:
    if sample_size < 5:
        return "low"
    valid = sum(1 for z in z_values if z is not None)
    if valid < 2:
        return "low"
    if sample_size < 10:
        return "medium"
    return "high"


def _by_label_stats(
    *,
    eligible: Sequence[Dict[str, Any]],
    target_abs_dihedral: Optional[float],
    target_gap: Optional[float],
) -> Dict[str, Dict[str, Any]]:
    raw: Dict[str, List[Dict[str, Any]]] = {}
    for row in eligible:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        raw.setdefault(label, []).append(row)

    # Only keep labels with n>=2 and only emit if at least two such labels exist.
    filtered = {k: v for k, v in raw.items() if len(v) >= 2}
    if len(filtered) < 2:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for label, rows in sorted(filtered.items(), key=lambda kv: kv[0]):
        abs_vals = [float(r["abs_delta_dihedral"]) for r in rows]
        gap_vals = [float(r["delta_gap"]) for r in rows]
        out[label] = {
            "n": len(rows),
            "median_abs_delta_dihedral": _median(abs_vals),
            "iqr_abs_delta_dihedral": _iqr(abs_vals),
            "percentile_of_target_abs_delta_dihedral": _midrank_percentile(abs_vals, target_abs_dihedral),
            "median_delta_gap": _median(gap_vals),
            "iqr_delta_gap": _iqr(gap_vals),
            "percentile_of_target_delta_gap": _midrank_percentile(gap_vals, target_gap),
        }
    return out


def _closest_label_by_field(
    *,
    by_label: Mapping[str, Mapping[str, Any]],
    target_abs_dihedral: Optional[float],
    target_gap: Optional[float],
) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def _closest(target: Optional[float], key: str) -> str:
        if target is None or not by_label:
            return "unknown"
        best_label = "unknown"
        best_delta: Optional[float] = None
        for label in sorted(by_label.keys()):
            med = _to_float((by_label.get(label) or {}).get(key))
            if med is None:
                continue
            delta = abs(target - med)
            if best_delta is None or delta < best_delta or (delta == best_delta and label < best_label):
                best_delta = delta
                best_label = label
        return best_label if best_delta is not None else "unknown"

    out["abs_delta_dihedral"] = _closest(target_abs_dihedral, "median_abs_delta_dihedral")
    out["delta_gap"] = _closest(target_gap, "median_delta_gap")
    return out


def _separation_score(by_label: Mapping[str, Mapping[str, Any]]) -> float:
    # Deterministic, bounded [0,1] score from label-conditioned abs_delta_dihedral separation.
    labels = sorted(by_label.keys())
    if len(labels) < 2:
        return 0.0
    meds: List[float] = []
    iqrs: List[float] = []
    for label in labels:
        med = _to_float((by_label.get(label) or {}).get("median_abs_delta_dihedral"))
        if med is not None:
            meds.append(med)
        iqr = _to_float((by_label.get(label) or {}).get("iqr_abs_delta_dihedral"))
        if iqr is not None and iqr > 0:
            iqrs.append(iqr)
    if len(meds) < 2:
        return 0.0
    spread = max(meds) - min(meds)
    scale = _median(iqrs) if iqrs else 0.0
    if scale is None or scale <= 0:
        # If spread exists but IQR unavailable, keep small conservative score.
        return round(min(1.0, spread / 10.0), 4)
    raw = spread / max(scale, 1e-9)
    score = raw / (1.0 + raw)
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return round(float(score), 4)


def _summary_lines(
    *,
    abs_dihedral_stats: Mapping[str, Optional[float]],
    by_label: Mapping[str, Mapping[str, Any]],
    closest: Mapping[str, str],
    reliability: str,
) -> List[str]:
    out: List[str] = []
    pct = abs_dihedral_stats.get("target_percentile")
    if isinstance(pct, (int, float)):
        pct100 = int(round(float(pct) * 100))
        if pct <= 0.2:
            out.append(f"Target abs_delta_dihedral is in the lower {pct100}% of neighbor distribution.")
        elif pct >= 0.8:
            out.append(f"Target abs_delta_dihedral is in the upper {pct100}% of neighbor distribution.")
        else:
            out.append(f"Target abs_delta_dihedral is near the middle ({pct100}%) of neighbor distribution.")

    close_abs = str(closest.get("abs_delta_dihedral") or "unknown")
    if close_abs != "unknown" and by_label:
        out.append(f"Target abs_delta_dihedral is closest to label group {close_abs}.")

    out.append(f"Neighbor comparative reliability is {reliability}.")
    return out[:3]


def _stats_size_bytes(obj: Dict[str, Any]) -> int:
    return len(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def _trim_by_label(by_label: Mapping[str, Dict[str, Any]], keep: int) -> Dict[str, Dict[str, Any]]:
    rows = sorted(
        [(k, v) for k, v in by_label.items() if isinstance(v, dict)],
        key=lambda kv: (-int(kv[1].get("n") or 0), str(kv[0])),
    )
    out: Dict[str, Dict[str, Any]] = {}
    for label, row in rows[: max(0, int(keep))]:
        out[str(label)] = dict(row)
    return out


def _trim_for_budget(stats: Dict[str, Any], budget_bytes: int = _MAX_STATS_BYTES) -> Dict[str, Any]:
    out = dict(stats)
    if _stats_size_bytes(out) < budget_bytes:
        return out

    by_label = out.get("by_label") if isinstance(out.get("by_label"), dict) else {}
    if by_label:
        out["by_label"] = _trim_by_label(by_label, keep=2)
    if _stats_size_bytes(out) < budget_bytes:
        return out

    summary = out.get("summary")
    if isinstance(summary, list):
        out["summary"] = [str(x) for x in summary[:3]]
    if _stats_size_bytes(out) < budget_bytes:
        return out

    fields = out.get("fields") if isinstance(out.get("fields"), dict) else {}
    if fields:
        keep_fields = ["delta_dihedral", "abs_delta_dihedral", "delta_gap"]
        out["fields"] = {k: fields.get(k) for k in keep_fields if k in fields}
    if _stats_size_bytes(out) < budget_bytes:
        return out

    if isinstance(out.get("by_label"), dict):
        compact_by_label: Dict[str, Dict[str, Any]] = {}
        for label, row in sorted(out["by_label"].items()):
            if not isinstance(row, dict):
                continue
            compact_by_label[str(label)] = {
                "n": row.get("n"),
                "median_abs_delta_dihedral": row.get("median_abs_delta_dihedral"),
            }
        out["by_label"] = compact_by_label

    # Last resort: keep only essential keys while preserving discriminative summary.
    if _stats_size_bytes(out) >= budget_bytes:
        out = {
            "sample_size": out.get("sample_size"),
            "reliability": out.get("reliability"),
            "separation_score": out.get("separation_score"),
            "fields": out.get("fields"),
            "by_label": out.get("by_label"),
            "summary": out.get("summary"),
            "closest_label_by_field": out.get("closest_label_by_field"),
        }
    return out


def compute_neighbor_atb_stats_by_label(
    *,
    target_features_summary: Mapping[str, Any],
    neighbor_atb_features_all: Sequence[Mapping[str, Any]],
    neighbor_label_lookup: Optional[Mapping[str, str]] = None,
    min_sample_size_for_z: int = 5,
) -> Dict[str, Any]:
    """
    Compute compact target-vs-neighbor aTB distribution stats.

    Inputs:
    - target_features_summary: target aTB summary dict with delta fields.
    - neighbor_atb_features_all: neighbor rows; only rows with cache_status=success and
      complete delta fields are included.
    - neighbor_label_lookup: optional map for missing row labels, keyed by neighbor_inchikey
      or rank key format `rank:<int>`.
    """
    target = dict(target_features_summary or {})
    compact_rows = compact_neighbor_atb_rows(list(neighbor_atb_features_all or []))

    eligible: List[Dict[str, Any]] = []
    for row in compact_rows:
        if str(row.get("cache_status") or "").lower() != "success":
            continue
        fs = neighbor_row_features_summary(row)
        vals = {f: _to_float(fs.get(f)) for f in ATB_DELTA_FIELDS}
        if any(v is None for v in vals.values()):
            continue
        dihedral = float(vals["delta_dihedral"])
        eligible.append(
            {
                "row": row,
                "features_summary": fs,
                "delta_gap": float(vals["delta_gap"]),
                "delta_dihedral": dihedral,
                "delta_volume": float(vals["delta_volume"]),
                "abs_delta_dihedral": abs(dihedral),
                "label": _resolve_label(row, neighbor_label_lookup),
            }
        )

    n = len(eligible)
    field_values = {
        "delta_dihedral": [r["delta_dihedral"] for r in eligible],
        "abs_delta_dihedral": [r["abs_delta_dihedral"] for r in eligible],
        "delta_gap": [r["delta_gap"] for r in eligible],
        "delta_volume": [r["delta_volume"] for r in eligible],
    }

    target_dihedral = _to_float(target.get("delta_dihedral"))
    target_abs_dihedral = abs(target_dihedral) if target_dihedral is not None else None
    target_gap = _to_float(target.get("delta_gap"))
    target_volume = _to_float(target.get("delta_volume"))

    fields = {
        "delta_dihedral": _field_stats(
            target=target_dihedral,
            neighbors=field_values.get("delta_dihedral") or [],
            sample_size=n,
            min_sample_size=min_sample_size_for_z,
        ),
        "abs_delta_dihedral": _field_stats(
            target=target_abs_dihedral,
            neighbors=field_values.get("abs_delta_dihedral") or [],
            sample_size=n,
            min_sample_size=min_sample_size_for_z,
        ),
        "delta_gap": _field_stats(
            target=target_gap,
            neighbors=field_values.get("delta_gap") or [],
            sample_size=n,
            min_sample_size=min_sample_size_for_z,
        ),
        "delta_volume": _field_stats(
            target=target_volume,
            neighbors=field_values.get("delta_volume") or [],
            sample_size=n,
            min_sample_size=min_sample_size_for_z,
        ),
    }

    if n < min_sample_size_for_z:
        for key in list(fields.keys()):
            fields[key]["z_robust"] = None

    by_label = _by_label_stats(
        eligible=eligible,
        target_abs_dihedral=target_abs_dihedral,
        target_gap=target_gap,
    )

    rel = _reliability(
        n,
        (
            fields.get("abs_delta_dihedral", {}).get("z_robust"),
            fields.get("delta_gap", {}).get("z_robust"),
            fields.get("delta_volume", {}).get("z_robust"),
        ),
    )

    closest = _closest_label_by_field(
        by_label=by_label,
        target_abs_dihedral=target_abs_dihedral,
        target_gap=target_gap,
    )
    separation_score = _separation_score(by_label)

    out = {
        "sample_size": n,
        "reliability": rel,
        "fields": fields,
        "by_label": by_label,
        "closest_label_by_field": closest,
        "separation_score": separation_score,
        "summary": _summary_lines(
            abs_dihedral_stats=fields.get("abs_delta_dihedral", {}),
            by_label=by_label,
            closest=closest,
            reliability=rel,
        ),
    }
    return _trim_for_budget(out, budget_bytes=_MAX_STATS_BYTES)


def compute_neighbor_atb_stats(
    *,
    target_features_summary: Mapping[str, Any],
    neighbor_atb_features_all: Sequence[Mapping[str, Any]],
    neighbor_label_lookup: Optional[Mapping[str, str]] = None,
    min_sample_size_for_z: int = 5,
) -> Dict[str, Any]:
    # Backward-compatible alias.
    return compute_neighbor_atb_stats_by_label(
        target_features_summary=target_features_summary,
        neighbor_atb_features_all=neighbor_atb_features_all,
        neighbor_label_lookup=neighbor_label_lookup,
        min_sample_size_for_z=min_sample_size_for_z,
    )


__all__ = [
    "ATB_DELTA_FIELDS",
    "compact_neighbor_atb_rows",
    "compute_neighbor_atb_stats",
    "compute_neighbor_atb_stats_by_label",
    "neighbor_row_features_summary",
]

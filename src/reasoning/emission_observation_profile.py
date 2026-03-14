"""
Compact target-side emission observation profile for R1+ reasoning packs.

This module summarizes direct emission observations from the case file into a
small, mechanism-agnostic profile. It describes only observed values and their
relationship; it does not encode mechanism-specific interpretations.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _has_required_provenance(prov: Mapping[str, Any]) -> bool:
    source_ref = str(prov.get("source_ref") or "").strip()
    source_locator = str(prov.get("source_locator") or "").strip()
    confidence = _to_float(prov.get("confidence"))
    return bool(source_ref and source_locator and confidence is not None)


def compute_emission_observation_profile(
    target_fields: Mapping[str, Any],
    target_fields_provenance: Mapping[str, Any],
) -> Dict[str, Any]:
    aggr = _to_float(target_fields.get("emission_aggr_nm"))
    solid = _to_float(target_fields.get("emission_solid_or_film_nm"))

    if aggr is not None and solid is not None:
        coverage = "both"
    elif aggr is not None:
        coverage = "aggr_only"
    elif solid is not None:
        coverage = "solid_only"
    else:
        coverage = "none"

    shift_nm: Optional[float] = None
    shift_direction = "unknown"
    shift_bucket = "unknown"
    if aggr is not None and solid is not None:
        shift_nm = round(float(solid - aggr), 4)
        abs_shift = abs(shift_nm)
        if abs_shift < 10.0:
            shift_direction = "flat"
            shift_bucket = "small"
        elif shift_nm >= 10.0:
            shift_direction = "red_shift"
            shift_bucket = "moderate" if abs_shift < 40.0 else "large"
        else:
            shift_direction = "blue_shift"
            shift_bucket = "moderate" if abs_shift < 40.0 else "large"

    aggr_prov = target_fields_provenance.get("emission_aggr_nm")
    solid_prov = target_fields_provenance.get("emission_solid_or_film_nm")
    aggr_ok = isinstance(aggr_prov, Mapping) and _has_required_provenance(aggr_prov)
    solid_ok = isinstance(solid_prov, Mapping) and _has_required_provenance(solid_prov)
    if coverage == "both" and aggr_ok and solid_ok:
        reliability = "high"
    elif coverage in {"aggr_only", "solid_only", "both"}:
        reliability = "medium"
    else:
        reliability = "low"

    notes = []
    if coverage == "both":
        notes.append(f"Both aggregate and solid-state emission observations are present; shift is {shift_direction}.")
    elif coverage == "aggr_only":
        notes.append("Only aggregate-state emission observation is present.")
    elif coverage == "solid_only":
        notes.append("Only solid/film emission observation is present.")
    else:
        notes.append("No direct emission observation is present.")
    notes.append(f"Observation coverage is {coverage} with {reliability} reliability.")

    return {
        "version": "emission_observation_v1",
        "coverage": coverage,
        "emission_aggr_nm": aggr,
        "emission_solid_or_film_nm": solid,
        "shift_nm": shift_nm,
        "shift_direction": shift_direction,
        "shift_magnitude_bucket": shift_bucket,
        "reliability": reliability,
        "notes": notes[:3],
    }


__all__ = ["compute_emission_observation_profile"]

"""
Deprecated compatibility wrapper for the old CT-proxy profile name.

The underlying semantics now live in `charge_redistribution_profile`. This
module preserves one-version compatibility for existing pack fields and tests.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from src.reasoning.charge_redistribution_profile import compute_charge_redistribution_profile


def compute_atb_ct_proxy_profile(
    features_summary: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    base = compute_charge_redistribution_profile(features_summary, thresholds=thresholds)
    delta_dipole_abs = base.get("max_abs_atom_variation")
    delta_dipole_bucket = base.get("redistribution_magnitude_bucket")
    if base.get("source") == "atomwise_charge_variation":
        delta_dipole_abs = base.get("total_abs_charge_variation")
        delta_dipole_bucket = base.get("redistribution_magnitude_bucket")
    elif base.get("source") == "scalar_delta_dipole":
        delta_dipole_abs = base.get("max_abs_atom_variation")
    return {
        "version": "atb_ct_proxy_v1",
        "delta_dipole_abs": delta_dipole_abs,
        "delta_dipole_direction": "unknown",
        "delta_dipole_bucket": delta_dipole_bucket,
        "delta_gap_abs": base.get("delta_gap_abs"),
        "delta_gap_direction": base.get("delta_gap_direction"),
        "delta_gap_bucket": base.get("delta_gap_bucket"),
        "ct_proxy_score": base.get("redistribution_score"),
        "reliability": base.get("reliability"),
        "notes": [
            "Deprecated alias of the generic electronic redistribution profile.",
            "Not a true dipole-moment interpretation.",
        ],
    }


__all__ = ["compute_atb_ct_proxy_profile"]

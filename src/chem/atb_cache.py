"""
src/chem/atb_cache.py

Shared helpers for reading aTB cache artifacts and summarizing status/features.
Used by cache-to-parquet build and SMILES-first case files (single source of truth).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.cases.case_schema import KEY_ATB_FIELDS, AtbCacheStatus


DEFAULT_CACHE_DIR = "cache/atb"

# Stable numeric fields to extract into atb_features.parquet
ATB_FEATURE_FIELDS = [
    "delta_volume",
    "delta_gap",
    "delta_dihedral",
    "delta_bonds",
    "delta_angles",
    "excitation_energy",
    "exciting_path_mean_volume",
    "s0_volume",
    "s1_volume",
    "s0_homo_lumo_gap",
    "s1_homo_lumo_gap",
    "s0_dihedral_avg",
    "s1_dihedral_avg",
    "s0_rays_asymmetry_parameter",
    "s1_rays_asymmetry_parameter",
    "s0_rotational_constant_a",
    "s0_rotational_constant_b",
    "s0_rotational_constant_c",
    "s1_rotational_constant_a",
    "s1_rotational_constant_b",
    "s1_rotational_constant_c",
    "s0_charge_dipole",
    "s1_charge_dipole",
    "delta_dipole",
]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Read JSON file safely; return None if missing or invalid."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def get_cache_paths(inchikey: str, cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, Path]:
    """Return cache paths for a given inchikey."""
    prefix = inchikey[:2]
    base = Path(cache_dir) / prefix / inchikey
    return {
        "cache_dir": base,
        "status_path": base / "status.json",
        "features_path": base / "features.json",
        "smiles_path": base / "canonical_smiles.txt",
    }


def extract_features_summary(features: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Build a lightweight features_summary for case files.

    Returns:
        (summary, missing_fields)
    """
    summary: Dict[str, Any] = {}
    missing: List[str] = []

    for key in KEY_ATB_FIELDS:
        val = features.get(key)
        if val is None:
            missing.append(key)
            continue
        try:
            summary[key] = float(val)
            if key == "excitation_energy":
                summary["_excitation_energy_raw"] = str(val)
        except (TypeError, ValueError):
            missing.append(key)

    # Optional stable fields useful for reasoning but not required for gate.
    optional_fields = [
        "s0_volume",
        "s1_volume",
        "s0_charge_dipole",
        "s1_charge_dipole",
        "delta_dipole",
        "delta_bonds",
        "delta_angles",
        "s0_rays_asymmetry_parameter",
        "s1_rays_asymmetry_parameter",
        "s0_rotational_constant_a",
        "s0_rotational_constant_b",
        "s0_rotational_constant_c",
        "s1_rotational_constant_a",
        "s1_rotational_constant_b",
        "s1_rotational_constant_c",
    ]
    for key in optional_fields:
        val = features.get(key)
        if val is not None:
            try:
                summary[key] = float(val)
            except (TypeError, ValueError):
                pass

    neb = features.get("exciting_path_mean_volume")
    if neb is None:
        neb = features.get("neb_mean_volume")
    if neb is not None:
        try:
            summary["exciting_path_mean_volume"] = float(neb)
        except (TypeError, ValueError):
            pass

    return (summary if summary else None), missing


def extract_numeric_features(features: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Extract stable numeric fields for atb_features.parquet."""
    row: Dict[str, Optional[float]] = {}
    for key in ATB_FEATURE_FIELDS:
        val = features.get(key)
        if val is None:
            row[key] = None
            continue
        try:
            row[key] = float(val)
        except (TypeError, ValueError):
            row[key] = None
    return row


def compute_cache_status(
    run_status: Optional[str],
    has_features_json: bool,
    missing_fields: List[str],
) -> str:
    """
    Compute cache_status from run_status + feature completeness.

    Rules:
    - success requires all key fields present
    - partial if some key fields missing but features exist
    - failed if no key fields and run_status indicates failure
    - pending if run_status == pending
    - absent if no cache artifacts
    """
    if run_status == AtbCacheStatus.PENDING.value:
        return AtbCacheStatus.PENDING.value

    if run_status == AtbCacheStatus.SUCCESS.value:
        if not has_features_json:
            return AtbCacheStatus.PARTIAL.value
        return AtbCacheStatus.SUCCESS.value if len(missing_fields) == 0 else AtbCacheStatus.PARTIAL.value

    if run_status in {AtbCacheStatus.FAILED.value, "skipped"}:
        if has_features_json and len(missing_fields) < len(KEY_ATB_FIELDS):
            return AtbCacheStatus.PARTIAL.value
        return AtbCacheStatus.FAILED.value

    if not run_status:
        return AtbCacheStatus.PARTIAL.value if has_features_json else AtbCacheStatus.ABSENT.value

    # Fallback
    return run_status if run_status in {
        AtbCacheStatus.ABSENT.value,
        AtbCacheStatus.PENDING.value,
        AtbCacheStatus.SUCCESS.value,
        AtbCacheStatus.PARTIAL.value,
        AtbCacheStatus.FAILED.value,
    } else AtbCacheStatus.ABSENT.value


def get_atb_cache_record(
    inchikey: Optional[str],
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> Dict[str, Any]:
    """
    Load cache status + features for an inchikey.

    Returns:
        dict with cache_status, has_features_json, keyfield_complete, missing_fields,
        status metadata, and optional features_summary/features_raw.
    """
    if not inchikey:
        return {
            "cache_status": AtbCacheStatus.ABSENT.value,
            "has_features_json": False,
            "keyfield_complete": False,
            "missing_fields": [],
            "status": None,
            "features": None,
            "features_summary": None,
        }

    paths = get_cache_paths(inchikey, cache_dir=cache_dir)
    status = _read_json(paths["status_path"])
    features = _read_json(paths["features_path"])

    has_features_json = features is not None
    features_summary, missing_fields = (None, [])
    if features is not None:
        features_summary, missing_fields = extract_features_summary(features)

    run_status = status.get("run_status") if status else None
    cache_status = compute_cache_status(run_status, has_features_json, missing_fields)

    keyfield_complete = has_features_json and len(missing_fields) == 0

    return {
        "cache_status": cache_status,
        "has_features_json": has_features_json,
        "keyfield_complete": keyfield_complete,
        "missing_fields": missing_fields,
        "status": status,
        "features": features,
        "features_summary": features_summary,
    }


def get_atb_cache_status(inchikey: Optional[str], cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    """Public helper for cache_status lookup."""
    return get_atb_cache_record(inchikey, cache_dir=cache_dir)["cache_status"]


def get_atb_features_summary(
    inchikey: Optional[str],
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Public helper for features_summary lookup."""
    record = get_atb_cache_record(inchikey, cache_dir=cache_dir)
    return record.get("features_summary"), record.get("missing_fields", [])


def get_atb_evidence_pack(
    inchikey: Optional[str],
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> Dict[str, Any]:
    """
    Build evidence pack for case file neighbor/target.

    Returns:
        {cache_status, missing_fields?, features_summary?}
    """
    record = get_atb_cache_record(inchikey, cache_dir=cache_dir)
    cache_status = record["cache_status"]
    pack: Dict[str, Any] = {"cache_status": cache_status}

    if record["features_summary"] is not None:
        pack["features_summary"] = record["features_summary"]
    if record["missing_fields"]:
        pack["missing_fields"] = record["missing_fields"]

    return pack

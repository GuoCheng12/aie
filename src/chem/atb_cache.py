"""
src/chem/atb_cache.py

Shared helpers for reading aTB cache artifacts and summarizing status/features.
Used by cache-to-parquet build and SMILES-first case files (single source of truth).
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.cases.case_schema import KEY_ATB_FIELDS, AtbCacheStatus
from src.chem.atb_aop_compact import extract_aop_compact


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
    "charge_redis_total_abs",
    "charge_redis_max_abs_atom",
    "charge_redis_top3_abs_share",
    "charge_redis_heteroatom_abs_share",
    "charge_redis_n_atoms_ge_0p01",
    "charge_redis_n_atoms_ge_0p02",
    "s0_perm_dipole_tot_debye",
    "s1_perm_dipole_tot_debye",
    "delta_perm_dipole_tot_debye",
    "s1_transition_electric_dip_au",
    "s1_transition_magnetic_dip_norm_au",
    "s1_rotatory_strength_cgs",
    "s1_oscillator_strength_f",
    "s1_excitation_wavelength_nm",
    "aop_compact_reliability_score",
]

_AOP_RELIABILITY_SCORE = {"low": 0.0, "medium": 1.0, "high": 2.0}


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _summarize_charge_redistribution(value: Any) -> Dict[str, float]:
    """
    Compress atomwise charge variation into compact scalar summaries.

    Expected raw shape:
        {"element": [...], "charge_variation": [...]}

    Returns an empty dict for missing/malformed inputs.
    """
    if not isinstance(value, dict):
        return {}
    elements = value.get("element")
    variations = value.get("charge_variation")
    if not isinstance(elements, list) or not isinstance(variations, list):
        return {}
    if not elements or not variations or len(elements) != len(variations):
        return {}

    rows: List[Tuple[str, float]] = []
    for element, delta_q in zip(elements, variations):
        val = _to_float(delta_q)
        if val is None:
            continue
        rows.append((str(element or "").strip(), abs(val)))
    if not rows:
        return {}

    total_abs = sum(val for _, val in rows)
    if total_abs <= 0.0:
        return {
            "charge_redis_total_abs": 0.0,
            "charge_redis_max_abs_atom": 0.0,
            "charge_redis_top3_abs_share": 0.0,
            "charge_redis_heteroatom_abs_share": 0.0,
            "charge_redis_n_atoms_ge_0p01": 0.0,
            "charge_redis_n_atoms_ge_0p02": 0.0,
        }

    sorted_abs = sorted((val for _, val in rows), reverse=True)
    top3_abs = sum(sorted_abs[:3])
    hetero_abs = sum(val for element, val in rows if element.upper() not in {"C", "H"})
    n_atoms_ge_0p01 = sum(1 for _, val in rows if val >= 0.01)
    n_atoms_ge_0p02 = sum(1 for _, val in rows if val >= 0.02)
    return {
        "charge_redis_total_abs": float(total_abs),
        "charge_redis_max_abs_atom": float(sorted_abs[0]),
        "charge_redis_top3_abs_share": float(top3_abs / total_abs),
        "charge_redis_heteroatom_abs_share": float(hetero_abs / total_abs),
        "charge_redis_n_atoms_ge_0p01": float(n_atoms_ge_0p01),
        "charge_redis_n_atoms_ge_0p02": float(n_atoms_ge_0p02),
    }


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Read JSON file safely; return None if missing or invalid."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _load_or_build_aop_compact(cache_path: Path) -> Optional[Dict[str, Any]]:
    compact_path = cache_path / "aop_compact.json"
    payload = _read_json(compact_path)
    if isinstance(payload, dict):
        return payload

    opt_path = cache_path / "opt" / "opt_run.aop"
    excit_path = cache_path / "excit" / "excit_run.aop"
    if not opt_path.exists() and not excit_path.exists():
        return None

    try:
        payload = extract_aop_compact(cache_path)
    except Exception:
        return None

    try:
        with open(compact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # Lazy writeback should never block the main read path.
        pass
    return payload if isinstance(payload, dict) else None


def _extract_aop_compact_scalars(aop_compact: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not isinstance(aop_compact, dict):
        return {}

    s0 = aop_compact.get("s0_permanent_dipole_debye") or {}
    s1 = aop_compact.get("s1_permanent_dipole_debye") or {}
    trans_e = aop_compact.get("s1_transition_electric_dipole_au") or {}
    trans_m = aop_compact.get("s1_transition_magnetic_dipole_au") or {}
    s1_exc = aop_compact.get("s1_excitation") or {}

    mx = _to_float(trans_m.get("x"))
    my = _to_float(trans_m.get("y"))
    mz = _to_float(trans_m.get("z"))
    mag_norm: Optional[float] = None
    if mx is not None and my is not None and mz is not None:
        mag_norm = math.sqrt((mx * mx) + (my * my) + (mz * mz))

    values = {
        "s0_perm_dipole_tot_debye": _to_float(s0.get("tot")),
        "s1_perm_dipole_tot_debye": _to_float(s1.get("tot")),
        "delta_perm_dipole_tot_debye": _to_float(aop_compact.get("delta_permanent_dipole_tot_debye")),
        "s1_transition_electric_dip_au": _to_float(trans_e.get("dip")),
        "s1_transition_magnetic_dip_norm_au": mag_norm,
        "s1_rotatory_strength_cgs": _to_float(aop_compact.get("s1_rotatory_strength_cgs")),
        "s1_oscillator_strength_f": _to_float(s1_exc.get("oscillator_strength_f")),
        "s1_excitation_wavelength_nm": _to_float(s1_exc.get("wavelength_nm")),
    }
    out: Dict[str, float] = {}
    for key, val in values.items():
        if val is not None:
            out[key] = float(val)

    reliability = str(aop_compact.get("reliability") or "").strip().lower()
    if reliability in _AOP_RELIABILITY_SCORE:
        out["aop_compact_reliability_score"] = float(_AOP_RELIABILITY_SCORE[reliability])
    return out


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


def extract_features_summary(
    features: Dict[str, Any],
    *,
    aop_compact: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Build a lightweight features_summary for case files.

    Returns:
        (summary, missing_fields)
    """
    summary: Dict[str, Any] = {}
    missing: List[str] = []
    charge_redis_summary = _summarize_charge_redistribution(features.get("delta_dipole"))
    summary.update(charge_redis_summary)
    summary.update(_extract_aop_compact_scalars(aop_compact))

    for key in KEY_ATB_FIELDS:
        val = features.get(key)
        if val is None:
            missing.append(key)
            continue
        if key == "delta_dipole" and isinstance(val, dict):
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
            if key == "delta_dipole" and isinstance(val, dict):
                continue
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


def extract_numeric_features(
    features: Dict[str, Any],
    *,
    aop_compact: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[float]]:
    """Extract stable numeric fields for atb_features.parquet."""
    row: Dict[str, Optional[float]] = {}
    charge_redis_summary = _summarize_charge_redistribution(features.get("delta_dipole"))
    aop_scalars = _extract_aop_compact_scalars(aop_compact)
    for key in ATB_FEATURE_FIELDS:
        if key in charge_redis_summary:
            row[key] = charge_redis_summary[key]
            continue
        if key in aop_scalars:
            row[key] = aop_scalars[key]
            continue
        val = features.get(key)
        if val is None:
            row[key] = None
            continue
        if key == "delta_dipole" and isinstance(val, dict):
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
    aop_compact = _load_or_build_aop_compact(paths["cache_dir"])

    has_features_json = features is not None
    features_summary, missing_fields = (None, [])
    if features is not None:
        features_summary, missing_fields = extract_features_summary(features, aop_compact=aop_compact)

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
        "aop_compact": aop_compact,
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

"""
src/graph/build_evidence_table_v1_p1.py

V1-P1: Build data/evidence_table.parquet from existing sources only (no literature yet).

Inputs:
- data/private_clean.parquet
- data/atb_features.parquet
- data/atb_qc.parquet

Outputs:
- data/evidence_table.parquet
- data/evidence_table_build_manifest.json

Usage:
    python -m src.graph.build_evidence_table_v1_p1
"""

import argparse
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger


logger = get_logger(__name__)


EVIDENCE_TYPES = {"private_observation", "atb_computation", "literature_claim"}
SOURCE_TYPES = {"private_db", "atb_cache", "paper_doi"}
CONDITION_STATES = {"sol", "solid", "aggr", "crys", "unknown"}

QUALITY_FLAGS = {
    "OK",
    "OUT_OF_RANGE_NEGATIVE",
    "OUT_OF_RANGE_GT1",
    "OUTLIER_TAU_EXTREME",
    "OUT_OF_RANGE_NONPOSITIVE",
    "PARSE_WARNING",
}


PRIVATE_FIELDS = [
    "absorption",
    "absorption_peak_nm",
    "emission_sol",
    "emission_solid",
    "emission_aggr",
    "emission_crys",
    "qy_sol",
    "qy_solid",
    "qy_aggr",
    "qy_crys",
    "tau_sol",
    "tau_solid",
    "tau_aggr",
    "tau_crys",
    "tested_solvent",
]


PRIVATE_UNITS: Dict[str, Optional[str]] = {
    "absorption": None,
    "absorption_peak_nm": "nm",
    "emission_sol": "nm",
    "emission_solid": "nm",
    "emission_aggr": "nm",
    "emission_crys": "nm",
    "qy_sol": "fraction",
    "qy_solid": "fraction",
    "qy_aggr": "fraction",
    "qy_crys": "fraction",
    "tau_sol": "ns",
    "tau_solid": "ns",
    "tau_aggr": "ns",
    "tau_crys": "ns",
    "tested_solvent": None,
}


ATB_UNITS: Dict[str, Optional[str]] = {
    "delta_volume": "A^3",
    "s0_volume": "A^3",
    "s1_volume": "A^3",
    "delta_gap": "eV",
    "s0_homo_lumo_gap": "eV",
    "s1_homo_lumo_gap": "eV",
    "excitation_energy": "eV",
    "delta_dihedral": "deg",
    "s0_dihedral_avg": "deg",
    "s1_dihedral_avg": "deg",
    # Dipole units are tool-dependent; keep null unless standardized later.
    "s0_charge_dipole": None,
    "s1_charge_dipole": None,
    "delta_dipole": None,
}


NAMESPACE_EVIDENCE = uuid.UUID("2b1d3f7e-2b8b-4e70-9c7c-4b7a4b00a2b9")


def now_iso() -> str:
    return datetime.now().isoformat()


def norm_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    s = str(val).strip()
    return s if s != "" else None


def safe_float(val: Any) -> Tuple[Optional[float], bool]:
    """Return (float_or_none, parse_ok)."""
    if val is None:
        return None, True
    if isinstance(val, float) and np.isnan(val):
        return None, True
    try:
        return float(val), True
    except Exception:
        return None, False


def make_evidence_id(
    evidence_type: str,
    source_type: str,
    source_id: str,
    field: str,
    condition_state: str,
    condition_solvent: str,
) -> str:
    key = f"{evidence_type}|{source_type}|{source_id}|{field}|{condition_state}|{condition_solvent}"
    return str(uuid.uuid5(NAMESPACE_EVIDENCE, key))


def infer_condition_state(field: str) -> str:
    if field in {"absorption", "absorption_peak_nm"}:
        return "sol"
    for prefix in ("emission_", "qy_", "tau_"):
        if field.startswith(prefix):
            suffix = field[len(prefix):]
            if suffix in {"sol", "solid", "aggr", "crys"}:
                return suffix
    return "unknown"


def infer_condition_solvent(tested_solvent: Any, condition_state: str, field: str) -> str:
    solvent = norm_str(tested_solvent)
    if field == "tested_solvent":
        return solvent or "unknown"
    if condition_state == "sol":
        return solvent or "unknown"
    return "unknown"


def build_private_observations(
    private_clean: pd.DataFrame,
    build_timestamp: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    counts_by_field: Dict[str, int] = defaultdict(int)
    parse_fail_by_field: Dict[str, int] = defaultdict(int)
    invalid_samples: List[Dict[str, Any]] = []

    for _, r in private_clean.iterrows():
        record_id = r.get("id")
        source_id = str(int(record_id)) if record_id is not None and not pd.isna(record_id) else "unknown_record"
        inchikey = r.get("inchikey")

        for field in PRIVATE_FIELDS:
            val = r.get(field)
            raw = norm_str(val)
            if raw is None:
                continue

            condition_state = infer_condition_state(field)
            condition_solvent = infer_condition_solvent(r.get("tested_solvent"), condition_state, field)
            unit = PRIVATE_UNITS.get(field)

            value_num: Optional[float] = None
            parse_ok = True
            if field == "absorption_peak_nm":
                # Enforce float-parsable value string for absorption_peak_nm
                value_num, parse_ok = safe_float(val)
                if value_num is None or not parse_ok:
                    parse_fail_by_field[field] += 1
                    if len(invalid_samples) < 50:
                        invalid_samples.append({
                            "evidence_type": "private_observation",
                            "source_id": source_id,
                            "field": field,
                            "reason": "absorption_peak_nm_parse_failed",
                            "value": raw,
                        })
                    continue
                raw = str(value_num)
                unit = "nm"
            elif field not in {"absorption", "tested_solvent"}:
                value_num, parse_ok = safe_float(val)
                if not parse_ok:
                    parse_fail_by_field[field] += 1
                    if len(invalid_samples) < 50:
                        invalid_samples.append({
                            "evidence_type": "private_observation",
                            "source_id": source_id,
                            "field": field,
                            "reason": "value_num_parse_failed",
                            "value": raw,
                        })

            evidence_id = make_evidence_id(
                evidence_type="private_observation",
                source_type="private_db",
                source_id=source_id,
                field=field,
                condition_state=condition_state,
                condition_solvent=condition_solvent,
            )

            rows.append({
                "evidence_id": evidence_id,
                "subject_inchikey": None if pd.isna(inchikey) else inchikey,
                "evidence_type": "private_observation",
                "field": field,
                "value_num": value_num,
                "value": raw,
                "unit": unit,
                "condition_state": condition_state,
                "condition_solvent": condition_solvent,
                "source_type": "private_db",
                "source_id": source_id,
                "timestamp": build_timestamp,
                "timestamp_source": None,
                "confidence": 1.0,
                "extraction_method": "private_db",
            })

            counts_by_field[field] += 1

    return rows, dict(counts_by_field), dict(parse_fail_by_field), invalid_samples


def build_atb_observations(
    atb_features: pd.DataFrame,
    atb_qc: pd.DataFrame,
    build_timestamp: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    counts_by_field: Dict[str, int] = defaultdict(int)
    parse_fail_by_field: Dict[str, int] = defaultdict(int)
    invalid_samples: List[Dict[str, Any]] = []

    qc_map: Dict[str, Dict[str, Any]] = {}
    if not atb_qc.empty and "inchikey" in atb_qc.columns:
        qc_map = atb_qc.set_index("inchikey").to_dict(orient="index")

    feature_cols = [c for c in atb_features.columns if c != "inchikey"]

    for _, r in atb_features.iterrows():
        inchikey = r.get("inchikey")
        if inchikey is None or pd.isna(inchikey):
            continue

        qc = qc_map.get(inchikey, {})
        qc_ts = norm_str(qc.get("timestamp"))
        if qc_ts:
            ts = qc_ts
            ts_source = "atb_qc"
        else:
            ts = build_timestamp
            ts_source = "build_fallback"

        for field in feature_cols:
            val = r.get(field)
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue

            raw = norm_str(val)
            value_num, parse_ok = safe_float(val)
            if not parse_ok:
                parse_fail_by_field[field] += 1
                if len(invalid_samples) < 50:
                    invalid_samples.append({
                        "evidence_type": "atb_computation",
                        "source_id": inchikey,
                        "field": field,
                        "reason": "value_num_parse_failed",
                        "value": raw,
                    })

            evidence_id = make_evidence_id(
                evidence_type="atb_computation",
                source_type="atb_cache",
                source_id=inchikey,
                field=field,
                condition_state="unknown",
                condition_solvent="unknown",
            )

            rows.append({
                "evidence_id": evidence_id,
                "subject_inchikey": inchikey,
                "evidence_type": "atb_computation",
                "field": field,
                "value_num": value_num,
                "value": raw,
                "unit": ATB_UNITS.get(field),
                "condition_state": "unknown",
                "condition_solvent": "unknown",
                "source_type": "atb_cache",
                "source_id": inchikey,
                "timestamp": ts,
                "timestamp_source": ts_source,
                "confidence": 1.0,
                "extraction_method": "atb_parser",
            })

            counts_by_field[field] += 1

    return rows, dict(counts_by_field), dict(parse_fail_by_field), invalid_samples


def write_manifest(
    path: Path,
    build_timestamp: str,
    n_private_records: int,
    n_atb_rows: int,
    n_atb_qc_rows: int,
    counts_by_type: Dict[str, int],
    counts_by_field: Dict[str, int],
    counts_by_type_field: Dict[str, Dict[str, int]],
    counts_by_quality_flag: Dict[str, int],
    counts_by_field_out_of_range: Dict[str, int],
    invalid_summary: Dict[str, Any],
) -> None:
    manifest = {
        "build_timestamp": build_timestamp,
        "inputs": {
            "n_private_clean_records": n_private_records,
            "n_atb_features_rows": n_atb_rows,
            "n_atb_qc_rows": n_atb_qc_rows,
        },
        "counts_by_evidence_type": counts_by_type,
        "counts_by_field": counts_by_field,
        "counts_by_evidence_type_field": counts_by_type_field,
        "counts_by_quality_flag": counts_by_quality_flag,
        "counts_by_field_out_of_range": counts_by_field_out_of_range,
        "invalid": invalid_summary,
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="V1-P1: build evidence_table.parquet from existing sources")
    parser.add_argument("--private-clean", default="data/private_clean.parquet")
    parser.add_argument("--atb-features", default="data/atb_features.parquet")
    parser.add_argument("--atb-qc", default="data/atb_qc.parquet")
    parser.add_argument("--output", default="data/evidence_table.parquet")
    parser.add_argument("--manifest", default="data/evidence_table_build_manifest.json")
    args = parser.parse_args()

    build_ts = now_iso()

    logger.info(f"Loading private_clean: {args.private_clean}")
    private_clean = pd.read_parquet(args.private_clean)
    logger.info(f"Loading atb_features: {args.atb_features}")
    atb_features = pd.read_parquet(args.atb_features)
    logger.info(f"Loading atb_qc: {args.atb_qc}")
    atb_qc = pd.read_parquet(args.atb_qc)

    private_rows, private_counts, private_parse_fails, private_invalid_samples = build_private_observations(
        private_clean, build_ts
    )
    atb_rows, atb_counts, atb_parse_fails, atb_invalid_samples = build_atb_observations(
        atb_features, atb_qc, build_ts
    )

    all_rows = private_rows + atb_rows
    df = pd.DataFrame(all_rows)

    # Quality annotations: preserve raw values; never "fix" numbers.
    df["quality_flag"] = "OK"
    df["quality_score"] = 1.0

    # qy_* should be in [0,1]
    qy = df["field"].astype(str).str.startswith("qy_") & df["value_num"].notna()
    qy_neg = qy & (df["value_num"] < 0)
    df.loc[qy_neg, "quality_flag"] = "OUT_OF_RANGE_NEGATIVE"
    df.loc[qy_neg, "quality_score"] = 0.3
    qy_gt1 = qy & (df["value_num"] > 1)
    df.loc[qy_gt1, "quality_flag"] = "OUT_OF_RANGE_GT1"
    df.loc[qy_gt1, "quality_score"] = 0.3

    # tau_* extreme outliers (ns)
    tau_ext = df["field"].astype(str).str.startswith("tau_") & df["value_num"].notna() & (df["value_num"] > 1e6)
    df.loc[tau_ext, "quality_flag"] = "OUTLIER_TAU_EXTREME"
    df.loc[tau_ext, "quality_score"] = 0.3

    # absorption_peak_nm should be positive
    abs_peak_bad = (df["field"] == "absorption_peak_nm") & df["value_num"].notna() & (df["value_num"] <= 0)
    df.loc[abs_peak_bad, "quality_flag"] = "OUT_OF_RANGE_NONPOSITIVE"
    df.loc[abs_peak_bad, "quality_score"] = 0.3

    # Parse warnings: fields expected to be numeric but value_num couldn't be parsed.
    # Keep these as WARN (not errors) because they come from source data quality.
    parse_warn = (
        (df["quality_flag"] == "OK")
        & df["evidence_type"].isin(["private_observation", "atb_computation"])
        & df["value_num"].isna()
        & df["value"].notna()
        & (~df["field"].isin(["absorption", "tested_solvent"]))
    )
    df.loc[parse_warn, "quality_flag"] = "PARSE_WARNING"
    df.loc[parse_warn, "quality_score"] = 0.7

    # Basic schema sanity
    required_cols = [
        "evidence_id",
        "subject_inchikey",
        "evidence_type",
        "field",
        "value_num",
        "value",
        "unit",
        "condition_state",
        "condition_solvent",
        "source_type",
        "source_id",
        "timestamp",
        "timestamp_source",
        "confidence",
        "extraction_method",
        "quality_flag",
        "quality_score",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in output: {missing}")

    bad_flags = sorted(set(df["quality_flag"].dropna()) - QUALITY_FLAGS)
    if bad_flags:
        raise ValueError(f"Invalid quality_flag values: {bad_flags}")

    # Manifest stats
    counts_by_type = df["evidence_type"].value_counts(dropna=False).to_dict()
    counts_by_field = df["field"].value_counts(dropna=False).to_dict()
    counts_by_type_field: Dict[str, Dict[str, int]] = {}
    for etype, grp in df.groupby("evidence_type"):
        counts_by_type_field[str(etype)] = grp["field"].value_counts(dropna=False).to_dict()

    counts_by_quality_flag = df["quality_flag"].value_counts(dropna=False).head(10).to_dict()
    out_of_range_fields_mask = (
        (df["quality_flag"] != "OK")
        & (
            df["field"].astype(str).str.startswith("qy_")
            | df["field"].astype(str).str.startswith("tau_")
            | (df["field"] == "absorption_peak_nm")
        )
    )
    counts_by_field_out_of_range = df.loc[out_of_range_fields_mask, "field"].value_counts(dropna=False).to_dict()

    atb_ts_source_counts = (
        df[df["evidence_type"] == "atb_computation"]["timestamp_source"]
        .value_counts(dropna=False)
        .to_dict()
    )
    sol_fields = {"emission_sol", "qy_sol", "tau_sol", "absorption_peak_nm", "absorption"}
    n_sol_unknown_solvent = int(
        ((df["field"].isin(sol_fields)) & (df["condition_solvent"] == "unknown")).sum()
    )

    invalid_summary = {
        "n_rows_subject_inchikey_null": int(df["subject_inchikey"].isna().sum()),
        "parse_failures_by_field_private": private_parse_fails,
        "parse_failures_by_field_atb": atb_parse_fails,
        "atb_timestamp_source_counts": atb_ts_source_counts,
        "n_sol_state_rows_solvent_unknown": n_sol_unknown_solvent,
        "invalid_samples": (private_invalid_samples + atb_invalid_samples)[:50],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing evidence_table: {out_path} ({len(df)} rows)")
    df.to_parquet(out_path, index=False)

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing manifest: {manifest_path}")
    write_manifest(
        manifest_path,
        build_ts,
        n_private_records=len(private_clean),
        n_atb_rows=len(atb_features),
        n_atb_qc_rows=len(atb_qc),
        counts_by_type=counts_by_type,
        counts_by_field=counts_by_field,
        counts_by_type_field=counts_by_type_field,
        counts_by_quality_flag=counts_by_quality_flag,
        counts_by_field_out_of_range=counts_by_field_out_of_range,
        invalid_summary=invalid_summary,
    )

    logger.info("Done.")
    logger.info(f"Counts by evidence_type: {counts_by_type}")
    logger.info(f"Rows with subject_inchikey null: {invalid_summary['n_rows_subject_inchikey_null']}")


if __name__ == "__main__":
    main()

"""
src/chem/build_atb_tables_from_cache.py

Build atb_features.parquet and atb_qc.parquet from cached aTB results.

Usage:
    python -m src.chem.build_atb_tables_from_cache
"""

import argparse
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.utils.logging import get_logger
from src.chem.atb_cache import (
    ATB_FEATURE_FIELDS,
    get_atb_cache_record,
    extract_numeric_features,
)

logger = get_logger(__name__)


def build_tables(
    molecule_table_path: str = "data/molecule_table.parquet",
    cache_dir: str = "cache/atb",
    output_dir: str = "data",
) -> Dict[str, Any]:
    """
    Scan cache and build atb_qc + atb_features tables.
    Returns summary stats.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading molecule table: {molecule_table_path}")
    mol_df = pd.read_parquet(molecule_table_path)
    inchikeys = mol_df["inchikey"].dropna().unique().tolist()
    logger.info(f"Found {len(inchikeys)} unique inchikeys")

    qc_rows: List[Dict[str, Any]] = []
    feat_rows: List[Dict[str, Any]] = []

    for ik in inchikeys:
        record = get_atb_cache_record(ik, cache_dir=cache_dir)
        status = record.get("status") or {}
        features = record.get("features")

        cache_status = record["cache_status"]
        has_features_json = record["has_features_json"]
        keyfield_complete = record["keyfield_complete"]
        missing_fields = record.get("missing_fields", [])

        qc_rows.append({
            "inchikey": ik,
            "cache_status": cache_status,
            "fail_stage": status.get("fail_stage"),
            "error_msg": status.get("error_msg"),
            "runtime_sec": status.get("runtime_sec"),
            "atb_version": status.get("atb_version"),
            "timestamp": status.get("timestamp") or datetime.now().isoformat(),
            "has_features_json": bool(has_features_json),
            "keyfield_complete": bool(keyfield_complete),
            "missing_fields": missing_fields if missing_fields else None,
        })

        if features is not None:
            row = {"inchikey": ik}
            row.update(extract_numeric_features(features))
            feat_rows.append(row)

    qc_df = pd.DataFrame(qc_rows)
    feat_df = pd.DataFrame(feat_rows)

    # Validate uniqueness
    if qc_df["inchikey"].duplicated().any():
        raise ValueError("Duplicate inchikey rows detected in atb_qc")
    if not feat_df.empty and feat_df["inchikey"].duplicated().any():
        raise ValueError("Duplicate inchikey rows detected in atb_features")

    # Save
    qc_path = output_dir / "atb_qc.parquet"
    feat_path = output_dir / "atb_features.parquet"
    qc_df.to_parquet(qc_path, index=False)
    feat_df.to_parquet(feat_path, index=False)

    logger.info(f"Saved QC table: {qc_path} ({len(qc_df)} rows)")
    logger.info(f"Saved features table: {feat_path} ({len(feat_df)} rows)")

    return {
        "qc_path": str(qc_path),
        "features_path": str(feat_path),
        "n_qc": len(qc_df),
        "n_features": len(feat_df),
        "qc_df": qc_df,
        "feat_df": feat_df,
    }


def print_validation(qc_df: pd.DataFrame, feat_df: pd.DataFrame, k: int = 5) -> None:
    """Print required validation stats."""
    logger.info("Validation: cache_status counts")
    counts = qc_df["cache_status"].value_counts(dropna=False)
    for status, count in counts.items():
        logger.info(f"  {status}: {count}")

    # Keyfield completeness rate among success
    success_df = qc_df[qc_df["cache_status"] == "success"]
    if len(success_df) > 0:
        rate = success_df["keyfield_complete"].mean()
        logger.info(f"Keyfield completeness rate among success: {rate:.3f}")
    else:
        logger.info("Keyfield completeness rate among success: n/a (no success rows)")

    # Spot-check k random inchikeys
    logger.info(f"Spot-check {k} random inchikeys")
    sample = qc_df["inchikey"].sample(min(k, len(qc_df)), random_state=42).tolist()
    feat_map = {r["inchikey"]: r for r in feat_df.to_dict(orient="records")}
    for ik in sample:
        qc_row = qc_df[qc_df["inchikey"] == ik].iloc[0].to_dict()
        feat_row = feat_map.get(ik)
        logger.info(f"  {ik} qc={qc_row} features={feat_row}")


def main():
    parser = argparse.ArgumentParser(description="Build aTB tables from cache")
    parser.add_argument("--molecule-table", default="data/molecule_table.parquet")
    parser.add_argument("--cache-dir", default="cache/atb")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--no-validate", action="store_true", help="Skip validation prints")
    args = parser.parse_args()

    result = build_tables(
        molecule_table_path=args.molecule_table,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
    )

    if not args.no_validate:
        print_validation(result["qc_df"], result["feat_df"], k=5)


if __name__ == "__main__":
    main()

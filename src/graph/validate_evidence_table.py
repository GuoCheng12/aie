"""
src/graph/validate_evidence_table.py

Validator for V1 evidence_table.parquet.

Checks:
- Required columns and basic nullability
- Enum value validity
- evidence_id uniqueness
- confidence range
- timestamp parseability
- Prints summary stats

Usage:
    python -m src.graph.validate_evidence_table --path data/evidence_table.parquet
"""

import argparse
from datetime import datetime
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.utils.logging import get_logger


logger = get_logger(__name__)


REQUIRED_COLS = [
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
]

EVIDENCE_TYPES = {"private_observation", "atb_computation", "literature_claim"}
SOURCE_TYPES = {"private_db", "atb_cache", "paper_doi"}
CONDITION_STATES = {"sol", "solid", "aggr", "crys", "unknown"}
ATB_TIMESTAMP_SOURCES = {"atb_qc", "build_fallback"}


def _is_null(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and np.isnan(x):
        return True
    return False


def validate(df: pd.DataFrame) -> List[str]:
    errors: List[str] = []

    missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        return errors

    # Nullability checks
    for col in ["evidence_id", "evidence_type", "field", "condition_state", "source_type", "source_id", "timestamp", "confidence", "extraction_method"]:
        n_null = int(df[col].isna().sum())
        if n_null > 0:
            errors.append(f"Column {col} has {n_null} nulls (must be non-null)")

    # evidence_id uniqueness
    n_dupe = int(df["evidence_id"].duplicated().sum())
    if n_dupe > 0:
        errors.append(f"evidence_id has {n_dupe} duplicates")

    # Enum checks
    bad_types = sorted(set(df["evidence_type"].dropna()) - EVIDENCE_TYPES)
    if bad_types:
        errors.append(f"Invalid evidence_type values: {bad_types}")

    bad_sources = sorted(set(df["source_type"].dropna()) - SOURCE_TYPES)
    if bad_sources:
        errors.append(f"Invalid source_type values: {bad_sources}")

    bad_states = sorted(set(df["condition_state"].dropna()) - CONDITION_STATES)
    if bad_states:
        errors.append(f"Invalid condition_state values: {bad_states}")

    # Confidence range
    conf = df["confidence"]
    if conf.isna().any():
        pass
    else:
        bad = df[(conf < 0.0) | (conf > 1.0)]
        if len(bad) > 0:
            errors.append(f"confidence out of range [0,1] for {len(bad)} rows")

    # At least one of value_num/value must be present
    both_null = df["value_num"].isna() & df["value"].isna()
    n_both_null = int(both_null.sum())
    if n_both_null > 0:
        errors.append(f"value_num and value both null for {n_both_null} rows")

    # Timestamp parseability (sample all; table should be small)
    ts_bad = 0
    for t in df["timestamp"].astype(str).tolist():
        try:
            datetime.fromisoformat(t)
        except Exception:
            ts_bad += 1
            if ts_bad >= 5:
                break
    if ts_bad > 0:
        errors.append("timestamp not ISO-8601 parseable for at least 1 row (showing first 5)")

    # Source/evidence-type alignment checks (soft errors -> warnings)
    # Keep as errors to catch schema drift early.
    private = df[df["evidence_type"] == "private_observation"]
    if len(private) > 0:
        if (private["source_type"] != "private_db").any():
            errors.append("private_observation rows must have source_type=private_db")
        if (private["confidence"] != 1.0).any():
            errors.append("private_observation rows must have confidence=1.0")

    atb = df[df["evidence_type"] == "atb_computation"]
    if len(atb) > 0:
        if (atb["source_type"] != "atb_cache").any():
            errors.append("atb_computation rows must have source_type=atb_cache")
        if (atb["confidence"] != 1.0).any():
            errors.append("atb_computation rows must have confidence=1.0")
        if atb["timestamp_source"].isna().any():
            errors.append("atb_computation rows must have non-null timestamp_source")
        bad_ts_sources = sorted(set(atb["timestamp_source"].dropna()) - ATB_TIMESTAMP_SOURCES)
        if bad_ts_sources:
            errors.append(f"Invalid atb timestamp_source values: {bad_ts_sources}")

    return errors


def print_summary(df: pd.DataFrame) -> None:
    logger.info(f"Rows: {len(df)}")
    logger.info("Counts by evidence_type:")
    logger.info(df["evidence_type"].value_counts(dropna=False).to_dict())
    logger.info("Top fields:")
    logger.info(df["field"].value_counts().head(20).to_dict())
    logger.info(f"Rows with subject_inchikey null: {int(df['subject_inchikey'].isna().sum())}")
    logger.info(f"Rows with value_num non-null: {int(df['value_num'].notna().sum())}")
    atb_ts = df[df["evidence_type"] == "atb_computation"]["timestamp_source"].value_counts(dropna=False).to_dict()
    logger.info(f"atb timestamp_source counts: {atb_ts}")
    sol_fields = {"emission_sol", "qy_sol", "tau_sol", "absorption_peak_nm", "absorption"}
    n_sol_unknown = int(((df["field"].isin(sol_fields)) & (df["condition_solvent"] == "unknown")).sum())
    logger.info(f"sol-state rows with condition_solvent=='unknown': {n_sol_unknown}")


def main():
    parser = argparse.ArgumentParser(description="Validate V1 evidence_table.parquet")
    parser.add_argument("--path", default="data/evidence_table.parquet")
    args = parser.parse_args()

    df = pd.read_parquet(args.path)
    print_summary(df)
    errors = validate(df)
    if errors:
        logger.error("VALIDATION FAILED")
        for e in errors[:20]:
            logger.error(f"- {e}")
        raise SystemExit(1)
    logger.info("VALIDATION PASSED")


if __name__ == "__main__":
    main()

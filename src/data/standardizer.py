"""
src/data/standardizer.py

Train-only schema enforcement and missing value handling for the private AIE dataset.
"""

import pandas as pd
import numpy as np
from typing import List

from src.utils.logging import get_logger

logger = get_logger(__name__)


# Authoritative business column set from data/train.csv.
TRAIN_BUSINESS_COLUMNS = [
    "id",
    "code",
    "SMILES",
    "reference",
    "molecular_weight",
    "emission_solid",
    "emission_aggr",
    "features_id",
    "mechanism_id",
    "doi",
]

# Required to construct a valid facts table.
REQUIRED_COLUMNS = ["id", "SMILES"]

# Fields used by downstream readiness logic.
CRITICAL_FIELDS = ["emission_solid", "emission_aggr"]

# Numeric fields to coerce explicitly.
NUMERIC_COLUMNS = ["molecular_weight", "emission_solid", "emission_aggr", "features_id"]

def _normalize_string_column(series: pd.Series) -> pd.Series:
    def _norm(value: object) -> object:
        if value is None:
            return np.nan
        if isinstance(value, float) and np.isnan(value):
            return np.nan
        stripped = str(value).strip()
        return stripped if stripped != "" else np.nan

    return series.apply(_norm)


def add_missing_indicators(df: pd.DataFrame, fields: List[str]) -> pd.DataFrame:
    """
    Add missing value indicator columns for critical fields.

    Creates {field}_missing boolean columns (True = missing).

    Args:
        df: DataFrame
        fields: List of field names to check

    Returns:
        DataFrame with {field}_missing columns added
    """
    df = df.copy()

    for field in fields:
        if field not in df.columns:
            logger.warning(f"Field {field} not found in DataFrame, skipping missing indicator")
            continue

        missing_col = f"{field}_missing"
        df[missing_col] = df[field].isna()

        n_missing = df[missing_col].sum()
        pct_missing = 100 * n_missing / len(df)
        logger.info(f"{field}: {n_missing}/{len(df)} missing ({pct_missing:.1f}%)")

    return df


def enforce_train_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only train.csv business columns and coerce key types.

    Missing optional train columns are created as null to keep a stable schema.
    """
    missing_required = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_required:
        raise ValueError(f"Missing required input columns for train-only facts: {missing_required}")

    out = df.copy()

    # Fill missing optional columns with nulls, then keep only train business columns.
    for col in TRAIN_BUSINESS_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[TRAIN_BUSINESS_COLUMNS].copy()

    # Normalize string-like columns.
    for col in ["code", "SMILES", "reference", "mechanism_id", "doi"]:
        out[col] = _normalize_string_column(out[col])

    # id must be numeric and non-null.
    out["id"] = pd.to_numeric(out["id"], errors="coerce")
    n_bad_id = int(out["id"].isna().sum())
    if n_bad_id > 0:
        logger.warning(f"Dropping {n_bad_id} rows with invalid id after numeric coercion")
        out = out[out["id"].notna()].copy()
    out["id"] = out["id"].astype("int64")

    # Numeric coercion for train numeric fields.
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    # Keep features_id as nullable integer when possible.
    out["features_id"] = out["features_id"].astype("Int64")

    return out


def standardize_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply train-only standardization steps.

    Steps:
    1. Enforce train business column set.
    2. Coerce emission fields to numeric.
    3. Add missing indicators for emission fields.

    Args:
        df: Raw DataFrame

    Returns:
        Standardized DataFrame
    """
    logger.info("Starting dataset standardization")

    df = enforce_train_schema(df)
    df = add_missing_indicators(df, CRITICAL_FIELDS)

    logger.info(f"Standardization complete. Final shape: {df.shape}")

    return df

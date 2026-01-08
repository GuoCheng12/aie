"""
src/data/standardizer.py

Unit normalization and missing value handling for the private AIE dataset.
"""

import pandas as pd
import numpy as np
import re
from typing import Dict, List

from src.utils.logging import get_logger

logger = get_logger(__name__)


# Critical fields requiring missing indicators
CRITICAL_FIELDS = [
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
    "absorption",
    "tested_solvent",
]


def normalize_qy_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize quantum yield (qy) columns from percent (0-100) to fraction [0,1].

    Creates:
    - qy_{condition}: normalized value [0,1]
    - qy_{condition}_raw: original percent value
    - qy_unit_inferred: "percent" (constant)
    - qy_inferred_confidence: "high" (constant for this dataset)

    Args:
        df: DataFrame with qy_* columns

    Returns:
        DataFrame with normalized qy columns
    """
    qy_conditions = ["sol", "solid", "aggr", "crys"]
    df = df.copy()

    for condition in qy_conditions:
        col = f"qy_{condition}"
        if col not in df.columns:
            logger.warning(f"Column {col} not found, skipping")
            continue

        # Store raw values
        df[f"{col}_raw"] = df[col].copy()

        # Normalize: divide by 100 to get [0,1]
        df[col] = df[col] / 100.0

        logger.info(
            f"Normalized {col}: "
            f"raw range [{df[f'{col}_raw'].min():.2f}, {df[f'{col}_raw'].max():.2f}] → "
            f"normalized range [{df[col].min():.4f}, {df[col].max():.4f}]"
        )

    # Add unit metadata
    df["qy_unit_inferred"] = "percent"
    df["qy_inferred_confidence"] = "high"

    return df


def normalize_tau_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize lifetime (tau) columns.

    Assumes unit is ns (based on bulk median). Flags outliers.

    Creates:
    - tau_{condition}: value in ns (unchanged)
    - tau_{condition}_raw: copy of original value
    - tau_{condition}_outlier: True if value > 3×IQR above Q3 OR > 1000 ns
    - tau_{condition}_log: log10(tau + 1e-9) for modeling (optional)

    Args:
        df: DataFrame with tau_* columns

    Returns:
        DataFrame with normalized tau columns
    """
    tau_conditions = ["sol", "solid", "aggr", "crys"]
    df = df.copy()

    for condition in tau_conditions:
        col = f"tau_{condition}"
        if col not in df.columns:
            logger.warning(f"Column {col} not found, skipping")
            continue

        # Store raw values
        df[f"{col}_raw"] = df[col].copy()

        # Compute outlier threshold: Q3 + 3×IQR or > 1000 ns
        valid_values = df[col].dropna()
        if len(valid_values) > 0:
            q1 = valid_values.quantile(0.25)
            q3 = valid_values.quantile(0.75)
            iqr = q3 - q1
            threshold_iqr = q3 + 3 * iqr
            threshold_abs = 1000.0  # ns

            # Mark outliers
            df[f"{col}_outlier"] = (df[col] > threshold_iqr) | (df[col] > threshold_abs)

            n_outliers = df[f"{col}_outlier"].sum()
            logger.info(
                f"{col}: {n_outliers} outliers detected "
                f"(threshold: {threshold_iqr:.2f} ns or > {threshold_abs} ns)"
            )
        else:
            df[f"{col}_outlier"] = False

        # Optional: compute log transform for modeling
        df[f"{col}_log"] = np.log10(df[col] + 1e-9)

    return df


def parse_absorption_peak(absorption_str: str) -> float:
    """
    Parse absorption peak wavelength from string.

    Extracts first numeric value (assumed to be in nm).

    Args:
        absorption_str: Raw absorption string (e.g., "450 nm", "450", "450, 500")

    Returns:
        Peak wavelength in nm, or NaN if not parsable
    """
    if pd.isna(absorption_str):
        return np.nan

    # Try to extract first numeric value
    match = re.search(r"(\d+\.?\d*)", str(absorption_str))
    if match:
        return float(match.group(1))
    return np.nan


def standardize_absorption(df: pd.DataFrame) -> pd.DataFrame:
    """
    Standardize absorption column.

    Preserves raw string, extracts peak wavelength if possible.

    Args:
        df: DataFrame with 'absorption' column

    Returns:
        DataFrame with absorption_peak_nm column added
    """
    df = df.copy()

    if "absorption" not in df.columns:
        logger.warning("Column 'absorption' not found, skipping")
        return df

    # Parse peak wavelength
    df["absorption_peak_nm"] = df["absorption"].apply(parse_absorption_peak)

    n_parsed = df["absorption_peak_nm"].notna().sum()
    logger.info(f"Parsed absorption peaks for {n_parsed}/{len(df)} rows")

    return df


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
        df[missing_col] = df[field].isna() | (df[field] == "") | (df[field] == " ")

        n_missing = df[missing_col].sum()
        pct_missing = 100 * n_missing / len(df)
        logger.info(f"{field}: {n_missing}/{len(df)} missing ({pct_missing:.1f}%)")

    return df


def standardize_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all standardization steps to the dataset.

    Steps:
    1. Normalize qy columns (% → [0,1])
    2. Normalize tau columns (flag outliers, add log transform)
    3. Parse absorption peaks
    4. Add missing indicators for critical fields

    Args:
        df: Raw DataFrame

    Returns:
        Standardized DataFrame
    """
    logger.info("Starting dataset standardization")

    df = normalize_qy_columns(df)
    df = normalize_tau_columns(df)
    df = standardize_absorption(df)
    df = add_missing_indicators(df, CRITICAL_FIELDS)

    logger.info(f"Standardization complete. Final shape: {df.shape}")

    return df

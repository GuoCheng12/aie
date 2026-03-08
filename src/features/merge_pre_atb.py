"""
src/features/merge_pre_atb.py

DEPRECATED (not in current mainline):
- This script belongs to the historical V0 pre-aTB pipeline.
- It assumes legacy private fields (absorption/qy/tau/solvent) and must not be used
  in the train-only facts main chain.
- Mainline uses: data pipeline -> anchor_ecfp -> pre-aTB UQ/reports -> V1 evidence/graph.

Usage:
    python -m src.features.merge_pre_atb
"""

import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.preprocessing import StandardScaler

from src.utils.logging import get_logger

logger = get_logger(__name__)


# RDKit descriptors to standardize (z-score)
RDKIT_DESCRIPTORS = [
    "mw",
    "logp",
    "tpsa",
    "n_rotatable_bonds",
    "n_hbd",
    "n_hba",
    "n_rings",
    "n_aromatic_rings",
    "n_heavy_atoms",
]


def load_private_clean(path: str = "data/private_clean.parquet") -> pd.DataFrame:
    """Load private_clean.parquet (1225 rows, record-level)."""
    logger.info(f"Loading private_clean from {path}")
    df = pd.read_parquet(path)
    logger.info(f"Loaded {len(df)} records")
    return df


def load_rdkit_features(path: str = "data/rdkit_features.parquet") -> pd.DataFrame:
    """Load rdkit_features.parquet (1050 rows, molecule-level)."""
    logger.info(f"Loading rdkit_features from {path}")
    df = pd.read_parquet(path)
    logger.info(f"Loaded {len(df)} molecules")
    return df


def merge_features(
    private_clean: pd.DataFrame,
    rdkit_features: pd.DataFrame
) -> pd.DataFrame:
    """
    Merge private_clean + rdkit_features on inchikey.

    Left join from private_clean to preserve all experimental records.

    Args:
        private_clean: Record-level data (1225 rows)
        rdkit_features: Molecule-level data (1050 rows)

    Returns:
        Merged DataFrame (1225 rows)
    """
    logger.info("Merging private_clean + rdkit_features on inchikey")

    # Left join to preserve all records from private_clean
    merged = private_clean.merge(
        rdkit_features,
        on="inchikey",
        how="left",
        suffixes=("", "_rdkit")
    )

    # Check for duplicate columns (shouldn't happen with proper suffix handling)
    duplicate_cols = [col for col in merged.columns if col.endswith("_rdkit")]
    if duplicate_cols:
        logger.warning(f"Duplicate columns detected (with _rdkit suffix): {duplicate_cols}")

    logger.info(f"Merge complete: {len(merged)} rows")

    return merged


def fit_scaler(
    df: pd.DataFrame,
    columns: list
) -> StandardScaler:
    """
    Fit StandardScaler on specified columns.

    Ignores NaN values during fit.

    Args:
        df: DataFrame with features
        columns: List of column names to scale

    Returns:
        Fitted StandardScaler
    """
    logger.info(f"Fitting StandardScaler on {len(columns)} columns")

    # Extract data for scaling (drop NaN values)
    scaler = StandardScaler()

    # For each column, we need to handle NaN values
    # StandardScaler will raise error on NaN, so we fit only on valid rows
    X = df[columns].values

    # Fit scaler (sklearn handles NaN internally in newer versions, but let's be explicit)
    scaler.fit(X)

    logger.info("Scaler fitted successfully")
    logger.info(f"  Mean: {scaler.mean_}")
    logger.info(f"  Scale (std): {scaler.scale_}")

    return scaler


def apply_scaler(
    df: pd.DataFrame,
    scaler: StandardScaler,
    columns: list
) -> pd.DataFrame:
    """
    Apply fitted scaler to columns and create scaled versions.

    Original columns are preserved. New columns with suffix '_scaled' are added.

    Args:
        df: DataFrame with features
        scaler: Fitted StandardScaler
        columns: List of column names to scale

    Returns:
        DataFrame with added scaled columns
    """
    logger.info(f"Applying scaler to {len(columns)} columns")

    df_scaled = df.copy()

    # Transform the data
    X = df[columns].values
    X_scaled = scaler.transform(X)

    # Add scaled columns
    for i, col in enumerate(columns):
        scaled_col_name = f"{col}_scaled"
        df_scaled[scaled_col_name] = X_scaled[:, i]

    logger.info(f"Added {len(columns)} scaled columns (suffix: _scaled)")

    return df_scaled


def save_feature_config(
    output_path: str,
    rdkit_descriptors: list,
    scaler_path: str,
    n_rows: int,
    n_molecules_with_rdkit: int
):
    """
    Save feature configuration YAML.

    Documents feature blocks, columns, and scaler details.
    """
    logger.info(f"Saving feature config to {output_path}")

    config = {
        "version": "P3a_pre_atb",
        "timestamp": datetime.now().isoformat(),
        "data_summary": {
            "n_rows": n_rows,
            "n_molecules_with_rdkit": n_molecules_with_rdkit,
        },
        "feature_blocks": {
            "experimental_observables": {
                "description": "Experimental photophysical properties from private dataset",
                "examples": [
                    "emission_sol", "emission_solid", "emission_aggr", "emission_crys",
                    "qy_sol", "qy_solid", "qy_aggr", "qy_crys",
                    "tau_sol", "tau_solid", "tau_aggr", "tau_crys",
                    "absorption_peak_nm", "tested_solvent"
                ],
            },
            "rdkit_descriptors": {
                "description": "RDKit molecular descriptors (z-score normalized)",
                "columns": rdkit_descriptors,
                "scaled_columns": [f"{col}_scaled" for col in rdkit_descriptors],
                "scaling_method": "z-score (StandardScaler)",
                "scaler_path": scaler_path,
            },
            "ecfp_fingerprints": {
                "description": "ECFP4 fingerprints (radius=2, 2048 bits)",
                "column": "ecfp_2048",
                "dtype": "list[int8]",
                "scaling": "none (preserved as-is)",
            },
            "missing_indicators": {
                "description": "Boolean indicators for missing critical fields",
                "pattern": "{field}_missing",
                "count": 14,
            },
            "metadata": {
                "description": "Identifiers and auxiliary info",
                "examples": ["id", "code", "inchikey", "canonical_smiles", "molecular_weight", "mechanism_id", "features_id"],
            },
        },
        "atb_block": {
            "status": "absent",
            "note": "P3a is pre-aTB merge. aTB features will be added in P3b after P2 completes.",
        },
    }

    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    logger.info("Feature config saved")


def run_merge(
    private_clean_path: str = "data/private_clean.parquet",
    rdkit_features_path: str = "data/rdkit_features.parquet",
    output_path: str = "data/X_full_pre_atb.parquet",
    scaler_path: str = "data/scaler_pre_atb.pkl",
    config_path: str = "data/feature_config_pre_atb.yaml"
):
    """
    Deprecated entry point.
    """
    raise RuntimeError(
        "src.features.merge_pre_atb is DEPRECATED and not part of the train-only main chain. "
        "Do not run this script. Use the current pipeline: "
        "python -m src.data.pipeline -> python -m src.features.anchor_ecfp -> "
        "python -m src.uq.compute_uq_pre_atb_p5b -> report/evidence/graph steps."
    )
    logger.info("=" * 60)
    logger.info("P3a: Feature Merge (pre-aTB)")
    logger.info("=" * 60)

    # Step 1: Load data
    private_clean = load_private_clean(private_clean_path)
    rdkit_features = load_rdkit_features(rdkit_features_path)

    # Step 2: Merge
    merged = merge_features(private_clean, rdkit_features)

    # Step 3: Check merge coverage
    n_rows = len(merged)
    n_with_rdkit = merged["mw"].notna().sum()
    logger.info(f"Merge coverage: {n_with_rdkit}/{n_rows} rows have RDKit descriptors")

    # Step 4: Fit scaler on RDKit descriptors
    scaler = fit_scaler(merged, RDKIT_DESCRIPTORS)

    # Step 5: Apply scaler
    merged_scaled = apply_scaler(merged, scaler, RDKIT_DESCRIPTORS)

    # Step 6: Save outputs
    logger.info(f"Saving merged table to {output_path}")
    merged_scaled.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(merged_scaled)} rows, {len(merged_scaled.columns)} columns")

    logger.info(f"Saving scaler to {scaler_path}")
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    logger.info("Scaler saved")

    save_feature_config(
        output_path=config_path,
        rdkit_descriptors=RDKIT_DESCRIPTORS,
        scaler_path=scaler_path,
        n_rows=n_rows,
        n_molecules_with_rdkit=n_with_rdkit
    )

    logger.info("=" * 60)
    logger.info("P3a merge complete ✅")
    logger.info("=" * 60)
    logger.info(f"Outputs:")
    logger.info(f"  - {output_path}")
    logger.info(f"  - {scaler_path}")
    logger.info(f"  - {config_path}")


if __name__ == "__main__":
    run_merge()

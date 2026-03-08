"""
src/features/merge_with_atb.py

P3b: Merge X_full_pre_atb with aTB cache-derived tables.

Usage:
    python -m src.features.merge_with_atb
"""

import pickle
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import yaml

from src.utils.logging import get_logger
from src.chem.atb_cache import ATB_FEATURE_FIELDS

logger = get_logger(__name__)


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


def load_df(path: str) -> pd.DataFrame:
    logger.info(f"Loading {path}")
    return pd.read_parquet(path)


def merge_tables(
    x_pre: pd.DataFrame,
    atb_features: pd.DataFrame,
    atb_qc: pd.DataFrame,
) -> pd.DataFrame:
    """Left join aTB features + QC onto X_full_pre_atb by inchikey."""
    logger.info("Merging X_full_pre_atb + atb_features + atb_qc on inchikey")

    merged = x_pre.merge(atb_features, on="inchikey", how="left")

    qc_cols = [
        "inchikey",
        "cache_status",
        "keyfield_complete",
        "has_features_json",
        "missing_fields",
    ]
    qc_cols = [c for c in qc_cols if c in atb_qc.columns]
    qc_trim = atb_qc[qc_cols].copy()

    merged = merged.merge(qc_trim, on="inchikey", how="left", suffixes=("", "_qc"))

    # Normalize QC column names
    merged["atb_cache_status"] = merged.get("cache_status")
    merged["atb_keyfield_complete"] = merged.get("keyfield_complete")
    merged["atb_available"] = (
        (merged["atb_cache_status"] == "success") & (merged["atb_keyfield_complete"] == True)
    )

    return merged


def fit_scaler_ignore_nan(df: pd.DataFrame, columns: List[str]) -> Dict[str, Dict[str, float]]:
    """Compute mean/std per column ignoring NaN."""
    stats: Dict[str, Dict[str, float]] = {"mean": {}, "std": {}}
    for col in columns:
        if col not in df.columns:
            continue
        series = df[col]
        mean = float(series.mean(skipna=True))
        std = float(series.std(skipna=True, ddof=0))
        if np.isnan(std) or std == 0.0:
            std = 1.0
        stats["mean"][col] = mean
        stats["std"][col] = std
    return stats


def apply_scaler(df: pd.DataFrame, stats: Dict[str, Dict[str, float]]) -> pd.DataFrame:
    """Apply per-column scaling; creates *_scaled columns."""
    df_scaled = df.copy()
    for col, mean in stats["mean"].items():
        std = stats["std"][col]
        if col not in df_scaled.columns:
            continue
        df_scaled[f"{col}_scaled"] = (df_scaled[col] - mean) / std
    return df_scaled


def save_feature_config(
    output_path: str,
    scale_cols: List[str],
    scaler_path: str,
    n_rows: int,
) -> None:
    logger.info(f"Saving feature config to {output_path}")
    config = {
        "version": "P3b_post_atb",
        "timestamp": datetime.now().isoformat(),
        "data_summary": {
            "n_rows": n_rows,
        },
        "feature_blocks": {
            "experimental_observables": {
                "description": "Experimental photophysical properties from private dataset",
            },
            "rdkit_descriptors": {
                "description": "RDKit molecular descriptors (z-score normalized)",
                "columns": RDKIT_DESCRIPTORS,
                "scaled_columns": [f"{c}_scaled" for c in RDKIT_DESCRIPTORS],
            },
            "atb_features": {
                "description": "aTB cache-derived descriptors (z-score normalized where available)",
                "columns": ATB_FEATURE_FIELDS,
                "scaled_columns": [f"{c}_scaled" for c in ATB_FEATURE_FIELDS],
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
            },
            "metadata": {
                "description": "Identifiers and auxiliary info",
                "examples": ["id", "code", "inchikey", "canonical_smiles", "mechanism_id", "features_id"],
            },
        },
        "scaling": {
            "scaler_path": scaler_path,
            "scaled_columns": scale_cols,
            "method": "z-score (mean/std, NaN ignored)",
        },
        "anchor_retrieval": {
            "policy": "structure-only",
            "note": "Do NOT use aTB features for retrieval/re-ranking",
        },
    }

    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def run_merge(
    x_pre_path: str = "data/X_full_pre_atb.parquet",
    atb_features_path: str = "data/atb_features.parquet",
    atb_qc_path: str = "data/atb_qc.parquet",
    output_path: str = "data/X_full.parquet",
    scaler_path: str = "data/scaler.pkl",
    config_path: str = "data/feature_config.yaml",
):
    logger.info("=" * 60)
    logger.info("P3b: Feature Merge (post-aTB)")
    logger.info("=" * 60)

    x_pre = load_df(x_pre_path)
    atb_features = load_df(atb_features_path)
    atb_qc = load_df(atb_qc_path)

    merged = merge_tables(x_pre, atb_features, atb_qc)
    n_rows = len(merged)
    logger.info(f"Merged rows: {n_rows}")

    # Scaling: RDKit + aTB numeric features
    scale_cols = [c for c in (RDKIT_DESCRIPTORS + ATB_FEATURE_FIELDS) if c in merged.columns]
    stats = fit_scaler_ignore_nan(merged, scale_cols)
    merged = apply_scaler(merged, stats)

    # Save scaler stats
    with open(scaler_path, "wb") as f:
        pickle.dump(stats, f)

    # Save outputs
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output_path, index=False)
    save_feature_config(config_path, scale_cols, scaler_path, n_rows)

    logger.info(f"Saved X_full: {output_path}")
    logger.info(f"Saved scaler: {scaler_path}")
    logger.info(f"Saved feature config: {config_path}")


if __name__ == "__main__":
    run_merge()

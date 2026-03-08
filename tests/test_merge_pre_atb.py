"""
tests/test_merge_pre_atb.py

Minimal tests for P3a merge (pre-aTB).

Tests:
1. Row count preservation (left join from private_clean)
2. RDKit descriptor columns present
3. ecfp_2048 preserved as length-2048 array for valid rows
"""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

pytestmark = pytest.mark.skip(
    reason="merge_pre_atb is deprecated and not part of the train-only main chain"
)


@pytest.fixture
def data_paths():
    """Return paths to input/output data files."""
    return {
        "private_clean": Path("data/private_clean.parquet"),
        "rdkit_features": Path("data/rdkit_features.parquet"),
        "merged": Path("data/X_full_pre_atb.parquet")
    }


@pytest.fixture
def rdkit_descriptors():
    """Return list of RDKit descriptor column names."""
    return [
        "mw", "logp", "tpsa", "n_rotatable_bonds",
        "n_hbd", "n_hba", "n_rings", "n_aromatic_rings", "n_heavy_atoms"
    ]


def test_row_count_preservation(data_paths):
    """
    Test that merge preserves row count from private_clean.

    Left join from private_clean should keep all 1225 rows.
    """
    if not data_paths["merged"].exists():
        pytest.skip("X_full_pre_atb.parquet not found - run merge script first")

    private_clean = pd.read_parquet(data_paths["private_clean"])
    merged = pd.read_parquet(data_paths["merged"])

    assert len(merged) == len(private_clean), (
        f"Row count mismatch: merged has {len(merged)} rows, "
        f"private_clean has {len(private_clean)} rows"
    )


def test_rdkit_descriptor_columns_present(data_paths, rdkit_descriptors):
    """
    Test that RDKit descriptor columns are present in merged table.

    All 9 RDKit descriptors should exist (even if some values are null).
    Scaled versions ({col}_scaled) should also exist.
    """
    if not data_paths["merged"].exists():
        pytest.skip("X_full_pre_atb.parquet not found - run merge script first")

    merged = pd.read_parquet(data_paths["merged"])

    # Check original descriptor columns
    for col in rdkit_descriptors:
        assert col in merged.columns, f"Missing RDKit descriptor column: {col}"

    # Check scaled versions
    for col in rdkit_descriptors:
        scaled_col = f"{col}_scaled"
        assert scaled_col in merged.columns, f"Missing scaled column: {scaled_col}"


def test_ecfp_array_integrity(data_paths):
    """
    Test that ecfp_2048 is preserved as length-2048 array for valid rows.

    Checks:
    - ecfp_2048 column exists
    - Non-null values are array-like
    - Arrays have length 2048
    - Array values are integers (0 or 1 for binary fingerprints)
    """
    if not data_paths["merged"].exists():
        pytest.skip("X_full_pre_atb.parquet not found - run merge script first")

    merged = pd.read_parquet(data_paths["merged"])

    # Check column exists
    assert "ecfp_2048" in merged.columns, "ecfp_2048 column not found"

    # Check non-null samples
    ecfp_samples = merged[merged["ecfp_2048"].notna()]["ecfp_2048"]

    if len(ecfp_samples) == 0:
        pytest.skip("No non-null ecfp_2048 values found")

    # Check a few samples
    for i, arr in enumerate(ecfp_samples.head(10)):
        # Must be array-like
        assert isinstance(arr, (list, np.ndarray)), (
            f"Sample {i}: ecfp_2048 is not array-like (type={type(arr)})"
        )

        # Must have length 2048
        arr_np = np.array(arr)
        assert len(arr_np) == 2048, (
            f"Sample {i}: ecfp_2048 has length {len(arr_np)}, expected 2048"
        )

        # Values should be integers (0 or 1 for binary fingerprints)
        assert np.issubdtype(arr_np.dtype, np.integer), (
            f"Sample {i}: ecfp_2048 values are not integers (dtype={arr_np.dtype})"
        )


def test_missing_indicator_columns_preserved(data_paths):
    """
    Test that {field}_missing indicator columns are preserved from private_clean.

    These columns track which fields had missing values in the original data.
    """
    if not data_paths["merged"].exists():
        pytest.skip("X_full_pre_atb.parquet not found - run merge script first")

    private_clean = pd.read_parquet(data_paths["private_clean"])
    merged = pd.read_parquet(data_paths["merged"])

    # Find missing indicator columns in private_clean
    missing_cols = [col for col in private_clean.columns if col.endswith("_missing")]

    # Check they're preserved in merged
    for col in missing_cols:
        assert col in merged.columns, f"Missing indicator column not preserved: {col}"


def test_metadata_columns_preserved(data_paths):
    """
    Test that key metadata columns are preserved.

    Columns: id, code, inchikey, canonical_smiles, molecular_weight
    """
    if not data_paths["merged"].exists():
        pytest.skip("X_full_pre_atb.parquet not found - run merge script first")

    merged = pd.read_parquet(data_paths["merged"])

    metadata_cols = ["id", "code", "inchikey", "canonical_smiles", "molecular_weight"]

    for col in metadata_cols:
        assert col in merged.columns, f"Missing metadata column: {col}"

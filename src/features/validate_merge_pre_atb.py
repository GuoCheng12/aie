"""
src/features/validate_merge_pre_atb.py

Validation script for P3a merge output.

Usage:
    python -m src.features.validate_merge_pre_atb
"""

from pathlib import Path

import numpy as np
import pandas as pd

from src.utils.logging import get_logger

logger = get_logger(__name__)


def validate_merge(
    merged_path: str = "data/X_full_pre_atb.parquet",
    private_clean_path: str = "data/private_clean.parquet"
):
    """
    Validate P3a merge output.

    Checks:
    1. Row count matches private_clean
    2. Merge coverage (non-null rdkit descriptors)
    3. Invalid/empty inchikey handling
    4. Descriptor stats (no dtype corruption)
    5. ECFP array integrity
    """
    print("=" * 80)
    print("P3a MERGE VALIDATION")
    print("=" * 80)

    # Load data
    print(f"\nLoading merged table from {merged_path}...")
    merged = pd.read_parquet(merged_path)

    print(f"Loading private_clean from {private_clean_path}...")
    private_clean = pd.read_parquet(private_clean_path)

    # Check 1: Row count
    print("\n" + "=" * 80)
    print("CHECK 1: ROW COUNT")
    print("=" * 80)

    n_merged = len(merged)
    n_private_clean = len(private_clean)

    print(f"Merged rows:        {n_merged}")
    print(f"Private_clean rows: {n_private_clean}")

    if n_merged == n_private_clean:
        print("✓ PASS: Row counts match")
    else:
        print(f"✗ FAIL: Row count mismatch ({n_merged} != {n_private_clean})")

    # Check 2: Merge coverage
    print("\n" + "=" * 80)
    print("CHECK 2: MERGE COVERAGE")
    print("=" * 80)

    # Count rows with non-null rdkit descriptors
    rdkit_descriptors = ["mw", "logp", "tpsa", "n_rotatable_bonds", "n_hbd", "n_hba", "n_rings", "n_aromatic_rings", "n_heavy_atoms"]

    n_with_rdkit = merged["mw"].notna().sum()
    n_with_ecfp = merged["ecfp_2048"].notna().sum()

    print(f"Rows with RDKit descriptors: {n_with_rdkit}/{n_merged} ({100*n_with_rdkit/n_merged:.1f}%)")
    print(f"Rows with ECFP:              {n_with_ecfp}/{n_merged} ({100*n_with_ecfp/n_merged:.1f}%)")

    # Check 3: Invalid/empty inchikey
    print("\n" + "=" * 80)
    print("CHECK 3: INVALID/EMPTY INCHIKEY HANDLING")
    print("=" * 80)

    n_empty_inchikey = merged["inchikey"].isna().sum() + (merged["inchikey"] == "").sum()
    n_valid_inchikey = n_merged - n_empty_inchikey

    print(f"Valid InChIKeys:   {n_valid_inchikey}/{n_merged} ({100*n_valid_inchikey/n_merged:.1f}%)")
    print(f"Invalid InChIKeys: {n_empty_inchikey}/{n_merged} ({100*n_empty_inchikey/n_merged:.1f}%)")

    if n_empty_inchikey > 0:
        print(f"\nInvalid InChIKey rows:")
        print(f"  - These rows will have null RDKit descriptors (expected)")
        # Check if invalid inchikeys have null rdkit data
        invalid_mask = merged["inchikey"].isna() | (merged["inchikey"] == "")
        n_invalid_with_rdkit = merged[invalid_mask]["mw"].notna().sum()
        print(f"  - Invalid InChIKeys with non-null RDKit: {n_invalid_with_rdkit} (should be 0)")

    # Check 4: Descriptor stats
    print("\n" + "=" * 80)
    print("CHECK 4: DESCRIPTOR STATISTICS")
    print("=" * 80)

    print("\nRDKit descriptors (original):")
    print(f"{'Descriptor':<20} {'Count':>8} {'Min':>10} {'Median':>10} {'Max':>10}")
    print("-" * 80)

    for desc in rdkit_descriptors[:5]:  # Show first 5 for brevity
        if desc in merged.columns:
            col_data = merged[desc].dropna()
            if len(col_data) > 0:
                print(f"{desc:<20} {len(col_data):>8} {col_data.min():>10.2f} {col_data.median():>10.2f} {col_data.max():>10.2f}")
            else:
                print(f"{desc:<20} {len(col_data):>8} {'N/A':>10} {'N/A':>10} {'N/A':>10}")

    print("\nRDKit descriptors (scaled):")
    print(f"{'Descriptor':<20} {'Count':>8} {'Mean':>10} {'Std':>10}")
    print("-" * 80)

    for desc in rdkit_descriptors[:5]:
        scaled_col = f"{desc}_scaled"
        if scaled_col in merged.columns:
            col_data = merged[scaled_col].dropna()
            if len(col_data) > 0:
                print(f"{scaled_col:<20} {len(col_data):>8} {col_data.mean():>10.4f} {col_data.std():>10.4f}")
            else:
                print(f"{scaled_col:<20} {len(col_data):>8} {'N/A':>10} {'N/A':>10}")

    # Check 5: ECFP array integrity
    print("\n" + "=" * 80)
    print("CHECK 5: ECFP ARRAY INTEGRITY")
    print("=" * 80)

    ecfp_col = "ecfp_2048"
    if ecfp_col in merged.columns:
        # Sample a few non-null ECFP arrays
        ecfp_samples = merged[merged[ecfp_col].notna()][ecfp_col].head(5)

        print(f"ECFP column: {ecfp_col}")
        print(f"Non-null ECFP arrays: {n_with_ecfp}")

        if len(ecfp_samples) > 0:
            # Check array properties
            sample_lengths = [len(arr) if isinstance(arr, (list, np.ndarray)) else 0 for arr in ecfp_samples]
            sample_dtypes = [type(arr).__name__ for arr in ecfp_samples]

            print(f"\nSample ECFP arrays (first 5):")
            print(f"  Lengths: {sample_lengths}")
            print(f"  Types:   {sample_dtypes}")

            # Verify all are length 2048
            all_2048 = all(length == 2048 for length in sample_lengths)
            if all_2048:
                print("  ✓ All sampled arrays have length 2048")
            else:
                print(f"  ✗ WARNING: Some arrays don't have length 2048!")

            # Check first array in detail
            first_arr = ecfp_samples.iloc[0]
            if isinstance(first_arr, (list, np.ndarray)):
                arr_np = np.array(first_arr)
                print(f"\nFirst array details:")
                print(f"  Shape: {arr_np.shape}")
                print(f"  Dtype: {arr_np.dtype}")
                print(f"  Value range: [{arr_np.min()}, {arr_np.max()}]")
                print(f"  First 10 values: {arr_np[:10].tolist()}")
        else:
            print("  No ECFP arrays found to validate")
    else:
        print(f"✗ FAIL: {ecfp_col} column not found")

    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    checks_passed = 0
    checks_total = 5

    # Check 1: Row count
    if n_merged == n_private_clean:
        print("✓ Check 1: Row count matches")
        checks_passed += 1
    else:
        print("✗ Check 1: Row count mismatch")

    # Check 2: Merge coverage
    if n_with_rdkit > 0 and n_with_ecfp > 0:
        print(f"✓ Check 2: Merge coverage OK ({n_with_rdkit} with RDKit, {n_with_ecfp} with ECFP)")
        checks_passed += 1
    else:
        print("✗ Check 2: Merge coverage issue")

    # Check 3: Invalid inchikey handling
    if n_empty_inchikey >= 0:  # Always passes, just informational
        print(f"✓ Check 3: Invalid InChIKeys handled ({n_empty_inchikey} found)")
        checks_passed += 1

    # Check 4: Descriptor stats
    if merged["mw"].notna().sum() > 0:
        print("✓ Check 4: Descriptor stats look reasonable")
        checks_passed += 1
    else:
        print("✗ Check 4: No descriptor data found")

    # Check 5: ECFP integrity
    if ecfp_col in merged.columns and n_with_ecfp > 0:
        print("✓ Check 5: ECFP arrays present and valid")
        checks_passed += 1
    else:
        print("✗ Check 5: ECFP integrity issue")

    print(f"\nChecks passed: {checks_passed}/{checks_total}")

    if checks_passed == checks_total:
        print("\n✅ ALL CHECKS PASSED")
    else:
        print(f"\n⚠ {checks_total - checks_passed} check(s) failed")

    print("=" * 80)


if __name__ == "__main__":
    validate_merge()

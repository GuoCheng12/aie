"""
src/features/anchor_ecfp.py

Build ECFP-only anchor neighbor relationships using Tanimoto similarity.
This is P4a - an urgent branch to enable UQ development before P2 (aTB) completes.

Usage:
    python -m src.features.anchor_ecfp --k 10
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger

logger = get_logger(__name__)

# InChIKey pattern: 14 chars - 10 chars - 1 char (e.g., AAAQKTZKLRYKHR-UHFFFAOYSA-N)
INCHIKEY_PATTERN = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


def is_valid_inchikey(inchikey: str) -> bool:
    """Check if string is a valid InChIKey format."""
    if not inchikey or pd.isna(inchikey):
        return False
    return bool(INCHIKEY_PATTERN.match(str(inchikey)))


def load_ecfp_data(rdkit_features_path: str = "data/rdkit_features.parquet") -> pd.DataFrame:
    """
    Load rdkit_features.parquet and filter to valid InChIKeys.

    Returns:
        DataFrame with columns: inchikey, canonical_smiles, ecfp_2048
    """
    logger.info(f"Loading ECFP data from {rdkit_features_path}")
    df = pd.read_parquet(rdkit_features_path)

    n_total = len(df)
    logger.info(f"Loaded {n_total} molecules")

    # Filter to valid InChIKeys
    valid_mask = df["inchikey"].apply(is_valid_inchikey)
    df_valid = df[valid_mask].copy()

    n_valid = len(df_valid)
    n_invalid = n_total - n_valid
    if n_invalid > 0:
        logger.warning(f"Filtered out {n_invalid} molecules with invalid InChIKeys")

    logger.info(f"Valid molecules: {n_valid}")

    selected_cols = ["inchikey", "ecfp_2048"]
    if "canonical_smiles" in df_valid.columns:
        selected_cols.insert(1, "canonical_smiles")
    result = df_valid[selected_cols].copy()
    result.attrs["skipped_invalid_inchikey_count"] = int(n_invalid)
    result.attrs["total_molecules_input"] = int(n_total)
    result.attrs["valid_molecules"] = int(n_valid)
    return result


def to_binary_fingerprint(fp: np.ndarray) -> np.ndarray:
    """
    Coerce fingerprint to binary (0/1) uint8 array.

    Guards against non-{0,1} values by treating any positive value as 1.
    """
    if fp is None:
        return None
    fp_array = np.asarray(fp)
    # Coerce to boolean: any value > 0 becomes 1
    return (fp_array > 0).astype(np.uint8)


def tanimoto_similarity(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """
    Compute Tanimoto similarity between two binary fingerprints.

    Tanimoto = |A ∩ B| / |A ∪ B| = c / (a + b - c)
    where:
        a = number of 1-bits in fp1
        b = number of 1-bits in fp2
        c = number of positions where both are 1

    Args:
        fp1: Binary fingerprint (uint8 array with 0/1 values)
        fp2: Binary fingerprint (uint8 array with 0/1 values)

    Returns:
        Tanimoto similarity in [0, 1]
    """
    # Use logical_and for intersection (safer than bitwise &)
    intersection = np.sum(np.logical_and(fp1, fp2))

    # Count 1-bits in each
    a = np.sum(fp1 > 0)
    b = np.sum(fp2 > 0)

    # Union = a + b - intersection
    union = a + b - intersection

    if union == 0:
        # Both fingerprints are all zeros - undefined, return 0
        return 0.0

    return float(intersection) / float(union)


def compute_all_neighbors(
    df: pd.DataFrame,
    k: int = 10,
    progress_every: int = 100
) -> pd.DataFrame:
    """
    Compute top-k Tanimoto neighbors for all molecules (excluding self).

    Args:
        df: DataFrame with inchikey and ecfp_2048 columns
        k: Number of neighbors to return per molecule
        progress_every: Log progress every N molecules

    Returns:
        DataFrame with columns: inchikey, neighbor_inchikey, rank, tanimoto_sim
    """
    n = len(df)
    logger.info(f"Computing {k} nearest neighbors for {n} molecules")
    logger.info(f"Total pairwise comparisons: {n * (n - 1) // 2}")

    # Extract data as lists for faster iteration
    inchikeys = df["inchikey"].tolist()
    canonical_smiles = (
        df["canonical_smiles"].fillna("").astype(str).str.strip().tolist()
        if "canonical_smiles" in df.columns
        else ["" for _ in range(n)]
    )

    # Precompute binary fingerprints
    logger.info("Converting fingerprints to binary...")
    fingerprints = [to_binary_fingerprint(fp) for fp in df["ecfp_2048"]]

    # Check for any None fingerprints
    none_count = sum(1 for fp in fingerprints if fp is None)
    if none_count > 0:
        logger.warning(f"Found {none_count} molecules with None fingerprints")

    # Precompute bit counts for efficiency
    bit_counts = [np.sum(fp > 0) if fp is not None else 0 for fp in fingerprints]

    # Results accumulator
    results: List[Tuple[str, str, int, float]] = []

    logger.info("Computing pairwise Tanimoto similarities...")
    for i in range(n):
        if (i + 1) % progress_every == 0:
            logger.info(f"Progress: {i + 1}/{n} molecules processed")

        fp_i = fingerprints[i]
        if fp_i is None:
            continue

        # Compute similarity to all other molecules
        similarities = []
        for j in range(n):
            if i == j:  # Skip self index
                continue
            # Extra self-exclusion guard for deduplicated canonical molecules.
            if canonical_smiles[i] and canonical_smiles[i] == canonical_smiles[j]:
                continue
            fp_j = fingerprints[j]
            if fp_j is None:
                continue

            # Compute Tanimoto
            intersection = np.sum(np.logical_and(fp_i, fp_j))
            union = bit_counts[i] + bit_counts[j] - intersection

            if union > 0:
                sim = float(intersection) / float(union)
            else:
                sim = 0.0

            similarities.append((j, sim))

        # Sort by similarity (descending) and take top-k
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_k = similarities[:k]

        # Add to results
        for rank, (j, sim) in enumerate(top_k, start=1):
            results.append((inchikeys[i], inchikeys[j], rank, sim))

    logger.info(f"Computed {len(results)} neighbor relationships")

    # Create DataFrame
    neighbors_df = pd.DataFrame(results, columns=[
        "inchikey", "neighbor_inchikey", "rank", "tanimoto_sim"
    ])

    return neighbors_df


def build_anchor_neighbors(
    output_path: str = "data/anchor_neighbors_ecfp.parquet",
    rdkit_features_path: str = "data/rdkit_features.parquet",
    k: int = 10,
    manifest_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Main entry point: build and save anchor neighbor relationships.

    Args:
        output_path: Path to save the neighbors parquet
        rdkit_features_path: Path to rdkit_features.parquet
        k: Number of neighbors per molecule

    Returns:
        The computed neighbors DataFrame
    """
    # Load ECFP data
    df = load_ecfp_data(rdkit_features_path)

    # Compute neighbors
    neighbors_df = compute_all_neighbors(df, k=k)

    # Save
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    neighbors_df.to_parquet(output_path, index=False)
    logger.info(f"Saved neighbors to {output_path}")

    skipped_invalid_inchikey_count = int(df.attrs.get("skipped_invalid_inchikey_count", 0))
    total_molecules_input = int(df.attrs.get("total_molecules_input", len(df)))
    valid_molecules = int(df.attrs.get("valid_molecules", len(df)))

    # Summary stats
    logger.info("=== Summary ===")
    logger.info(f"Total molecules input: {total_molecules_input}")
    logger.info(f"Valid molecules: {valid_molecules}")
    logger.info(f"Skipped invalid InChIKeys: {skipped_invalid_inchikey_count}")
    logger.info(f"Total neighbor records: {len(neighbors_df)}")
    logger.info(f"Neighbors per molecule: {k}")

    # Top-1 similarity stats
    top1 = neighbors_df[neighbors_df["rank"] == 1]["tanimoto_sim"]
    logger.info(f"Top-1 similarity: min={top1.min():.3f}, median={top1.median():.3f}, max={top1.max():.3f}")

    if manifest_path is None:
        manifest_file = output_path.with_name(f"{output_path.stem}_manifest.json")
    else:
        manifest_file = Path(manifest_path)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "timestamp": datetime.now().isoformat(),
        "input": str(rdkit_features_path),
        "output": str(output_path),
        "k": int(k),
        "total_molecules_input": total_molecules_input,
        "valid_molecules": valid_molecules,
        "skipped_invalid_inchikey_count": skipped_invalid_inchikey_count,
        "neighbor_records": int(len(neighbors_df)),
        "anchor_query_unique_inchikey": int(neighbors_df["inchikey"].nunique()) if len(neighbors_df) > 0 else 0,
    }

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Saved manifest to {manifest_file}")

    return neighbors_df


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build ECFP-only anchor neighbor relationships using Tanimoto similarity"
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Number of neighbors per molecule (default: 10)"
    )
    parser.add_argument(
        "--output", type=str, default="data/anchor_neighbors_ecfp.parquet",
        help="Output parquet path (default: data/anchor_neighbors_ecfp.parquet)"
    )
    parser.add_argument(
        "--input", type=str, default="data/rdkit_features.parquet",
        help="Input rdkit_features.parquet path"
    )
    parser.add_argument(
        "--manifest", type=str, default=None,
        help="Manifest JSON path (default: <output_stem>_manifest.json)"
    )

    args = parser.parse_args()

    build_anchor_neighbors(
        output_path=args.output,
        rdkit_features_path=args.input,
        k=args.k,
        manifest_path=args.manifest
    )


if __name__ == "__main__":
    main()

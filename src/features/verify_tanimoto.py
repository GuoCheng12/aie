"""
src/features/verify_tanimoto.py

Verify Tanimoto similarity computation against RDKit's official implementation.

This script compares the current numpy-based Tanimoto implementation in anchor_ecfp.py
against RDKit's DataStructs.TanimotoSimilarity() function.

Usage:
    python -m src.features.verify_tanimoto
"""

import argparse
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem

try:
    from rdkit.Chem import rdFingerprintGenerator
    USE_NEW_API = True
except ImportError:
    USE_NEW_API = False

from src.utils.logging import get_logger
from src.features.anchor_ecfp import (
    is_valid_inchikey,
    to_binary_fingerprint,
    tanimoto_similarity as numpy_tanimoto,
)

logger = get_logger(__name__)


def numpy_array_to_rdkit_bitvector(fp_array: np.ndarray) -> DataStructs.ExplicitBitVect:
    """
    Convert numpy fingerprint array to RDKit ExplicitBitVect.
    
    Args:
        fp_array: Numpy array with shape (2048,) containing 0/1 values
        
    Returns:
        RDKit ExplicitBitVect for use with DataStructs.TanimotoSimilarity
    """
    # Ensure binary
    fp_binary = (fp_array > 0).astype(np.uint8)
    
    # Create RDKit bit vector
    n_bits = len(fp_binary)
    bv = DataStructs.ExplicitBitVect(n_bits)
    
    # Set bits
    on_bits = np.where(fp_binary > 0)[0]
    for bit in on_bits:
        bv.SetBit(int(bit))
    
    return bv


def rdkit_tanimoto(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """
    Compute Tanimoto similarity using RDKit's official implementation.
    
    Args:
        fp1: Numpy fingerprint array
        fp2: Numpy fingerprint array
        
    Returns:
        Tanimoto similarity computed by RDKit
    """
    bv1 = numpy_array_to_rdkit_bitvector(fp1)
    bv2 = numpy_array_to_rdkit_bitvector(fp2)
    return DataStructs.TanimotoSimilarity(bv1, bv2)


def compute_ecfp_from_smiles(smiles: str, n_bits: int = 2048, radius: int = 2) -> np.ndarray:
    """
    Compute ECFP fingerprint from SMILES using RDKit.
    
    Returns numpy array for consistency with existing data.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    
    if USE_NEW_API:
        generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
        fp = generator.GetFingerprint(mol)
    else:
        fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=n_bits)
    
    # Convert to numpy array
    fp_array = np.zeros(n_bits, dtype=np.int8)
    DataStructs.ConvertToNumpyArray(fp, fp_array)
    return fp_array


def get_rdkit_tanimoto_direct(smiles1: str, smiles2: str, n_bits: int = 2048, radius: int = 2) -> float:
    """
    Compute Tanimoto similarity directly from SMILES using RDKit's native workflow.
    
    This bypasses numpy arrays entirely for maximum fidelity to RDKit's implementation.
    """
    mol1 = Chem.MolFromSmiles(smiles1)
    mol2 = Chem.MolFromSmiles(smiles2)
    
    if mol1 is None or mol2 is None:
        return None
    
    if USE_NEW_API:
        generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
        fp1 = generator.GetFingerprint(mol1)
        fp2 = generator.GetFingerprint(mol2)
    else:
        fp1 = AllChem.GetMorganFingerprintAsBitVect(mol1, radius=radius, nBits=n_bits)
        fp2 = AllChem.GetMorganFingerprintAsBitVect(mol2, radius=radius, nBits=n_bits)
    
    return DataStructs.TanimotoSimilarity(fp1, fp2)


def verify_single_pair(fp1: np.ndarray, fp2: np.ndarray) -> Tuple[float, float, float]:
    """
    Verify a single pair of fingerprints.
    
    Returns:
        (numpy_tanimoto, rdkit_tanimoto, absolute_difference)
    """
    # Convert to binary
    fp1_binary = to_binary_fingerprint(fp1)
    fp2_binary = to_binary_fingerprint(fp2)
    
    # Compute with our numpy implementation
    np_sim = numpy_tanimoto(fp1_binary, fp2_binary)
    
    # Compute with RDKit
    rdkit_sim = rdkit_tanimoto(fp1, fp2)
    
    diff = abs(np_sim - rdkit_sim)
    
    return np_sim, rdkit_sim, diff


def verify_against_stored_neighbors(
    neighbors_path: str = "data/anchor_neighbors_ecfp.parquet",
    rdkit_features_path: str = "data/rdkit_features.parquet",
    sample_size: int = 100,
    tolerance: float = 1e-9
) -> dict:
    """
    Verify stored neighbor similarities against RDKit's computation.
    
    Args:
        neighbors_path: Path to anchor_neighbors_ecfp.parquet
        rdkit_features_path: Path to rdkit_features.parquet
        sample_size: Number of random pairs to verify
        tolerance: Maximum acceptable difference
        
    Returns:
        Verification report dict
    """
    logger.info("=== Verifying Tanimoto Implementation Against RDKit ===")
    
    # Load data
    neighbors_df = pd.read_parquet(neighbors_path)
    rdkit_df = pd.read_parquet(rdkit_features_path)
    
    logger.info(f"Loaded {len(neighbors_df)} neighbor relationships")
    logger.info(f"Loaded {len(rdkit_df)} molecules with ECFP")
    
    # Create InChIKey -> fingerprint lookup
    fp_lookup = {}
    for _, row in rdkit_df.iterrows():
        if is_valid_inchikey(row["inchikey"]):
            fp_lookup[row["inchikey"]] = row["ecfp_2048"]
    
    logger.info(f"Created fingerprint lookup for {len(fp_lookup)} molecules")
    
    # Sample random pairs from neighbors
    sample_size = min(sample_size, len(neighbors_df))
    sample_df = neighbors_df.sample(n=sample_size, random_state=42)
    
    logger.info(f"Verifying {sample_size} random neighbor pairs...")
    
    results = []
    discrepancies = []
    
    for _, row in sample_df.iterrows():
        inchikey1 = row["inchikey"]
        inchikey2 = row["neighbor_inchikey"]
        stored_sim = row["tanimoto_sim"]
        
        # Get fingerprints
        if inchikey1 not in fp_lookup or inchikey2 not in fp_lookup:
            continue
        
        fp1 = fp_lookup[inchikey1]
        fp2 = fp_lookup[inchikey2]
        
        # Verify
        np_sim, rdkit_sim, diff = verify_single_pair(fp1, fp2)
        
        results.append({
            "inchikey1": inchikey1,
            "inchikey2": inchikey2,
            "stored_sim": stored_sim,
            "numpy_sim": np_sim,
            "rdkit_sim": rdkit_sim,
            "stored_vs_numpy_diff": abs(stored_sim - np_sim),
            "numpy_vs_rdkit_diff": diff,
        })
        
        # Check for discrepancies
        if diff > tolerance:
            discrepancies.append({
                "inchikey1": inchikey1,
                "inchikey2": inchikey2,
                "numpy_sim": np_sim,
                "rdkit_sim": rdkit_sim,
                "diff": diff,
            })
        
        # Check if stored matches numpy (sanity check)
        if abs(stored_sim - np_sim) > tolerance:
            discrepancies.append({
                "type": "stored_vs_numpy",
                "inchikey1": inchikey1,
                "inchikey2": inchikey2,
                "stored_sim": stored_sim,
                "numpy_sim": np_sim,
                "diff": abs(stored_sim - np_sim),
            })
    
    # Summary stats
    results_df = pd.DataFrame(results)
    
    report = {
        "pairs_verified": len(results),
        "discrepancies_found": len(discrepancies),
        "tolerance": tolerance,
        "stats": {
            "stored_vs_numpy_diff": {
                "mean": results_df["stored_vs_numpy_diff"].mean(),
                "max": results_df["stored_vs_numpy_diff"].max(),
                "std": results_df["stored_vs_numpy_diff"].std(),
            },
            "numpy_vs_rdkit_diff": {
                "mean": results_df["numpy_vs_rdkit_diff"].mean(),
                "max": results_df["numpy_vs_rdkit_diff"].max(),
                "std": results_df["numpy_vs_rdkit_diff"].std(),
            },
        },
        "discrepancies": discrepancies[:10],  # First 10 only
    }
    
    # Log results
    logger.info("")
    logger.info("=== Verification Results ===")
    logger.info(f"Pairs verified: {report['pairs_verified']}")
    logger.info(f"Discrepancies found: {report['discrepancies_found']}")
    logger.info("")
    
    logger.info("Stored vs Numpy Implementation:")
    logger.info(f"  Mean diff: {report['stats']['stored_vs_numpy_diff']['mean']:.2e}")
    logger.info(f"  Max diff:  {report['stats']['stored_vs_numpy_diff']['max']:.2e}")
    
    logger.info("")
    logger.info("Numpy vs RDKit Official:")
    logger.info(f"  Mean diff: {report['stats']['numpy_vs_rdkit_diff']['mean']:.2e}")
    logger.info(f"  Max diff:  {report['stats']['numpy_vs_rdkit_diff']['max']:.2e}")
    
    if len(discrepancies) == 0:
        logger.info("")
        logger.info("✅ SUCCESS: All pairs match RDKit's official Tanimoto within tolerance!")
    else:
        logger.warning("")
        logger.warning(f"⚠️ WARNING: {len(discrepancies)} discrepancies found!")
        for d in discrepancies[:5]:
            logger.warning(f"  {d}")
    
    return report


def recalculate_neighbors_with_rdkit(
    rdkit_features_path: str = "data/rdkit_features.parquet",
    output_path: str = "data/anchor_neighbors_ecfp_rdkit_verified.parquet",
    k: int = 10,
    progress_every: int = 100
) -> pd.DataFrame:
    """
    Recalculate all neighbor similarities using RDKit's official Tanimoto.
    
    This provides a verified baseline using RDKit's DataStructs module.
    
    Args:
        rdkit_features_path: Path to rdkit_features.parquet
        output_path: Path to save verified neighbors
        k: Number of neighbors per molecule
        
    Returns:
        DataFrame with verified neighbors
    """
    logger.info("=== Recalculating Neighbors with RDKit Official Tanimoto ===")
    
    # Load data
    df = pd.read_parquet(rdkit_features_path)
    logger.info(f"Loaded {len(df)} molecules")
    
    # Filter valid InChIKeys
    valid_mask = df["inchikey"].apply(is_valid_inchikey)
    df = df[valid_mask].copy()
    logger.info(f"Valid molecules: {len(df)}")
    
    n = len(df)
    inchikeys = df["inchikey"].tolist()
    
    # Convert fingerprints to RDKit bit vectors
    logger.info("Converting fingerprints to RDKit BitVects...")
    bit_vectors = []
    for fp in df["ecfp_2048"]:
        bv = numpy_array_to_rdkit_bitvector(fp)
        bit_vectors.append(bv)
    
    logger.info(f"Computing {k} nearest neighbors for {n} molecules using RDKit Tanimoto...")
    logger.info(f"Total pairwise comparisons: {n * (n - 1) // 2}")
    
    results: List[Tuple[str, str, int, float]] = []
    
    for i in range(n):
        if (i + 1) % progress_every == 0:
            logger.info(f"Progress: {i + 1}/{n} molecules processed")
        
        bv_i = bit_vectors[i]
        
        # Compute similarity to all other molecules using RDKit
        similarities = []
        for j in range(n):
            if i == j:  # Skip self
                continue
            
            bv_j = bit_vectors[j]
            sim = DataStructs.TanimotoSimilarity(bv_i, bv_j)
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
    
    # Save
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    neighbors_df.to_parquet(output_path, index=False)
    logger.info(f"Saved RDKit-verified neighbors to {output_path}")
    
    # Summary stats
    top1 = neighbors_df[neighbors_df["rank"] == 1]["tanimoto_sim"]
    logger.info(f"Top-1 similarity: min={top1.min():.3f}, median={top1.median():.3f}, max={top1.max():.3f}")
    
    return neighbors_df


def compare_implementations(
    original_path: str = "data/anchor_neighbors_ecfp.parquet",
    verified_path: str = "data/anchor_neighbors_ecfp_rdkit_verified.parquet"
) -> dict:
    """
    Compare original numpy-based results with RDKit-verified results.
    """
    logger.info("=== Comparing Original vs RDKit-Verified Neighbors ===")
    
    original_df = pd.read_parquet(original_path)
    verified_df = pd.read_parquet(verified_path)
    
    # Merge on (inchikey, neighbor_inchikey, rank)
    merged = original_df.merge(
        verified_df,
        on=["inchikey", "neighbor_inchikey", "rank"],
        suffixes=("_original", "_rdkit")
    )
    
    logger.info(f"Matched {len(merged)} of {len(original_df)} neighbor pairs")
    
    # Compute differences
    merged["diff"] = abs(merged["tanimoto_sim_original"] - merged["tanimoto_sim_rdkit"])
    
    report = {
        "total_pairs": len(original_df),
        "matched_pairs": len(merged),
        "match_rate": len(merged) / len(original_df) if len(original_df) > 0 else 0,
        "diff_stats": {
            "mean": merged["diff"].mean(),
            "std": merged["diff"].std(),
            "max": merged["diff"].max(),
            "min": merged["diff"].min(),
        },
        "exact_matches": (merged["diff"] == 0).sum(),
        "close_matches_1e9": (merged["diff"] < 1e-9).sum(),
        "close_matches_1e6": (merged["diff"] < 1e-6).sum(),
    }
    
    logger.info("")
    logger.info(f"Total pairs: {report['total_pairs']}")
    logger.info(f"Matched pairs: {report['matched_pairs']} ({report['match_rate']*100:.1f}%)")
    logger.info("")
    logger.info("Tanimoto difference (original vs RDKit):")
    logger.info(f"  Mean: {report['diff_stats']['mean']:.2e}")
    logger.info(f"  Max:  {report['diff_stats']['max']:.2e}")
    logger.info(f"  Min:  {report['diff_stats']['min']:.2e}")
    logger.info("")
    logger.info(f"Exact matches (diff=0): {report['exact_matches']}")
    logger.info(f"Within 1e-9: {report['close_matches_1e9']}")
    logger.info(f"Within 1e-6: {report['close_matches_1e6']}")
    
    if report['diff_stats']['max'] < 1e-9:
        logger.info("")
        logger.info("✅ PERFECT MATCH: Implementation is identical to RDKit!")
    elif report['diff_stats']['max'] < 1e-6:
        logger.info("")
        logger.info("✅ EXCELLENT: Within floating-point tolerance!")
    else:
        logger.warning("")
        logger.warning("⚠️ Significant differences found - investigate!")
    
    return report


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Verify Tanimoto implementation against RDKit's official computation"
    )
    parser.add_argument(
        "--mode", type=str, default="verify",
        choices=["verify", "recalculate", "compare", "full"],
        help="Mode: verify (sample check), recalculate (full recompute), compare (diff two files), full (all steps)"
    )
    parser.add_argument(
        "--sample-size", type=int, default=100,
        help="Number of pairs to verify in 'verify' mode (default: 100)"
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Number of neighbors in 'recalculate' mode (default: 10)"
    )
    
    args = parser.parse_args()
    
    if args.mode == "verify" or args.mode == "full":
        verify_against_stored_neighbors(sample_size=args.sample_size)
    
    if args.mode == "recalculate" or args.mode == "full":
        recalculate_neighbors_with_rdkit(k=args.k)
    
    if args.mode == "compare" or args.mode == "full":
        # Only run compare if verified file exists
        verified_path = Path("data/anchor_neighbors_ecfp_rdkit_verified.parquet")
        if verified_path.exists():
            compare_implementations()
        else:
            logger.warning("Verified file doesn't exist. Run with --mode recalculate first.")


if __name__ == "__main__":
    main()

"""
src/features/validate_anchor_space.py

Validation script for ECFP anchor neighbor relationships.
Prints a comprehensive report with similarity distributions and suspicious case detection.

Usage:
    python -m src.features.validate_anchor_space
"""

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

from src.utils.logging import get_logger

logger = get_logger(__name__)


def load_neighbors(
    neighbors_path: str = "data/anchor_neighbors_ecfp.parquet"
) -> pd.DataFrame:
    """Load anchor neighbors parquet."""
    return pd.read_parquet(neighbors_path)


def load_rdkit_features(
    rdkit_path: str = "data/rdkit_features.parquet"
) -> pd.DataFrame:
    """Load rdkit features for descriptor correlation check."""
    return pd.read_parquet(rdkit_path)


def similarity_distribution_summary(
    neighbors_df: pd.DataFrame
) -> dict:
    """
    Compute similarity distribution statistics for top-1 and top-10 neighbors.

    Returns dict with stats for each rank level.
    """
    stats = {}

    # Top-1 stats
    top1 = neighbors_df[neighbors_df["rank"] == 1]["tanimoto_sim"]
    stats["top1"] = {
        "min": top1.min(),
        "median": top1.median(),
        "p95": top1.quantile(0.95),
        "max": top1.max(),
        "mean": top1.mean(),
        "std": top1.std(),
    }

    # Top-10 stats (all neighbors)
    all_sims = neighbors_df["tanimoto_sim"]
    stats["all"] = {
        "min": all_sims.min(),
        "median": all_sims.median(),
        "p95": all_sims.quantile(0.95),
        "max": all_sims.max(),
        "mean": all_sims.mean(),
        "std": all_sims.std(),
    }

    # Top-10 (rank=10 specifically)
    top10 = neighbors_df[neighbors_df["rank"] == 10]["tanimoto_sim"]
    stats["top10"] = {
        "min": top10.min(),
        "median": top10.median(),
        "p95": top10.quantile(0.95),
        "max": top10.max(),
        "mean": top10.mean(),
        "std": top10.std(),
    }

    return stats


def sample_neighbors(
    neighbors_df: pd.DataFrame,
    n_samples: int = 5,
    top_k: int = 5,
    seed: int = 42
) -> list:
    """
    Sample n random molecules and return their top-k neighbors.

    Returns list of dicts with inchikey and neighbors.
    """
    random.seed(seed)
    unique_inchikeys = neighbors_df["inchikey"].unique().tolist()
    sampled = random.sample(unique_inchikeys, min(n_samples, len(unique_inchikeys)))

    results = []
    for ik in sampled:
        mol_neighbors = neighbors_df[neighbors_df["inchikey"] == ik]
        mol_neighbors = mol_neighbors.sort_values("rank").head(top_k)

        neighbors_list = []
        for _, row in mol_neighbors.iterrows():
            neighbors_list.append({
                "rank": int(row["rank"]),
                "neighbor": row["neighbor_inchikey"],
                "sim": float(row["tanimoto_sim"]),
            })

        results.append({
            "inchikey": ik,
            "neighbors": neighbors_list,
        })

    return results


def detect_suspicious_cases(
    neighbors_df: pd.DataFrame,
    high_sim_threshold: float = 0.95,
    low_sim_threshold: float = 0.10
) -> dict:
    """
    Detect suspicious cases:
    - Molecules with top-1 similarity >= high_sim_threshold (potential duplicates)
    - Molecules with top-1 similarity <= low_sim_threshold (fingerprint issues)

    Returns dict with counts and example InChIKeys.
    """
    top1_df = neighbors_df[neighbors_df["rank"] == 1]

    # High similarity (potential duplicates)
    high_sim_mask = top1_df["tanimoto_sim"] >= high_sim_threshold
    high_sim_df = top1_df[high_sim_mask]
    high_sim_count = len(high_sim_df)
    high_sim_examples = high_sim_df.head(5)[["inchikey", "neighbor_inchikey", "tanimoto_sim"]].to_dict("records")

    # Low similarity (fingerprint issues)
    low_sim_mask = top1_df["tanimoto_sim"] <= low_sim_threshold
    low_sim_df = top1_df[low_sim_mask]
    low_sim_count = len(low_sim_df)
    low_sim_examples = low_sim_df.head(5)[["inchikey", "neighbor_inchikey", "tanimoto_sim"]].to_dict("records")

    return {
        "high_sim": {
            "threshold": high_sim_threshold,
            "count": high_sim_count,
            "examples": high_sim_examples,
        },
        "low_sim": {
            "threshold": low_sim_threshold,
            "count": low_sim_count,
            "examples": low_sim_examples,
        },
    }


def load_molecule_table(
    molecule_table_path: str = "data/molecule_table.parquet"
) -> pd.DataFrame:
    """Load molecule table for SMILES lookup."""
    return pd.read_parquet(molecule_table_path)


def random_similarity_distribution(
    rdkit_df: pd.DataFrame,
    n_samples: int = 1000,
    seed: int = 42
) -> dict:
    """
    Compute Tanimoto similarity distribution for RANDOM pairs.

    This provides a baseline to compare against actual neighbor similarities.
    If neighbor similarities are not much higher than random, the fingerprints
    may not be discriminative enough.
    """
    from src.features.anchor_ecfp import to_binary_fingerprint, tanimoto_similarity

    random.seed(seed)
    np.random.seed(seed)

    # Get valid fingerprints
    valid_fps = []
    for fp in rdkit_df["ecfp_2048"]:
        if fp is not None:
            valid_fps.append(to_binary_fingerprint(fp))

    n_mols = len(valid_fps)
    if n_mols < 2:
        return {"error": "Not enough valid fingerprints", "n_pairs": 0}

    # Sample random pairs
    sims = []
    for _ in range(n_samples):
        i, j = random.sample(range(n_mols), 2)
        sim = tanimoto_similarity(valid_fps[i], valid_fps[j])
        sims.append(sim)

    sims = np.array(sims)

    return {
        "n_pairs": n_samples,
        "min": float(sims.min()),
        "median": float(np.median(sims)),
        "mean": float(sims.mean()),
        "std": float(sims.std()),
        "p95": float(np.percentile(sims, 95)),
        "max": float(sims.max()),
    }


def find_perfect_similarity_pairs(
    neighbors_df: pd.DataFrame,
    molecule_table_df: pd.DataFrame,
    sim_threshold: float = 1.0,
    max_pairs: int = 10
) -> list:
    """
    Find pairs with similarity == sim_threshold (default 1.0) and return their SMILES.

    This helps identify potential duplicates or identical molecules with different InChIKeys.
    """
    # Filter to pairs with perfect similarity
    top1_df = neighbors_df[neighbors_df["rank"] == 1]
    perfect_pairs = top1_df[top1_df["tanimoto_sim"] >= sim_threshold]

    if len(perfect_pairs) == 0:
        return []

    # Build InChIKey → SMILES lookup
    smiles_lookup = molecule_table_df.set_index("inchikey")["canonical_smiles"].to_dict()

    results = []
    for _, row in perfect_pairs.head(max_pairs).iterrows():
        ik1 = row["inchikey"]
        ik2 = row["neighbor_inchikey"]
        sim = row["tanimoto_sim"]

        smiles1 = smiles_lookup.get(ik1, "N/A")
        smiles2 = smiles_lookup.get(ik2, "N/A")

        results.append({
            "inchikey_1": ik1,
            "inchikey_2": ik2,
            "tanimoto_sim": sim,
            "smiles_1": smiles1,
            "smiles_2": smiles2,
            "smiles_identical": smiles1 == smiles2,
        })

    return results


def descriptor_correlation_check(
    neighbors_df: pd.DataFrame,
    rdkit_df: pd.DataFrame,
    n_pairs: int = 100,
    high_sim_threshold: float = 0.8,
    seed: int = 42
) -> dict:
    """
    Check if high-Tanimoto pairs have similar descriptor values (mw, logp).

    This is a sanity check: structurally similar molecules should have similar properties.
    """
    random.seed(seed)

    # Get high-similarity pairs
    top1_df = neighbors_df[neighbors_df["rank"] == 1]
    high_sim_pairs = top1_df[top1_df["tanimoto_sim"] >= high_sim_threshold]

    if len(high_sim_pairs) < n_pairs:
        n_pairs = len(high_sim_pairs)

    if n_pairs == 0:
        return {"error": "No high-similarity pairs found", "n_pairs": 0}

    sampled_pairs = high_sim_pairs.sample(n=n_pairs, random_state=seed)

    # Build InChIKey → descriptor lookup
    rdkit_indexed = rdkit_df.set_index("inchikey")

    mw_diffs = []
    logp_diffs = []

    for _, row in sampled_pairs.iterrows():
        ik1, ik2 = row["inchikey"], row["neighbor_inchikey"]

        try:
            mw1 = rdkit_indexed.loc[ik1, "mw"]
            mw2 = rdkit_indexed.loc[ik2, "mw"]
            logp1 = rdkit_indexed.loc[ik1, "logp"]
            logp2 = rdkit_indexed.loc[ik2, "logp"]

            # Relative difference for MW, absolute for LogP
            if mw1 > 0:
                mw_diffs.append(abs(mw1 - mw2) / mw1)
            logp_diffs.append(abs(logp1 - logp2))
        except KeyError:
            continue

    if len(mw_diffs) == 0:
        return {"error": "Could not compute descriptor differences", "n_pairs": 0}

    return {
        "n_pairs": len(mw_diffs),
        "mw_relative_diff": {
            "mean": np.mean(mw_diffs),
            "median": np.median(mw_diffs),
            "max": np.max(mw_diffs),
        },
        "logp_absolute_diff": {
            "mean": np.mean(logp_diffs),
            "median": np.median(logp_diffs),
            "max": np.max(logp_diffs),
        },
    }


def print_report(
    neighbors_df: pd.DataFrame,
    rdkit_df: pd.DataFrame = None,
    molecule_table_df: pd.DataFrame = None,
    n_sample_molecules: int = 5,
) -> None:
    """Print comprehensive validation report to stdout."""
    print("=" * 60)
    print("ANCHOR SPACE VALIDATION REPORT (ECFP-only)")
    print("=" * 60)

    # Basic stats
    n_molecules = neighbors_df["inchikey"].nunique()
    n_records = len(neighbors_df)
    k = n_records // n_molecules if n_molecules > 0 else 0

    print(f"\nTotal molecules: {n_molecules}")
    print(f"Total neighbor records: {n_records}")
    print(f"Neighbors per molecule (k): {k}")

    # Similarity distribution
    print("\n" + "-" * 40)
    print("SIMILARITY DISTRIBUTION")
    print("-" * 40)

    stats = similarity_distribution_summary(neighbors_df)

    print("\nTop-1 (most similar neighbor):")
    print(f"  Min:    {stats['top1']['min']:.4f}")
    print(f"  Median: {stats['top1']['median']:.4f}")
    print(f"  95th:   {stats['top1']['p95']:.4f}")
    print(f"  Max:    {stats['top1']['max']:.4f}")
    print(f"  Mean:   {stats['top1']['mean']:.4f} (std={stats['top1']['std']:.4f})")

    print("\nTop-10 (10th neighbor, least similar in top-k):")
    print(f"  Min:    {stats['top10']['min']:.4f}")
    print(f"  Median: {stats['top10']['median']:.4f}")
    print(f"  95th:   {stats['top10']['p95']:.4f}")
    print(f"  Max:    {stats['top10']['max']:.4f}")

    print("\nAll neighbors (combined):")
    print(f"  Min:    {stats['all']['min']:.4f}")
    print(f"  Median: {stats['all']['median']:.4f}")
    print(f"  Mean:   {stats['all']['mean']:.4f} (std={stats['all']['std']:.4f})")

    # Random similarity distribution (baseline)
    if rdkit_df is not None:
        print("\n" + "-" * 40)
        print("RANDOM SIMILARITY DISTRIBUTION (baseline)")
        print("-" * 40)

        random_stats = random_similarity_distribution(rdkit_df, n_samples=1000)
        if "error" in random_stats:
            print(f"  {random_stats['error']}")
        else:
            print(f"\nRandom pairs sampled: {random_stats['n_pairs']}")
            print(f"  Min:    {random_stats['min']:.4f}")
            print(f"  Median: {random_stats['median']:.4f}")
            print(f"  Mean:   {random_stats['mean']:.4f} (std={random_stats['std']:.4f})")
            print(f"  95th:   {random_stats['p95']:.4f}")
            print(f"  Max:    {random_stats['max']:.4f}")

            # Compare with top-1
            print(f"\n  Comparison:")
            print(f"    Top-1 median ({stats['top1']['median']:.4f}) vs Random median ({random_stats['median']:.4f})")
            ratio = stats['top1']['median'] / random_stats['median'] if random_stats['median'] > 0 else float('inf')
            print(f"    Ratio: {ratio:.2f}x (higher = more discriminative neighbors)")

    # Sample neighbors
    print("\n" + "-" * 40)
    print(f"SAMPLE NEIGHBORS ({n_sample_molecules} random molecules)")
    print("-" * 40)

    samples = sample_neighbors(neighbors_df, n_samples=n_sample_molecules, top_k=5)
    for sample in samples:
        print(f"\n{sample['inchikey']}:")
        for nb in sample["neighbors"]:
            print(f"  {nb['rank']}. {nb['neighbor'][:20]}... (sim={nb['sim']:.4f})")

    # Suspicious cases
    print("\n" + "-" * 40)
    print("SUSPICIOUS CASES")
    print("-" * 40)

    suspicious = detect_suspicious_cases(neighbors_df)

    high = suspicious["high_sim"]
    print(f"\nTop-1 sim >= {high['threshold']}: {high['count']} molecules (potential duplicates)")
    if high["examples"]:
        print("  Examples:")
        for ex in high["examples"][:3]:
            print(f"    {ex['inchikey'][:20]}... → {ex['neighbor_inchikey'][:20]}... (sim={ex['tanimoto_sim']:.4f})")

    low = suspicious["low_sim"]
    print(f"\nTop-1 sim <= {low['threshold']}: {low['count']} molecules (check fingerprints)")
    if low["examples"]:
        print("  Examples:")
        for ex in low["examples"][:3]:
            print(f"    {ex['inchikey'][:20]}... → {ex['neighbor_inchikey'][:20]}... (sim={ex['tanimoto_sim']:.4f})")

    # Descriptor correlation (optional)
    if rdkit_df is not None:
        print("\n" + "-" * 40)
        print("DESCRIPTOR CORRELATION CHECK")
        print("-" * 40)

        corr = descriptor_correlation_check(neighbors_df, rdkit_df)
        if "error" in corr:
            print(f"  {corr['error']}")
        else:
            print(f"\nHigh-sim pairs checked: {corr['n_pairs']}")
            print(f"MW relative difference (should be small for similar molecules):")
            print(f"  Mean:   {corr['mw_relative_diff']['mean']:.4f}")
            print(f"  Median: {corr['mw_relative_diff']['median']:.4f}")
            print(f"  Max:    {corr['mw_relative_diff']['max']:.4f}")
            print(f"LogP absolute difference:")
            print(f"  Mean:   {corr['logp_absolute_diff']['mean']:.4f}")
            print(f"  Median: {corr['logp_absolute_diff']['median']:.4f}")
            print(f"  Max:    {corr['logp_absolute_diff']['max']:.4f}")

    # Perfect similarity pairs with SMILES comparison
    if molecule_table_df is not None:
        print("\n" + "-" * 40)
        print("PERFECT SIMILARITY PAIRS (sim=1.0) - SMILES COMPARISON")
        print("-" * 40)

        perfect_pairs = find_perfect_similarity_pairs(neighbors_df, molecule_table_df, sim_threshold=1.0, max_pairs=10)
        if not perfect_pairs:
            print("\n  No pairs with similarity = 1.0 found.")
        else:
            print(f"\nFound {len(perfect_pairs)} pairs with sim=1.0 (showing up to 10):")
            for i, pair in enumerate(perfect_pairs, 1):
                print(f"\n  Pair {i}:")
                print(f"    InChIKey 1: {pair['inchikey_1']}")
                print(f"    InChIKey 2: {pair['inchikey_2']}")
                print(f"    Tanimoto:   {pair['tanimoto_sim']:.4f}")
                print(f"    SMILES identical: {pair['smiles_identical']}")
                # Truncate long SMILES for display
                s1 = pair['smiles_1'][:80] + "..." if len(pair['smiles_1']) > 80 else pair['smiles_1']
                s2 = pair['smiles_2'][:80] + "..." if len(pair['smiles_2']) > 80 else pair['smiles_2']
                print(f"    SMILES 1: {s1}")
                print(f"    SMILES 2: {s2}")

    print("\n" + "=" * 60)
    print("END OF REPORT")
    print("=" * 60)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Validate ECFP anchor neighbor relationships"
    )
    parser.add_argument(
        "--neighbors", type=str, default="data/anchor_neighbors_ecfp.parquet",
        help="Path to anchor_neighbors_ecfp.parquet"
    )
    parser.add_argument(
        "--rdkit", type=str, default="data/rdkit_features.parquet",
        help="Path to rdkit_features.parquet (for descriptor correlation)"
    )
    parser.add_argument(
        "--samples", type=int, default=5,
        help="Number of random molecules to sample (default: 5)"
    )
    parser.add_argument(
        "--skip-correlation", action="store_true",
        help="Skip descriptor correlation check"
    )
    parser.add_argument(
        "--molecule-table", type=str, default="data/molecule_table.parquet",
        help="Path to molecule_table.parquet (for SMILES lookup)"
    )

    args = parser.parse_args()

    # Load data
    neighbors_df = load_neighbors(args.neighbors)

    rdkit_df = None
    if not args.skip_correlation:
        try:
            rdkit_df = load_rdkit_features(args.rdkit)
        except Exception as e:
            logger.warning(f"Could not load rdkit features: {e}")

    molecule_table_df = None
    try:
        molecule_table_df = load_molecule_table(args.molecule_table)
    except Exception as e:
        logger.warning(f"Could not load molecule table: {e}")

    # Print report
    print_report(neighbors_df, rdkit_df, molecule_table_df, n_sample_molecules=args.samples)


if __name__ == "__main__":
    main()

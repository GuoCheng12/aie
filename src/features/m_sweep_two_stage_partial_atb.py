"""
src/features/m_sweep_two_stage_partial_atb.py

M-parameter sweep for two-stage retrieval on the 76-molecule partial-aTB subset.

Tests different Stage 1 candidate pool sizes (M) to find optimal gating parameter.

Usage:
    python -m src.features.m_sweep_two_stage_partial_atb
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

from src.features.anchor_two_stage_partial_atb import (
    discover_successful_cache,
    load_ecfp_for_subset,
    build_atb_matrix,
    tanimoto_similarity,
    cosine_to_sim,
)


ECFP_DRIFT_THRESHOLD = 0.2


def compute_two_stage_neighbors_with_M(
    ecfp_matrix: np.ndarray,
    atb_matrix: np.ndarray,
    inchikeys: List[str],
    M: int = 50,
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3
) -> pd.DataFrame:
    """
    Two-stage retrieval with specified M parameter.
    Returns DataFrame with neighbor relationships.
    """
    n = len(inchikeys)
    results = []

    for i in range(n):
        query_ik = inchikeys[i]
        query_ecfp = ecfp_matrix[i]
        query_atb = atb_matrix[i]

        # Stage 1: ECFP candidate generation
        ecfp_sims = []
        for j in range(n):
            if i == j:
                continue
            sim_ecfp = tanimoto_similarity(query_ecfp, ecfp_matrix[j])
            ecfp_sims.append((j, sim_ecfp))

        # Sort by ECFP similarity and take top-M
        ecfp_sims.sort(key=lambda x: x[1], reverse=True)
        M_actual = min(M, len(ecfp_sims))
        candidates = ecfp_sims[:M_actual]

        # Stage 2: Rerank by fused similarity
        fused_scores = []
        for stage1_rank_0based, (j, sim_ecfp) in enumerate(candidates):
            # Compute aTB similarity
            cosine = np.dot(query_atb, atb_matrix[j])
            sim_atb = cosine_to_sim(cosine)

            # Fused similarity
            sim_fused = w_ecfp * sim_ecfp + w_atb * sim_atb

            fused_scores.append({
                "j": j,
                "sim_ecfp": sim_ecfp,
                "sim_atb": sim_atb,
                "sim": sim_fused,
                "stage1_rank": stage1_rank_0based + 1
            })

        # Sort by fused similarity and take top-k
        fused_scores.sort(key=lambda x: x["sim"], reverse=True)
        top_k = fused_scores[:k]

        # Record results
        for rank_0based, entry in enumerate(top_k):
            j = entry["j"]
            results.append({
                "inchikey": query_ik,
                "neighbor_inchikey": inchikeys[j],
                "rank": rank_0based + 1,
                "sim": entry["sim"],
                "sim_ecfp": entry["sim_ecfp"],
                "sim_atb": entry["sim_atb"],
                "stage1_rank": entry["stage1_rank"]
            })

    return pd.DataFrame(results)


def compute_metrics(df: pd.DataFrame, ecfp_df: pd.DataFrame) -> Dict:
    """
    Compute drift and overlap metrics for a neighbor table.

    Returns:
        Dict with: low_ecfp_pct, rank1_low_pct, rank10_low_pct,
                   ecfp_median, overlap_at_10
    """
    # Overall low_ecfp%
    low_ecfp_count = (df["sim_ecfp"] < ECFP_DRIFT_THRESHOLD).sum()
    low_ecfp_pct = 100.0 * low_ecfp_count / len(df)

    # Rank-1 low_ecfp%
    rank1 = df[df["rank"] == 1]
    rank1_low = (rank1["sim_ecfp"] < ECFP_DRIFT_THRESHOLD).sum()
    rank1_low_pct = 100.0 * rank1_low / len(rank1) if len(rank1) > 0 else 0.0

    # Rank-10 low_ecfp%
    rank10 = df[df["rank"] == 10]
    rank10_low = (rank10["sim_ecfp"] < ECFP_DRIFT_THRESHOLD).sum()
    rank10_low_pct = 100.0 * rank10_low / len(rank10) if len(rank10) > 0 else 0.0

    # ECFP median
    ecfp_median = df["sim_ecfp"].median()

    # Overlap@10 with ECFP-only
    # Normalize column names
    ecfp_df_norm = ecfp_df.copy()
    if "tanimoto_sim" in ecfp_df_norm.columns:
        ecfp_df_norm = ecfp_df_norm.rename(columns={"tanimoto_sim": "sim_ecfp"})

    overlaps = []
    for ik in df["inchikey"].unique():
        # Two-stage neighbors
        ts_neighbors = set(df[(df["inchikey"] == ik) & (df["rank"] <= 10)]["neighbor_inchikey"])

        # ECFP-only neighbors
        ecfp_neighbors = set(ecfp_df_norm[(ecfp_df_norm["inchikey"] == ik) & (ecfp_df_norm["rank"] <= 10)]["neighbor_inchikey"])

        if len(ecfp_neighbors) > 0:
            intersection = len(ts_neighbors & ecfp_neighbors)
            union = len(ts_neighbors | ecfp_neighbors)
            if union > 0:
                overlaps.append(intersection / union)

    overlap_at_10 = np.mean(overlaps) if overlaps else 0.0

    return {
        "low_ecfp_pct": low_ecfp_pct,
        "rank1_low_pct": rank1_low_pct,
        "rank10_low_pct": rank10_low_pct,
        "ecfp_median": ecfp_median,
        "overlap_at_10": overlap_at_10
    }


def run_m_sweep(
    M_values: List[int] = [15, 20, 25, 30, 40, 50],
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3
):
    """
    Run M-parameter sweep for two-stage retrieval.
    """
    print("=" * 80)
    print("M-PARAMETER SWEEP FOR TWO-STAGE RETRIEVAL")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  k = {k} (final neighbors per query)")
    print(f"  w_ecfp = {w_ecfp}, w_atb = {w_atb}")
    print(f"  M values: {M_values}")
    print(f"  ECFP drift threshold: {ECFP_DRIFT_THRESHOLD}")

    # Load data
    print("\n[1/4] Loading data...")

    # Discover aTB cache
    print("  Discovering aTB cache...")
    atb_data = discover_successful_cache()
    print(f"  Found {len(atb_data)} molecules with complete aTB features")

    # Get InChIKeys
    inchikeys = [d["inchikey"] for d in atb_data]

    # Load ECFP
    print("  Loading ECFP features...")
    rdkit_path = Path("data/rdkit_features.parquet")
    ecfp_matrix, inchikeys = load_ecfp_for_subset(rdkit_path, inchikeys)
    print(f"  Loaded ECFP for {len(inchikeys)} molecules")

    # Filter aTB data to match
    atb_data = [d for d in atb_data if d["inchikey"] in inchikeys]

    # Build aTB matrix
    print("  Building aTB matrix...")
    atb_matrix, _, _, _ = build_atb_matrix(atb_data, inchikeys)
    print(f"  aTB matrix shape: {atb_matrix.shape}")

    # Load ECFP-only neighbors for overlap comparison
    print("  Loading ECFP-only neighbors...")
    ecfp_df = pd.read_parquet("data/anchor_neighbors_ecfp.parquet")
    print(f"  Loaded {len(ecfp_df)} ECFP-only neighbor pairs")

    # Run sweep
    print(f"\n[2/4] Running M-sweep for {len(M_values)} values...")

    results = []

    for M in tqdm(M_values, desc="M-sweep"):
        # Compute neighbors
        neighbors_df = compute_two_stage_neighbors_with_M(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=M,
            k=k,
            w_ecfp=w_ecfp,
            w_atb=w_atb
        )

        # Compute metrics
        metrics = compute_metrics(neighbors_df, ecfp_df)

        results.append({
            "M": M,
            **metrics
        })

    # Report
    print("\n[3/4] Results:")
    print()
    print(f"{'M':>5} {'low_ecfp%':>10} {'rank1_low%':>11} {'rank10_low%':>12} {'ecfp_median':>12} {'overlap@10':>11}")
    print("-" * 80)

    for r in results:
        print(f"{r['M']:>5} {r['low_ecfp_pct']:>9.1f}% {r['rank1_low_pct']:>10.1f}% "
              f"{r['rank10_low_pct']:>11.1f}% {r['ecfp_median']:>12.4f} {r['overlap_at_10']:>11.4f}")

    # Analysis
    print("\n[4/4] Analysis:")
    print()

    # Find best M (lowest low_ecfp%)
    best_m = min(results, key=lambda x: x["low_ecfp_pct"])
    print(f"  Lowest drift: M={best_m['M']} with {best_m['low_ecfp_pct']:.1f}% low-ECFP neighbors")

    # Find M with best overlap
    best_overlap_m = max(results, key=lambda x: x["overlap_at_10"])
    print(f"  Best overlap: M={best_overlap_m['M']} with {best_overlap_m['overlap_at_10']:.4f} overlap@10")

    # Find M with highest ECFP median
    best_ecfp_m = max(results, key=lambda x: x["ecfp_median"])
    print(f"  Best ECFP preservation: M={best_ecfp_m['M']} with median={best_ecfp_m['ecfp_median']:.4f}")

    # Drift thresholds
    print()
    print("  Drift threshold evaluation:")
    for r in results:
        if r["low_ecfp_pct"] < 10:
            status = "PASS"
        elif r["low_ecfp_pct"] < 30:
            status = "CAUTION"
        else:
            status = "WARNING"
        print(f"    M={r['M']:2d}: {status:8s} ({r['low_ecfp_pct']:5.1f}% low-ECFP)")

    # Recommendations
    print()
    print("  Recommendations:")

    passing_m = [r for r in results if r["low_ecfp_pct"] < 10]
    if passing_m:
        # Among passing, prefer higher M for diversity
        best_passing = max(passing_m, key=lambda x: x["M"])
        print(f"    - Recommended M={best_passing['M']} (PASS threshold, highest diversity)")
    else:
        caution_m = [r for r in results if r["low_ecfp_pct"] < 30]
        if caution_m:
            best_caution = min(caution_m, key=lambda x: x["low_ecfp_pct"])
            print(f"    - Recommended M={best_caution['M']} (best CAUTION-level drift)")
        else:
            print(f"    - All M values exceed WARNING threshold (>30% drift)")
            print(f"    - Use smallest M={min(M_values)} to minimize drift")

    # Trend analysis
    print()
    print("  Trends:")
    low_ecfp_trend = [r["low_ecfp_pct"] for r in results]
    if low_ecfp_trend[0] < low_ecfp_trend[-1]:
        print("    - low_ecfp% INCREASES with M (larger M includes more low-ECFP candidates)")
    elif low_ecfp_trend[0] > low_ecfp_trend[-1]:
        print("    - low_ecfp% DECREASES with M (unexpected, investigate)")
    else:
        print("    - low_ecfp% relatively FLAT across M values")

    ecfp_median_trend = [r["ecfp_median"] for r in results]
    if ecfp_median_trend[0] > ecfp_median_trend[-1]:
        print("    - ecfp_median DECREASES with M (larger M admits lower-ECFP candidates)")
    elif ecfp_median_trend[0] < ecfp_median_trend[-1]:
        print("    - ecfp_median INCREASES with M (unexpected, investigate)")
    else:
        print("    - ecfp_median relatively FLAT across M values")

    # Save results
    output_path = Path("data/m_sweep_results.json")
    with open(output_path, "w") as f:
        json.dump({
            "config": {
                "k": k,
                "w_ecfp": w_ecfp,
                "w_atb": w_atb,
                "ecfp_threshold": ECFP_DRIFT_THRESHOLD,
                "n_molecules": len(inchikeys)
            },
            "results": results,
            "recommendations": {
                "lowest_drift_M": best_m["M"],
                "best_overlap_M": best_overlap_m["M"],
                "best_ecfp_M": best_ecfp_m["M"]
            }
        }, f, indent=2)

    print(f"\n  Results saved to: {output_path}")
    print()
    print("=" * 80)
    print("M-SWEEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_m_sweep()

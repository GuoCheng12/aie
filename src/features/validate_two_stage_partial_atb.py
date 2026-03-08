"""
src/features/validate_two_stage_partial_atb.py

Compare ECFP-only, linear-fusion hybrid, and two-stage retrieval strategies.

Reports:
- Overlap@10 with ECFP-only
- ECFP drift metrics (ecfp_median, low_ecfp%)
- Top-1 fused similarity distribution

Usage:
    python -m src.features.validate_two_stage_partial_atb \
        --ecfp data/anchor_neighbors_ecfp.parquet \
        --linear data/anchor_neighbors_hybrid_partial_atb.parquet \
        --two-stage data/anchor_neighbors_two_stage_partial_atb.parquet
"""

import argparse
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd


# ========== THRESHOLDS ==========
ECFP_DRIFT_THRESHOLD = 0.2  # sim_ecfp < 0.2 considered structurally dissimilar


# ========== HELPER FUNCTIONS ==========

def load_neighbors(path: Path) -> pd.DataFrame:
    """Load neighbor table and validate schema."""
    df = pd.read_parquet(path)
    required_cols = ["inchikey", "neighbor_inchikey", "rank"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in {path}")

    # Normalize column names: tanimoto_sim -> sim_ecfp for ECFP-only tables
    if "tanimoto_sim" in df.columns and "sim_ecfp" not in df.columns:
        df = df.rename(columns={"tanimoto_sim": "sim_ecfp"})

    if "sim_ecfp" not in df.columns:
        raise ValueError(f"Missing 'sim_ecfp' or 'tanimoto_sim' column in {path}")

    return df


def compute_overlap_at_k(df1: pd.DataFrame, df2: pd.DataFrame, k: int = 10) -> float:
    """
    Compute overlap@k between two neighbor tables.
    Returns fraction of shared neighbors in top-k.
    """
    overlaps = []

    for ik in df1["inchikey"].unique():
        neighbors1 = set(df1[(df1["inchikey"] == ik) & (df1["rank"] <= k)]["neighbor_inchikey"])
        neighbors2 = set(df2[(df2["inchikey"] == ik) & (df2["rank"] <= k)]["neighbor_inchikey"])

        if len(neighbors1) == 0 and len(neighbors2) == 0:
            continue

        intersection = len(neighbors1 & neighbors2)
        union = len(neighbors1 | neighbors2)

        if union > 0:
            overlaps.append(intersection / union)

    if len(overlaps) == 0:
        return 0.0

    return np.mean(overlaps)


def compute_ecfp_drift_metrics(df: pd.DataFrame, k: int = 10) -> Dict[str, float]:
    """
    Compute ECFP drift metrics for top-k neighbors.

    Returns:
        ecfp_median: Median sim_ecfp
        low_ecfp_pct: Percentage of neighbors with sim_ecfp < threshold
        rank1_low_ecfp_pct: Percentage for rank-1 neighbors only
        rank10_low_ecfp_pct: Percentage for rank-10 neighbors only
    """
    topk = df[df["rank"] <= k].copy()

    ecfp_values = topk["sim_ecfp"].values
    ecfp_median = np.median(ecfp_values)

    low_ecfp_count = (ecfp_values < ECFP_DRIFT_THRESHOLD).sum()
    low_ecfp_pct = 100.0 * low_ecfp_count / len(ecfp_values)

    # Rank-1 metrics
    rank1 = df[df["rank"] == 1].copy()
    rank1_low = (rank1["sim_ecfp"] < ECFP_DRIFT_THRESHOLD).sum()
    rank1_low_pct = 100.0 * rank1_low / len(rank1) if len(rank1) > 0 else 0.0

    # Rank-10 metrics (if k >= 10)
    rank10_low_pct = None
    if k >= 10:
        rank10 = df[df["rank"] == 10].copy()
        rank10_low = (rank10["sim_ecfp"] < ECFP_DRIFT_THRESHOLD).sum()
        rank10_low_pct = 100.0 * rank10_low / len(rank10) if len(rank10) > 0 else 0.0

    return {
        "ecfp_median": ecfp_median,
        "low_ecfp_pct": low_ecfp_pct,
        "rank1_low_ecfp_pct": rank1_low_pct,
        "rank10_low_ecfp_pct": rank10_low_pct
    }


def compute_top1_distribution(df: pd.DataFrame) -> Dict[str, float]:
    """
    Compute distribution statistics for rank-1 fused similarity.
    """
    rank1 = df[df["rank"] == 1].copy()

    if "sim" in rank1.columns:
        sims = rank1["sim"].values
    else:
        # For ECFP-only, use sim_ecfp
        sims = rank1["sim_ecfp"].values

    return {
        "min": np.min(sims),
        "median": np.median(sims),
        "p95": np.percentile(sims, 95),
        "max": np.max(sims)
    }


def print_section(title: str):
    """Print section header."""
    print(f"\n{'='*80}")
    print(f"{title}")
    print(f"{'='*80}\n")


def compare_strategies(
    ecfp_df: pd.DataFrame,
    linear_df: pd.DataFrame,
    two_stage_df: pd.DataFrame,
    k: int = 10
):
    """
    Compare ECFP-only, linear-fusion, and two-stage retrieval strategies.
    """
    print_section("TWO-STAGE VALIDATION REPORT")

    # ========== OVERLAP ANALYSIS ==========
    print_section("1. OVERLAP@10 WITH ECFP-ONLY")

    overlap_linear = compute_overlap_at_k(ecfp_df, linear_df, k=k)
    overlap_two_stage = compute_overlap_at_k(ecfp_df, two_stage_df, k=k)

    print(f"Linear-fusion vs ECFP-only:  {overlap_linear:.4f} (Jaccard)")
    print(f"Two-stage vs ECFP-only:      {overlap_two_stage:.4f} (Jaccard)")
    print()
    print("Interpretation:")
    print(f"  - Linear-fusion overlap: {100*overlap_linear:.1f}% shared neighbors")
    print(f"  - Two-stage overlap:     {100*overlap_two_stage:.1f}% shared neighbors")
    print(f"  Two-stage should have HIGHER overlap with ECFP-only (Stage 1 restricts candidates)")

    # ========== ECFP DRIFT METRICS ==========
    print_section("2. ECFP DRIFT METRICS")

    ecfp_metrics = compute_ecfp_drift_metrics(ecfp_df, k=k)
    linear_metrics = compute_ecfp_drift_metrics(linear_df, k=k)
    two_stage_metrics = compute_ecfp_drift_metrics(two_stage_df, k=k)

    print(f"{'Strategy':<20} {'ecfp_median':>12} {'low_ecfp%':>12} {'rank1_low%':>12} {'rank10_low%':>12}")
    print(f"{'-'*20} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")

    print(f"{'ECFP-only':<20} {ecfp_metrics['ecfp_median']:>12.4f} "
          f"{ecfp_metrics['low_ecfp_pct']:>11.1f}% "
          f"{ecfp_metrics['rank1_low_ecfp_pct']:>11.1f}% "
          f"{ecfp_metrics['rank10_low_ecfp_pct'] if ecfp_metrics['rank10_low_ecfp_pct'] is not None else 'N/A':>11}")

    print(f"{'Linear-fusion':<20} {linear_metrics['ecfp_median']:>12.4f} "
          f"{linear_metrics['low_ecfp_pct']:>11.1f}% "
          f"{linear_metrics['rank1_low_ecfp_pct']:>11.1f}% "
          f"{linear_metrics['rank10_low_ecfp_pct'] if linear_metrics['rank10_low_ecfp_pct'] is not None else 'N/A':>11}")

    print(f"{'Two-stage':<20} {two_stage_metrics['ecfp_median']:>12.4f} "
          f"{two_stage_metrics['low_ecfp_pct']:>11.1f}% "
          f"{two_stage_metrics['rank1_low_ecfp_pct']:>11.1f}% "
          f"{two_stage_metrics['rank10_low_ecfp_pct'] if two_stage_metrics['rank10_low_ecfp_pct'] is not None else 'N/A':>11}")

    print()
    print("Drift Thresholds:")
    print(f"  - low_ecfp% > 30%: WARNING (significant structural drift)")
    print(f"  - low_ecfp% > 10%: CAUTION")
    print(f"  - low_ecfp% < 10%: PASS")
    print()

    # Evaluate two-stage
    two_stage_status = "PASS"
    if two_stage_metrics['low_ecfp_pct'] > 30:
        two_stage_status = "WARNING"
    elif two_stage_metrics['low_ecfp_pct'] > 10:
        two_stage_status = "CAUTION"

    print(f"Two-stage drift status: {two_stage_status}")
    print()

    # ========== TOP-1 FUSED SIMILARITY ==========
    print_section("3. TOP-1 FUSED SIMILARITY DISTRIBUTION")

    ecfp_dist = compute_top1_distribution(ecfp_df)
    linear_dist = compute_top1_distribution(linear_df)
    two_stage_dist = compute_top1_distribution(two_stage_df)

    print(f"{'Strategy':<20} {'min':>10} {'median':>10} {'p95':>10} {'max':>10}")
    print(f"{'-'*20} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    print(f"{'ECFP-only':<20} {ecfp_dist['min']:>10.4f} {ecfp_dist['median']:>10.4f} "
          f"{ecfp_dist['p95']:>10.4f} {ecfp_dist['max']:>10.4f}")

    print(f"{'Linear-fusion':<20} {linear_dist['min']:>10.4f} {linear_dist['median']:>10.4f} "
          f"{linear_dist['p95']:>10.4f} {linear_dist['max']:>10.4f}")

    print(f"{'Two-stage':<20} {two_stage_dist['min']:>10.4f} {two_stage_dist['median']:>10.4f} "
          f"{two_stage_dist['p95']:>10.4f} {two_stage_dist['max']:>10.4f}")

    print()
    print("Interpretation:")
    print("  Top-1 similarity reflects the quality of the best neighbor match.")
    print("  Higher values indicate better overall retrieval quality.")

    # ========== STAGE1_RANK ANALYSIS (Two-stage only) ==========
    if "stage1_rank" in two_stage_df.columns:
        print_section("4. STAGE1_RANK ANALYSIS (Two-stage)")

        stage1_ranks = two_stage_df["stage1_rank"].values
        print(f"Stage 1 rank statistics:")
        print(f"  min:    {np.min(stage1_ranks)}")
        print(f"  median: {np.median(stage1_ranks):.1f}")
        print(f"  max:    {np.max(stage1_ranks)}")
        print()
        print("Interpretation:")
        print("  - Low median: Stage 2 reranking mostly picks from top ECFP candidates")
        print("  - High median: aTB features significantly change the ranking")

    # ========== SUMMARY ==========
    print_section("SUMMARY")

    print("Expected outcomes:")
    print("  ✓ Two-stage should have ~0% low_ecfp% (structural drift eliminated)")
    print("  ✓ Two-stage ecfp_median should be higher than linear-fusion")
    print("  ✓ Two-stage overlap@10 with ECFP-only should be higher than linear-fusion")
    print()

    # Check expectations
    checks_passed = 0
    checks_total = 3

    if two_stage_metrics['low_ecfp_pct'] < linear_metrics['low_ecfp_pct']:
        print("✓ Two-stage reduces ECFP drift vs linear-fusion")
        checks_passed += 1
    else:
        print("✗ Two-stage did NOT reduce ECFP drift vs linear-fusion")

    if two_stage_metrics['ecfp_median'] > linear_metrics['ecfp_median']:
        print("✓ Two-stage has higher ecfp_median vs linear-fusion")
        checks_passed += 1
    else:
        print("✗ Two-stage did NOT improve ecfp_median vs linear-fusion")

    if overlap_two_stage > overlap_linear:
        print("✓ Two-stage has higher overlap@10 with ECFP-only vs linear-fusion")
        checks_passed += 1
    else:
        print("✗ Two-stage did NOT improve overlap@10 with ECFP-only vs linear-fusion")

    print()
    print(f"Validation checks passed: {checks_passed}/{checks_total}")

    if checks_passed == checks_total:
        print("✓ ALL CHECKS PASSED - Two-stage retrieval validated successfully")
    else:
        print(f"⚠ {checks_total - checks_passed} check(s) failed - review results")

    print()


def main():
    parser = argparse.ArgumentParser(description="Validate two-stage retrieval strategy")
    parser.add_argument(
        "--ecfp", type=Path, required=True,
        help="Path to ECFP-only neighbor table (e.g., data/anchor_neighbors_ecfp.parquet)"
    )
    parser.add_argument(
        "--linear", type=Path, required=True,
        help="Path to linear-fusion neighbor table (e.g., data/anchor_neighbors_hybrid_partial_atb.parquet)"
    )
    parser.add_argument(
        "--two-stage", type=Path, required=True,
        help="Path to two-stage neighbor table (e.g., data/anchor_neighbors_two_stage_partial_atb.parquet)"
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Number of top neighbors to analyze (default: 10)"
    )

    args = parser.parse_args()

    # Load data
    print(f"Loading ECFP-only neighbors from: {args.ecfp}")
    ecfp_df = load_neighbors(args.ecfp)

    print(f"Loading linear-fusion neighbors from: {args.linear}")
    linear_df = load_neighbors(args.linear)

    print(f"Loading two-stage neighbors from: {args.two_stage}")
    two_stage_df = load_neighbors(args.two_stage)

    # Filter to common subset
    ecfp_iks = set(ecfp_df["inchikey"].unique())
    linear_iks = set(linear_df["inchikey"].unique())
    two_stage_iks = set(two_stage_df["inchikey"].unique())

    common_iks = ecfp_iks & linear_iks & two_stage_iks

    if len(common_iks) < len(two_stage_iks):
        print(f"\nWarning: Filtering to common subset of {len(common_iks)} molecules")
        print(f"  ECFP-only: {len(ecfp_iks)} molecules")
        print(f"  Linear:    {len(linear_iks)} molecules")
        print(f"  Two-stage: {len(two_stage_iks)} molecules")

        ecfp_df = ecfp_df[ecfp_df["inchikey"].isin(common_iks)].copy()
        linear_df = linear_df[linear_df["inchikey"].isin(common_iks)].copy()
        two_stage_df = two_stage_df[two_stage_df["inchikey"].isin(common_iks)].copy()

    # Run comparison
    compare_strategies(
        ecfp_df=ecfp_df,
        linear_df=linear_df,
        two_stage_df=two_stage_df,
        k=args.k
    )


if __name__ == "__main__":
    main()

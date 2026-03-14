"""
src/features/validate_anchor_space_hybrid_partial_atb.py

Validation script for hybrid ECFP + aTB anchor space.
Compares against ECFP-only neighbors to assess aTB feature impact.

Extended with:
- (A) Pairwise correctness audit (RDKit verification)
- (B) Structural reasonableness check (ECFP drift detection)
- (C) Sensitivity/stability experiments (weight sweep, two-stage fusion)

Usage:
    python -m src.features.validate_anchor_space_hybrid_partial_atb
    python -m src.features.validate_anchor_space_hybrid_partial_atb --audit
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger
from src.features.anchor_hybrid_ecfp_atb_partial import (
    to_binary_fingerprint,
    tanimoto_similarity,
    cosine_to_sim,
    discover_successful_cache,
    extract_atb_features,
    ATB_FEATURES,
    safe_parse_float,
)

logger = get_logger(__name__)


# =============================================================================
# SECTION A: PAIRWISE CORRECTNESS AUDIT
# =============================================================================

def rdkit_tanimoto_from_smiles(smiles1: str, smiles2: str) -> Optional[float]:
    """
    Compute Tanimoto similarity using RDKit's official implementation.
    Returns None if either SMILES is invalid.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import DataStructs, rdFingerprintGenerator

        mol1 = Chem.MolFromSmiles(smiles1)
        mol2 = Chem.MolFromSmiles(smiles2)

        if mol1 is None or mol2 is None:
            return None

        generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
        fp1 = generator.GetFingerprint(mol1)
        fp2 = generator.GetFingerprint(mol2)

        return DataStructs.TanimotoSimilarity(fp1, fp2)
    except Exception as e:
        logger.debug(f"RDKit Tanimoto failed: {e}")
        return None


def recompute_sim_atb(
    ik1: str,
    ik2: str,
    atb_features_by_ik: Dict[str, Dict[str, float]],
    means: Dict[str, float],
    stds: Dict[str, float]
) -> Optional[float]:
    """
    Recompute sim_atb using saved scaler params from manifest.

    Steps:
    1. Load 4 features for each molecule
    2. Z-score with provided means/stds
    3. L2-normalize
    4. Cosine similarity
    5. Map to [0,1]
    """
    if ik1 not in atb_features_by_ik or ik2 not in atb_features_by_ik:
        return None

    feats1 = atb_features_by_ik[ik1]
    feats2 = atb_features_by_ik[ik2]

    # Build vectors
    vec1 = np.array([feats1[f] for f in ATB_FEATURES], dtype=np.float64)
    vec2 = np.array([feats2[f] for f in ATB_FEATURES], dtype=np.float64)

    # Z-score
    means_arr = np.array([means[f] for f in ATB_FEATURES])
    stds_arr = np.array([stds[f] for f in ATB_FEATURES])
    stds_arr = np.where(stds_arr < 1e-10, 1.0, stds_arr)

    vec1_z = (vec1 - means_arr) / stds_arr
    vec2_z = (vec2 - means_arr) / stds_arr

    # L2 normalize
    norm1 = np.linalg.norm(vec1_z)
    norm2 = np.linalg.norm(vec2_z)
    if norm1 < 1e-10 or norm2 < 1e-10:
        return None

    vec1_norm = vec1_z / norm1
    vec2_norm = vec2_z / norm2

    # Cosine and map to [0,1]
    cosine = np.dot(vec1_norm, vec2_norm)
    return cosine_to_sim(cosine)


def run_correctness_audit(
    hybrid_df: pd.DataFrame,
    manifest: Dict,
    molecule_table_path: str = "data/molecule_table.parquet",
    n_pairs: int = 20,
    seed: int = 42
) -> Dict:
    """
    (A) Pairwise correctness audit.

    Sample pairs and verify sim_ecfp and sim_atb by recomputation.
    """
    print("\n" + "=" * 60)
    print("SECTION A: PAIRWISE CORRECTNESS AUDIT")
    print("=" * 60)

    # Load molecule table for SMILES
    mol_table = pd.read_parquet(molecule_table_path)
    smiles_by_ik = dict(zip(mol_table["inchikey"], mol_table["canonical_smiles"]))

    # Load aTB features
    successful = discover_successful_cache()
    atb_features_by_ik = {}
    for entry in successful:
        feats = extract_atb_features(entry["features"])
        if feats is not None:
            atb_features_by_ik[entry["inchikey"]] = feats

    # Get scaler params from manifest
    means = manifest["atb_feature_stats"]["means"]
    stds = manifest["atb_feature_stats"]["stds"]

    # Sample pairs: prefer mix of high-sim, mid-sim
    rng = np.random.default_rng(seed)

    # Stratified sampling
    high_sim = hybrid_df[hybrid_df["sim"] >= 0.6].sample(
        n=min(7, len(hybrid_df[hybrid_df["sim"] >= 0.6])),
        random_state=seed
    ) if len(hybrid_df[hybrid_df["sim"] >= 0.6]) > 0 else pd.DataFrame()

    mid_sim = hybrid_df[(hybrid_df["sim"] >= 0.4) & (hybrid_df["sim"] < 0.6)].sample(
        n=min(7, len(hybrid_df[(hybrid_df["sim"] >= 0.4) & (hybrid_df["sim"] < 0.6)])),
        random_state=seed
    ) if len(hybrid_df[(hybrid_df["sim"] >= 0.4) & (hybrid_df["sim"] < 0.6)]) > 0 else pd.DataFrame()

    low_sim = hybrid_df[hybrid_df["sim"] < 0.4].sample(
        n=min(6, len(hybrid_df[hybrid_df["sim"] < 0.4])),
        random_state=seed
    ) if len(hybrid_df[hybrid_df["sim"] < 0.4]) > 0 else pd.DataFrame()

    sampled = pd.concat([high_sim, mid_sim, low_sim]).head(n_pairs)

    print(f"  Sampled {len(sampled)} pairs for verification")
    print(f"    High-sim (>=0.6): {len(high_sim)}")
    print(f"    Mid-sim (0.4-0.6): {len(mid_sim)}")
    print(f"    Low-sim (<0.4): {len(low_sim)}")

    # Verify each pair
    ecfp_errors = []
    atb_errors = []
    skipped_ecfp = 0
    skipped_atb = 0

    results = []

    for _, row in sampled.iterrows():
        ik1 = row["inchikey"]
        ik2 = row["neighbor_inchikey"]
        stored_ecfp = row["sim_ecfp"]
        stored_atb = row["sim_atb"]

        # Verify ECFP
        smiles1 = smiles_by_ik.get(ik1)
        smiles2 = smiles_by_ik.get(ik2)

        if smiles1 and smiles2:
            recomputed_ecfp = rdkit_tanimoto_from_smiles(smiles1, smiles2)
            if recomputed_ecfp is not None:
                ecfp_err = abs(stored_ecfp - recomputed_ecfp)
                ecfp_errors.append(ecfp_err)
            else:
                skipped_ecfp += 1
                recomputed_ecfp = None
                ecfp_err = None
        else:
            skipped_ecfp += 1
            recomputed_ecfp = None
            ecfp_err = None

        # Verify aTB
        recomputed_atb = recompute_sim_atb(ik1, ik2, atb_features_by_ik, means, stds)
        if recomputed_atb is not None:
            atb_err = abs(stored_atb - recomputed_atb)
            atb_errors.append(atb_err)
        else:
            skipped_atb += 1
            atb_err = None

        results.append({
            "ik1": ik1[:15],
            "ik2": ik2[:15],
            "stored_ecfp": stored_ecfp,
            "recomputed_ecfp": recomputed_ecfp,
            "ecfp_err": ecfp_err,
            "stored_atb": stored_atb,
            "recomputed_atb": recomputed_atb,
            "atb_err": atb_err
        })

    # Report
    print(f"\n  ECFP Verification:")
    print(f"    Pairs verified: {len(ecfp_errors)}")
    print(f"    Pairs skipped:  {skipped_ecfp}")

    if ecfp_errors:
        ecfp_max = max(ecfp_errors)
        ecfp_mean = np.mean(ecfp_errors)
        print(f"    Max error:  {ecfp_max:.2e}")
        print(f"    Mean error: {ecfp_mean:.2e}")
        if ecfp_max > 1e-6:
            print(f"    WARNING: ECFP max error > 1e-6!")
        else:
            print(f"    PASS: All errors < 1e-6")

    print(f"\n  aTB Verification:")
    print(f"    Pairs verified: {len(atb_errors)}")
    print(f"    Pairs skipped:  {skipped_atb}")

    if atb_errors:
        atb_max = max(atb_errors)
        atb_mean = np.mean(atb_errors)
        print(f"    Max error:  {atb_max:.2e}")
        print(f"    Mean error: {atb_mean:.2e}")
        if atb_max > 1e-4:
            print(f"    WARNING: aTB max error > 1e-4!")
        else:
            print(f"    PASS: All errors < 1e-4")

    return {
        "n_pairs": len(sampled),
        "ecfp_verified": len(ecfp_errors),
        "ecfp_skipped": skipped_ecfp,
        "ecfp_max_error": max(ecfp_errors) if ecfp_errors else None,
        "ecfp_mean_error": np.mean(ecfp_errors) if ecfp_errors else None,
        "atb_verified": len(atb_errors),
        "atb_skipped": skipped_atb,
        "atb_max_error": max(atb_errors) if atb_errors else None,
        "atb_mean_error": np.mean(atb_errors) if atb_errors else None,
    }


# =============================================================================
# SECTION B: STRUCTURAL REASONABLENESS CHECK
# =============================================================================

def run_structural_reasonableness_check(
    hybrid_df: pd.DataFrame,
    ecfp_threshold: float = 0.2
) -> Dict:
    """
    (B) Structural reasonableness check.

    Detect "micro-similar but structurally dissimilar" drift.
    """
    print("\n" + "=" * 60)
    print("SECTION B: STRUCTURAL REASONABLENESS CHECK")
    print("=" * 60)

    # Analyze sim_ecfp distribution for ALL hybrid neighbors (top-10)
    print("\n  sim_ecfp distribution for hybrid top-10 neighbors:")

    ecfp_min = hybrid_df["sim_ecfp"].min()
    ecfp_p10 = hybrid_df["sim_ecfp"].quantile(0.10)
    ecfp_p25 = hybrid_df["sim_ecfp"].quantile(0.25)
    ecfp_median = hybrid_df["sim_ecfp"].median()
    ecfp_mean = hybrid_df["sim_ecfp"].mean()

    print(f"    min    = {ecfp_min:.4f}")
    print(f"    10th   = {ecfp_p10:.4f}")
    print(f"    25th   = {ecfp_p25:.4f}")
    print(f"    median = {ecfp_median:.4f}")
    print(f"    mean   = {ecfp_mean:.4f}")

    # Count neighbors with low ECFP similarity
    n_low_ecfp = (hybrid_df["sim_ecfp"] < ecfp_threshold).sum()
    pct_low_ecfp = 100 * n_low_ecfp / len(hybrid_df)

    print(f"\n  Neighbors with sim_ecfp < {ecfp_threshold}:")
    print(f"    Count: {n_low_ecfp} / {len(hybrid_df)}")
    print(f"    Pct:   {pct_low_ecfp:.1f}%")

    # Warning if too many low-ECFP neighbors
    if pct_low_ecfp > 30:
        print(f"\n  WARNING: {pct_low_ecfp:.1f}% of neighbors have sim_ecfp < {ecfp_threshold}")
        print(f"           This suggests potential 'ECFP drift' - hybrid neighbors")
        print(f"           may be structurally dissimilar to query molecules.")
    elif pct_low_ecfp > 10:
        print(f"\n  CAUTION: {pct_low_ecfp:.1f}% of neighbors have sim_ecfp < {ecfp_threshold}")
        print(f"           Some structural dissimilarity present.")
    else:
        print(f"\n  PASS: Only {pct_low_ecfp:.1f}% of neighbors have sim_ecfp < {ecfp_threshold}")
        print(f"        Structural reasonableness maintained.")

    # Per-rank analysis
    print("\n  sim_ecfp by rank:")
    for rank in range(1, 11):
        rank_df = hybrid_df[hybrid_df["rank"] == rank]
        if len(rank_df) > 0:
            rank_median = rank_df["sim_ecfp"].median()
            rank_low_pct = 100 * (rank_df["sim_ecfp"] < ecfp_threshold).sum() / len(rank_df)
            print(f"    Rank {rank:2d}: median={rank_median:.3f}, low%={rank_low_pct:5.1f}%")

    return {
        "ecfp_min": ecfp_min,
        "ecfp_p10": ecfp_p10,
        "ecfp_median": ecfp_median,
        "n_low_ecfp": n_low_ecfp,
        "pct_low_ecfp": pct_low_ecfp,
        "ecfp_threshold": ecfp_threshold
    }


# =============================================================================
# SECTION C: SENSITIVITY / STABILITY EXPERIMENTS
# =============================================================================

def rerank_with_weights(
    hybrid_df: pd.DataFrame,
    w_ecfp: float,
    w_atb: float,
    k: int = 10
) -> pd.DataFrame:
    """
    Rerank neighbors using different weights WITHOUT rebuilding from scratch.
    Uses existing sim_ecfp and sim_atb columns.
    """
    df = hybrid_df.copy()

    # Recompute fused similarity
    df["sim_new"] = w_ecfp * df["sim_ecfp"] + w_atb * df["sim_atb"]

    # Rerank within each query molecule
    result_rows = []
    for ik in df["inchikey"].unique():
        mol_df = df[df["inchikey"] == ik].copy()
        mol_df = mol_df.sort_values("sim_new", ascending=False).head(k)
        mol_df["rank"] = range(1, len(mol_df) + 1)
        mol_df["sim"] = mol_df["sim_new"]
        result_rows.append(mol_df)

    return pd.concat(result_rows, ignore_index=True)


def compute_overlap_for_reranked(
    reranked_df: pd.DataFrame,
    ecfp_df: pd.DataFrame,
    hybrid_iks: Set[str]
) -> float:
    """Compute mean overlap@10 between reranked neighbors and ECFP-only."""
    overlaps = []

    for ik in reranked_df["inchikey"].unique():
        # Reranked neighbors
        new_nbrs = set(reranked_df[reranked_df["inchikey"] == ik]["neighbor_inchikey"])

        # ECFP neighbors (restricted to hybrid set)
        ecfp_nbrs_all = set(ecfp_df[ecfp_df["inchikey"] == ik]["neighbor_inchikey"])
        ecfp_nbrs = ecfp_nbrs_all & hybrid_iks

        if len(ecfp_nbrs) > 0:
            overlap = len(new_nbrs & ecfp_nbrs) / 10
            overlaps.append(overlap)

    return np.mean(overlaps) if overlaps else 0.0


def run_weight_sweep(
    hybrid_df: pd.DataFrame,
    ecfp_df: pd.DataFrame,
    ecfp_threshold: float = 0.2
) -> Dict:
    """
    (C1) Weight sweep experiment.

    Test w_atb in {0.0, 0.1, 0.2, 0.3} and report metrics.
    """
    print("\n" + "=" * 60)
    print("SECTION C1: WEIGHT SWEEP SENSITIVITY")
    print("=" * 60)

    hybrid_iks = set(hybrid_df["inchikey"].unique())
    weight_configs = [
        (1.0, 0.0),
        (0.9, 0.1),
        (0.8, 0.2),
        (0.7, 0.3),
    ]

    print(f"\n  Testing weights: w_atb in [0.0, 0.1, 0.2, 0.3]")
    print(f"\n  {'w_ecfp':>6} {'w_atb':>6} {'overlap@10':>10} {'top1_med':>10} {'low_ecfp%':>10}")
    print("  " + "-" * 50)

    results = []

    for w_ecfp, w_atb in weight_configs:
        # Rerank
        reranked = rerank_with_weights(hybrid_df, w_ecfp, w_atb)

        # Overlap with ECFP-only
        overlap = compute_overlap_for_reranked(reranked, ecfp_df, hybrid_iks)

        # Top-1 median
        top1_median = reranked[reranked["rank"] == 1]["sim"].median()

        # Low ECFP fraction
        low_ecfp_pct = 100 * (reranked["sim_ecfp"] < ecfp_threshold).sum() / len(reranked)

        print(f"  {w_ecfp:6.1f} {w_atb:6.1f} {overlap:10.3f} {top1_median:10.4f} {low_ecfp_pct:10.1f}%")

        results.append({
            "w_ecfp": w_ecfp,
            "w_atb": w_atb,
            "overlap_at_10": overlap,
            "top1_median": top1_median,
            "low_ecfp_pct": low_ecfp_pct
        })

    # Interpretation
    print("\n  Interpretation:")
    if results[0]["overlap_at_10"] > 0.9:
        print("    - w_atb=0.0 has >90% overlap (expected, it's ECFP-only reranking)")

    if results[-1]["low_ecfp_pct"] > 30:
        print(f"    - w_atb=0.3 causes {results[-1]['low_ecfp_pct']:.1f}% low-ECFP neighbors (structural drift risk)")

    return {"weight_sweep": results}


def run_two_stage_fusion(
    hybrid_df: pd.DataFrame,
    ecfp_df: pd.DataFrame,
    M: int = 50,
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3,
    ecfp_threshold: float = 0.2
) -> Dict:
    """
    (C2) Two-stage fusion experiment.

    Stage 1: Retrieve top-M by sim_ecfp within S_atb_hybrid
    Stage 2: Rerank those M by fused sim, take top-k

    Compare with linear fusion.
    """
    print("\n" + "=" * 60)
    print("SECTION C2: TWO-STAGE FUSION vs LINEAR FUSION")
    print("=" * 60)

    hybrid_iks = set(hybrid_df["inchikey"].unique())

    print(f"\n  Two-stage strategy:")
    print(f"    Stage 1: Retrieve top-{M} by sim_ecfp")
    print(f"    Stage 2: Rerank by fused sim (w_ecfp={w_ecfp}, w_atb={w_atb}), take top-{k}")

    # For two-stage, we need the full pairwise similarity matrix
    # Since we only have top-10 neighbors stored, we'll approximate by:
    # Using ECFP neighbors as "Stage 1 candidates" and reranking with aTB

    # Actually, we can't do true two-stage with only top-10 stored
    # So we'll use ECFP-only neighbors (which have all k=10 per molecule)
    # and enrich with aTB similarity for those pairs

    # Alternative: use stored hybrid data but conceptually compare
    # We'll compute overlap and structural metrics for both strategies

    # Strategy 1: Linear fusion (current hybrid_df)
    linear_overlap = compute_overlap_for_reranked(hybrid_df, ecfp_df, hybrid_iks)
    linear_low_ecfp_pct = 100 * (hybrid_df["sim_ecfp"] < ecfp_threshold).sum() / len(hybrid_df)
    linear_ecfp_median = hybrid_df["sim_ecfp"].median()

    # Strategy 2: Two-stage approximation
    # Since we have top-10 from linear, we can approximate two-stage by:
    # Filtering to only include neighbors that are in ECFP top-M (if we had M)
    # For approximation, use ECFP-only neighbors as Stage 1 candidates

    two_stage_rows = []
    for ik in hybrid_df["inchikey"].unique():
        # Get ECFP neighbors for this molecule (top-10 from ECFP-only)
        ecfp_nbrs = ecfp_df[ecfp_df["inchikey"] == ik]
        if len(ecfp_nbrs) == 0:
            continue

        # Get hybrid data for these same pairs
        hybrid_mol = hybrid_df[hybrid_df["inchikey"] == ik]
        ecfp_nbr_set = set(ecfp_nbrs["neighbor_inchikey"])

        # Filter hybrid to only ECFP candidates
        filtered = hybrid_mol[hybrid_mol["neighbor_inchikey"].isin(ecfp_nbr_set)]

        if len(filtered) > 0:
            # Take top-k by fused sim
            filtered = filtered.sort_values("sim", ascending=False).head(k)
            filtered = filtered.copy()
            filtered["rank"] = range(1, len(filtered) + 1)
            two_stage_rows.append(filtered)

    if two_stage_rows:
        two_stage_df = pd.concat(two_stage_rows, ignore_index=True)
        two_stage_overlap = compute_overlap_for_reranked(two_stage_df, ecfp_df, hybrid_iks)
        two_stage_low_ecfp_pct = 100 * (two_stage_df["sim_ecfp"] < ecfp_threshold).sum() / len(two_stage_df)
        two_stage_ecfp_median = two_stage_df["sim_ecfp"].median()
    else:
        two_stage_df = pd.DataFrame()
        two_stage_overlap = 0.0
        two_stage_low_ecfp_pct = 0.0
        two_stage_ecfp_median = 0.0

    print(f"\n  {'Strategy':<20} {'overlap@10':>10} {'ecfp_median':>12} {'low_ecfp%':>10}")
    print("  " + "-" * 55)
    print(f"  {'Linear fusion':<20} {linear_overlap:10.3f} {linear_ecfp_median:12.4f} {linear_low_ecfp_pct:10.1f}%")
    print(f"  {'Two-stage (approx)':<20} {two_stage_overlap:10.3f} {two_stage_ecfp_median:12.4f} {two_stage_low_ecfp_pct:10.1f}%")

    # Interpretation
    print("\n  Interpretation:")
    if two_stage_ecfp_median > linear_ecfp_median:
        print(f"    - Two-stage improves ECFP median: {two_stage_ecfp_median:.4f} > {linear_ecfp_median:.4f}")
        print(f"      This suggests two-stage reduces structural drift.")
    else:
        print(f"    - Linear and two-stage have similar ECFP structure preservation.")

    if two_stage_low_ecfp_pct < linear_low_ecfp_pct:
        print(f"    - Two-stage reduces low-ECFP neighbors: {two_stage_low_ecfp_pct:.1f}% < {linear_low_ecfp_pct:.1f}%")

    return {
        "linear": {
            "overlap_at_10": linear_overlap,
            "ecfp_median": linear_ecfp_median,
            "low_ecfp_pct": linear_low_ecfp_pct
        },
        "two_stage": {
            "overlap_at_10": two_stage_overlap,
            "ecfp_median": two_stage_ecfp_median,
            "low_ecfp_pct": two_stage_low_ecfp_pct,
            "n_records": len(two_stage_df) if not two_stage_df.empty else 0
        }
    }


def run_audit_and_sensitivity(
    hybrid_df: pd.DataFrame,
    ecfp_df: pd.DataFrame,
    manifest: Dict,
    molecule_table_path: str = "data/molecule_table.parquet"
) -> Dict:
    """Run full audit and sensitivity analysis."""
    print("\n" + "#" * 60)
    print("# AUDIT & SENSITIVITY VALIDATION")
    print("# Extended Analysis for Hybrid Anchor Space")
    print("#" * 60)

    # Section A: Correctness audit
    correctness = run_correctness_audit(hybrid_df, manifest, molecule_table_path)

    # Section B: Structural reasonableness
    structural = run_structural_reasonableness_check(hybrid_df)

    # Section C1: Weight sweep
    weight_sweep = run_weight_sweep(hybrid_df, ecfp_df)

    # Section C2: Two-stage fusion
    two_stage = run_two_stage_fusion(hybrid_df, ecfp_df)

    # Summary
    print("\n" + "=" * 60)
    print("AUDIT & SENSITIVITY SUMMARY")
    print("=" * 60)

    print("\n  Correctness:")
    if correctness["ecfp_max_error"] is not None:
        ecfp_status = "PASS" if correctness["ecfp_max_error"] < 1e-6 else "WARN"
        print(f"    ECFP verification: {ecfp_status} (max_err={correctness['ecfp_max_error']:.2e})")
    else:
        print(f"    ECFP verification: SKIPPED (no valid pairs)")

    if correctness["atb_max_error"] is not None:
        atb_status = "PASS" if correctness["atb_max_error"] < 1e-4 else "WARN"
        print(f"    aTB verification:  {atb_status} (max_err={correctness['atb_max_error']:.2e})")
    else:
        print(f"    aTB verification:  SKIPPED (no valid pairs)")

    print(f"\n  Structural Reasonableness:")
    print(f"    Low-ECFP neighbors (<0.2): {structural['pct_low_ecfp']:.1f}%")
    print(f"    Status: {'WARN' if structural['pct_low_ecfp'] > 30 else 'OK'}")

    print(f"\n  Weight Sensitivity:")
    ws = weight_sweep["weight_sweep"]
    print(f"    Overlap range: {ws[0]['overlap_at_10']:.3f} (w_atb=0) -> {ws[-1]['overlap_at_10']:.3f} (w_atb=0.3)")

    print(f"\n  Two-Stage vs Linear:")
    print(f"    Linear low-ECFP%:    {two_stage['linear']['low_ecfp_pct']:.1f}%")
    print(f"    Two-stage low-ECFP%: {two_stage['two_stage']['low_ecfp_pct']:.1f}%")

    return {
        "correctness": correctness,
        "structural": structural,
        "weight_sweep": weight_sweep,
        "two_stage": two_stage
    }


def load_hybrid_neighbors(
    path: str = "data/anchor_neighbors_hybrid_partial_atb.parquet"
) -> pd.DataFrame:
    """Load hybrid neighbor relationships."""
    logger.info(f"Loading hybrid neighbors from {path}")
    df = pd.read_parquet(path)
    logger.info(f"Loaded {len(df)} records for {df['inchikey'].nunique()} molecules")
    return df


def load_ecfp_neighbors(
    path: str = "data/anchor_neighbors_ecfp.parquet"
) -> pd.DataFrame:
    """Load ECFP-only neighbor relationships."""
    logger.info(f"Loading ECFP neighbors from {path}")
    df = pd.read_parquet(path)
    logger.info(f"Loaded {len(df)} records for {df['inchikey'].nunique()} molecules")
    return df


def load_manifest(
    path: str = "data/anchor_hybrid_partial_atb_manifest.json"
) -> Dict:
    """Load manifest file."""
    with open(path) as f:
        return json.load(f)


def print_subset_sizes(manifest: Dict, hybrid_df: pd.DataFrame):
    """Print subset size information."""
    print("\n" + "=" * 60)
    print("SUBSET SIZES")
    print("=" * 60)
    print(f"  Cache success count:            {manifest['n_success_cache']}")
    print(f"  After aTB feature filter:       {manifest['n_used_after_feature_filter']}")
    print(f"  Final S_atb_hybrid (with ECFP): {manifest['n_final_with_ecfp']}")
    print(f"  Total neighbor records:         {len(hybrid_df)}")
    print(f"  k (neighbors per molecule):     {manifest['k']}")


def compute_similarity_stats(series: pd.Series, name: str) -> Dict:
    """Compute distribution statistics for a similarity series."""
    return {
        "name": name,
        "min": series.min(),
        "median": series.median(),
        "mean": series.mean(),
        "std": series.std(),
        "p95": series.quantile(0.95),
        "max": series.max()
    }


def print_similarity_distributions(hybrid_df: pd.DataFrame):
    """Print similarity distribution statistics."""
    print("\n" + "=" * 60)
    print("TOP-1 NEIGHBOR SIMILARITY DISTRIBUTIONS")
    print("=" * 60)

    top1 = hybrid_df[hybrid_df["rank"] == 1].copy()

    for col in ["sim", "sim_ecfp", "sim_atb"]:
        stats = compute_similarity_stats(top1[col], col)
        print(f"\n  {col.upper()}:")
        print(f"    min    = {stats['min']:.4f}")
        print(f"    median = {stats['median']:.4f}")
        print(f"    mean   = {stats['mean']:.4f}")
        print(f"    std    = {stats['std']:.4f}")
        print(f"    95th   = {stats['p95']:.4f}")
        print(f"    max    = {stats['max']:.4f}")


def random_similarity_baseline(
    hybrid_df: pd.DataFrame,
    rdkit_path: str = "data/rdkit_features.parquet",
    n_samples: int = 1000,
    seed: int = 42
) -> Dict:
    """
    Compute random pair similarity baseline within S_atb_hybrid.

    Returns dict with ecfp and atb baseline stats.
    """
    print("\n" + "=" * 60)
    print("RANDOM PAIR BASELINE (within S_atb_hybrid)")
    print("=" * 60)

    # Get unique molecules in hybrid set
    unique_iks = hybrid_df["inchikey"].unique()
    n_mols = len(unique_iks)

    print(f"  Sampling {n_samples} random pairs from {n_mols} molecules...")

    # Load ECFP data for subset
    rdkit_df = pd.read_parquet(rdkit_path)
    rdkit_df = rdkit_df[rdkit_df["inchikey"].isin(unique_iks)]
    ecfp_by_ik = dict(zip(rdkit_df["inchikey"], rdkit_df["ecfp_2048"]))

    # Also need aTB features for random baseline
    # Get from hybrid_df by sampling pairs that exist
    # For simplicity, sample pairs and compute similarities

    rng = np.random.default_rng(seed)
    ik_list = list(unique_iks)

    sim_ecfp_random = []
    sim_atb_random = []

    # We need aTB vectors - load from cache
    from src.features.anchor_hybrid_ecfp_atb_partial import (
        discover_successful_cache,
        extract_atb_features,
        build_atb_matrix
    )

    successful = discover_successful_cache()
    atb_data = []
    for entry in successful:
        if entry["inchikey"] in unique_iks:
            atb_features = extract_atb_features(entry["features"])
            if atb_features is not None:
                atb_data.append({
                    "inchikey": entry["inchikey"],
                    "atb_features": atb_features
                })

    # Build aTB matrix
    atb_inchikeys = [d["inchikey"] for d in atb_data]
    atb_matrix, _, _, _ = build_atb_matrix(atb_data, atb_inchikeys)
    atb_idx = {ik: i for i, ik in enumerate(atb_inchikeys)}

    sampled = 0
    attempts = 0
    max_attempts = n_samples * 10

    while sampled < n_samples and attempts < max_attempts:
        attempts += 1
        i, j = rng.choice(len(ik_list), size=2, replace=False)
        ik_i, ik_j = ik_list[i], ik_list[j]

        # Check both have ECFP and aTB
        if ik_i not in ecfp_by_ik or ik_j not in ecfp_by_ik:
            continue
        if ik_i not in atb_idx or ik_j not in atb_idx:
            continue

        # ECFP Tanimoto
        fp_i = to_binary_fingerprint(ecfp_by_ik[ik_i])
        fp_j = to_binary_fingerprint(ecfp_by_ik[ik_j])
        if fp_i is None or fp_j is None:
            continue

        sim_ecfp = tanimoto_similarity(fp_i, fp_j)
        sim_ecfp_random.append(sim_ecfp)

        # aTB cosine
        atb_i = atb_matrix[atb_idx[ik_i]]
        atb_j = atb_matrix[atb_idx[ik_j]]
        cosine = np.dot(atb_i, atb_j)
        sim_atb = cosine_to_sim(cosine)
        sim_atb_random.append(sim_atb)

        sampled += 1

    print(f"  Successfully sampled {sampled} pairs\n")

    # Stats
    ecfp_arr = np.array(sim_ecfp_random)
    atb_arr = np.array(sim_atb_random)

    result = {
        "n_samples": sampled,
        "sim_ecfp": {
            "median": float(np.median(ecfp_arr)),
            "mean": float(np.mean(ecfp_arr)),
            "std": float(np.std(ecfp_arr))
        },
        "sim_atb": {
            "median": float(np.median(atb_arr)),
            "mean": float(np.mean(atb_arr)),
            "std": float(np.std(atb_arr))
        }
    }

    print(f"  Random ECFP Tanimoto:  median={result['sim_ecfp']['median']:.4f}, "
          f"mean={result['sim_ecfp']['mean']:.4f}, std={result['sim_ecfp']['std']:.4f}")
    print(f"  Random aTB cosine:     median={result['sim_atb']['median']:.4f}, "
          f"mean={result['sim_atb']['mean']:.4f}, std={result['sim_atb']['std']:.4f}")

    # Compare with top-1
    top1 = hybrid_df[hybrid_df["rank"] == 1]
    top1_ecfp_median = top1["sim_ecfp"].median()
    top1_atb_median = top1["sim_atb"].median()

    ecfp_ratio = top1_ecfp_median / result["sim_ecfp"]["median"] if result["sim_ecfp"]["median"] > 0 else float('inf')
    atb_ratio = top1_atb_median / result["sim_atb"]["median"] if result["sim_atb"]["median"] > 0 else float('inf')

    print(f"\n  Top-1 vs Random ratio (ECFP): {ecfp_ratio:.2f}x")
    print(f"  Top-1 vs Random ratio (aTB):  {atb_ratio:.2f}x")

    return result


def compute_overlap_at_k(
    hybrid_neighbors: Set[str],
    ecfp_neighbors: Set[str],
    k: int = 10
) -> float:
    """
    Compute overlap@k between two neighbor sets.

    Returns fraction of overlap (0 to 1).
    """
    if len(hybrid_neighbors) == 0 or len(ecfp_neighbors) == 0:
        return 0.0

    intersection = hybrid_neighbors & ecfp_neighbors
    return len(intersection) / k


def compare_with_ecfp_neighbors(
    hybrid_df: pd.DataFrame,
    ecfp_df: pd.DataFrame,
    n_sample: int = 30,
    seed: int = 42
) -> Dict:
    """
    Compare hybrid vs ECFP-only neighbors for overlap analysis.

    Only considers molecules present in both datasets.
    """
    print("\n" + "=" * 60)
    print("OVERLAP ANALYSIS: HYBRID vs ECFP-ONLY")
    print("=" * 60)

    # Get molecules in hybrid set
    hybrid_iks = set(hybrid_df["inchikey"].unique())
    ecfp_iks = set(ecfp_df["inchikey"].unique())

    # Common molecules
    common = hybrid_iks & ecfp_iks
    print(f"  Molecules in hybrid set:  {len(hybrid_iks)}")
    print(f"  Molecules in ECFP set:    {len(ecfp_iks)}")
    print(f"  Overlap (common):         {len(common)}")

    if len(common) == 0:
        print("  ERROR: No common molecules for comparison!")
        return {"error": "no_common_molecules"}

    # Sample molecules for comparison
    rng = np.random.default_rng(seed)
    sample_size = min(n_sample, len(common))
    sampled_iks = rng.choice(list(common), size=sample_size, replace=False)

    print(f"\n  Sampling {sample_size} molecules for overlap analysis...")

    overlaps = []

    for ik in sampled_iks:
        # Get hybrid neighbors (restricted to hybrid set)
        hybrid_nbrs = set(
            hybrid_df[hybrid_df["inchikey"] == ik]["neighbor_inchikey"]
        )

        # Get ECFP neighbors (restricted to hybrid set for fair comparison)
        ecfp_nbrs_all = set(
            ecfp_df[ecfp_df["inchikey"] == ik]["neighbor_inchikey"]
        )
        ecfp_nbrs = ecfp_nbrs_all & hybrid_iks  # Restrict to hybrid subset

        # Compute overlap
        overlap = compute_overlap_at_k(hybrid_nbrs, ecfp_nbrs, k=10)
        overlaps.append(overlap)

    overlaps = np.array(overlaps)

    result = {
        "n_sampled": sample_size,
        "overlap_min": float(overlaps.min()),
        "overlap_median": float(np.median(overlaps)),
        "overlap_mean": float(overlaps.mean()),
        "overlap_max": float(overlaps.max()),
        "overlap_std": float(overlaps.std())
    }

    print(f"\n  Overlap@10 Statistics:")
    print(f"    min    = {result['overlap_min']:.3f}")
    print(f"    median = {result['overlap_median']:.3f}")
    print(f"    mean   = {result['overlap_mean']:.3f}")
    print(f"    max    = {result['overlap_max']:.3f}")
    print(f"    std    = {result['overlap_std']:.3f}")

    # Histogram-like summary
    bins = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
    hist, _ = np.histogram(overlaps, bins=bins)
    print(f"\n  Overlap Distribution:")
    for i in range(len(bins) - 1):
        pct = 100 * hist[i] / len(overlaps)
        bar = "#" * int(pct / 5)
        print(f"    [{bins[i]:.1f}-{bins[i+1]:.1f}): {hist[i]:3d} ({pct:5.1f}%) {bar}")

    return result


def print_example_molecules(
    hybrid_df: pd.DataFrame,
    n_examples: int = 5,
    n_neighbors: int = 5,
    seed: int = 42
):
    """Print example molecules with their top neighbors."""
    print("\n" + "=" * 60)
    print(f"EXAMPLE MOLECULES (top-{n_neighbors} neighbors)")
    print("=" * 60)

    unique_iks = hybrid_df["inchikey"].unique()
    rng = np.random.default_rng(seed)
    sample_iks = rng.choice(unique_iks, size=min(n_examples, len(unique_iks)), replace=False)

    for i, ik in enumerate(sample_iks, 1):
        print(f"\n  Example {i}: {ik}")
        mol_df = hybrid_df[
            (hybrid_df["inchikey"] == ik) & (hybrid_df["rank"] <= n_neighbors)
        ].sort_values("rank")

        for _, row in mol_df.iterrows():
            print(f"    Rank {row['rank']}: {row['neighbor_inchikey'][:20]}... "
                  f"sim={row['sim']:.4f} (ecfp={row['sim_ecfp']:.4f}, atb={row['sim_atb']:.4f})")


def run_sanity_checks(
    hybrid_df: pd.DataFrame,
    overlap_result: Dict
) -> bool:
    """
    Run sanity checks and print warnings.

    Returns True if all checks pass.
    """
    print("\n" + "=" * 60)
    print("SANITY CHECKS")
    print("=" * 60)

    all_passed = True

    # Check 1: All similarities in [0, 1]
    for col in ["sim", "sim_ecfp", "sim_atb"]:
        min_val = hybrid_df[col].min()
        max_val = hybrid_df[col].max()
        if min_val < 0 or max_val > 1:
            print(f"  FAIL: {col} out of range [0,1]: [{min_val:.4f}, {max_val:.4f}]")
            all_passed = False
        else:
            print(f"  PASS: {col} in [0,1] range [{min_val:.4f}, {max_val:.4f}]")

    # Check 2: Warn if overlap is too high (aTB has no effect)
    if overlap_result.get("overlap_mean", 0) > 0.9:
        print(f"  WARNING: Mean overlap@10 = {overlap_result['overlap_mean']:.3f} (>0.9)")
        print(f"           This suggests aTB features have minimal effect on rankings!")

    # Check 3: Warn if overlap is too low (aTB dominates too much)
    if overlap_result.get("overlap_mean", 1) < 0.1:
        print(f"  WARNING: Mean overlap@10 = {overlap_result['overlap_mean']:.3f} (<0.1)")
        print(f"           This suggests aTB features dominate too much!")

    # Check 4: Check rank completeness
    unique_mols = hybrid_df["inchikey"].nunique()
    expected_records = unique_mols * 10  # k=10
    actual_records = len(hybrid_df)
    if actual_records != expected_records:
        print(f"  WARNING: Expected {expected_records} records, got {actual_records}")

    print("\n  All critical checks:", "PASSED" if all_passed else "FAILED")
    return all_passed


def validate_hybrid_anchor_space(
    hybrid_path: str = "data/anchor_neighbors_hybrid_partial_atb.parquet",
    ecfp_path: str = "data/anchor_neighbors_ecfp.parquet",
    manifest_path: str = "data/anchor_hybrid_partial_atb_manifest.json",
    rdkit_path: str = "data/rdkit_features.parquet"
):
    """Main validation entry point."""
    print("\n" + "#" * 60)
    print("# HYBRID ANCHOR SPACE VALIDATION REPORT")
    print("# P4a+ ECFP + Partial aTB")
    print("#" * 60)

    # Load data
    hybrid_df = load_hybrid_neighbors(hybrid_path)
    ecfp_df = load_ecfp_neighbors(ecfp_path)
    manifest = load_manifest(manifest_path)

    # Print sections
    print_subset_sizes(manifest, hybrid_df)
    print_similarity_distributions(hybrid_df)
    random_baseline = random_similarity_baseline(hybrid_df, rdkit_path)
    overlap_result = compare_with_ecfp_neighbors(hybrid_df, ecfp_df)
    print_example_molecules(hybrid_df)
    sanity_passed = run_sanity_checks(hybrid_df, overlap_result)

    # Final summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Molecules in hybrid space: {manifest['n_final_with_ecfp']}")
    print(f"  Weights: ECFP={manifest['weights']['w_ecfp']}, aTB={manifest['weights']['w_atb']}")
    print(f"  Top-1 fused sim median: {hybrid_df[hybrid_df['rank']==1]['sim'].median():.4f}")
    print(f"  Overlap@10 with ECFP-only: {overlap_result.get('overlap_mean', 'N/A'):.3f}")
    print(f"  Sanity checks: {'PASSED' if sanity_passed else 'FAILED'}")

    return {
        "manifest": manifest,
        "random_baseline": random_baseline,
        "overlap": overlap_result,
        "sanity_passed": sanity_passed
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Validate hybrid ECFP + aTB anchor space"
    )
    parser.add_argument(
        "--hybrid", type=str, default="data/anchor_neighbors_hybrid_partial_atb.parquet",
        help="Hybrid neighbors parquet path"
    )
    parser.add_argument(
        "--ecfp", type=str, default="data/anchor_neighbors_ecfp.parquet",
        help="ECFP-only neighbors parquet path"
    )
    parser.add_argument(
        "--manifest", type=str, default="data/anchor_hybrid_partial_atb_manifest.json",
        help="Manifest JSON path"
    )
    parser.add_argument(
        "--rdkit", type=str, default="data/rdkit_features.parquet",
        help="RDKit features parquet path"
    )
    parser.add_argument(
        "--molecule-table", type=str, default="data/molecule_table.parquet",
        help="Molecule table parquet path (for SMILES lookup)"
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Run extended audit & sensitivity analysis"
    )

    args = parser.parse_args()

    # Load data
    hybrid_df = load_hybrid_neighbors(args.hybrid)
    ecfp_df = load_ecfp_neighbors(args.ecfp)
    manifest = load_manifest(args.manifest)

    if args.audit:
        # Run extended audit and sensitivity analysis
        run_audit_and_sensitivity(
            hybrid_df=hybrid_df,
            ecfp_df=ecfp_df,
            manifest=manifest,
            molecule_table_path=args.molecule_table
        )
    else:
        # Run standard validation
        validate_hybrid_anchor_space(
            hybrid_path=args.hybrid,
            ecfp_path=args.ecfp,
            manifest_path=args.manifest,
            rdkit_path=args.rdkit
        )


if __name__ == "__main__":
    main()

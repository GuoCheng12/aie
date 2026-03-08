"""
src/features/anchor_hybrid_ecfp_atb_partial.py

Build hybrid anchor neighbor relationships using ECFP + aTB features.
Restricted to molecules with successful aTB cache (S_atb subset).

This is P4a+ - a validation branch to test if aTB features improve reference space.

Usage:
    python -m src.features.anchor_hybrid_ecfp_atb_partial --k 10 --w-ecfp 0.7 --w-atb 0.3
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logging import get_logger

logger = get_logger(__name__)

# InChIKey pattern
INCHIKEY_PATTERN = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

# aTB features to use (minimal stable set)
ATB_FEATURES = ["delta_volume", "delta_gap", "delta_dihedral", "excitation_energy"]


def is_valid_inchikey(inchikey: str) -> bool:
    """Check if string is a valid InChIKey format."""
    if not inchikey or pd.isna(inchikey):
        return False
    return bool(INCHIKEY_PATTERN.match(str(inchikey)))


def to_binary_fingerprint(fp: np.ndarray) -> np.ndarray:
    """Coerce fingerprint to binary (0/1) uint8 array."""
    if fp is None:
        return None
    fp_array = np.asarray(fp)
    return (fp_array > 0).astype(np.uint8)


def tanimoto_similarity(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """Compute Tanimoto similarity between two binary fingerprints."""
    intersection = np.sum(np.logical_and(fp1, fp2))
    a = np.sum(fp1 > 0)
    b = np.sum(fp2 > 0)
    union = a + b - intersection

    if union == 0:
        return 0.0
    return float(intersection) / float(union)


def safe_parse_float(value) -> Optional[float]:
    """Safely parse a value to float, handling strings and None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            val = float(value)
            if np.isnan(val) or np.isinf(val):
                return None
            return val
        except (ValueError, TypeError):
            return None
    return None


def discover_successful_cache(cache_dir: str = "cache/atb") -> List[Dict]:
    """
    Scan cache directory for successful aTB runs with valid features.

    Returns:
        List of dicts with keys: inchikey, features_path, status_path
    """
    cache_path = Path(cache_dir)
    successful = []

    logger.info(f"Scanning {cache_path} for successful aTB runs...")

    if not cache_path.exists():
        logger.warning(f"Cache directory does not exist: {cache_path}")
        return successful

    # Scan prefix directories
    for prefix_dir in cache_path.iterdir():
        if not prefix_dir.is_dir():
            continue

        for mol_dir in prefix_dir.iterdir():
            if not mol_dir.is_dir():
                continue

            status_path = mol_dir / "status.json"
            features_path = mol_dir / "features.json"

            if not status_path.exists():
                continue

            try:
                with open(status_path) as f:
                    status = json.load(f)

                if status.get("run_status") != "success":
                    continue

                if not features_path.exists():
                    logger.debug(f"Success but no features.json: {mol_dir.name}")
                    continue

                # Validate features.json is parseable
                with open(features_path) as f:
                    features = json.load(f)

                successful.append({
                    "inchikey": mol_dir.name,
                    "features_path": str(features_path),
                    "status_path": str(status_path),
                    "features": features
                })

            except (json.JSONDecodeError, IOError) as e:
                logger.debug(f"Error reading {mol_dir.name}: {e}")
                continue

    logger.info(f"Found {len(successful)} molecules with successful aTB runs")
    return successful


def extract_atb_features(features: Dict) -> Optional[Dict[str, float]]:
    """
    Extract the 4 minimal aTB features, returning None if any are missing.

    Args:
        features: Dict from features.json

    Returns:
        Dict with delta_volume, delta_gap, delta_dihedral, excitation_energy
        or None if any are missing/invalid
    """
    result = {}

    for feat_name in ATB_FEATURES:
        value = safe_parse_float(features.get(feat_name))
        if value is None:
            return None  # Any missing feature -> exclude molecule
        result[feat_name] = value

    return result


def load_ecfp_for_subset(
    inchikeys: List[str],
    rdkit_features_path: str = "data/rdkit_features.parquet"
) -> pd.DataFrame:
    """
    Load ECFP fingerprints for a subset of InChIKeys.

    Returns:
        DataFrame with columns: inchikey, ecfp_2048
    """
    logger.info(f"Loading ECFP data from {rdkit_features_path}")
    df = pd.read_parquet(rdkit_features_path)

    # Filter to subset
    inchikey_set = set(inchikeys)
    df_subset = df[df["inchikey"].isin(inchikey_set)].copy()

    logger.info(f"ECFP data: {len(df_subset)}/{len(inchikeys)} molecules found")

    return df_subset[["inchikey", "ecfp_2048"]]


def build_atb_matrix(
    atb_data: List[Dict],
    inchikeys: List[str]
) -> Tuple[np.ndarray, List[str], Dict[str, float], Dict[str, float]]:
    """
    Build z-scored and L2-normalized aTB feature matrix.

    Args:
        atb_data: List of dicts with inchikey and atb_features
        inchikeys: Ordered list of InChIKeys

    Returns:
        Tuple of:
        - Normalized matrix (n x 4)
        - Feature names
        - Mean dict
        - Std dict
    """
    # Create mapping
    feat_by_ik = {d["inchikey"]: d["atb_features"] for d in atb_data}

    n = len(inchikeys)
    n_feats = len(ATB_FEATURES)

    # Build raw matrix
    X = np.zeros((n, n_feats), dtype=np.float64)
    for i, ik in enumerate(inchikeys):
        feats = feat_by_ik[ik]
        for j, fname in enumerate(ATB_FEATURES):
            X[i, j] = feats[fname]

    # Z-score normalization
    means = {}
    stds = {}
    for j, fname in enumerate(ATB_FEATURES):
        col = X[:, j]
        mu = np.mean(col)
        sigma = np.std(col)
        if sigma < 1e-10:
            sigma = 1.0  # Avoid division by zero
        means[fname] = mu
        stds[fname] = sigma
        X[:, j] = (col - mu) / sigma

    # L2 normalize each row
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms < 1e-10] = 1.0  # Avoid division by zero
    X_normalized = X / norms

    return X_normalized, ATB_FEATURES, means, stds


def cosine_to_sim(cosine_val: float) -> float:
    """Map cosine similarity [-1, 1] to [0, 1]."""
    return (cosine_val + 1.0) / 2.0


def compute_hybrid_neighbors(
    ecfp_df: pd.DataFrame,
    atb_matrix: np.ndarray,
    inchikeys: List[str],
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3,
    progress_every: int = 50
) -> pd.DataFrame:
    """
    Compute top-k hybrid neighbors using ECFP + aTB similarity fusion.

    Args:
        ecfp_df: DataFrame with inchikey and ecfp_2048
        atb_matrix: L2-normalized aTB feature matrix (n x 4)
        inchikeys: Ordered list of InChIKeys matching atb_matrix rows
        k: Number of neighbors
        w_ecfp: Weight for ECFP Tanimoto
        w_atb: Weight for aTB cosine

    Returns:
        DataFrame with columns: inchikey, neighbor_inchikey, rank, sim, sim_ecfp, sim_atb
    """
    n = len(inchikeys)
    logger.info(f"Computing {k} hybrid neighbors for {n} molecules")
    logger.info(f"Weights: ECFP={w_ecfp}, aTB={w_atb}")

    # Build InChIKey to index mapping
    ik_to_idx = {ik: i for i, ik in enumerate(inchikeys)}

    # Get fingerprints in order
    ecfp_by_ik = dict(zip(ecfp_df["inchikey"], ecfp_df["ecfp_2048"]))
    fingerprints = [to_binary_fingerprint(ecfp_by_ik.get(ik)) for ik in inchikeys]

    # Precompute bit counts
    bit_counts = [np.sum(fp > 0) if fp is not None else 0 for fp in fingerprints]

    results: List[Tuple[str, str, int, float, float, float]] = []

    for i in range(n):
        if (i + 1) % progress_every == 0:
            logger.info(f"Progress: {i + 1}/{n} molecules")

        fp_i = fingerprints[i]
        if fp_i is None:
            continue

        atb_i = atb_matrix[i]

        similarities = []
        for j in range(n):
            if i == j:
                continue

            fp_j = fingerprints[j]
            if fp_j is None:
                continue

            # ECFP Tanimoto
            intersection = np.sum(np.logical_and(fp_i, fp_j))
            union = bit_counts[i] + bit_counts[j] - intersection
            sim_ecfp = float(intersection) / float(union) if union > 0 else 0.0

            # aTB cosine (already L2-normalized)
            atb_j = atb_matrix[j]
            cosine_val = np.dot(atb_i, atb_j)
            sim_atb = cosine_to_sim(cosine_val)

            # Fused similarity
            sim = w_ecfp * sim_ecfp + w_atb * sim_atb

            similarities.append((j, sim, sim_ecfp, sim_atb))

        # Sort by fused similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_k = similarities[:k]

        for rank, (j, sim, sim_ecfp, sim_atb) in enumerate(top_k, start=1):
            results.append((inchikeys[i], inchikeys[j], rank, sim, sim_ecfp, sim_atb))

    logger.info(f"Computed {len(results)} neighbor relationships")

    return pd.DataFrame(results, columns=[
        "inchikey", "neighbor_inchikey", "rank", "sim", "sim_ecfp", "sim_atb"
    ])


def build_hybrid_anchor_neighbors(
    output_path: str = "data/anchor_neighbors_hybrid_partial_atb.parquet",
    manifest_path: str = "data/anchor_hybrid_partial_atb_manifest.json",
    rdkit_features_path: str = "data/rdkit_features.parquet",
    cache_dir: str = "cache/atb",
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3
) -> Tuple[pd.DataFrame, Dict]:
    """
    Main entry point: build hybrid anchor neighbors.

    Returns:
        Tuple of (neighbors_df, manifest_dict)
    """
    # Step 1: Discover successful cache
    successful = discover_successful_cache(cache_dir)
    n_success_cache = len(successful)

    if n_success_cache == 0:
        logger.error("No successful aTB runs found in cache!")
        raise ValueError("No successful aTB runs found")

    # Step 2: Extract aTB features and filter to complete set
    atb_data = []
    for entry in successful:
        atb_features = extract_atb_features(entry["features"])
        if atb_features is not None:
            atb_data.append({
                "inchikey": entry["inchikey"],
                "atb_features": atb_features
            })

    n_used = len(atb_data)
    logger.info(f"After feature filter: {n_used}/{n_success_cache} molecules")

    if n_used == 0:
        logger.error("No molecules have all 4 required aTB features!")
        raise ValueError("No molecules with complete aTB features")

    # Step 3: Get InChIKeys and load ECFP
    inchikeys = [d["inchikey"] for d in atb_data]
    ecfp_df = load_ecfp_for_subset(inchikeys, rdkit_features_path)

    # Filter to molecules in ECFP data
    ecfp_inchikeys = set(ecfp_df["inchikey"])
    atb_data = [d for d in atb_data if d["inchikey"] in ecfp_inchikeys]
    inchikeys = [d["inchikey"] for d in atb_data]
    n_final = len(inchikeys)

    logger.info(f"Final molecule count: {n_final}")

    # Step 4: Build aTB matrix
    atb_matrix, feature_names, means, stds = build_atb_matrix(atb_data, inchikeys)

    # Log aTB feature stats
    logger.info("aTB feature z-score stats:")
    for fname in ATB_FEATURES:
        logger.info(f"  {fname}: mean={means[fname]:.4f}, std={stds[fname]:.4f}")

    # Step 5: Compute neighbors
    neighbors_df = compute_hybrid_neighbors(
        ecfp_df, atb_matrix, inchikeys,
        k=k, w_ecfp=w_ecfp, w_atb=w_atb
    )

    # Step 6: Save outputs
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    neighbors_df.to_parquet(output_path, index=False)
    logger.info(f"Saved neighbors to {output_path}")

    # Build manifest
    manifest = {
        "n_success_cache": n_success_cache,
        "n_used_after_feature_filter": n_used,
        "n_final_with_ecfp": n_final,
        "feature_list": feature_names,
        "weights": {"w_ecfp": w_ecfp, "w_atb": w_atb},
        "k": k,
        "atb_feature_stats": {
            "means": means,
            "stds": stds
        },
        "timestamp": datetime.now().isoformat(),
        "output_path": str(output_path)
    }

    manifest_path = Path(manifest_path)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Saved manifest to {manifest_path}")

    # Summary stats
    logger.info("=== Summary ===")
    logger.info(f"Cache success count: {n_success_cache}")
    logger.info(f"After feature filter: {n_used}")
    logger.info(f"Final count (with ECFP): {n_final}")
    logger.info(f"Total neighbor records: {len(neighbors_df)}")

    # Top-1 stats
    top1 = neighbors_df[neighbors_df["rank"] == 1]
    logger.info(f"Top-1 sim: min={top1['sim'].min():.3f}, median={top1['sim'].median():.3f}, max={top1['sim'].max():.3f}")
    logger.info(f"Top-1 sim_ecfp: min={top1['sim_ecfp'].min():.3f}, median={top1['sim_ecfp'].median():.3f}, max={top1['sim_ecfp'].max():.3f}")
    logger.info(f"Top-1 sim_atb: min={top1['sim_atb'].min():.3f}, median={top1['sim_atb'].median():.3f}, max={top1['sim_atb'].max():.3f}")

    return neighbors_df, manifest


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build hybrid anchor neighbors (ECFP + aTB) for partial cache subset"
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Number of neighbors per molecule (default: 10)"
    )
    parser.add_argument(
        "--w-ecfp", type=float, default=0.7,
        help="Weight for ECFP Tanimoto (default: 0.7)"
    )
    parser.add_argument(
        "--w-atb", type=float, default=0.3,
        help="Weight for aTB cosine (default: 0.3)"
    )
    parser.add_argument(
        "--output", type=str, default="data/anchor_neighbors_hybrid_partial_atb.parquet",
        help="Output parquet path"
    )
    parser.add_argument(
        "--manifest", type=str, default="data/anchor_hybrid_partial_atb_manifest.json",
        help="Output manifest JSON path"
    )
    parser.add_argument(
        "--input", type=str, default="data/rdkit_features.parquet",
        help="Input rdkit_features.parquet path"
    )
    parser.add_argument(
        "--cache-dir", type=str, default="cache/atb",
        help="aTB cache directory"
    )

    args = parser.parse_args()

    build_hybrid_anchor_neighbors(
        output_path=args.output,
        manifest_path=args.manifest,
        rdkit_features_path=args.input,
        cache_dir=args.cache_dir,
        k=args.k,
        w_ecfp=args.w_ecfp,
        w_atb=args.w_atb
    )


if __name__ == "__main__":
    main()

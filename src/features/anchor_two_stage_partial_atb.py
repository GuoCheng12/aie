"""
src/features/anchor_two_stage_partial_atb.py

Two-stage retrieval for hybrid ECFP + aTB anchor space (partial-aTB subset).

Stage 1: Candidate generation by ECFP Tanimoto (top-M)
Stage 2: Rerank by fused similarity within candidates

Usage:
    python -m src.features.anchor_two_stage_partial_atb \
        --rdkit data/rdkit_features.parquet \
        --atb-manifest data/anchor_hybrid_partial_atb_manifest.json \
        --output data/anchor_neighbors_two_stage_partial_atb.parquet \
        --M 50 \
        --k 10 \
        --w-ecfp 0.7 \
        --w-atb 0.3
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm


# ========== CONSTANTS ==========
ATB_FEATURES = ["delta_volume", "delta_gap", "delta_dihedral", "excitation_energy"]


# ========== HELPER FUNCTIONS ==========

def safe_parse_float(val) -> Optional[float]:
    """Parse float from string or numeric, return None if invalid."""
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            parsed = float(val)
        except ValueError:
            return None
    else:
        parsed = float(val)

    if not np.isfinite(parsed):
        return None
    return parsed


def extract_atb_features(feature_dict: dict) -> Optional[Dict[str, float]]:
    """
    Extract the 4 required aTB features from dict.
    Returns None if any feature is missing or invalid.
    """
    result = {}
    for feat in ATB_FEATURES:
        val = feature_dict.get(feat)
        if feat == "excitation_energy":
            parsed = safe_parse_float(val)
        else:
            parsed = safe_parse_float(val) if val is not None else None

        if parsed is None:
            return None
        result[feat] = parsed

    return result


def is_valid_inchikey(ik: str) -> bool:
    """Check if InChIKey format is valid."""
    if not isinstance(ik, str) or len(ik) != 27:
        return False
    if ik[14] != '-' or ik[25] != '-':
        return False
    if not ik[:14].isupper() or not ik[15:25].isupper() or not ik[26].isupper():
        return False
    return True


def to_binary_fingerprint(fp: np.ndarray) -> np.ndarray:
    """Coerce fingerprint to binary uint8."""
    return (fp > 0).astype(np.uint8)


def tanimoto_similarity(fp1: np.ndarray, fp2: np.ndarray) -> float:
    """Compute Tanimoto similarity for binary fingerprints."""
    fp1_bin = to_binary_fingerprint(fp1)
    fp2_bin = to_binary_fingerprint(fp2)

    intersection = np.sum(fp1_bin & fp2_bin)
    union = np.sum(fp1_bin | fp2_bin)

    if union == 0:
        return 0.0
    return float(intersection) / float(union)


def cosine_to_sim(cosine: float) -> float:
    """Map cosine similarity [-1, 1] to [0, 1]."""
    return (cosine + 1.0) / 2.0


def build_atb_matrix(
    atb_data: List[dict],
    inchikeys: List[str]
) -> Tuple[np.ndarray, List[str], Dict[str, float], Dict[str, float]]:
    """
    Build aTB feature matrix with z-score + L2 normalization.

    Returns:
        matrix: (n, 4) L2-normalized aTB features
        feature_names: List of feature names
        means: Dict of feature means
        stds: Dict of feature stds
    """
    ik_to_atb = {d["inchikey"]: d["atb_features"] for d in atb_data}

    # Extract features in order
    raw_matrix = []
    for ik in inchikeys:
        feats = ik_to_atb[ik]
        raw_matrix.append([feats[f] for f in ATB_FEATURES])

    raw_matrix = np.array(raw_matrix, dtype=np.float64)

    # Compute stats
    means = {ATB_FEATURES[i]: raw_matrix[:, i].mean() for i in range(4)}
    stds = {ATB_FEATURES[i]: raw_matrix[:, i].std(ddof=1) for i in range(4)}

    # Z-score normalization
    mean_vec = np.array([means[f] for f in ATB_FEATURES])
    std_vec = np.array([stds[f] for f in ATB_FEATURES])
    std_vec[std_vec == 0] = 1.0  # Prevent division by zero

    normalized = (raw_matrix - mean_vec) / std_vec

    # L2 normalization
    norms = np.linalg.norm(normalized, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = normalized / norms

    return normalized, ATB_FEATURES, means, stds


def discover_successful_cache() -> List[dict]:
    """
    Discover all successful aTB cache entries from nested directory structure.
    Returns list of dicts with 'inchikey' and 'atb_features'.
    """
    cache_dir = Path("cache/atb")
    if not cache_dir.exists():
        return []

    results = []

    # Scan prefix directories (cache/atb/AA/AAAQKTZKLRYKHR-UHFFFAOYSA-N/)
    for prefix_dir in cache_dir.iterdir():
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
                # Check status
                with open(status_path) as f:
                    status = json.load(f)

                if status.get("run_status") != "success":
                    continue

                if not features_path.exists():
                    continue

                # Load features
                with open(features_path) as f:
                    features = json.load(f)

                # Extract aTB features
                ik = mol_dir.name
                if not is_valid_inchikey(ik):
                    continue

                atb_feats = extract_atb_features(features)
                if atb_feats is None:
                    continue

                results.append({"inchikey": ik, "atb_features": atb_feats})

            except (json.JSONDecodeError, IOError):
                continue

    return results


def load_ecfp_for_subset(rdkit_path: Path, inchikeys: List[str]) -> Tuple[np.ndarray, List[str]]:
    """
    Load ECFP fingerprints for the given InChIKey subset.

    Returns:
        ecfp_matrix: (n, 2048) uint8 array
        inchikeys_ordered: List of InChIKeys in matrix order
    """
    df = pd.read_parquet(rdkit_path)
    df = df[df["inchikey"].isin(inchikeys)].copy()

    # Sort by inchikey for consistency
    df = df.sort_values("inchikey").reset_index(drop=True)

    # ECFP is stored as a single column with array values
    if "ecfp_2048" in df.columns:
        ecfp_matrix = np.vstack(df["ecfp_2048"].values).astype(np.int8)
    else:
        # Fallback: try to extract from separate columns
        ecfp_cols = [f"ecfp_{i}" for i in range(2048)]
        ecfp_matrix = df[ecfp_cols].values.astype(np.int8)

    return ecfp_matrix, df["inchikey"].tolist()


def compute_two_stage_neighbors(
    ecfp_matrix: np.ndarray,
    atb_matrix: np.ndarray,
    inchikeys: List[str],
    M: int = 50,
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3
) -> pd.DataFrame:
    """
    Two-stage retrieval:
    Stage 1: Get top-M candidates by ECFP Tanimoto
    Stage 2: Rerank by fused similarity within candidates

    Returns DataFrame with columns:
        inchikey, neighbor_inchikey, rank, sim, sim_ecfp, sim_atb, stage1_rank
    """
    n = len(inchikeys)
    results = []

    for i in tqdm(range(n), desc="Two-stage retrieval"):
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
                "stage1_rank": stage1_rank_0based + 1  # 1-indexed
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


def build_two_stage_anchor_neighbors(
    rdkit_path: Path,
    atb_manifest_path: Path,
    output_path: Path,
    M: int = 50,
    k: int = 10,
    w_ecfp: float = 0.7,
    w_atb: float = 0.3
) -> dict:
    """
    Main function to build two-stage anchor neighbors.

    Returns manifest dict with metadata.
    """
    print(f"[Two-Stage Builder] M={M}, k={k}, w_ecfp={w_ecfp}, w_atb={w_atb}")

    # Load manifest for existing aTB subset
    with open(atb_manifest_path, "r") as f:
        manifest = json.load(f)

    print(f"[Two-Stage Builder] Using aTB subset from: {atb_manifest_path}")
    print(f"[Two-Stage Builder] n_final_with_ecfp: {manifest['n_final_with_ecfp']}")

    # Discover successful aTB cache
    print("[Two-Stage Builder] Discovering aTB cache...")
    atb_data = discover_successful_cache()
    print(f"[Two-Stage Builder] Found {len(atb_data)} molecules with complete aTB features")

    # Filter to only the subset used in manifest
    atb_inchikeys = {d["inchikey"] for d in atb_data}

    # Load ECFP for this subset
    print("[Two-Stage Builder] Loading ECFP features...")
    ecfp_matrix, inchikeys = load_ecfp_for_subset(rdkit_path, list(atb_inchikeys))
    print(f"[Two-Stage Builder] Loaded ECFP for {len(inchikeys)} molecules")

    # Filter aTB data to match ECFP subset
    atb_data = [d for d in atb_data if d["inchikey"] in inchikeys]

    # Build aTB matrix
    print("[Two-Stage Builder] Building aTB matrix...")
    atb_matrix, feat_names, means, stds = build_atb_matrix(atb_data, inchikeys)
    print(f"[Two-Stage Builder] aTB matrix shape: {atb_matrix.shape}")

    # Compute two-stage neighbors
    print("[Two-Stage Builder] Computing two-stage neighbors...")
    neighbors_df = compute_two_stage_neighbors(
        ecfp_matrix=ecfp_matrix,
        atb_matrix=atb_matrix,
        inchikeys=inchikeys,
        M=M,
        k=k,
        w_ecfp=w_ecfp,
        w_atb=w_atb
    )

    # Save output
    print(f"[Two-Stage Builder] Saving to {output_path}...")
    neighbors_df.to_parquet(output_path, index=False)

    # Create manifest
    output_manifest = {
        "strategy": "two_stage",
        "n_molecules": len(inchikeys),
        "M": M,
        "k": k,
        "weights": {
            "w_ecfp": w_ecfp,
            "w_atb": w_atb
        },
        "feature_list": feat_names,
        "atb_feature_stats": {
            "means": means,
            "stds": stds
        },
        "source_manifest": str(atb_manifest_path),
        "output_path": str(output_path)
    }

    manifest_path = output_path.parent / f"{output_path.stem}_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(output_manifest, f, indent=2)

    print(f"[Two-Stage Builder] Manifest saved to {manifest_path}")
    print(f"[Two-Stage Builder] Total neighbor pairs: {len(neighbors_df)}")
    print("[Two-Stage Builder] DONE")

    return output_manifest


def main():
    parser = argparse.ArgumentParser(description="Two-stage retrieval for ECFP + aTB anchor space")
    parser.add_argument(
        "--rdkit", type=Path, required=True,
        help="Path to rdkit_features.parquet"
    )
    parser.add_argument(
        "--atb-manifest", type=Path, required=True,
        help="Path to anchor_hybrid_partial_atb_manifest.json"
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Output path for neighbor table (e.g., data/anchor_neighbors_two_stage_partial_atb.parquet)"
    )
    parser.add_argument(
        "--M", type=int, default=50,
        help="Stage 1: Number of ECFP candidates (default: 50)"
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Stage 2: Number of final neighbors to retrieve (default: 10)"
    )
    parser.add_argument(
        "--w-ecfp", type=float, default=0.7,
        help="Weight for ECFP in fused similarity (default: 0.7)"
    )
    parser.add_argument(
        "--w-atb", type=float, default=0.3,
        help="Weight for aTB in fused similarity (default: 0.3)"
    )

    args = parser.parse_args()

    # Validate weights
    if not np.isclose(args.w_ecfp + args.w_atb, 1.0):
        raise ValueError(f"Weights must sum to 1.0, got {args.w_ecfp + args.w_atb}")

    build_two_stage_anchor_neighbors(
        rdkit_path=args.rdkit,
        atb_manifest_path=args.atb_manifest,
        output_path=args.output,
        M=args.M,
        k=args.k,
        w_ecfp=args.w_ecfp,
        w_atb=args.w_atb
    )


if __name__ == "__main__":
    main()

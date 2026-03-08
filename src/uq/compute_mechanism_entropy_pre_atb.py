"""
src/uq/compute_mechanism_entropy_pre_atb.py

Compute mechanism_entropy per molecule as neighborhood label ambiguity proxy.

For each query:
- Get top-k neighbors and their mechanism labels
- EXCLUDE "other" and "unknown" labels from entropy calculation
- Compute similarity-weighted label distribution p(m|q) over KNOWN mechanisms only
- mechanism_entropy = H(p) / log(M_eff)

Note: "other" and "unknown" are excluded because they represent unlabeled data,
not competing mechanism hypotheses. High entropy should indicate genuine
ambiguity among known mechanisms.
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def compute_softmax_weights(similarities: np.ndarray, beta: float = 10.0) -> np.ndarray:
    """
    Compute softmax weights from similarities.
    
    w_j = exp(beta * sim_j) / sum(exp(beta * sim_i))
    
    Args:
        similarities: Array of similarity values
        beta: Temperature parameter (higher = more weight on closest)
    
    Returns:
        Normalized weights summing to 1
    """
    if len(similarities) == 0:
        return np.array([])
    
    # For numerical stability, subtract max before exp
    scaled = beta * similarities
    scaled = scaled - np.max(scaled)
    exp_scaled = np.exp(scaled)
    weights = exp_scaled / np.sum(exp_scaled)
    
    return weights


def compute_entropy(probs: np.ndarray) -> float:
    """
    Compute entropy H(p) = -sum(p * log(p)) using natural log.
    
    Args:
        probs: Probability distribution (should sum to 1)
    
    Returns:
        Entropy value
    """
    # Filter out zeros to avoid log(0)
    probs = probs[probs > 0]
    if len(probs) == 0:
        return 0.0
    
    return -np.sum(probs * np.log(probs))


def compute_mechanism_entropy_for_query(
    neighbor_labels: List[str],
    neighbor_sims: np.ndarray,
    beta: float = 10.0,
    exclude_labels: List[str] = None
) -> Dict:
    """
    Compute mechanism_entropy for a single query molecule.

    Args:
        neighbor_labels: List of mechanism labels for neighbors
        neighbor_sims: Array of similarity values for neighbors
        beta: Softmax temperature
        exclude_labels: Labels to exclude from entropy calculation (default: ["other", "unknown"])

    Returns:
        Dict with mechanism_entropy, M_eff, top_label, top_label_prob, n_excluded_neighbors
    """
    if exclude_labels is None:
        exclude_labels = ["other", "unknown"]

    if len(neighbor_labels) == 0:
        return {
            'mechanism_entropy': np.nan,
            'M_eff': 0,
            'top_label': None,
            'top_label_prob': np.nan,
            'n_excluded_neighbors': 0,
            'n_unknown_neighbors': 0,
            'excluded_labels': exclude_labels
        }

    # Compute softmax weights
    weights = compute_softmax_weights(neighbor_sims, beta)

    # Count excluded neighbors (and keep a direct count for "unknown" for diagnostics/tests).
    n_excluded = sum(1 for label in neighbor_labels if label in exclude_labels)
    n_unknown = sum(1 for label in neighbor_labels if label == "unknown")

    # Aggregate weights by label, EXCLUDING specified labels
    label_weights = {}
    for label, weight in zip(neighbor_labels, weights):
        if label not in exclude_labels:
            label_weights[label] = label_weights.get(label, 0.0) + weight

    # Handle case where all neighbors have excluded labels
    if len(label_weights) == 0:
        # Find top label from ALL neighbors (including excluded) for reference
        all_label_weights = {}
        for label, weight in zip(neighbor_labels, weights):
            all_label_weights[label] = all_label_weights.get(label, 0.0) + weight
        all_labels = list(all_label_weights.keys())
        all_probs = np.array([all_label_weights[l] for l in all_labels])
        top_label = all_labels[np.argmax(all_probs)] if all_labels else None
        top_label_prob = all_probs[np.argmax(all_probs)] if all_labels else np.nan

        return {
            'mechanism_entropy': np.nan,  # Cannot compute entropy without known labels
            'M_eff': 0,
            'top_label': top_label,
            'top_label_prob': top_label_prob,
            'n_excluded_neighbors': n_excluded,
            'n_unknown_neighbors': n_unknown,
            'excluded_labels': exclude_labels
        }

    # Re-normalize weights after exclusion
    labels = list(label_weights.keys())
    raw_probs = np.array([label_weights[label] for label in labels])
    probs = raw_probs / np.sum(raw_probs)  # Re-normalize to sum to 1

    # M_eff = number of distinct KNOWN labels
    M_eff = len(labels)

    if M_eff <= 1:
        # Only one known label -> entropy = 0
        mechanism_entropy = 0.0
    else:
        # Compute normalized entropy
        H = compute_entropy(probs)
        mechanism_entropy = H / np.log(M_eff)

    # Top label (among known labels)
    top_idx = np.argmax(probs)
    top_label = labels[top_idx]
    top_label_prob = probs[top_idx]

    return {
        'mechanism_entropy': mechanism_entropy,
        'M_eff': M_eff,
        'top_label': top_label,
        'top_label_prob': top_label_prob,
        'n_excluded_neighbors': n_excluded,
        'n_unknown_neighbors': n_unknown,
        'excluded_labels': exclude_labels
    }


def compute_all_mechanism_entropies(
    neighbors: pd.DataFrame,
    label_map: pd.DataFrame,
    beta: float = 10.0
) -> pd.DataFrame:
    """
    Compute mechanism_entropy for all query molecules.
    
    Args:
        neighbors: DataFrame with inchikey, neighbor_inchikey, rank, tanimoto_sim
        label_map: DataFrame with inchikey, mechanism_label
    
    Returns:
        DataFrame with mechanism_entropy per query inchikey
    """
    logger.info(f"Computing mechanism_entropy with beta={beta}")
    
    # Create label lookup dict
    label_dict = dict(zip(label_map['inchikey'], label_map['mechanism_label']))
    
    results = []
    query_inchikeys = neighbors['inchikey'].unique()
    
    for query_ik in query_inchikeys:
        # Get neighbors for this query
        query_neighbors = neighbors[neighbors['inchikey'] == query_ik].sort_values('rank')
        
        neighbor_iks = query_neighbors['neighbor_inchikey'].tolist()
        neighbor_sims = query_neighbors['tanimoto_sim'].values
        
        # Get labels for neighbors
        neighbor_labels = [label_dict.get(nik, "unknown") for nik in neighbor_iks]
        
        # Compute entropy
        result = compute_mechanism_entropy_for_query(neighbor_labels, neighbor_sims, beta)
        result['inchikey'] = query_ik
        result['beta_used'] = beta
        result['k_used'] = len(neighbor_labels)
        
        results.append(result)
    
    entropy_df = pd.DataFrame(results)
    
    # Reorder columns (exclude 'excluded_labels' from parquet - it's always the same)
    cols = ['inchikey', 'mechanism_entropy', 'M_eff', 'top_label', 'top_label_prob',
            'beta_used', 'k_used', 'n_excluded_neighbors']
    entropy_df = entropy_df[cols]

    # Log stats
    valid = entropy_df['mechanism_entropy'].dropna()
    n_all_excluded = entropy_df['mechanism_entropy'].isna().sum()
    logger.info(f"  Computed for {len(entropy_df)} molecules")
    logger.info(f"  Excluded labels: ['other', 'unknown']")
    logger.info(f"  Molecules with all neighbors excluded: {n_all_excluded}")
    if len(valid) > 0:
        logger.info(f"  mechanism_entropy range: [{valid.min():.4f}, {valid.max():.4f}]")
        logger.info(f"  mechanism_entropy median: {valid.median():.4f}")
    
    return entropy_df


def run_compute_mechanism_entropy(
    neighbors_path: str = "data/anchor_neighbors_ecfp.parquet",
    label_map_path: str = "data/mechanism_label_map.parquet",
    output_path: str = "data/mechanism_entropy_pre_atb.parquet",
    beta: float = 10.0
) -> pd.DataFrame:
    """Run the full mechanism_entropy computation pipeline."""
    
    logger.info("=" * 60)
    logger.info("Computing Mechanism Entropy (Pre-aTB)")
    logger.info("=" * 60)
    
    # Load data
    logger.info(f"Loading neighbors from {neighbors_path}")
    neighbors = pd.read_parquet(neighbors_path)
    logger.info(f"  Loaded {len(neighbors)} neighbor records")
    
    logger.info(f"Loading label map from {label_map_path}")
    label_map = pd.read_parquet(label_map_path)
    logger.info(f"  Loaded {len(label_map)} molecule labels")
    
    # Compute entropies
    entropy_df = compute_all_mechanism_entropies(neighbors, label_map, beta)
    
    # Save
    logger.info(f"Saving to {output_path}")
    entropy_df.to_parquet(output_path, index=False)
    
    logger.info("=" * 60)
    logger.info("Done!")
    logger.info("=" * 60)
    
    return entropy_df


def main():
    parser = argparse.ArgumentParser(description="Compute mechanism_entropy")
    parser.add_argument('--neighbors', type=str, default='data/anchor_neighbors_ecfp.parquet',
                       help='Path to anchor_neighbors_ecfp.parquet')
    parser.add_argument('--label-map', type=str, default='data/mechanism_label_map.parquet',
                       help='Path to mechanism_label_map.parquet')
    parser.add_argument('--output', type=str, default='data/mechanism_entropy_pre_atb.parquet',
                       help='Output path')
    parser.add_argument('--beta', type=float, default=10.0,
                       help='Softmax temperature (default: 10.0)')
    
    args = parser.parse_args()
    run_compute_mechanism_entropy(args.neighbors, args.label_map, args.output, args.beta)


if __name__ == "__main__":
    main()

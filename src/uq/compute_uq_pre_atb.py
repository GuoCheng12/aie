"""
src/uq/compute_uq_pre_atb.py

Compute pre-aTB UQ scores and router actions for all experimental records.
Uses ECFP-only anchor neighbor space from P4a while P2 (aTB) is temporarily skipped.

Inputs:
- data/private_clean.parquet (record-level)
- data/anchor_neighbors_ecfp.parquet (molecule-level, k=10, Tanimoto similarities)

Outputs:
- data/uq_scores_pre_atb.parquet (record-level)
- data/uq_manifest_pre_atb.json (thresholds, percentiles, counts, timestamp)
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Critical fields for missing-rate computation (train-only facts).
CRITICAL_FIELDS = [
    'emission_solid',
    'emission_aggr',
]

# Missing indicator column names
MISSING_COLUMNS = [f"{field}_missing" for field in CRITICAL_FIELDS]


def load_data(private_clean_path: str, neighbors_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load private_clean and anchor_neighbors data."""
    logger.info(f"Loading private_clean from {private_clean_path}")
    private_clean = pd.read_parquet(private_clean_path)
    logger.info(f"  Loaded {len(private_clean)} records")
    
    logger.info(f"Loading anchor neighbors from {neighbors_path}")
    neighbors = pd.read_parquet(neighbors_path)
    logger.info(f"  Loaded {len(neighbors)} neighbor records for {neighbors['inchikey'].nunique()} molecules")
    
    return private_clean, neighbors


def compute_c_sim(neighbors: pd.DataFrame) -> pd.DataFrame:
    """
    Compute C_sim (coverage - similarity) for each molecule.
    C_sim = mean of top-k Tanimoto similarities.
    
    Returns DataFrame with columns: inchikey, C_sim, top1_sim, similarities (list)
    """
    logger.info("Computing C_sim (mean of top-k Tanimoto similarities)")
    
    # Group neighbors by query molecule
    result = []
    for inchikey, group in neighbors.groupby('inchikey'):
        # Sort by rank to ensure correct ordering
        group_sorted = group.sort_values('rank')
        similarities = group_sorted['tanimoto_sim'].values.tolist()
        
        # Compute statistics
        c_sim = np.mean(similarities)
        top1_sim = similarities[0] if len(similarities) > 0 else np.nan
        
        result.append({
            'inchikey': inchikey,
            'C_sim': c_sim,
            'top1_sim': top1_sim,
            'similarities': similarities
        })
    
    c_sim_df = pd.DataFrame(result)
    logger.info(f"  Computed C_sim for {len(c_sim_df)} molecules")
    logger.info(f"  C_sim range: [{c_sim_df['C_sim'].min():.4f}, {c_sim_df['C_sim'].max():.4f}]")
    
    return c_sim_df


def compute_c_meta(private_clean: pd.DataFrame) -> pd.DataFrame:
    """
    Compute C_meta (coverage - metadata) for each record.
    C_meta = 1 - missing_rate where missing_rate = sum(missing) / len(CRITICAL_FIELDS).
    
    Returns DataFrame with columns: id, C_meta, missing_rate, missing_count, missing_fields
    """
    logger.info("Computing C_meta (1 - missing_rate)")
    
    result = []
    for idx, row in private_clean.iterrows():
        missing_count = 0
        missing_fields = []
        
        for field, col in zip(CRITICAL_FIELDS, MISSING_COLUMNS):
            if col in row and row[col] == True:
                missing_count += 1
                missing_fields.append(field)
        
        missing_rate = missing_count / len(CRITICAL_FIELDS)
        c_meta = 1 - missing_rate
        
        result.append({
            'id': row['id'],
            'inchikey': row.get('inchikey', ''),
            'C_meta': c_meta,
            'missing_rate': missing_rate,
            'missing_count': missing_count,
            'missing_fields': missing_fields
        })
    
    c_meta_df = pd.DataFrame(result)
    logger.info(f"  Computed C_meta for {len(c_meta_df)} records")
    logger.info(f"  C_meta range: [{c_meta_df['C_meta'].min():.4f}, {c_meta_df['C_meta'].max():.4f}]")
    
    return c_meta_df


def compute_novelty(c_sim_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute novelty (pre-aTB) from top-1 similarity.
    novelty_raw = 1 - top1_sim
    novelty = percentile normalized to [0, 1] using p05/p95 on valid rows.
    
    Returns DataFrame with: inchikey, novelty_raw, novelty
    """
    logger.info("Computing novelty (1 - top1_sim, percentile normalized)")
    
    # Compute raw novelty
    c_sim_df = c_sim_df.copy()
    c_sim_df['novelty_raw'] = 1 - c_sim_df['top1_sim']
    
    # Percentile scaling on valid rows
    valid_novelty = c_sim_df['novelty_raw'].dropna()
    p05 = valid_novelty.quantile(0.05)
    p95 = valid_novelty.quantile(0.95)
    
    logger.info(f"  novelty_raw p05={p05:.4f}, p95={p95:.4f}")
    
    # Normalize to [0, 1]
    if p95 > p05:
        c_sim_df['novelty'] = np.clip((c_sim_df['novelty_raw'] - p05) / (p95 - p05), 0, 1)
    else:
        # Edge case: all same value
        c_sim_df['novelty'] = 0.5
    
    logger.info(f"  novelty range: [{c_sim_df['novelty'].min():.4f}, {c_sim_df['novelty'].max():.4f}]")
    
    return c_sim_df[['inchikey', 'novelty_raw', 'novelty']], (p05, p95)


def compute_aleatoric(c_sim_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute aleatoric (pre-aTB proxy) as entropy of normalized top-k similarities.
    p_i = s_i / sum(s_i)
    aleatoric = entropy(p) / log(k) normalized to [0, 1]
    
    Returns DataFrame with: inchikey, aleatoric
    """
    logger.info("Computing aleatoric (entropy of normalized similarities)")
    
    result = []
    for idx, row in c_sim_df.iterrows():
        inchikey = row['inchikey']
        sims = row['similarities']
        
        if not sims or len(sims) == 0:
            result.append({'inchikey': inchikey, 'aleatoric': np.nan})
            continue
        
        sims = np.array(sims)
        total = np.sum(sims)
        
        if total == 0:
            # No similarity at all -> maximum uncertainty
            result.append({'inchikey': inchikey, 'aleatoric': 1.0})
            continue
        
        # Convert to probabilities
        p = sims / total
        
        # Compute entropy (avoiding log(0))
        p_nonzero = p[p > 0]
        entropy = -np.sum(p_nonzero * np.log(p_nonzero))
        
        # Normalize by log(k)
        k = len(sims)
        max_entropy = np.log(k)
        aleatoric = entropy / max_entropy if max_entropy > 0 else 0.0
        
        result.append({'inchikey': inchikey, 'aleatoric': aleatoric})
    
    aleatoric_df = pd.DataFrame(result)
    logger.info(f"  aleatoric range: [{aleatoric_df['aleatoric'].min():.4f}, {aleatoric_df['aleatoric'].max():.4f}]")
    
    return aleatoric_df


def compute_thresholds(scores_df: pd.DataFrame) -> Dict[str, float]:
    """
    Compute router thresholds on valid population.
    - cov_low = 20th percentile of coverage
    - cov_high = 80th percentile of coverage
    - nov_high = 80th percentile of novelty
    - ale_high = 80th percentile of aleatoric
    """
    logger.info("Computing router thresholds on valid population")
    
    # Valid rows: those with non-NaN coverage
    valid = scores_df[scores_df['coverage'].notna()]
    logger.info(f"  Valid rows for threshold computation: {len(valid)}")
    
    thresholds = {
        'cov_low': valid['coverage'].quantile(0.20),
        'cov_high': valid['coverage'].quantile(0.80),
        'nov_high': valid['novelty'].quantile(0.80) if 'novelty' in valid.columns else 0.8,
        'ale_high': valid['aleatoric'].quantile(0.80) if 'aleatoric' in valid.columns else 0.8
    }
    
    logger.info(f"  cov_low={thresholds['cov_low']:.4f}, cov_high={thresholds['cov_high']:.4f}")
    logger.info(f"  nov_high={thresholds['nov_high']:.4f}, ale_high={thresholds['ale_high']:.4f}")
    
    return thresholds


def compute_router_action(row: pd.Series, thresholds: Dict[str, float]) -> str:
    """
    Compute router action for a single row.
    Deterministic if/elif cascade evaluated in order.
    """
    c_sim = row.get('C_sim')
    coverage = row.get('coverage')
    novelty = row.get('novelty')
    aleatoric = row.get('aleatoric')
    
    cov_low = thresholds['cov_low']
    cov_high = thresholds['cov_high']
    nov_high = thresholds['nov_high']
    ale_high = thresholds['ale_high']
    
    # Priority 0: Invalid/missing data
    if pd.isna(c_sim) or pd.isna(coverage) or coverage < cov_low:
        return "Evidence-insufficient"
    
    # Priority 1: Novelty-candidate (CONSERVATIVE GATE)
    if not pd.isna(novelty) and novelty >= nov_high:
        if coverage < cov_high or (not pd.isna(aleatoric) and aleatoric >= ale_high):
            return "Novelty-candidate"
    
    # Priority 2: In-domain ambiguous
    if not pd.isna(aleatoric) and aleatoric >= ale_high:
        return "In-domain ambiguous"
    
    # Priority 3: Known/Stable (default)
    return "Known/Stable"


def compute_recommended_next_steps(row: pd.Series) -> List[str]:
    """
    Generate recommended next steps based on router action and missing fields.
    """
    action = row.get('router_action', '')
    missing_fields = row.get('missing_fields', [])
    
    # Ensure missing_fields is a list
    if isinstance(missing_fields, str):
        try:
            missing_fields = json.loads(missing_fields)
        except:
            missing_fields = []
    
    steps = []
    
    if action == "Evidence-insufficient":
        steps.append("check_smiles_validity")
        # Add top 5 missing fields
        for field in missing_fields[:5]:
            steps.append(f"collect_{field}")
        steps.append("verify_inchikey")
    
    elif action == "Novelty-candidate":
        steps.append("manual_review")
        steps.append("request_atb_compute_on_linux")
        # Add missing fields
        for field in missing_fields[:3]:
            steps.append(f"collect_{field}")
    
    elif action == "In-domain ambiguous":
        steps.append("compare_with_neighbors")
        # Add missing fields
        for field in missing_fields[:3]:
            steps.append(f"collect_{field}")
    
    else:  # Known/Stable
        # Only add missing field reminders if any
        for field in missing_fields[:3]:
            steps.append(f"collect_{field}")
    
    return steps


def merge_and_compute_coverage(private_clean: pd.DataFrame, 
                                c_sim_df: pd.DataFrame,
                                c_meta_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge C_sim and C_meta, then compute combined coverage.
    coverage = 0.7 * C_sim + 0.3 * C_meta
    """
    logger.info("Merging C_sim and C_meta, computing combined coverage")
    
    # Start with c_meta (record-level, has id and inchikey)
    merged = c_meta_df.copy()
    
    # Join C_sim on inchikey
    c_sim_cols = c_sim_df[['inchikey', 'C_sim', 'top1_sim', 'similarities']]
    merged = merged.merge(c_sim_cols, on='inchikey', how='left')
    
    # Compute coverage
    merged['coverage'] = 0.7 * merged['C_sim'] + 0.3 * merged['C_meta']
    
    # Log stats
    valid_coverage = merged['coverage'].dropna()
    logger.info(f"  coverage range: [{valid_coverage.min():.4f}, {valid_coverage.max():.4f}]")
    logger.info(f"  Rows with valid coverage: {len(valid_coverage)}/{len(merged)}")
    logger.info(f"  Rows with NaN coverage: {merged['coverage'].isna().sum()}")
    
    return merged


def run_computation(private_clean_path: str = "data/private_clean.parquet",
                   neighbors_path: str = "data/anchor_neighbors_ecfp.parquet",
                   output_path: str = "data/uq_scores_pre_atb.parquet",
                   manifest_path: str = "data/uq_manifest_pre_atb.json") -> pd.DataFrame:
    """
    Run the full P5a UQ computation pipeline.
    """
    logger.info("=" * 60)
    logger.info("P5a Pre-aTB UQ Computation")
    logger.info("=" * 60)
    
    # 1. Load data
    private_clean, neighbors = load_data(private_clean_path, neighbors_path)
    
    # 2. Compute C_sim (molecule-level)
    c_sim_df = compute_c_sim(neighbors)
    
    # 3. Compute C_meta (record-level)
    c_meta_df = compute_c_meta(private_clean)
    
    # 4. Merge and compute coverage
    merged = merge_and_compute_coverage(private_clean, c_sim_df, c_meta_df)
    
    # 5. Compute novelty
    novelty_df, novelty_percentiles = compute_novelty(c_sim_df)
    merged = merged.merge(novelty_df, on='inchikey', how='left')
    
    # 6. Compute aleatoric
    aleatoric_df = compute_aleatoric(c_sim_df)
    merged = merged.merge(aleatoric_df, on='inchikey', how='left')
    
    # 7. Compute thresholds
    thresholds = compute_thresholds(merged)
    
    # 8. Compute router actions
    logger.info("Computing router actions")
    merged['router_action'] = merged.apply(lambda row: compute_router_action(row, thresholds), axis=1)
    
    # Log action distribution
    action_counts = merged['router_action'].value_counts().to_dict()
    logger.info(f"  Router action distribution: {action_counts}")
    
    # 9. Compute recommended next steps
    logger.info("Computing recommended next steps")
    merged['recommended_next_steps'] = merged.apply(compute_recommended_next_steps, axis=1)
    
    # 10. Prepare output DataFrame
    output_cols = [
        'id', 'inchikey', 'C_sim', 'C_meta', 'coverage', 'novelty', 'novelty_raw',
        'aleatoric', 'top1_sim', 'router_action', 'recommended_next_steps',
        'missing_count', 'missing_fields', 'missing_rate'
    ]
    output_df = merged[output_cols].copy()
    
    # Convert lists to JSON strings for parquet compatibility
    output_df['recommended_next_steps'] = output_df['recommended_next_steps'].apply(json.dumps)
    output_df['missing_fields'] = output_df['missing_fields'].apply(json.dumps)
    
    # Add notes column for special cases
    output_df['notes'] = ''
    output_df.loc[output_df['C_sim'].isna(), 'notes'] = 'invalid_or_missing_inchikey'
    
    # 11. Save output
    logger.info(f"Saving UQ scores to {output_path}")
    output_df.to_parquet(output_path, index=False)
    logger.info(f"  Saved {len(output_df)} rows")
    
    # 12. Save manifest
    manifest = {
        'timestamp': datetime.now().isoformat(),
        'input_files': {
            'private_clean': private_clean_path,
            'anchor_neighbors': neighbors_path
        },
        'n_records': len(output_df),
        'n_molecules_in_neighbor_table': len(c_sim_df),
        'k_neighbors': 10,
        'thresholds': thresholds,
        'novelty_percentiles': {
            'p05': float(novelty_percentiles[0]),
            'p95': float(novelty_percentiles[1])
        },
        'action_counts': action_counts,
        'scores_summary': {
            'coverage': {
                'min': float(output_df['coverage'].min()) if output_df['coverage'].notna().any() else None,
                'median': float(output_df['coverage'].median()) if output_df['coverage'].notna().any() else None,
                'max': float(output_df['coverage'].max()) if output_df['coverage'].notna().any() else None
            },
            'novelty': {
                'min': float(output_df['novelty'].min()) if output_df['novelty'].notna().any() else None,
                'median': float(output_df['novelty'].median()) if output_df['novelty'].notna().any() else None,
                'max': float(output_df['novelty'].max()) if output_df['novelty'].notna().any() else None
            },
            'aleatoric': {
                'min': float(output_df['aleatoric'].min()) if output_df['aleatoric'].notna().any() else None,
                'median': float(output_df['aleatoric'].median()) if output_df['aleatoric'].notna().any() else None,
                'max': float(output_df['aleatoric'].max()) if output_df['aleatoric'].notna().any() else None
            }
        }
    }
    
    logger.info(f"Saving manifest to {manifest_path}")
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    logger.info("=" * 60)
    logger.info("P5a computation complete!")
    logger.info("=" * 60)
    
    return output_df


def main():
    parser = argparse.ArgumentParser(description="Compute pre-aTB UQ scores")
    parser.add_argument('--private-clean', type=str, default='data/private_clean.parquet',
                       help='Path to private_clean.parquet')
    parser.add_argument('--neighbors', type=str, default='data/anchor_neighbors_ecfp.parquet',
                       help='Path to anchor_neighbors_ecfp.parquet')
    parser.add_argument('--output', type=str, default='data/uq_scores_pre_atb.parquet',
                       help='Output path for UQ scores')
    parser.add_argument('--manifest', type=str, default='data/uq_manifest_pre_atb.json',
                       help='Output path for manifest')
    
    args = parser.parse_args()
    
    run_computation(
        private_clean_path=args.private_clean,
        neighbors_path=args.neighbors,
        output_path=args.output,
        manifest_path=args.manifest
    )


if __name__ == "__main__":
    main()

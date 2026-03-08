"""
src/uq/validate_uq_pre_atb_p5b.py

Validation script for P5b pre-aTB UQ scores.
Compares P5a vs P5b router actions and validates mechanism_entropy.
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def print_router_action_counts(df: pd.DataFrame, action_col: str, title: str):
    """Print counts of each router action."""
    print(f"\n{'='*70}")
    print(title)
    print("="*70)
    
    action_counts = df[action_col].value_counts()
    total = len(df)
    
    for action, count in action_counts.items():
        pct = 100 * count / total
        print(f"  {action:25s}: {count:5d} ({pct:5.1f}%)")
    
    print(f"\n  {'Total':25s}: {total:5d}")


def print_mechanism_entropy_distribution(df: pd.DataFrame):
    """Print distribution summary for mechanism_entropy."""
    print(f"\n{'='*70}")
    print("MECHANISM_ENTROPY DISTRIBUTION (valid rows only)")
    print("="*70)
    
    valid = df['mechanism_entropy'].dropna()
    if len(valid) == 0:
        print("  No valid values")
        return
    
    print(f"\n  Valid rows: {len(valid)} / {len(df)}")
    print(f"  min={valid.min():.4f}, median={valid.median():.4f}, 95th={valid.quantile(0.95):.4f}, max={valid.max():.4f}")
    
    # Distribution of M_eff
    if 'M_eff' in df.columns:
        meff_valid = df['M_eff'].dropna()
        print(f"\n  M_eff distribution:")
        print(f"    min={meff_valid.min():.0f}, median={meff_valid.median():.1f}, max={meff_valid.max():.0f}")


def print_p5a_vs_p5b_comparison(df: pd.DataFrame):
    """Compare P5a vs P5b router actions."""
    print(f"\n{'='*70}")
    print("P5a vs P5b ROUTER ACTION COMPARISON")
    print("="*70)
    
    if 'router_action' not in df.columns or 'router_action_p5b' not in df.columns:
        print("  Missing required columns")
        return
    
    # Crosstab
    comparison = pd.crosstab(df['router_action'], df['router_action_p5b'], margins=True)
    print("\n  Crosstab (rows=P5a, cols=P5b):")
    print(comparison.to_string())
    
    # Transition counts
    print("\n  Transitions:")
    transitions = df.groupby(['router_action', 'router_action_p5b']).size().reset_index(name='count')
    transitions = transitions.sort_values('count', ascending=False)
    
    for _, row in transitions.head(10).iterrows():
        if row['router_action'] != row['router_action_p5b']:
            print(f"    {row['router_action']} -> {row['router_action_p5b']}: {row['count']}")


def print_entropy_gap_correlation(df: pd.DataFrame, neighbors_path: str = "data/anchor_neighbors_ecfp.parquet"):
    """Correlate mechanism_entropy with neighbor gap."""
    print(f"\n{'='*70}")
    print("MECHANISM_ENTROPY vs NEIGHBOR GAP ANALYSIS")
    print("="*70)
    
    try:
        neighbors = pd.read_parquet(neighbors_path)
        
        # Compute gap for each query
        top1 = neighbors[neighbors['rank'] == 1].set_index('inchikey')['tanimoto_sim']
        top2 = neighbors[neighbors['rank'] == 2].set_index('inchikey')['tanimoto_sim']
        gap = (top1 - top2).dropna()
        gap.name = 'gap'
        
        # Merge with df
        df_with_gap = df.set_index('inchikey').join(gap)
        
        # Valid rows
        valid = df_with_gap[df_with_gap['mechanism_entropy'].notna() & df_with_gap['gap'].notna()]
        
        if len(valid) == 0:
            print("  No valid rows for correlation")
            return
        
        # Split into low/high entropy
        mech_ent_median = valid['mechanism_entropy'].median()
        low_ent = valid[valid['mechanism_entropy'] < mech_ent_median]
        high_ent = valid[valid['mechanism_entropy'] >= mech_ent_median]
        
        print(f"\n  Using mechanism_entropy median threshold: {mech_ent_median:.4f}")
        print(f"\n  Low mechanism_entropy (n={len(low_ent)}):")
        print(f"    gap median: {low_ent['gap'].median():.4f}")
        print(f"\n  High mechanism_entropy (n={len(high_ent)}):")
        print(f"    gap median: {high_ent['gap'].median():.4f}")
        
        # Brief interpretation
        if high_ent['gap'].median() < low_ent['gap'].median():
            print("\n  Interpretation: High entropy molecules have smaller neighbor gaps.")
        else:
            print("\n  Interpretation: Gap patterns don't clearly separate entropy groups.")
            
    except Exception as e:
        print(f"  Failed to load neighbors: {e}")


def print_threshold_info(manifest_path: str):
    """Print threshold information from manifest."""
    print(f"\n{'='*70}")
    print("P5b THRESHOLDS (from manifest)")
    print("="*70)
    
    if not Path(manifest_path).exists():
        print("  Manifest not found")
        return
    
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)
    
    thresholds = manifest.get('thresholds', {})
    print(f"\n  cov_low  (20th pctl, record-level): {thresholds.get('cov_low', 'N/A'):.4f}")
    print(f"  cov_high (80th pctl, record-level): {thresholds.get('cov_high', 'N/A'):.4f}")
    print(f"  nov_high (80th pctl, record-level): {thresholds.get('nov_high', 'N/A'):.4f}")
    
    mech_ent_source = thresholds.get('mech_ent_high_source', 'record_level')
    n_molecules = thresholds.get('n_molecules_for_mech_ent_high', 'N/A')
    print(f"\n  mech_ent_high (80th pctl): {thresholds.get('mech_ent_high', 'N/A'):.4f}")
    print(f"    Source: {mech_ent_source}")
    print(f"    N molecules used: {n_molecules}")


def run_validation(
    uq_p5b_path: str = "data/uq_scores_pre_atb_p5b.parquet",
    manifest_path: str = "data/uq_manifest_pre_atb_p5b.json",
    neighbors_path: str = "data/anchor_neighbors_ecfp.parquet"
):
    """Run the full P5b validation."""
    print(f"\n{'='*70}")
    print("P5b PRE-aTB UQ SCORES VALIDATION")
    print("="*70)
    
    # Check file exists
    if not Path(uq_p5b_path).exists():
        print(f"\n✗ ERROR: P5b UQ scores not found: {uq_p5b_path}")
        print("  Run: python -m src.uq.compute_uq_pre_atb_p5b")
        return False
    
    # Load data
    logger.info(f"Loading P5b UQ scores from {uq_p5b_path}")
    df = pd.read_parquet(uq_p5b_path)
    logger.info(f"  Loaded {len(df)} rows")
    
    # Print P5b router action counts
    print_router_action_counts(df, 'router_action_p5b', 'P5b ROUTER ACTION DISTRIBUTION')
    
    # Print P5a router action counts for comparison
    if 'router_action' in df.columns:
        print_router_action_counts(df, 'router_action', 'P5a ROUTER ACTION DISTRIBUTION (for comparison)')
    
    # Print mechanism_entropy distribution
    print_mechanism_entropy_distribution(df)
    
    # Print P5a vs P5b comparison
    print_p5a_vs_p5b_comparison(df)
    
    # Print entropy-gap correlation
    print_entropy_gap_correlation(df, neighbors_path)
    
    # Print thresholds
    print_threshold_info(manifest_path)
    
    # Final status
    print(f"\n{'='*70}")
    print("✅ P5b VALIDATION COMPLETE")
    print("="*70 + "\n")
    
    return True


def main():
    parser = argparse.ArgumentParser(description="Validate P5b UQ scores")
    parser.add_argument('--uq-p5b', type=str, default='data/uq_scores_pre_atb_p5b.parquet',
                       help='Path to P5b UQ scores')
    parser.add_argument('--manifest', type=str, default='data/uq_manifest_pre_atb_p5b.json',
                       help='Path to P5b manifest')
    parser.add_argument('--neighbors', type=str, default='data/anchor_neighbors_ecfp.parquet',
                       help='Path to neighbors parquet')
    
    args = parser.parse_args()
    run_validation(args.uq_p5b, args.manifest, args.neighbors)


if __name__ == "__main__":
    main()

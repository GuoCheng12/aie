"""
src/uq/validate_uq_pre_atb.py

Validation script for P5a pre-aTB UQ scores.
Prints summary statistics, distribution analysis, and spot-checks.

Usage:
    python -m src.uq.validate_uq_pre_atb
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_uq_scores(path: str) -> pd.DataFrame:
    """Load UQ scores parquet file."""
    logger.info(f"Loading UQ scores from {path}")
    df = pd.read_parquet(path)
    logger.info(f"  Loaded {len(df)} rows")
    return df


def load_manifest(path: str) -> Dict:
    """Load manifest JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


def print_router_action_counts(df: pd.DataFrame):
    """Print counts of each router action."""
    print("\n" + "=" * 70)
    print("ROUTER ACTION DISTRIBUTION")
    print("=" * 70)
    
    action_counts = df['router_action'].value_counts()
    total = len(df)
    
    for action, count in action_counts.items():
        pct = 100 * count / total
        print(f"  {action:25s}: {count:5d} ({pct:5.1f}%)")
    
    print(f"\n  {'Total':25s}: {total:5d}")


def print_score_distributions(df: pd.DataFrame):
    """Print distribution summary for coverage/novelty/aleatoric."""
    print("\n" + "=" * 70)
    print("SCORE DISTRIBUTIONS (valid rows only)")
    print("=" * 70)
    
    scores = ['coverage', 'novelty', 'aleatoric', 'C_sim', 'C_meta']
    
    for score in scores:
        if score not in df.columns:
            continue
            
        valid = df[score].dropna()
        if len(valid) == 0:
            print(f"\n  {score}: No valid values")
            continue
        
        min_val = valid.min()
        median_val = valid.median()
        p95 = valid.quantile(0.95)
        max_val = valid.max()
        
        print(f"\n  {score}:")
        print(f"    Valid rows: {len(valid):5d} / {len(df)}")
        print(f"    min={min_val:.4f}, median={median_val:.4f}, 95th={p95:.4f}, max={max_val:.4f}")


def print_invalid_inchikey_stats(df: pd.DataFrame):
    """Print statistics for invalid/missing inchikey rows."""
    print("\n" + "=" * 70)
    print("INVALID/MISSING INCHIKEY ROWS")
    print("=" * 70)
    
    # Identify invalid rows (NaN C_sim)
    invalid_mask = df['C_sim'].isna()
    invalid_df = df[invalid_mask]
    
    print(f"\n  Rows with invalid/missing inchikey: {len(invalid_df)} / {len(df)}")
    
    if len(invalid_df) > 0:
        # How were they routed?
        invalid_actions = invalid_df['router_action'].value_counts()
        print(f"\n  Router actions for invalid rows:")
        for action, count in invalid_actions.items():
            print(f"    {action}: {count}")
        
        # Sample some
        sample_size = min(5, len(invalid_df))
        sample = invalid_df.sample(sample_size, random_state=42)
        print(f"\n  Sample invalid rows (ids): {sample['id'].tolist()}")


def spot_check_random_records(df: pd.DataFrame, n: int = 5):
    """Spot-check n random records with full details."""
    print("\n" + "=" * 70)
    print(f"SPOT-CHECK: {n} RANDOM RECORDS")
    print("=" * 70)
    
    # Sample from valid rows
    valid_df = df[df['C_sim'].notna()]
    if len(valid_df) < n:
        sample = valid_df
    else:
        sample = valid_df.sample(n, random_state=42)
    
    for idx, row in sample.iterrows():
        print(f"\n  Record ID: {row['id']}")
        print(f"    inchikey:     {row['inchikey'][:27]}...")
        print(f"    top1_sim:     {row['top1_sim']:.4f}")
        print(f"    C_sim:        {row['C_sim']:.4f}")
        print(f"    C_meta:       {row['C_meta']:.4f}")
        print(f"    coverage:     {row['coverage']:.4f}")
        print(f"    novelty:      {row['novelty']:.4f}")
        print(f"    aleatoric:    {row['aleatoric']:.4f}")
        print(f"    action:       {row['router_action']}")
        print(f"    missing_count: {row['missing_count']}")


def print_threshold_info(manifest: Dict):
    """Print threshold information from manifest."""
    print("\n" + "=" * 70)
    print("ROUTER THRESHOLDS (from manifest)")
    print("=" * 70)
    
    thresholds = manifest.get('thresholds', {})
    print(f"\n  cov_low  (20th pctl): {thresholds.get('cov_low', 'N/A'):.4f}")
    print(f"  cov_high (80th pctl): {thresholds.get('cov_high', 'N/A'):.4f}")
    print(f"  nov_high (80th pctl): {thresholds.get('nov_high', 'N/A'):.4f}")
    print(f"  ale_high (80th pctl): {thresholds.get('ale_high', 'N/A'):.4f}")
    
    novelty_pct = manifest.get('novelty_percentiles', {})
    print(f"\n  novelty normalization: p05={novelty_pct.get('p05', 'N/A'):.4f}, p95={novelty_pct.get('p95', 'N/A'):.4f}")


def validate_score_ranges(df: pd.DataFrame) -> bool:
    """Validate that all scores are in [0, 1] range."""
    print("\n" + "=" * 70)
    print("SCORE RANGE VALIDATION")
    print("=" * 70)
    
    scores = ['coverage', 'novelty', 'aleatoric', 'C_sim', 'C_meta']
    all_pass = True
    
    for score in scores:
        if score not in df.columns:
            continue
        
        valid = df[score].dropna()
        if len(valid) == 0:
            continue
        
        min_val = valid.min()
        max_val = valid.max()
        
        in_range = (min_val >= 0) and (max_val <= 1)
        status = "✓ PASS" if in_range else "✗ FAIL"
        
        print(f"  {score}: range [{min_val:.4f}, {max_val:.4f}] {status}")
        
        if not in_range:
            all_pass = False
    
    return all_pass


def print_summary(manifest: Dict):
    """Print overall summary from manifest."""
    print("\n" + "=" * 70)
    print("SUMMARY (from manifest)")
    print("=" * 70)
    
    print(f"\n  Timestamp:              {manifest.get('timestamp', 'N/A')}")
    print(f"  Total records:          {manifest.get('n_records', 'N/A')}")
    print(f"  Molecules in neighbors: {manifest.get('n_molecules_in_neighbor_table', 'N/A')}")
    print(f"  k neighbors:            {manifest.get('k_neighbors', 'N/A')}")
    
    # Score summary
    scores_summary = manifest.get('scores_summary', {})
    for score_name, stats in scores_summary.items():
        if stats:
            print(f"\n  {score_name}:")
            print(f"    min={stats.get('min', 'N/A'):.4f}, median={stats.get('median', 'N/A'):.4f}, max={stats.get('max', 'N/A'):.4f}")


def run_validation(uq_path: str = "data/uq_scores_pre_atb.parquet",
                   manifest_path: str = "data/uq_manifest_pre_atb.json"):
    """Run the full validation."""
    print("\n" + "=" * 70)
    print("P5a PRE-aTB UQ SCORES VALIDATION")
    print("=" * 70)
    
    # Check files exist
    if not Path(uq_path).exists():
        print(f"\n✗ ERROR: UQ scores file not found: {uq_path}")
        print("  Run: python -m src.uq.compute_uq_pre_atb")
        return False
    
    if not Path(manifest_path).exists():
        print(f"\n⚠ WARNING: Manifest file not found: {manifest_path}")
        manifest = {}
    else:
        manifest = load_manifest(manifest_path)
    
    # Load data
    df = load_uq_scores(uq_path)
    
    # Run validations
    print_router_action_counts(df)
    print_score_distributions(df)
    print_invalid_inchikey_stats(df)
    
    if manifest:
        print_threshold_info(manifest)
    
    ranges_ok = validate_score_ranges(df)
    
    spot_check_random_records(df, n=5)
    
    if manifest:
        print_summary(manifest)
    
    # Final status
    print("\n" + "=" * 70)
    if ranges_ok:
        print("✅ ALL VALIDATIONS PASSED")
    else:
        print("⚠️ SOME VALIDATIONS FAILED - Review above output")
    print("=" * 70 + "\n")
    
    return ranges_ok


def main():
    parser = argparse.ArgumentParser(description="Validate pre-aTB UQ scores")
    parser.add_argument('--uq-scores', type=str, default='data/uq_scores_pre_atb.parquet',
                       help='Path to uq_scores_pre_atb.parquet')
    parser.add_argument('--manifest', type=str, default='data/uq_manifest_pre_atb.json',
                       help='Path to uq_manifest_pre_atb.json')
    
    args = parser.parse_args()
    
    run_validation(
        uq_path=args.uq_scores,
        manifest_path=args.manifest
    )


if __name__ == "__main__":
    main()

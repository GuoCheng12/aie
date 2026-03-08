"""
src/uq/compute_uq_pre_atb_p5b.py

Compute P5b UQ scores with mechanism_entropy for router.

Updates router to use mechanism_entropy for "In-domain ambiguous" instead of P5a aleatoric.
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def compute_thresholds_p5b(scores_df: pd.DataFrame, mech_ent_df: pd.DataFrame) -> Dict:
    """
    Compute router thresholds on valid population.
    - cov_low/cov_high/nov_high = computed on valid RECORD-level rows
    - mech_ent_high = computed on MOLECULE-level (unique inchikeys) to avoid duplicate-record bias
    
    Returns dict with thresholds and metadata about mech_ent_high computation.
    """
    logger.info("Computing P5b router thresholds")
    
    # Valid rows for coverage/novelty thresholds (record-level)
    valid_records = scores_df[
        scores_df['coverage'].notna() &
        scores_df['novelty'].notna() &
        scores_df['mechanism_entropy'].notna()
    ]
    logger.info(f"  Valid RECORDS for cov/nov thresholds: {len(valid_records)}")
    
    # Compute record-level thresholds
    cov_low = valid_records['coverage'].quantile(0.20)
    cov_high = valid_records['coverage'].quantile(0.80)
    nov_high = valid_records['novelty'].quantile(0.80)
    
    # mech_ent_high: compute on MOLECULE-level (unique inchikeys)
    # Filter to inchikeys that exist in valid records
    valid_inchikeys = set(valid_records['inchikey'].unique())
    mech_ent_molecules = mech_ent_df[mech_ent_df['inchikey'].isin(valid_inchikeys)]
    n_molecules_for_mech_ent = len(mech_ent_molecules)
    
    mech_ent_high = mech_ent_molecules['mechanism_entropy'].quantile(0.80)
    
    logger.info(f"  Valid MOLECULES for mech_ent_high: {n_molecules_for_mech_ent}")
    logger.info(f"  cov_low={cov_low:.4f}, cov_high={cov_high:.4f}")
    logger.info(f"  nov_high={nov_high:.4f}, mech_ent_high={mech_ent_high:.4f} (molecule-level)")
    
    return {
        'cov_low': cov_low,
        'cov_high': cov_high,
        'nov_high': nov_high,
        'mech_ent_high': mech_ent_high,
        'mech_ent_high_source': 'molecule_level',
        'n_molecules_for_mech_ent_high': n_molecules_for_mech_ent,
        'n_records_for_cov_nov': len(valid_records)
    }


def compute_router_action_p5b(row: pd.Series, thresholds: Dict[str, float]) -> str:
    """
    Compute P5b router action.
    
    Uses mechanism_entropy instead of aleatoric for "In-domain ambiguous".
    """
    c_sim = row.get('C_sim')
    coverage = row.get('coverage')
    novelty = row.get('novelty')
    mechanism_entropy = row.get('mechanism_entropy')
    
    cov_low = thresholds['cov_low']
    cov_high = thresholds['cov_high']
    nov_high = thresholds['nov_high']
    mech_ent_high = thresholds['mech_ent_high']
    
    # Priority 0: Invalid/missing data
    if pd.isna(c_sim) or pd.isna(coverage) or coverage < cov_low:
        return "Evidence-insufficient"
    
    # Priority 1: Novelty-candidate (CONSERVATIVE GATE)
    # Use mechanism_entropy instead of aleatoric for the secondary condition
    if not pd.isna(novelty) and novelty >= nov_high:
        if coverage < cov_high or (not pd.isna(mechanism_entropy) and mechanism_entropy >= mech_ent_high):
            return "Novelty-candidate"
    
    # Priority 2: In-domain ambiguous (using mechanism_entropy)
    if not pd.isna(mechanism_entropy) and mechanism_entropy >= mech_ent_high:
        return "In-domain ambiguous"
    
    # Priority 3: Known/Stable (default)
    return "Known/Stable"


def compute_recommended_next_steps_p5b(row: pd.Series) -> List[str]:
    """Generate recommended next steps based on P5b router action."""
    action = row.get('router_action_p5b', '')
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
        for field in missing_fields[:5]:
            steps.append(f"collect_{field}")
        steps.append("verify_inchikey")
    
    elif action == "Novelty-candidate":
        steps.append("manual_review")
        steps.append("request_atb_compute_on_linux")
        steps.append("compare_with_neighbors")
        for field in missing_fields[:2]:
            steps.append(f"collect_{field}")
    
    elif action == "In-domain ambiguous":
        steps.append("compare_with_neighbors")
        steps.append("check_mechanism_label_consistency")
        for field in missing_fields[:2]:
            steps.append(f"collect_{field}")
    
    else:  # Known/Stable
        for field in missing_fields[:3]:
            steps.append(f"collect_{field}")
    
    return steps


def run_compute_uq_p5b(
    uq_p5a_path: str = "data/uq_scores_pre_atb.parquet",
    mechanism_entropy_path: str = "data/mechanism_entropy_pre_atb.parquet",
    output_path: str = "data/uq_scores_pre_atb_p5b.parquet",
    manifest_path: str = "data/uq_manifest_pre_atb_p5b.json"
) -> pd.DataFrame:
    """Run the P5b UQ computation pipeline."""
    
    logger.info("=" * 60)
    logger.info("P5b Pre-aTB UQ Computation (mechanism_entropy router)")
    logger.info("=" * 60)
    
    # Load P5a UQ scores
    logger.info(f"Loading P5a UQ scores from {uq_p5a_path}")
    uq_p5a = pd.read_parquet(uq_p5a_path)
    logger.info(f"  Loaded {len(uq_p5a)} records")
    
    # Load mechanism_entropy
    logger.info(f"Loading mechanism_entropy from {mechanism_entropy_path}")
    mech_ent = pd.read_parquet(mechanism_entropy_path)
    logger.info(f"  Loaded {len(mech_ent)} molecules")
    
    # Merge mechanism_entropy onto P5a scores
    logger.info("Merging mechanism_entropy with P5a scores")
    merged = uq_p5a.merge(
        mech_ent[['inchikey', 'mechanism_entropy', 'M_eff', 'top_label', 'top_label_prob']],
        on='inchikey',
        how='left'
    )
    logger.info(f"  Merged: {merged['mechanism_entropy'].notna().sum()} rows with valid mechanism_entropy")
    
    # Compute P5b thresholds (mech_ent_high at molecule-level)
    thresholds = compute_thresholds_p5b(merged, mech_ent)
    
    # Compute P5b router actions
    logger.info("Computing P5b router actions")
    merged['router_action_p5b'] = merged.apply(
        lambda row: compute_router_action_p5b(row, thresholds), axis=1
    )
    
    # Log action distribution
    action_counts = merged['router_action_p5b'].value_counts().to_dict()
    logger.info(f"  P5b router action distribution: {action_counts}")
    
    # Compute recommended next steps
    logger.info("Computing recommended next steps (P5b)")
    merged['recommended_next_steps_p5b'] = merged.apply(compute_recommended_next_steps_p5b, axis=1)
    merged['recommended_next_steps_p5b'] = merged['recommended_next_steps_p5b'].apply(json.dumps)
    
    # Compare with P5a actions
    logger.info("Comparing P5a vs P5b router actions:")
    comparison = pd.crosstab(merged['router_action'], merged['router_action_p5b'])
    logger.info(f"\n{comparison}")
    
    # Save output
    logger.info(f"Saving P5b UQ scores to {output_path}")
    merged.to_parquet(output_path, index=False)
    logger.info(f"  Saved {len(merged)} rows")
    
    # Save manifest
    manifest = {
        'timestamp': datetime.now().isoformat(),
        'input_files': {
            'uq_p5a': uq_p5a_path,
            'mechanism_entropy': mechanism_entropy_path
        },
        'n_records': len(merged),
        'thresholds': thresholds,
        'action_counts_p5b': action_counts,
        'action_counts_p5a': merged['router_action'].value_counts().to_dict(),
        'mechanism_entropy_summary': {
            'min': float(merged['mechanism_entropy'].min()) if merged['mechanism_entropy'].notna().any() else None,
            'median': float(merged['mechanism_entropy'].median()) if merged['mechanism_entropy'].notna().any() else None,
            'max': float(merged['mechanism_entropy'].max()) if merged['mechanism_entropy'].notna().any() else None
        }
    }
    
    logger.info(f"Saving manifest to {manifest_path}")
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    logger.info("=" * 60)
    logger.info("P5b computation complete!")
    logger.info("=" * 60)
    
    return merged


def main():
    parser = argparse.ArgumentParser(description="Compute P5b UQ scores")
    parser.add_argument('--uq-p5a', type=str, default='data/uq_scores_pre_atb.parquet',
                       help='Path to P5a UQ scores')
    parser.add_argument('--mechanism-entropy', type=str, default='data/mechanism_entropy_pre_atb.parquet',
                       help='Path to mechanism_entropy parquet')
    parser.add_argument('--output', type=str, default='data/uq_scores_pre_atb_p5b.parquet',
                       help='Output path')
    parser.add_argument('--manifest', type=str, default='data/uq_manifest_pre_atb_p5b.json',
                       help='Manifest output path')
    
    args = parser.parse_args()
    run_compute_uq_p5b(args.uq_p5a, args.mechanism_entropy, args.output, args.manifest)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
P6a: Export queue files by router action.

Creates:
- data/queue_novelty_candidates_pre_atb_p5b.parquet
- data/queue_evidence_insufficient_pre_atb_p5b.parquet
- data/queue_in_domain_ambiguous_pre_atb_p5b.parquet
- data/p6_dashboard_pre_atb_p5b.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, Any

import pandas as pd
import numpy as np


def load_data():
    """Load required data files."""
    private_clean = pd.read_parquet('data/private_clean.parquet')
    uq_scores = pd.read_parquet('data/uq_scores_pre_atb_p5b.parquet')
    atb_qc = pd.read_parquet('data/atb_qc.parquet')

    with open('data/uq_manifest_pre_atb_p5b.json', 'r') as f:
        manifest = json.load(f)

    return private_clean, uq_scores, atb_qc, manifest


def build_atb_qc_map(atb_qc: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Build inchikey -> qc row mapping."""
    if atb_qc.empty:
        return {}
    return atb_qc.set_index('inchikey').to_dict(orient='index')


def compute_has_flags(row: pd.Series) -> Dict[str, bool]:
    """Compute has_* flags for a record."""
    has_emission = not all([
        row.get('emission_solid_missing', True),
        row.get('emission_aggr_missing', True),
    ])
    # Train-only facts do not provide qy/tau/solvent observations.
    has_qy = False
    has_tau = False
    has_solvent = False

    return {
        'has_emission': has_emission,
        'has_qy': has_qy,
        'has_tau': has_tau,
        'has_solvent': has_solvent
    }


def build_queue_dataframe(
    private_clean: pd.DataFrame,
    uq_scores: pd.DataFrame,
    router_action: str
) -> pd.DataFrame:
    """Build a queue dataframe for a specific router action."""

    # Filter by router action
    filtered = uq_scores[uq_scores['router_action_p5b'] == router_action].copy()

    if len(filtered) == 0:
        return pd.DataFrame()

    # Merge with private_clean to get canonical_smiles and other fields
    merged = filtered.merge(
        private_clean[['id', 'canonical_smiles', 'inchikey',
                       'emission_solid_missing', 'emission_aggr_missing']],
        on='id',
        how='left',
        suffixes=('', '_pc')
    )

    # Use inchikey from uq_scores if available, else from private_clean
    if 'inchikey_pc' in merged.columns:
        merged['inchikey'] = merged['inchikey'].fillna(merged['inchikey_pc'])
        merged = merged.drop(columns=['inchikey_pc'])

    # Compute has_* flags
    has_flags = merged.apply(compute_has_flags, axis=1).apply(pd.Series)
    merged = pd.concat([merged, has_flags], axis=1)

    # Select columns for output
    output_cols = [
        'id', 'inchikey', 'canonical_smiles',
        'coverage', 'novelty', 'mechanism_entropy',
        'router_action_p5b',
        'has_emission', 'has_qy', 'has_tau', 'has_solvent',
        'missing_count', 'recommended_next_steps_p5b'
    ]

    available_cols = [c for c in output_cols if c in merged.columns]
    result = merged[available_cols].copy()

    # Sort by novelty (descending) for novelty candidates, else by coverage
    if 'Novelty' in router_action:
        result = result.sort_values('novelty', ascending=False)
    else:
        result = result.sort_values('coverage', ascending=True)

    return result


def generate_dashboard(
    private_clean: pd.DataFrame,
    uq_scores: pd.DataFrame,
    atb_qc_map: Dict[str, Dict[str, Any]],
    manifest: Dict
) -> Dict[str, Any]:
    """Generate P6 dashboard statistics."""

    # Action counts
    action_counts = uq_scores['router_action_p5b'].value_counts().to_dict()

    # Top missing fields (frequency)
    missing_cols = [c for c in private_clean.columns if '_missing' in c]
    missing_counts = {}
    for col in missing_cols:
        count = private_clean[col].sum()
        field_name = col.replace('_missing', '')
        missing_counts[field_name] = int(count)

    top_missing = sorted(missing_counts.items(), key=lambda x: -x[1])[:10]

    # Top novelty IDs
    valid_novelty = uq_scores[uq_scores['novelty'].notna()].nlargest(20, 'novelty')
    top_novelty_ids = [
        {'id': int(row['id']), 'inchikey': row['inchikey'], 'novelty': round(float(row['novelty']), 4)}
        for _, row in valid_novelty.iterrows()
    ]

    # Top mechanism_entropy IDs
    valid_entropy = uq_scores[uq_scores['mechanism_entropy'].notna()].nlargest(20, 'mechanism_entropy')
    top_entropy_ids = [
        {'id': int(row['id']), 'inchikey': row['inchikey'], 'mechanism_entropy': round(float(row['mechanism_entropy']), 4)}
        for _, row in valid_entropy.iterrows()
    ]

    # Invalid inchikey count
    invalid_inchikey_count = int(uq_scores['inchikey'].isna().sum() +
                                   (uq_scores['inchikey'] == '').sum())

    # aTB status distribution (from atb_qc)
    atb_status_counts = Counter()
    for _, row in uq_scores.iterrows():
        ik = row.get('inchikey')
        rec = atb_qc_map.get(ik) if ik else None
        status = rec.get('cache_status') if rec else 'absent'
        atb_status_counts[status] += 1

    return {
        'dashboard_version': 'P6a_pre_atb_p5b',
        'total_records': len(uq_scores),
        'action_counts_p5b': action_counts,
        'top_missing_fields': dict(top_missing),
        'top_novelty_ids': top_novelty_ids,
        'top_mechanism_entropy_ids': top_entropy_ids,
        'invalid_inchikey_count': invalid_inchikey_count,
        'target_atb_status_counts': dict(atb_status_counts),
        'thresholds': manifest.get('thresholds', {})
    }


def main():
    parser = argparse.ArgumentParser(description='Export P6a router action queues')
    parser.add_argument('--output-dir', type=str, default='data',
                        help='Output directory for queue files (default: data)')
    args = parser.parse_args()

    print('Loading data...')
    private_clean, uq_scores, atb_qc, manifest = load_data()
    atb_qc_map = build_atb_qc_map(atb_qc)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Define queue configurations
    queues = [
        ('Novelty-candidate', 'queue_novelty_candidates_pre_atb_p5b.parquet'),
        ('Evidence-insufficient', 'queue_evidence_insufficient_pre_atb_p5b.parquet'),
        ('In-domain ambiguous', 'queue_in_domain_ambiguous_pre_atb_p5b.parquet'),
    ]

    print('\nExporting queues...')
    for action, filename in queues:
        queue_df = build_queue_dataframe(private_clean, uq_scores, action)
        output_path = output_dir / filename
        queue_df.to_parquet(output_path, index=False)
        print(f'  {action}: {len(queue_df)} records -> {output_path}')

    # Generate and save dashboard
    print('\nGenerating dashboard...')
    dashboard = generate_dashboard(private_clean, uq_scores, atb_qc_map, manifest)
    dashboard_path = output_dir / 'p6_dashboard_pre_atb_p5b.json'
    with open(dashboard_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
    print(f'  Dashboard saved to {dashboard_path}')

    # Print summary
    print('\n=== Dashboard Summary ===')
    print(f"Total records: {dashboard['total_records']}")
    print(f"Action counts: {dashboard['action_counts_p5b']}")
    print(f"Invalid InChIKeys: {dashboard['invalid_inchikey_count']}")
    print(f"aTB status distribution: {dashboard['target_atb_status_counts']}")
    print(f"Top 5 missing fields: {list(dashboard['top_missing_fields'].items())[:5]}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
P6a: Generate per-record JSON reports using P5b UQ scores.

Reports include:
- record_summary: id, inchikey, photophysical fields (excluding sensitive fields)
- risk_scores: coverage, novelty, mechanism_entropy, router_action_p5b
- evidence_readiness: target_atb_status, has_* flags, missing_critical_fields
- neighbors_ecfp: top-10 ECFP neighbors with mechanism labels
- recommended_next_steps: ordered by evidence ladder

Privacy: NEVER include 'comment' field in reports.
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Any

import pandas as pd
import numpy as np


# Fields to include in record_summary (photophysical properties)
PHOTOPHYSICAL_FIELDS = [
    'emission_solid',
    'emission_aggr',
]

# Fields that are NEVER allowed in reports (privacy/sensitivity)
BLOCKED_FIELDS = {'comment'}

# Critical missing field columns
CRITICAL_MISSING_COLS = [
    'emission_solid_missing',
    'emission_aggr_missing',
]


def load_data():
    """Load all required data files."""
    private_clean = pd.read_parquet('data/private_clean.parquet')
    uq_scores = pd.read_parquet('data/uq_scores_pre_atb_p5b.parquet')
    neighbors = pd.read_parquet('data/anchor_neighbors_ecfp.parquet')
    mechanism_labels = pd.read_parquet('data/mechanism_label_map.parquet')
    atb_qc = pd.read_parquet('data/atb_qc.parquet')

    # Load manifest for thresholds
    with open('data/uq_manifest_pre_atb_p5b.json', 'r') as f:
        manifest = json.load(f)

    return private_clean, uq_scores, neighbors, mechanism_labels, atb_qc, manifest


def build_atb_qc_map(atb_qc: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Build inchikey -> qc row mapping."""
    if atb_qc.empty:
        return {}
    return atb_qc.set_index('inchikey').to_dict(orient='index')


def build_neighbor_map(neighbors_df: pd.DataFrame) -> Dict[str, List[str]]:
    """Build inchikey -> list of neighbor inchikeys mapping."""
    neighbor_map = {}
    for ik, grp in neighbors_df.groupby('inchikey'):
        neighbor_map[ik] = grp.sort_values('rank')['neighbor_inchikey'].tolist()
    return neighbor_map


def get_target_atb_status_from_qc(inchikey: str, atb_qc_map: Dict[str, Dict[str, Any]]) -> tuple:
    """
    Lookup aTB cache status from atb_qc.parquet.

    Returns:
        (status, missing_fields, keyfield_complete)
    """
    if not inchikey or pd.isna(inchikey):
        return "absent", [], False
    rec = atb_qc_map.get(inchikey)
    if not rec:
        return "absent", [], False

    status = rec.get('cache_status', 'absent')
    missing = rec.get('missing_fields') or []
    if not isinstance(missing, list):
        missing = []
    keyfield_complete = bool(rec.get('keyfield_complete')) if rec.get('keyfield_complete') is not None else False
    return status, missing, keyfield_complete


def compute_neighbor_atb_rates(
    inchikey: str,
    neighbor_map: Dict[str, List[str]],
    atb_qc_map: Dict[str, Dict[str, Any]],
) -> tuple:
    """Compute neighbor atb success/keyfield rates from atb_qc."""
    neighbors = neighbor_map.get(inchikey, [])
    if not neighbors:
        return None, None

    n_success = 0
    n_keyfield = 0
    for nik in neighbors:
        rec = atb_qc_map.get(nik)
        if not rec:
            continue
        if rec.get('cache_status') == 'success':
            n_success += 1
        if rec.get('keyfield_complete') is True:
            n_keyfield += 1

    k = len(neighbors)
    return (n_success / k if k else None), (n_keyfield / k if k else None)


def compute_evidence_readiness(
    row: pd.Series,
    atb_qc_map: Dict[str, Dict[str, Any]],
    neighbor_map: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Compute evidence readiness fields for a record."""
    inchikey = row.get('inchikey')

    # Target aTB status (from atb_qc)
    target_atb_status, target_atb_missing_fields, keyfield_complete = get_target_atb_status_from_qc(
        inchikey, atb_qc_map
    )

    # Neighbor aTB rates (optional)
    neighbor_atb_success_rate, neighbor_atb_keyfield_rate = compute_neighbor_atb_rates(
        inchikey, neighbor_map, atb_qc_map
    )

    # Minimal experiment flags
    has_emission = not all([
        row.get('emission_solid_missing', True),
        row.get('emission_aggr_missing', True),
    ])
    # Train-only facts do not provide qy/tau/solvent observations.
    has_qy = False
    has_tau = False
    has_solvent = False

    # Missing critical fields
    missing_critical_fields = []
    for col in CRITICAL_MISSING_COLS:
        if row.get(col, True):
            field_name = col.replace('_missing', '')
            missing_critical_fields.append(field_name)

    # Evidence ladder action plan
    evidence_ladder_action_plan = []

    # 1) If aTB absent/pending: compute target aTB
    if target_atb_status in ('absent', 'pending'):
        evidence_ladder_action_plan.append('compute_target_atb')
    # 2) If aTB failed: literature search
    elif target_atb_status == 'failed':
        evidence_ladder_action_plan.append('literature_search')
    # 3) If partial: request retry with notes
    elif target_atb_status == 'partial':
        evidence_ladder_action_plan.append('retry_atb_computation')

    # 4) If no emission: request minimal experiment
    if not has_emission:
        evidence_ladder_action_plan.append('request_min_experiment_emission')

    # 5) Add specific missing field actions (train-only: emission_solid/aggr)
    for field in missing_critical_fields:
        action = f'collect_{field}'
        if action not in evidence_ladder_action_plan:
            evidence_ladder_action_plan.append(action)

    return {
        'target_atb_status': target_atb_status,
        'target_atb_missing_fields': target_atb_missing_fields,
        'target_atb_keyfield_complete': keyfield_complete,
        'neighbor_atb_success_rate': round(neighbor_atb_success_rate, 4) if neighbor_atb_success_rate is not None else None,
        'neighbor_atb_keyfield_rate': round(neighbor_atb_keyfield_rate, 4) if neighbor_atb_keyfield_rate is not None else None,
        'minimal_experiment_available': {
            'has_emission': has_emission,
            'has_qy': has_qy,
            'has_tau': has_tau,
            'has_solvent': has_solvent
        },
        'missing_critical_fields': missing_critical_fields,
        'evidence_ladder_action_plan': evidence_ladder_action_plan
    }


def get_neighbors_for_inchikey(
    inchikey: str,
    neighbors_df: pd.DataFrame,
    mechanism_labels_df: pd.DataFrame
) -> List[Dict]:
    """Get top-k ECFP neighbors for a given inchikey."""
    if not inchikey or pd.isna(inchikey):
        return []

    mol_neighbors = neighbors_df[neighbors_df['inchikey'] == inchikey].sort_values('rank')

    # Build mechanism label lookup
    label_dict = dict(zip(mechanism_labels_df['inchikey'], mechanism_labels_df['mechanism_label']))

    result = []
    for _, row in mol_neighbors.iterrows():
        neighbor_ik = row['neighbor_inchikey']
        result.append({
            'rank': int(row['rank']),
            'neighbor_inchikey': neighbor_ik,
            'tanimoto_sim': round(float(row['tanimoto_sim']), 4),
            'mechanism_label': label_dict.get(neighbor_ik, 'unknown')
        })

    return result


def reorder_next_steps(
    original_steps: List[str],
    evidence_readiness: Dict[str, Any]
) -> List[str]:
    """
    Reorder recommended_next_steps by evidence ladder priority.

    Uses evidence_ladder_action_plan from evidence_readiness as the primary source,
    then appends any additional original steps.
    """
    # Get evidence ladder actions (already ordered by priority)
    ladder_actions = evidence_readiness.get('evidence_ladder_action_plan', [])

    # Combine ladder actions with original steps
    seen = set()
    result = []

    # First: evidence ladder actions
    for action in ladder_actions:
        if action not in seen:
            seen.add(action)
            result.append(action)

    # Then: original steps (from UQ router)
    for step in (original_steps or []):
        if step not in seen:
            seen.add(step)
            result.append(step)

    return result


def safe_value(val):
    """Convert numpy types to Python native types for JSON serialization."""
    if pd.isna(val):
        return None
    if isinstance(val, (np.integer, np.int64, np.int32)):
        return int(val)
    if isinstance(val, (np.floating, np.float64, np.float32)):
        return round(float(val), 6)
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val


def generate_report(
    record_id: int,
    private_clean: pd.DataFrame,
    uq_scores: pd.DataFrame,
    neighbors_df: pd.DataFrame,
    mechanism_labels: pd.DataFrame,
    atb_qc_map: Dict[str, Dict[str, Any]],
    neighbor_map: Dict[str, List[str]],
    manifest: Dict
) -> Dict:
    """Generate a single report for a given record ID."""

    # Get record from private_clean
    pc_row = private_clean[private_clean['id'] == record_id]
    if len(pc_row) == 0:
        return {'error': f'Record id={record_id} not found in private_clean'}
    pc_row = pc_row.iloc[0]

    # Get UQ scores
    uq_row = uq_scores[uq_scores['id'] == record_id]
    if len(uq_row) == 0:
        return {'error': f'Record id={record_id} not found in uq_scores'}
    uq_row = uq_row.iloc[0]

    inchikey = pc_row.get('inchikey')

    # A) Record summary
    record_summary = {
        'id': int(record_id),
        'inchikey': safe_value(inchikey),
        'canonical_smiles': safe_value(pc_row.get('canonical_smiles')),
        'code': safe_value(pc_row.get('code')),
        'mechanism_id_hint': safe_value(pc_row.get('mechanism_id')),
    }

    # Add photophysical fields (never include blocked fields)
    photophysical = {}
    for field in PHOTOPHYSICAL_FIELDS:
        if field not in BLOCKED_FIELDS:
            val = pc_row.get(field)
            if not pd.isna(val):
                photophysical[field] = safe_value(val)
    record_summary['photophysical'] = photophysical

    # B) Risk scores
    risk_scores = {
        'coverage': safe_value(uq_row.get('coverage')),
        'C_sim': safe_value(uq_row.get('C_sim')),
        'C_meta': safe_value(uq_row.get('C_meta')),
        'novelty': safe_value(uq_row.get('novelty')),
        'novelty_raw': safe_value(uq_row.get('novelty_raw')),
        'top1_sim': safe_value(uq_row.get('top1_sim')),
        'mechanism_entropy': safe_value(uq_row.get('mechanism_entropy')),
        'M_eff': safe_value(uq_row.get('M_eff')),
        'top_label': safe_value(uq_row.get('top_label')),
        'top_label_prob': safe_value(uq_row.get('top_label_prob')),
        'router_action_p5b': safe_value(uq_row.get('router_action_p5b')),
        'thresholds': manifest.get('thresholds', {})
    }

    # C) Evidence readiness
    evidence_readiness = compute_evidence_readiness(pc_row, atb_qc_map, neighbor_map)

    # D) Neighbors
    neighbors_ecfp = get_neighbors_for_inchikey(inchikey, neighbors_df, mechanism_labels)

    # E) Recommended next steps (reordered by evidence ladder)
    original_steps = uq_row.get('recommended_next_steps_p5b')
    if isinstance(original_steps, str):
        try:
            original_steps = json.loads(original_steps)
        except json.JSONDecodeError:
            original_steps = [original_steps] if original_steps else []
    elif original_steps is None or pd.isna(original_steps):
        original_steps = []

    recommended_next_steps = reorder_next_steps(original_steps, evidence_readiness)

    return {
        'report_version': 'P6a_pre_atb_p5b',
        'record_summary': record_summary,
        'risk_scores': risk_scores,
        'evidence_readiness': evidence_readiness,
        'neighbors_ecfp': neighbors_ecfp,
        'recommended_next_steps': recommended_next_steps
    }


def main():
    parser = argparse.ArgumentParser(description='Generate P6a pre-aTB reports')
    parser.add_argument('--output-dir', type=str, default='reports',
                        help='Output directory for reports (default: reports)')
    parser.add_argument('--id', type=int, default=None,
                        help='Generate report for a single ID only')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limit number of reports to generate')
    args = parser.parse_args()

    print('Loading data...')
    private_clean, uq_scores, neighbors, mechanism_labels, atb_qc, manifest = load_data()
    atb_qc_map = build_atb_qc_map(atb_qc)
    neighbor_map = build_neighbor_map(neighbors)

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get list of IDs to process
    if args.id is not None:
        ids_to_process = [args.id]
    else:
        ids_to_process = sorted(private_clean['id'].unique())
        if args.limit:
            ids_to_process = ids_to_process[:args.limit]

    print(f'Generating {len(ids_to_process)} reports...')

    success_count = 0
    error_count = 0
    atb_status_counts = {'absent': 0, 'pending': 0, 'success': 0, 'failed': 0, 'partial': 0}

    for record_id in ids_to_process:
        report = generate_report(
            record_id, private_clean, uq_scores, neighbors, mechanism_labels, atb_qc_map, neighbor_map, manifest
        )

        if 'error' in report:
            print(f'  ERROR: {report["error"]}')
            error_count += 1
            continue

        # Track aTB status
        atb_status = report['evidence_readiness']['target_atb_status']
        atb_status_counts[atb_status] = atb_status_counts.get(atb_status, 0) + 1

        # Write report
        report_path = output_dir / f'{record_id}.json'
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        success_count += 1

    print(f'\nDone! Generated {success_count} reports, {error_count} errors')
    print(f'Output directory: {output_dir}')
    print(f'aTB status distribution: {atb_status_counts}')


if __name__ == '__main__':
    main()

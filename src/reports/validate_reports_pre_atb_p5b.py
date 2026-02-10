#!/usr/bin/env python3
"""
P6a: Validate generated reports and queues.

Checks:
1. reports generated == 1225
2. no report JSON contains "comment"
3. 20 random reports parse as JSON
4. queue counts match uq_scores_pre_atb_p5b distributions
5. neighbors list length <= 10 and sims in [0,1]
6. evidence_readiness schema validation:
   - target_atb_status exists and is valid
   - target_atb_missing_fields exists (list)
   - minimal_experiment_available exists (object with has_* booleans)
   - missing_critical_fields exists (list)
   - evidence_ladder_action_plan exists (list)
7. dashboard exists with required keys
"""

import argparse
import json
import random
from pathlib import Path
from typing import List, Tuple

import pandas as pd


def check_report_count(reports_dir: Path, data_dir: Path) -> Tuple[bool, str]:
    """Check that report count matches current UQ/private_clean rows."""
    expected = None
    uq_path = data_dir / "uq_scores_pre_atb_p5b.parquet"
    private_path = data_dir / "private_clean.parquet"
    if uq_path.exists():
        expected = len(pd.read_parquet(uq_path))
    elif private_path.exists():
        expected = len(pd.read_parquet(private_path))
    else:
        expected = 0

    report_files = list(reports_dir.glob('*.json'))
    actual = len(report_files)
    passed = actual == expected
    msg = f'Report count: {actual}/{expected}'
    if not passed:
        msg += ' FAIL'
    return passed, msg


def check_no_comment_field(reports_dir: Path, sample_size: int = 50) -> Tuple[bool, str]:
    """Check that no report contains 'comment' field."""
    report_files = list(reports_dir.glob('*.json'))
    if len(report_files) > sample_size:
        report_files = random.sample(report_files, sample_size)

    violations = []
    for report_path in report_files:
        try:
            with open(report_path, 'r') as f:
                content = f.read()
                if '"comment"' in content.lower():
                    violations.append(report_path.name)
        except Exception as e:
            violations.append(f'{report_path.name} (error: {e})')

    passed = len(violations) == 0
    msg = f'No comment field check (sampled {len(report_files)}): '
    if passed:
        msg += 'PASS'
    else:
        msg += f'FAIL - found in {violations}'
    return passed, msg


def check_json_parsing(reports_dir: Path, sample_size: int = 20) -> Tuple[bool, str]:
    """Check that random reports parse as valid JSON."""
    report_files = list(reports_dir.glob('*.json'))
    if len(report_files) > sample_size:
        report_files = random.sample(report_files, sample_size)

    parse_errors = []
    for report_path in report_files:
        try:
            with open(report_path, 'r') as f:
                json.load(f)
        except json.JSONDecodeError as e:
            parse_errors.append(f'{report_path.name}: {e}')

    passed = len(parse_errors) == 0
    msg = f'JSON parsing check (sampled {len(report_files)}): '
    if passed:
        msg += 'PASS'
    else:
        msg += f'FAIL - {parse_errors}'
    return passed, msg


def check_queue_counts(data_dir: Path) -> Tuple[bool, str]:
    """Check that queue counts match UQ scores distribution."""
    try:
        uq_scores = pd.read_parquet(data_dir / 'uq_scores_pre_atb_p5b.parquet')
        expected_counts = uq_scores['router_action_p5b'].value_counts().to_dict()
    except Exception as e:
        return False, f'Could not load uq_scores: {e}'

    queue_files = {
        'Novelty-candidate': 'queue_novelty_candidates_pre_atb_p5b.parquet',
        'Evidence-insufficient': 'queue_evidence_insufficient_pre_atb_p5b.parquet',
        'In-domain ambiguous': 'queue_in_domain_ambiguous_pre_atb_p5b.parquet',
    }

    mismatches = []
    for action, filename in queue_files.items():
        try:
            queue_df = pd.read_parquet(data_dir / filename)
            actual = len(queue_df)
            expected = expected_counts.get(action, 0)
            if actual != expected:
                mismatches.append(f'{action}: {actual} vs {expected}')
        except Exception as e:
            mismatches.append(f'{action}: error - {e}')

    passed = len(mismatches) == 0
    msg = 'Queue counts vs UQ scores: '
    if passed:
        msg += 'PASS'
    else:
        msg += f'FAIL - {mismatches}'
    return passed, msg


def check_neighbors_validity(reports_dir: Path, sample_size: int = 30) -> Tuple[bool, str]:
    """Check that neighbors list has length <= 10 and sims in [0,1]."""
    report_files = list(reports_dir.glob('*.json'))
    if len(report_files) > sample_size:
        report_files = random.sample(report_files, sample_size)

    issues = []
    for report_path in report_files:
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)

            neighbors = report.get('neighbors_ecfp', [])

            # Check length
            if len(neighbors) > 10:
                issues.append(f'{report_path.name}: {len(neighbors)} neighbors > 10')

            # Check similarity range
            for n in neighbors:
                sim = n.get('tanimoto_sim', 0)
                if sim < 0 or sim > 1:
                    issues.append(f'{report_path.name}: sim={sim} out of [0,1]')
                    break

        except Exception as e:
            issues.append(f'{report_path.name}: error - {e}')

    passed = len(issues) == 0
    msg = f'Neighbors validity check (sampled {len(report_files)}): '
    if passed:
        msg += 'PASS'
    else:
        msg += f'FAIL - {issues[:5]}'
    return passed, msg


def check_evidence_readiness(reports_dir: Path, sample_size: int = 50) -> Tuple[bool, str]:
    """Check evidence_readiness schema completeness and validity."""
    report_files = list(reports_dir.glob('*.json'))
    if len(report_files) > sample_size:
        report_files = random.sample(report_files, sample_size)

    # Valid target_atb_status values
    valid_atb_statuses = {'absent', 'pending', 'success', 'failed', 'partial'}

    # Required keys in evidence_readiness
    required_keys = [
        'target_atb_status',
        'target_atb_missing_fields',
        'target_atb_keyfield_complete',
        'minimal_experiment_available',
        'missing_critical_fields',
        'evidence_ladder_action_plan'
    ]

    # Required keys in minimal_experiment_available
    min_exp_keys = ['has_emission', 'has_qy', 'has_tau', 'has_solvent']

    issues = []
    for report_path in report_files:
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)

            er = report.get('evidence_readiness', {})

            # Check required keys exist
            for key in required_keys:
                if key not in er:
                    issues.append(f'{report_path.name}: missing {key}')
                    continue

            # Check target_atb_status is valid
            status = er.get('target_atb_status')
            if status not in valid_atb_statuses:
                issues.append(f'{report_path.name}: invalid status "{status}"')

            # Check target_atb_missing_fields is a list
            missing_fields = er.get('target_atb_missing_fields')
            if not isinstance(missing_fields, list):
                issues.append(f'{report_path.name}: target_atb_missing_fields not a list')

            # Check minimal_experiment_available structure
            min_exp = er.get('minimal_experiment_available', {})
            if not isinstance(min_exp, dict):
                issues.append(f'{report_path.name}: minimal_experiment_available not a dict')
            else:
                for key in min_exp_keys:
                    if key not in min_exp:
                        issues.append(f'{report_path.name}: minimal_experiment_available missing {key}')
                    elif not isinstance(min_exp[key], bool):
                        issues.append(f'{report_path.name}: {key} not a boolean')

            # Check missing_critical_fields is a list
            mcf = er.get('missing_critical_fields')
            if not isinstance(mcf, list):
                issues.append(f'{report_path.name}: missing_critical_fields not a list')

            # Check evidence_ladder_action_plan is a list
            elap = er.get('evidence_ladder_action_plan')
            if not isinstance(elap, list):
                issues.append(f'{report_path.name}: evidence_ladder_action_plan not a list')

        except Exception as e:
            issues.append(f'{report_path.name}: error - {e}')

    passed = len(issues) == 0
    msg = f'evidence_readiness schema check (sampled {len(report_files)}): '
    if passed:
        msg += 'PASS'
    else:
        msg += f'FAIL - {len(issues)} issues: {issues[:3]}...'
    return passed, msg


def check_dashboard_exists(data_dir: Path) -> Tuple[bool, str]:
    """Check that dashboard JSON exists and is valid."""
    dashboard_path = data_dir / 'p6_dashboard_pre_atb_p5b.json'

    if not dashboard_path.exists():
        return False, f'Dashboard not found: {dashboard_path}'

    try:
        with open(dashboard_path, 'r') as f:
            dashboard = json.load(f)

        required_keys = ['total_records', 'action_counts_p5b', 'top_missing_fields',
                         'top_novelty_ids', 'target_atb_status_counts']
        missing_keys = [k for k in required_keys if k not in dashboard]

        if missing_keys:
            return False, f'Dashboard missing keys: {missing_keys}'

        return True, f'Dashboard valid with {dashboard["total_records"]} records'

    except json.JSONDecodeError as e:
        return False, f'Dashboard parse error: {e}'


def main():
    parser = argparse.ArgumentParser(description='Validate P6a reports and queues')
    parser.add_argument('--reports-dir', type=str, default='reports',
                        help='Reports directory (default: reports)')
    parser.add_argument('--data-dir', type=str, default='data',
                        help='Data directory (default: data)')
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    data_dir = Path(args.data_dir)

    print('=' * 60)
    print('P6a REPORT VALIDATION')
    print('=' * 60)
    print()

    checks = [
        ('Report count', lambda: check_report_count(reports_dir, data_dir)),
        ('No comment field', lambda: check_no_comment_field(reports_dir)),
        ('JSON parsing', lambda: check_json_parsing(reports_dir)),
        ('Queue counts', lambda: check_queue_counts(data_dir)),
        ('Neighbors validity', lambda: check_neighbors_validity(reports_dir)),
        ('Evidence readiness', lambda: check_evidence_readiness(reports_dir)),
        ('Dashboard exists', lambda: check_dashboard_exists(data_dir)),
    ]

    results = []
    for name, check_fn in checks:
        passed, msg = check_fn()
        status = 'PASS' if passed else 'FAIL'
        print(f'[{status}] {name}: {msg}')
        results.append(passed)

    print()
    print('=' * 60)
    passed_count = sum(results)
    total_count = len(results)
    print(f'Checks passed: {passed_count}/{total_count}')

    if passed_count == total_count:
        print('ALL CHECKS PASSED')
    else:
        print('SOME CHECKS FAILED')

    print('=' * 60)

    return 0 if passed_count == total_count else 1


if __name__ == '__main__':
    exit(main())

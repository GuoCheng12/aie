"""
src/cases/chem_agent_update_case_stub.py

Update a Case File (Chem Agent stub - no real computation).

This is a V0 stub that updates case file status without performing
actual aTB computation, literature search, or experiment requests.
It demonstrates the state machine transitions.

CLI:
    python -m src.cases.chem_agent_update_case_stub --case cases/<case_id>.json --action "<action>"
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Any, Optional

from src.cases.case_schema import (
    AtbCacheStatus,
    AtbRequestStatus,
    LiteratureStatus,
    ExperimentStatus,
    Actor,
    EventType,
    now_iso,
    create_history_event,
    evaluate_gate,
    validate_case_file,
)


# =============================================================================
# Action Handlers (Stubs)
# =============================================================================

def _mark_action_done(case: Dict[str, Any], action_name: str) -> None:
    """
    Mark an action as completed in action_plan.

    Supports both:
    - legacy: list[str] (remove the action)
    - v0.7+: list[object] (set status="done")
    """
    ap = case.get("action_plan")
    if not isinstance(ap, list):
        return
    if all(isinstance(x, str) for x in ap):
        if action_name in ap:
            ap.remove(action_name)
        return
    if all(isinstance(x, dict) for x in ap):
        for x in ap:
            if x.get("action") == action_name:
                x["status"] = "done"


def handle_compute_target_atb(case: Dict[str, Any]) -> str:
    """
    Handle compute_target_atb action (stub).

    In V0 stub: just mark request_status as requested.
    Real implementation would trigger aTB computation.
    """
    atb = case['evidence_readiness']['atb']
    old_request_status = atb.get('request_status', AtbRequestStatus.NOT_REQUESTED.value)

    # Only update if not already requested/done
    if old_request_status == AtbRequestStatus.NOT_REQUESTED.value:
        atb['request_status'] = AtbRequestStatus.REQUESTED.value
        atb['last_update'] = now_iso()
        return f"atb request_status: {old_request_status} -> {AtbRequestStatus.REQUESTED.value}"
    else:
        return f"atb request_status unchanged: {old_request_status}"


def handle_literature_search(case: Dict[str, Any]) -> str:
    """
    Handle literature_search action (stub).

    In V0 stub: just mark as pending. Real implementation would trigger search.
    """
    lit = case['evidence_readiness']['literature']
    old_status = lit['status']

    if old_status == LiteratureStatus.NOT_STARTED.value:
        lit['status'] = LiteratureStatus.PENDING.value
        lit['last_update'] = now_iso()
        return f"literature status: {old_status} -> {LiteratureStatus.PENDING.value}"
    else:
        return f"literature status unchanged: {old_status}"


def handle_request_min_experiment(case: Dict[str, Any], field: str) -> str:
    """
    Handle request_min_experiment_* action (stub).

    Args:
        field: "emission" (train-only baseline)
    """
    exp = case['evidence_readiness']['experiment']
    old_status = exp['status']

    # Train-only facts support emission requests only.
    if field != "emission":
        return f"experiment request skipped for unsupported train-only field: {field}"

    requested = ['emission_solid', 'emission_aggr']

    # Add to requested fields
    for f in requested:
        if f not in exp['requested_fields']:
            exp['requested_fields'].append(f)

    if old_status == ExperimentStatus.NOT_REQUESTED.value:
        exp['status'] = ExperimentStatus.REQUESTED.value

    exp['last_update'] = now_iso()
    return f"experiment status: {old_status} -> {exp['status']}, requested: {requested}"


# =============================================================================
# Simulate Completion (for testing)
# =============================================================================

def simulate_atb_success(case: Dict[str, Any]) -> str:
    """Simulate aTB computation completing successfully (mark_atb_success)."""
    atb = case['evidence_readiness']['atb']
    old_cache = atb.get('cache_status', 'unknown')
    old_request = atb.get('request_status', 'unknown')

    # Update both cache_status and request_status
    atb['cache_status'] = AtbCacheStatus.SUCCESS.value
    atb['request_status'] = AtbRequestStatus.DONE.value
    atb['missing_fields'] = []
    atb['last_update'] = now_iso()

    # Mark compute_target_atb completed in action_plan (legacy remove / new status=done)
    _mark_action_done(case, "compute_target_atb")
    _mark_action_done(case, "retry_target_atb_alt_settings")

    return f"[SIMULATED] cache_status: {old_cache} -> {AtbCacheStatus.SUCCESS.value}, request_status: {old_request} -> {AtbRequestStatus.DONE.value}"


def simulate_atb_failed(case: Dict[str, Any], stage: str = "opt") -> str:
    """Simulate aTB computation failing (mark_atb_failed)."""
    atb = case['evidence_readiness']['atb']
    old_cache = atb.get('cache_status', 'unknown')
    old_request = atb.get('request_status', 'unknown')

    # Update both cache_status and request_status
    atb['cache_status'] = AtbCacheStatus.FAILED.value
    atb['request_status'] = AtbRequestStatus.DONE.value
    atb['error_stage'] = stage
    atb['error_msg'] = f"Simulated failure at {stage} stage"
    atb['last_update'] = now_iso()

    return f"[SIMULATED] cache_status: {old_cache} -> {AtbCacheStatus.FAILED.value}, request_status: {old_request} -> {AtbRequestStatus.DONE.value} (stage={stage})"


def simulate_has_emission(case: Dict[str, Any]) -> str:
    """Simulate receiving emission data."""
    min_exp = case['evidence_readiness']['minimal_experiment_available']
    old_val = min_exp['has_emission']
    min_exp['has_emission'] = True

    exp = case['evidence_readiness']['experiment']
    exp['received_fields'].append('emission_solid')
    if exp['status'] == ExperimentStatus.REQUESTED.value:
        exp['status'] = ExperimentStatus.RECEIVED_PARTIAL.value
    exp['last_update'] = now_iso()

    return f"[SIMULATED] has_emission: {old_val} -> True"


# =============================================================================
# Main Update Logic
# =============================================================================

def update_case(case: Dict[str, Any], action: str) -> str:
    """
    Update case based on action.

    Args:
        case: Case dict (will be modified in-place)
        action: Action string

    Returns:
        Description of changes made
    """
    result = ""

    # Handle different actions
    if action == "compute_target_atb":
        result = handle_compute_target_atb(case)
        _mark_action_done(case, "compute_target_atb")
    elif action in {"literature_search", "literature_search_web"}:
        result = handle_literature_search(case)
        _mark_action_done(case, "literature_search_web")
    elif action.startswith("request_min_experiment_"):
        field = action.replace("request_min_experiment_", "")
        result = handle_request_min_experiment(case, field)
        _mark_action_done(case, action)
    # Mark actions (for real workflow completion)
    elif action == "mark_atb_success":
        result = simulate_atb_success(case)
    elif action == "mark_atb_failed":
        result = simulate_atb_failed(case)
    # Simulation actions (legacy aliases for testing)
    elif action == "simulate_atb_success":
        result = simulate_atb_success(case)
    elif action == "simulate_atb_failed":
        result = simulate_atb_failed(case)
    elif action == "simulate_has_emission":
        result = simulate_has_emission(case)
    else:
        result = f"Unknown action: {action}"

    # Append history event
    case['history'].append(create_history_event(
        Actor.CHEM_AGENT.value,
        EventType.ACTION_MARKED.value,
        {'action': action, 'result': result}
    ))

    # Re-evaluate gate
    ready, reason = evaluate_gate(case['evidence_readiness'])
    case['evidence_readiness']['current_gate']['ready_for_reasoning'] = ready
    case['evidence_readiness']['current_gate']['reason'] = reason

    # Append gate evaluation event
    case['history'].append(create_history_event(
        Actor.SYSTEM.value,
        EventType.GATE_EVALUATED.value,
        {'ready_for_reasoning': ready, 'reason': reason}
    ))

    return result


def update_case_file(case_path: str, action: str) -> Dict[str, Any]:
    """
    Load, update, and save case file.

    Args:
        case_path: Path to case JSON file
        action: Action to perform

    Returns:
        Updated case dict
    """
    # Load case
    with open(case_path, 'r') as f:
        case = json.load(f)

    # Update
    result = update_case(case, action)
    print(f"Action result: {result}")

    # Validate
    is_valid, errors = validate_case_file(case)
    if not is_valid:
        raise ValueError(f"Updated case is invalid: {errors}")

    # Save
    with open(case_path, 'w') as f:
        json.dump(case, f, indent=2)

    return case


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Update Case File (Chem Agent stub)'
    )
    parser.add_argument('--case', type=str, required=True,
                        help='Path to case JSON file')
    parser.add_argument('--action', type=str, required=True,
                        help='Action to perform (compute_target_atb, literature_search, '
                             'request_min_experiment_emission, simulate_atb_success, etc.)')
    parser.add_argument('--print', action='store_true', dest='print_output',
                        help='Print updated case to stdout')

    args = parser.parse_args()

    if not Path(args.case).exists():
        print(f"Error: Case file not found: {args.case}")
        return 1

    print(f"Updating case: {args.case}")
    print(f"Action: {args.action}")

    case = update_case_file(args.case, args.action)

    print(f"\nUpdated state:")
    atb = case['evidence_readiness']['atb']
    # Support both new and legacy schema for display
    cache_status = atb.get('cache_status') or atb.get('status', 'unknown')
    request_status = atb.get('request_status', 'N/A')
    print(f"  atb.cache_status: {cache_status}")
    print(f"  atb.request_status: {request_status}")
    print(f"  literature.status: {case['evidence_readiness']['literature']['status']}")
    print(f"  experiment.status: {case['evidence_readiness']['experiment']['status']}")
    print(f"  ready_for_reasoning: {case['evidence_readiness']['current_gate']['ready_for_reasoning']}")
    print(f"  reason: {case['evidence_readiness']['current_gate']['reason']}")
    print(f"  action_plan: {case.get('action_plan', [])}")
    print(f"  history events: {len(case['history'])}")

    if args.print_output:
        print("\nFull case JSON:")
        print(json.dumps(case, indent=2))

    return 0


if __name__ == '__main__':
    exit(main())

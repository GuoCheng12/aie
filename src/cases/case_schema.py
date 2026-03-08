"""
src/cases/case_schema.py

Constants, enums, and schema validation helpers for Case File (SMILES-first workflow).
See doc/schemas.md §11 for full schema documentation.
"""

from enum import Enum
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Schema version
CASE_VERSION = "0.7"

# Key aTB fields required for reasoning gate
KEY_ATB_FIELDS = ['delta_volume', 'delta_gap', 'delta_dihedral', 'excitation_energy']

# Action plan (LLM-friendly) enums/constants
ACTION_STATUSES = {"not_started", "pending", "done", "skipped"}
REASONING_MODES = {"blocked", "normal", "conservative"}

# Minimal allowlist for action_plan.action (v0.7+). Keep small and explicit.
ALLOWED_ACTIONS = {
    "run_master_reasoner",
    "run_master_reasoner_stub",
    "compute_target_atb",
    "retry_target_atb_alt_settings",
    "retry_target_atb",
    "literature_search_web",
    "mineru_extract_pdf",
    "rerun_offline_pdf_extractor",
    "manual_extract",
    "manual_identity_verify_from_pdf",
    "request_manual_pdf",
    "request_min_experiment_emission",
    "request_experiment_qy",
    "request_experiment_tau",
    "request_experiment_solvent_details",
    "expand_structure_neighbors",
}

# =============================================================================
# Status Enums
# =============================================================================

class AtbCacheStatus(str, Enum):
    """aTB cache status (historical fact from cache lookup)."""
    ABSENT = "absent"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class AtbRequestStatus(str, Enum):
    """aTB request status (workflow state for this case)."""
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    DONE = "done"


# Legacy alias for backward compatibility
AtbStatus = AtbCacheStatus


class LiteratureStatus(str, Enum):
    """Literature search status."""
    NOT_STARTED = "not_started"
    PENDING = "pending"
    FOUND = "found"
    NOT_FOUND = "not_found"


class ExperimentStatus(str, Enum):
    """Experiment request status."""
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    RECEIVED_PARTIAL = "received_partial"
    RECEIVED_FULL = "received_full"


class Actor(str, Enum):
    """Event actors."""
    DATA_AGENT = "data_agent"
    CHEM_AGENT = "chem_agent"
    SYSTEM = "system"
    USER = "user"


class EventType(str, Enum):
    """History event types."""
    CASE_CREATED = "case_created"
    ACTION_MARKED = "action_marked"
    ATB_UPDATED = "atb_updated"
    LITERATURE_UPDATED = "literature_updated"
    EXPERIMENT_UPDATED = "experiment_updated"
    GATE_EVALUATED = "gate_evaluated"
    MANUAL_EDIT = "manual_edit"


# =============================================================================
# Evidence Ladder Actions
# =============================================================================

EVIDENCE_LADDER_ACTIONS = [
    "compute_target_atb",
    "literature_search",
    "request_min_experiment_emission",
    "run_master_reasoner",
]


# =============================================================================
# Schema Validation
# =============================================================================

def validate_case_file(case: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate case file against schema.

    Args:
        case: Case file dict

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Required top-level keys (V0.7 includes candidate_mechanisms and mechanism_signatures)
    required_top_level = [
        'case_id', 'case_version', 'query', 'risk_scores',
        'evidence_readiness', 'neighbors', 'action_plan', 'history',
        'candidate_mechanisms', 'mechanism_signatures'
    ]
    for key in required_top_level:
        if key not in case:
            errors.append(f"Missing required top-level key: {key}")

    if errors:
        return False, errors

    # Validate query
    query_errors = _validate_query(case.get('query', {}))
    errors.extend(query_errors)

    # Validate risk_scores
    risk_errors = _validate_risk_scores(case.get('risk_scores', {}))
    errors.extend(risk_errors)

    # Validate evidence_readiness
    readiness_errors = _validate_evidence_readiness(case.get('evidence_readiness', {}))
    errors.extend(readiness_errors)

    # Validate neighbors
    if not isinstance(case.get('neighbors'), list):
        errors.append("neighbors must be a list")

    # Validate action_plan
    action_plan = case.get('action_plan')
    if not isinstance(action_plan, list):
        errors.append("action_plan must be a list")
    else:
        # Accept both formats:
        # - legacy: list[str]
        # - v0.7+: list[object] with required keys
        if len(action_plan) > 0:
            if all(isinstance(a, str) for a in action_plan):
                pass  # legacy allowed
            elif all(isinstance(a, dict) for a in action_plan):
                errors.extend(_validate_action_plan_objects(action_plan))
            else:
                errors.append("action_plan must be either list[str] (legacy) or list[object] (v0.7+)")

    # Validate history
    if not isinstance(case.get('history'), list):
        errors.append("history must be a list")
    else:
        for i, event in enumerate(case['history']):
            event_errors = _validate_history_event(event, i)
            errors.extend(event_errors)

    # Optional: action_rationale
    if "action_rationale" in case:
        ar = case.get("action_rationale")
        if not isinstance(ar, list) or not all(isinstance(x, str) for x in ar):
            errors.append("action_rationale must be list[string] when present")

    return len(errors) == 0, errors


def _validate_query(query: Dict[str, Any]) -> List[str]:
    """Validate query section."""
    errors = []
    required = ['input_smiles', 'canonical_smiles', 'inchikey', 'created_at']
    for key in required:
        if key not in query:
            errors.append(f"query missing required key: {key}")
    return errors


def _validate_risk_scores(risk_scores: Dict[str, Any]) -> List[str]:
    """Validate risk_scores section."""
    errors = []
    required = [
        'top1_sim', 'mean_topk_sim', 'neighbor_gap', 'novelty_struct',
        'mechanism_entropy', 'mechanism_hint', 'hint_confidence'
    ]
    for key in required:
        if key not in risk_scores:
            errors.append(f"risk_scores missing required key: {key}")
    return errors


def _validate_evidence_readiness(er: Dict[str, Any]) -> List[str]:
    """Validate evidence_readiness section."""
    errors = []

    # Check atb section
    if 'atb' not in er:
        errors.append("evidence_readiness missing 'atb'")
    else:
        atb = er['atb']

        # Support both new schema (cache_status/request_status) and legacy (status)
        has_new_schema = 'cache_status' in atb and 'request_status' in atb
        has_legacy_schema = 'status' in atb and 'cache_status' not in atb

        if has_new_schema:
            # New schema validation
            atb_required = ['cache_status', 'request_status', 'missing_fields', 'last_update']
            for key in atb_required:
                if key not in atb:
                    errors.append(f"evidence_readiness.atb missing: {key}")

            # Validate cache_status enum
            if 'cache_status' in atb:
                valid_statuses = [s.value for s in AtbCacheStatus]
                if atb['cache_status'] not in valid_statuses:
                    errors.append(f"Invalid atb.cache_status: {atb['cache_status']}. Valid: {valid_statuses}")

            # Validate request_status enum
            if 'request_status' in atb:
                valid_statuses = [s.value for s in AtbRequestStatus]
                if atb['request_status'] not in valid_statuses:
                    errors.append(f"Invalid atb.request_status: {atb['request_status']}. Valid: {valid_statuses}")

            # REJECT neighbor metrics under atb (must be at evidence_readiness top-level)
            if 'neighbor_atb_success_rate' in atb:
                errors.append("neighbor_atb_success_rate must be at evidence_readiness top-level, not under atb")
            if 'neighbor_atb_keyfield_rate' in atb:
                errors.append("neighbor_atb_keyfield_rate must be at evidence_readiness top-level, not under atb")
            
            # Validate excitation_energy raw consistency (if _excitation_energy_raw exists)
            features_summary = atb.get('features_summary', {})
            if features_summary:
                raw = features_summary.get('_excitation_energy_raw')
                val = features_summary.get('excitation_energy')
                if raw is not None and val is not None:
                    try:
                        raw_float = float(raw)
                        if abs(raw_float - val) > 1e-9:
                            errors.append(f"excitation_energy mismatch: raw={raw_float}, val={val}")
                    except (ValueError, TypeError):
                        pass  # raw may not be numeric

        elif has_legacy_schema:
            # Legacy schema validation (backward compatibility)
            atb_required = ['status', 'missing_fields', 'last_update']
            for key in atb_required:
                if key not in atb:
                    errors.append(f"evidence_readiness.atb missing: {key}")

            # Validate status enum
            if 'status' in atb:
                valid_statuses = [s.value for s in AtbCacheStatus]
                if atb['status'] not in valid_statuses:
                    errors.append(f"Invalid atb.status: {atb['status']}. Valid: {valid_statuses}")
        else:
            errors.append("evidence_readiness.atb must have either 'cache_status'+'request_status' or legacy 'status'")

    # Check literature section
    if 'literature' not in er:
        errors.append("evidence_readiness missing 'literature'")
    else:
        lit = er['literature']
        lit_required = ['status', 'sources', 'last_update']
        for key in lit_required:
            if key not in lit:
                errors.append(f"evidence_readiness.literature missing: {key}")

        if 'status' in lit:
            valid_statuses = [s.value for s in LiteratureStatus]
            if lit['status'] not in valid_statuses:
                errors.append(f"Invalid literature.status: {lit['status']}. Valid: {valid_statuses}")

    # Check experiment section
    if 'experiment' not in er:
        errors.append("evidence_readiness missing 'experiment'")
    else:
        exp = er['experiment']
        exp_required = ['status', 'requested_fields', 'received_fields', 'last_update']
        for key in exp_required:
            if key not in exp:
                errors.append(f"evidence_readiness.experiment missing: {key}")

        if 'status' in exp:
            valid_statuses = [s.value for s in ExperimentStatus]
            if exp['status'] not in valid_statuses:
                errors.append(f"Invalid experiment.status: {exp['status']}. Valid: {valid_statuses}")

    # Check minimal_experiment_available
    if 'minimal_experiment_available' not in er:
        errors.append("evidence_readiness missing 'minimal_experiment_available'")
    else:
        mea = er['minimal_experiment_available']
        mea_required = ['has_emission', 'has_qy', 'has_tau', 'has_solvent']
        for key in mea_required:
            if key not in mea:
                errors.append(f"minimal_experiment_available missing: {key}")
            elif not isinstance(mea.get(key), bool):
                errors.append(f"minimal_experiment_available.{key} must be boolean")

    # Check current_gate
    if 'current_gate' not in er:
        errors.append("evidence_readiness missing 'current_gate'")
    else:
        gate = er['current_gate']
        if 'ready_for_reasoning' not in gate:
            errors.append("current_gate missing 'ready_for_reasoning'")
        elif not isinstance(gate['ready_for_reasoning'], bool):
            errors.append("current_gate.ready_for_reasoning must be boolean")
        if 'reason' not in gate:
            errors.append("current_gate missing 'reason'")
        # Optional: reasoning_mode (new)
        mode = gate.get("reasoning_mode")
        if mode is not None and mode not in REASONING_MODES:
            errors.append(f"Invalid current_gate.reasoning_mode: {mode}. Valid: {sorted(REASONING_MODES)}")

    return errors


def _validate_action_plan_objects(action_plan: List[Dict[str, Any]]) -> List[str]:
    """Validate structured action_plan objects (v0.7+)."""
    errors: List[str] = []

    required_keys = {"action", "priority", "status", "inputs", "expected_outputs", "blocking", "notes"}
    priorities: List[int] = []

    for i, a in enumerate(action_plan):
        missing = required_keys - set(a.keys())
        if missing:
            errors.append(f"action_plan[{i}] missing keys: {sorted(missing)}")
            continue

        act = a.get("action")
        if act not in ALLOWED_ACTIONS:
            errors.append(f"action_plan[{i}].action invalid: {act}. Allowed: {sorted(ALLOWED_ACTIONS)}")

        pr = a.get("priority")
        if not isinstance(pr, int) or pr < 1:
            errors.append(f"action_plan[{i}].priority must be int >= 1")
        else:
            priorities.append(pr)

        st = a.get("status")
        if st not in ACTION_STATUSES:
            errors.append(f"action_plan[{i}].status invalid: {st}. Allowed: {sorted(ACTION_STATUSES)}")

        if not isinstance(a.get("inputs"), dict):
            errors.append(f"action_plan[{i}].inputs must be object")

        if not isinstance(a.get("expected_outputs"), list) or not all(isinstance(x, str) for x in a.get("expected_outputs", [])):
            errors.append(f"action_plan[{i}].expected_outputs must be list[string]")

        if not isinstance(a.get("blocking"), bool):
            errors.append(f"action_plan[{i}].blocking must be boolean")

        if not isinstance(a.get("notes"), str):
            errors.append(f"action_plan[{i}].notes must be string")

    # Priorities strictly increasing starting from 1 is recommended.
    if priorities:
        if priorities != sorted(priorities):
            errors.append("action_plan priorities must be non-decreasing")
        if len(set(priorities)) != len(priorities):
            errors.append("action_plan priorities must be unique")
        if priorities[0] != 1:
            errors.append("action_plan should start with priority=1")

    return errors


def _validate_history_event(event: Dict[str, Any], index: int) -> List[str]:
    """Validate a single history event."""
    errors = []
    required = ['timestamp', 'actor', 'event_type']
    for key in required:
        if key not in event:
            errors.append(f"history[{index}] missing: {key}")

    if 'actor' in event:
        valid_actors = [a.value for a in Actor]
        if event['actor'] not in valid_actors:
            errors.append(f"history[{index}] invalid actor: {event['actor']}")

    if 'event_type' in event:
        valid_types = [e.value for e in EventType]
        if event['event_type'] not in valid_types:
            errors.append(f"history[{index}] invalid event_type: {event['event_type']}")

    return errors


# =============================================================================
# Gate Evaluation
# =============================================================================

def evaluate_gate(evidence_readiness: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Evaluate whether case is ready for reasoning.

    Args:
        evidence_readiness: The evidence_readiness section of a case file

    Returns:
        (ready_for_reasoning, reason)
    """
    atb = evidence_readiness.get('atb', {})
    min_exp = evidence_readiness.get('minimal_experiment_available', {})

    # Get cache status (support both new and legacy schema)
    cache_status = atb.get('cache_status') or atb.get('status')

    # Gate logic (V0.7):
    # Ready if (aTB cache shows success AND all key fields present) OR has emission data
    if cache_status == AtbCacheStatus.SUCCESS.value:
        # Check if all key fields are present in features_summary
        features_summary = atb.get('features_summary', {})
        missing_keys = [k for k in KEY_ATB_FIELDS if features_summary.get(k) is None]
        if len(missing_keys) == 0:
            return True, "atb_success"
        # If key fields missing, don't open gate (partial success)
    
    if min_exp.get('has_emission', False):
        return True, "has_emission_data"

    return False, "missing_target_atb_and_min_experiment"


# =============================================================================
# Timestamp Helper
# =============================================================================

def now_iso() -> str:
    """Get current timestamp in ISO 8601 format."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# Case File Factory Helpers
# =============================================================================

def create_empty_evidence_readiness(timestamp: str) -> Dict[str, Any]:
    """Create initial evidence_readiness with all placeholders."""
    return {
        'atb': {
            'cache_status': AtbCacheStatus.ABSENT.value,
            'request_status': AtbRequestStatus.NOT_REQUESTED.value,
            'missing_fields': [],
            'last_update': timestamp,
            'error_stage': None,
            'error_msg': None
        },
        'literature': {
            'status': LiteratureStatus.NOT_STARTED.value,
            'sources': [],
            'last_update': timestamp,
            'notes': None
        },
        'experiment': {
            'status': ExperimentStatus.NOT_REQUESTED.value,
            'requested_fields': [],
            'received_fields': [],
            'last_update': timestamp,
            'notes': None
        },
        'minimal_experiment_available': {
            'has_emission': False,
            'has_qy': False,
            'has_tau': False,
            'has_solvent': False
        },
        'current_gate': {
            'ready_for_reasoning': False,
            'reason': "missing_target_atb_and_min_experiment",
            # Optional field; present in new case files.
            'reasoning_mode': "blocked",
        }
    }


def create_history_event(
    actor: str,
    event_type: str,
    details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create a history event."""
    event = {
        'timestamp': now_iso(),
        'actor': actor,
        'event_type': event_type
    }
    if details:
        event['details'] = details
    return event

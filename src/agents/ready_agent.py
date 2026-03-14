"""
src/agents/ready_agent.py

READY_AGENT:
- Reads full case JSON
- Writes only gate/rationale/plan fields via RFC6902 patch
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult


GATE_BLOCKED = "blocked_input_missing"
GATE_NEEDS_MANUAL = "needs_manual"
GATE_READY = "ready_for_reasoning"
GATE_READY_CONSERVATIVE = "ready_conservative"

ALLOWED_PATCH_PATH_PREFIXES = (
    "/current_gate/",
    "/action_rationale",
    "/action_plan",
    "/risk_scores/readiness_",
)

AGGR_KEYWORDS = (
    "aggregate",
    "aggregation",
    "aggregated",
    "aggr",
    "nanoaggregate",
    "cluster",
    "water fraction",
    "poor solvent",
    "aie",
)

IDENTITY_ALLOWED = {
    "exact",
    "series_inferred",
    "uncertain",
    "not_found",
    # Backward compatibility for pre-ready-agent provenance values.
    "matched",
    "ambiguous",
    "unmatched",
}


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if v != v:  # NaN
        return None
    return v


def _set_or_replace_op(doc: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    cur = doc
    parts = [p for p in path.split("/") if p]
    exists = True
    for token in parts[:-1]:
        if isinstance(cur, dict) and token in cur:
            cur = cur[token]
        else:
            exists = False
            break
    if exists and isinstance(cur, dict) and parts[-1] in cur:
        return {"op": "replace", "path": path, "value": value}
    return {"op": "add", "path": path, "value": value}


def _apply_patch(doc: Dict[str, Any], patch_ops: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(doc)
    for op in patch_ops:
        if op.get("op") not in {"add", "replace"}:
            raise ValueError(f"Unsupported op: {op.get('op')}")
        path = str(op["path"])
        parts = [p for p in path.split("/") if p]
        parent: Any = out
        for token in parts[:-1]:
            if not isinstance(parent, dict):
                raise ValueError(f"Non-dict path parent: {path}")
            if token not in parent:
                parent[token] = {}
            parent = parent[token]
        if not isinstance(parent, dict):
            raise ValueError(f"Non-dict path leaf parent: {path}")
        parent[parts[-1]] = op.get("value")
    return out


def _validate_patch_paths(patch_ops: Sequence[Dict[str, Any]]) -> None:
    for op in patch_ops:
        path = str(op.get("path", ""))
        if not any(path == p or path.startswith(p) for p in ALLOWED_PATCH_PATH_PREFIXES):
            raise ValueError(f"READY_AGENT attempted forbidden patch path: {path}")


def _get_emission_value(case_json: Dict[str, Any], key: str) -> Optional[float]:
    return _to_float((case_json.get("target_fields") or {}).get(key))


def _get_provenance(case_json: Dict[str, Any], key: str) -> Dict[str, Any]:
    prov = (case_json.get("target_fields_provenance") or {}).get(key)
    return prov if isinstance(prov, dict) else {}


def _has_required_provenance(prov: Dict[str, Any]) -> bool:
    source_ref = str(prov.get("source_ref") or "").strip()
    source_locator = str(prov.get("source_locator") or "").strip()
    conf = _to_float(prov.get("confidence"))
    if source_ref == "" or source_locator == "" or conf is None:
        return False
    return True


def _normalize_identity_match(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().lower()
    return s if s in IDENTITY_ALLOWED else None


def _extract_candidate_for_field(case_json: Dict[str, Any], field: str, prov: Dict[str, Any]) -> Dict[str, Any]:
    staging = case_json.get("evidence_candidates_staging")
    if not isinstance(staging, list):
        return {}
    candidate_id = str(prov.get("candidate_id") or "").strip()
    for item in staging:
        if not isinstance(item, dict):
            continue
        if str(item.get("field")) != field:
            continue
        if candidate_id and str(item.get("candidate_id") or "") == candidate_id:
            return item
    # Fallback: first field-matching candidate.
    for item in staging:
        if isinstance(item, dict) and str(item.get("field")) == field:
            return item
    return {}


def _has_aggregation_signal(case_json: Dict[str, Any], prov: Dict[str, Any]) -> bool:
    texts: List[str] = []
    for k in ("condition", "condition_bucket", "source_locator", "notes"):
        v = prov.get(k)
        if v is not None:
            texts.append(str(v))
    cand = _extract_candidate_for_field(case_json, "emission_aggr_nm", prov)
    for k in ("condition", "condition_bucket", "source_locator"):
        v = cand.get(k)
        if v is not None:
            texts.append(str(v))
    merged = " ".join(texts).lower()
    return any(k in merged for k in AGGR_KEYWORDS)


def _action_obj(
    *,
    action: str,
    priority: int,
    blocking: bool,
    notes: str,
    status: str = "pending",
) -> Dict[str, Any]:
    return {
        "action": action,
        "priority": int(priority),
        "status": status,
        "inputs": {},
        "expected_outputs": [],
        "blocking": bool(blocking),
        "notes": notes,
    }


def _normalize_plan_item(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    out.setdefault("status", "pending")
    out.setdefault("inputs", {})
    out.setdefault("expected_outputs", [])
    out.setdefault("blocking", False)
    out.setdefault("notes", "")
    return out


def _choose_master_action(existing_actions: Sequence[str]) -> str:
    if "run_master_reasoner_stub" in existing_actions:
        return "run_master_reasoner_stub"
    if "run_master_reasoner" in existing_actions:
        return "run_master_reasoner"
    return "run_master_reasoner"


def _rebuild_action_plan(
    case_json: Dict[str, Any],
    gate_state: str,
    *,
    add_extraction_manual: bool,
    add_extraction_manual_non_blocking: bool,
    add_request_manual_pdf_non_blocking: bool,
    add_identity_manual_blocking: bool,
    add_identity_manual_non_blocking: bool,
    add_retry_atb: bool,
    add_outlier_followup: bool,
) -> List[Dict[str, Any]]:
    existing = case_json.get("action_plan")
    existing_list = existing if isinstance(existing, list) else []

    by_action: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in existing_list:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()
        if action == "":
            continue
        if action not in by_action:
            by_action[action] = _normalize_plan_item(raw)
            order.append(action)

    existing_actions = list(by_action.keys())
    required: List[Dict[str, Any]] = []

    def _add_required_once(item: Dict[str, Any]) -> None:
        if any(x.get("action") == item.get("action") for x in required):
            return
        required.append(item)

    if gate_state in {GATE_READY, GATE_READY_CONSERVATIVE}:
        _add_required_once(
            _action_obj(
                action=_choose_master_action(existing_actions),
                priority=1,
                blocking=False,
                notes="READY_AGENT promoted reasoning step to priority=1.",
            )
        )

    if gate_state == GATE_BLOCKED:
        _add_required_once(
            _action_obj(
                action="request_manual_pdf",
                priority=1,
                blocking=True,
                notes="Missing required emission evidence inputs.",
            )
        )

    if add_extraction_manual or gate_state == GATE_NEEDS_MANUAL:
        _add_required_once(
            _action_obj(
                action="rerun_offline_pdf_extractor",
                priority=2,
                blocking=True,
                notes="Re-extract emission evidence from PDF before reasoning.",
            )
        )

    if add_request_manual_pdf_non_blocking:
        _add_required_once(
            _action_obj(
                action="request_manual_pdf",
                priority=2,
                blocking=False,
                notes="Emission follow-up is missing PDF inputs; request PDF without blocking current reasoning lane.",
            )
        )

    if add_extraction_manual_non_blocking:
        _add_required_once(
            _action_obj(
                action="rerun_offline_pdf_extractor",
                priority=2,
                blocking=False,
                notes="Run extraction follow-up without blocking current reasoning lane.",
            )
        )

    if add_identity_manual_blocking:
        _add_required_once(
            _action_obj(
                action="manual_identity_verify_from_pdf",
                priority=2,
                blocking=True,
                notes="Identity metadata missing/invalid for emission provenance.",
            )
        )
    elif add_identity_manual_non_blocking:
        _add_required_once(
            _action_obj(
                action="manual_identity_verify_from_pdf",
                priority=3,
                blocking=False,
                notes="Low identity confidence; verify manually while reasoning conservatively.",
            )
        )

    if add_retry_atb:
        _add_required_once(
            _action_obj(
                action="retry_target_atb",
                priority=3,
                blocking=False,
                notes="aTB failed/partial; retry queued while preserving reasoning flow.",
            )
        )

    if add_outlier_followup:
        _add_required_once(
            _action_obj(
                action="literature_search_web",
                priority=3,
                blocking=False,
                notes="aTB neighborhood signal is outlier; collect corroborating literature evidence.",
            )
        )
        _add_required_once(
            _action_obj(
                action="request_min_experiment_emission",
                priority=4,
                blocking=False,
                notes="aTB neighborhood signal is outlier; request minimal emission verification.",
            )
        )

    # Keep still-relevant existing actions after required actions.
    keep_tail: List[Dict[str, Any]] = []
    required_names = {x["action"] for x in required}
    for action in order:
        if action in required_names:
            continue
        keep_tail.append(by_action[action])

    merged = required + keep_tail
    for i, item in enumerate(merged, start=1):
        item["priority"] = i
    return merged


def review_case_and_patch(case_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    READY_AGENT decision function.

    Returns RFC6902 patch ops and only writes:
    - /current_gate/*
    - /action_rationale
    - /action_plan
    - optional /risk_scores/readiness_*
    """
    aggr = _get_emission_value(case_json, "emission_aggr_nm")
    solid = _get_emission_value(case_json, "emission_solid_or_film_nm")

    has_aggr = aggr is not None
    has_solid = solid is not None

    reasons: List[str] = []
    add_extraction_manual = False
    add_extraction_manual_non_blocking = False
    add_request_manual_pdf_non_blocking = False
    add_identity_manual_blocking = False
    add_identity_manual_non_blocking = False
    add_retry_atb = False
    add_outlier_followup = False
    low_identity_conf = False

    atb_status = str(((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("cache_status") or "").lower()
    atb_neighbor = ((case_json.get("risk_scores") or {}).get("atb_neighbor_consistency") or {})
    atb_neighbor_flag = str(atb_neighbor.get("flag") or "").strip().lower()
    atb_neighbor_reliability = str(atb_neighbor.get("reliability") or "").strip().lower()
    run_lane = str(((case_json.get("runtime") or {}).get("run_lane") or "")).strip().lower()
    structure_prior_profile = ((case_json.get("risk_scores") or {}).get("structure_prior_profile") or {})
    has_structure_prior = isinstance(structure_prior_profile, dict) and bool(structure_prior_profile)

    if not has_aggr and not has_solid:
        has_pdf = bool((case_json.get("inputs") or {}).get("offline_pdfs"))
        if atb_status == "success":
            gate_state = GATE_READY_CONSERVATIVE
            reasons.append("atb_success_without_emission")
            add_retry_atb = False
            if not has_pdf:
                add_request_manual_pdf_non_blocking = True
                reasons.append("missing_pdf_for_emission_followup")
            else:
                add_extraction_manual_non_blocking = True
                reasons.append("emission_followup_required")
        elif run_lane == "atb_cache_only" and has_structure_prior:
            gate_state = GATE_READY_CONSERVATIVE
            reasons.append("structure_prior_without_target_atb")
            add_retry_atb = True
            if not has_pdf:
                add_request_manual_pdf_non_blocking = True
                reasons.append("missing_pdf_for_external_followup")
            else:
                add_extraction_manual_non_blocking = True
                reasons.append("emission_followup_required")
        else:
            gate_state = GATE_BLOCKED if not has_pdf else GATE_NEEDS_MANUAL
            if gate_state == GATE_BLOCKED:
                reasons.append("missing_emission_and_missing_pdf_input")
            else:
                reasons.append("missing_emission_after_extraction")
                add_extraction_manual = True
    else:
        gate_state = GATE_READY
        for field in ("emission_aggr_nm", "emission_solid_or_film_nm"):
            val = _get_emission_value(case_json, field)
            if val is None:
                continue
            prov = _get_provenance(case_json, field)

            if not _has_required_provenance(prov):
                gate_state = GATE_NEEDS_MANUAL
                reasons.append(f"{field}:missing_required_provenance")
                add_extraction_manual = True
                continue

            identity_match = _normalize_identity_match(prov.get("identity_match"))
            identity_conf = _to_float(prov.get("identity_match_confidence"))
            if identity_conf is None:
                identity_conf = _to_float(prov.get("identity_confidence"))
            if identity_match is None or identity_conf is None:
                gate_state = GATE_NEEDS_MANUAL
                reasons.append(f"{field}:missing_identity_match_metadata")
                add_identity_manual_blocking = True
                continue
            if identity_match in {"not_found", "unmatched"}:
                gate_state = GATE_NEEDS_MANUAL
                reasons.append(f"{field}:identity_not_found")
                add_identity_manual_blocking = True
                continue
            if identity_conf < 0.7:
                low_identity_conf = True
                reasons.append(f"{field}:low_identity_confidence")
                add_identity_manual_non_blocking = True

            if field == "emission_aggr_nm" and not _has_aggregation_signal(case_json, prov):
                gate_state = GATE_NEEDS_MANUAL
                reasons.append("emission_aggr_nm:anti_leakage_failed_no_aggregation_signal")
                add_extraction_manual = True

        if gate_state == GATE_READY and low_identity_conf:
            gate_state = GATE_READY_CONSERVATIVE

    if gate_state in {GATE_READY, GATE_READY_CONSERVATIVE} and atb_status in {"failed", "absent", "partial", "pending"}:
        gate_state = GATE_READY_CONSERVATIVE
        add_retry_atb = True
        reasons.append(f"atb_status:{atb_status}")

    if atb_neighbor_flag == "outlier":
        reasons.append(f"atb_neighbor_outlier:{atb_neighbor_reliability or 'unknown'}")
        if atb_neighbor_reliability in {"medium", "high"} and gate_state in {GATE_READY, GATE_READY_CONSERVATIVE}:
            gate_state = GATE_READY_CONSERVATIVE
            add_outlier_followup = True
    elif atb_neighbor_flag in {"target_missing", "insufficient_neighbors", "inlier"}:
        reasons.append(f"atb_neighbor_flag:{atb_neighbor_flag}")

    ready_for_reasoning = gate_state in {GATE_READY, GATE_READY_CONSERVATIVE}
    reasoning_mode = "normal" if gate_state == GATE_READY else ("conservative" if gate_state == GATE_READY_CONSERVATIVE else "blocked")
    reason_text = "; ".join(reasons) if reasons else "ready"

    action_rationale = (
        f"READY_AGENT gate={gate_state}; ready_for_reasoning={str(ready_for_reasoning).lower()}; "
        f"reasoning_mode={reasoning_mode}; reasons={reason_text}."
    )

    action_plan = _rebuild_action_plan(
        case_json,
        gate_state,
        add_extraction_manual=add_extraction_manual,
        add_extraction_manual_non_blocking=add_extraction_manual_non_blocking,
        add_request_manual_pdf_non_blocking=add_request_manual_pdf_non_blocking,
        add_identity_manual_blocking=add_identity_manual_blocking,
        add_identity_manual_non_blocking=add_identity_manual_non_blocking,
        add_retry_atb=add_retry_atb,
        add_outlier_followup=add_outlier_followup,
    )

    patch_ops: List[Dict[str, Any]] = []
    patch_ops.append(_set_or_replace_op(case_json, "/current_gate/state", gate_state))
    patch_ops.append(_set_or_replace_op(case_json, "/current_gate/ready_for_reasoning", ready_for_reasoning))
    patch_ops.append(_set_or_replace_op(case_json, "/current_gate/reason", reason_text))
    patch_ops.append(_set_or_replace_op(case_json, "/current_gate/reasoning_mode", reasoning_mode))
    patch_ops.append(_set_or_replace_op(case_json, "/action_rationale", action_rationale))
    patch_ops.append(_set_or_replace_op(case_json, "/action_plan", action_plan))
    patch_ops.append(_set_or_replace_op(case_json, "/risk_scores/readiness_identity_low_confidence", bool(low_identity_conf)))
    patch_ops.append(_set_or_replace_op(case_json, "/risk_scores/readiness_gate_state", gate_state))
    if atb_neighbor_flag:
        patch_ops.append(_set_or_replace_op(case_json, "/risk_scores/readiness_atb_neighbor_flag", atb_neighbor_flag))

    _validate_patch_paths(patch_ops)
    return patch_ops


def apply_ready_agent_patch(case_json: Dict[str, Any], patch_ops: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    _validate_patch_paths(patch_ops)
    return _apply_patch(case_json, patch_ops)


class ReadyAgent(CaseAgent):
    """
    Multi-agent Ready Agent wrapper over rule-based READY_AGENT logic.
    """

    name = "ready_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/current_gate/",
        "/action_rationale",
        "/action_plan",
        "/risk_scores/readiness_",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        query = case.get("query") or {}
        return {
            "case_id": case.get("case_id"),
            "inchikey": query.get("inchikey"),
            "atb_cache_status": ((case.get("evidence_readiness") or {}).get("atb") or {}).get("cache_status"),
            "target_fields": case.get("target_fields") or {},
            "target_fields_provenance": case.get("target_fields_provenance") or {},
            "staging_count": len(case.get("evidence_candidates_staging") or []),
            "action_plan": case.get("action_plan") or [],
            "post_uq": case.get("post_uq") or {},
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        patch = review_case_and_patch(case)
        return AgentResult(
            patch=patch,
            status="success",
            warnings=[],
            raw_outputs={"ready_review": {"inputs": inputs, "patch_count": len(patch)}},
        )

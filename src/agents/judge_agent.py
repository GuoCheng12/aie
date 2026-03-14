"""
Judge Agent: post-reasoning critique and next-action suggestions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.reasoning.master_reasoner import (
    AOP_COMPACT_EVIDENCE_IDS,
    ATB_ENRICHMENT_EVIDENCE_IDS,
    ATB_TREND_PROFILE_EVIDENCE_IDS,
    TARGET_OBSERVATION_EVIDENCE_IDS,
    TARGET_SIDE_PRIMARY_AXES,
)
from src.reasoning.reasoning_config import build_allowed_mechanism_labels
from src.tools.llm_client import LLMClientError, ResponsesLLMClient
from src.tools.llm_trace_store import write_agent_response_trace


def _judge_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string"},
            "confidence": {"type": "number"},
            "contradictions": {"type": "array", "items": {"type": "string"}},
            "missing_evidence": {"type": "array", "items": {"type": "string"}},
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["status", "confidence", "contradictions", "missing_evidence", "recommended_actions"],
    }


def _to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _next_round_profile(current_profile: str, *, literature_enabled: bool) -> str:
    cur = str(current_profile or "R0").upper()
    if cur == "R0":
        return "R1"
    if cur == "R1":
        return "R2"
    if cur == "R2":
        return "R3" if literature_enabled else "R2"
    if cur == "R3":
        return "NONE"
    return "R1"


FINAL_DECISION_STATES = {
    "closed_known",
    "provisional_known",
    "residual_supported",
    "insufficient_evidence",
}
STANDARD_LABEL_BLOCKLIST = {"other", "unknown"}
TARGET_SIDE_EVIDENCE_IDS = {
    *TARGET_OBSERVATION_EVIDENCE_IDS,
    *ATB_TREND_PROFILE_EVIDENCE_IDS,
    *ATB_ENRICHMENT_EVIDENCE_IDS,
    *AOP_COMPACT_EVIDENCE_IDS,
}


def _dedupe_strings(values: Sequence[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in out:
            out.append(token)
    return out


def _scorecard_row_lookup(candidate_scorecard: Sequence[Dict[str, Any]] | None) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in candidate_scorecard or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if label:
            lookup[label] = row
    return lookup


def _standard_candidate_labels(master_reasoning: Dict[str, Any], candidate_scorecard: Sequence[Dict[str, Any]] | None) -> List[str]:
    labels: List[str] = []
    claim = master_reasoning.get("mechanism_claim") if isinstance(master_reasoning.get("mechanism_claim"), dict) else {}
    primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
    primary_label = str(primary.get("mechanism_label") or "").strip()
    if primary_label and primary_label not in STANDARD_LABEL_BLOCKLIST:
        labels.append(primary_label)
    for row in master_reasoning.get("competing_hypotheses") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("name") or "").strip()
        if label and label not in STANDARD_LABEL_BLOCKLIST and label not in labels:
            labels.append(label)
    for row in candidate_scorecard or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if label and label not in STANDARD_LABEL_BLOCKLIST and label not in labels:
            labels.append(label)
    return labels


def evaluate_standard_candidate_closure(
    *,
    row: Optional[Dict[str, Any]],
    min_positive_axes: int,
    requires_target_axis: bool,
) -> Dict[str, Any]:
    support_axes = _dedupe_strings(((row or {}).get("support_axes") or []))
    primary_axes = [axis for axis in support_axes if axis != "comparative_transferability"]
    target_axes = [axis for axis in primary_axes if axis in TARGET_SIDE_PRIMARY_AXES]
    if len(primary_axes) >= max(1, int(min_positive_axes)) and (not requires_target_axis or bool(target_axes)):
        status = "closed"
    elif primary_axes:
        status = "provisional"
    else:
        status = "unsupported"
    return {
        "status": status,
        "primary_axes": primary_axes,
        "target_side_axes": target_axes,
    }


def evaluate_residual_other_admissibility(
    *,
    active_profile: str,
    used_evidence_ids: Sequence[str],
    candidate_scorecard: Sequence[Dict[str, Any]] | None,
    standard_candidate_closures: Dict[str, Dict[str, Any]],
    active_conflict_count: int,
    min_standard_candidates: int,
    min_conflicts: int,
) -> Dict[str, Any]:
    reasons: List[str] = []
    qualifying_signals: List[str] = []
    profile = str(active_profile or "").upper()
    if profile not in {"R2", "R3"}:
        reasons.append("pre_residual_round")
    used_ids = {str(x) for x in used_evidence_ids if str(x)}
    has_target_side_support = bool(used_ids.intersection(TARGET_SIDE_EVIDENCE_IDS))
    if not has_target_side_support:
        reasons.append("missing_target_side_evidence")
    standard_labels = list(standard_candidate_closures.keys())
    if len(standard_labels) < max(1, int(min_standard_candidates)):
        reasons.append("insufficient_standard_candidates")
    if any(str((closure or {}).get("status") or "") == "closed" for closure in standard_candidate_closures.values()):
        reasons.append("standard_label_closed")
    if active_conflict_count >= max(1, int(min_conflicts)):
        qualifying_signals.append("conflict_threshold_met")
    weakened_or_unresolved = 0
    for label in standard_labels:
        row = (_scorecard_row_lookup(candidate_scorecard)).get(label) or {}
        weakening_axes = _dedupe_strings(row.get("weakening_axes") or [])
        unresolved_axes = _dedupe_strings(row.get("unresolved_axes") or [])
        if weakening_axes or unresolved_axes:
            weakened_or_unresolved += 1
    if weakened_or_unresolved >= 2:
        qualifying_signals.append("standard_candidates_unresolved")
    if any((closure or {}).get("primary_axes") for closure in standard_candidate_closures.values()):
        qualifying_signals.append("primary_axis_present")
    admissible = not reasons and bool(qualifying_signals)
    if not admissible and not reasons:
        reasons.append("insufficient_residual_signals")
    return {
        "admissible": bool(admissible),
        "reasons": reasons,
        "qualifying_signals": qualifying_signals,
        "has_target_side_support": has_target_side_support,
    }


def evaluate_novelty_candidate(
    *,
    risk_scores: Dict[str, Any],
    residual_other_admissible: bool,
    active_conflict_count: int,
    entropy_threshold: float,
    struct_threshold: float,
) -> Dict[str, Any]:
    novelty_struct = _to_float((risk_scores or {}).get("novelty_struct"))
    mechanism_entropy = _to_float((risk_scores or {}).get("mechanism_entropy"))
    basis: List[str] = []
    if novelty_struct is not None and novelty_struct >= float(struct_threshold):
        basis.append("novelty_struct_high")
    if mechanism_entropy is not None and mechanism_entropy >= float(entropy_threshold):
        basis.append("mechanism_entropy_high")
    if residual_other_admissible and active_conflict_count >= 2:
        basis.append("late_round_residual_conflict")
    return {
        "is_novelty_candidate": bool(basis),
        "basis": basis,
    }


def build_final_adjudication_context(
    *,
    case_json: Dict[str, Any],
    master_reasoning: Dict[str, Any],
    active_profile: str,
    candidate_scorecard: Sequence[Dict[str, Any]] | None,
    normalization_summary: Dict[str, Any] | None,
    eval_report: Dict[str, Any] | None,
    reasoning_config: Dict[str, Any] | None,
    used_evidence_ids: Sequence[str],
) -> Dict[str, Any]:
    policy = (reasoning_config or {}).get("policy") if isinstance(reasoning_config, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    cfg_allowed_labels = reasoning_config.get("allowed_mechanism_labels") if isinstance(reasoning_config, dict) else None
    allow_other_label = "other" in build_allowed_mechanism_labels(
        cfg_allowed_labels,
        include_other=policy.get("allow_other_label"),
    )
    scorecard_lookup = _scorecard_row_lookup(candidate_scorecard)
    standard_labels = _standard_candidate_labels(master_reasoning, candidate_scorecard)
    closures: Dict[str, Dict[str, Any]] = {}
    for label in standard_labels:
        closures[label] = evaluate_standard_candidate_closure(
            row=scorecard_lookup.get(label),
            min_positive_axes=int(policy.get("standard_label_min_positive_axes") or 2),
            requires_target_axis=bool(policy.get("standard_label_requires_target_axis", True)),
        )
    contradictions = []
    if isinstance(eval_report, dict):
        contradictions = [
            row for row in (eval_report.get("conflict_adjudication") or [])
            if isinstance(row, dict) and str(row.get("status") or "").lower() != "resolved"
        ]
    active_conflict_count = len(contradictions)
    if allow_other_label:
        residual_eval = evaluate_residual_other_admissibility(
            active_profile=active_profile,
            used_evidence_ids=used_evidence_ids,
            candidate_scorecard=candidate_scorecard,
            standard_candidate_closures=closures,
            active_conflict_count=active_conflict_count,
            min_standard_candidates=int(policy.get("residual_other_min_standard_candidates") or 2),
            min_conflicts=int(policy.get("residual_other_min_conflicts") or 2),
        )
    else:
        residual_eval = {
            "admissible": False,
            "reasons": ["other_disabled_in_active_label_pool"],
            "qualifying_signals": [],
            "has_target_side_support": False,
        }
    novelty_eval = evaluate_novelty_candidate(
        risk_scores=case_json.get("risk_scores") or {},
        residual_other_admissible=bool(residual_eval.get("admissible")),
        active_conflict_count=active_conflict_count,
        entropy_threshold=float(policy.get("novelty_candidate_entropy_threshold") or 0.75),
        struct_threshold=float(policy.get("novelty_candidate_struct_threshold") or 0.60),
    )
    claim = master_reasoning.get("mechanism_claim") if isinstance(master_reasoning.get("mechanism_claim"), dict) else {}
    primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
    llm_primary_label = (
        str(((master_reasoning.get("__meta") or {}).get("llm_primary_label") or "")).strip()
        or str(primary.get("mechanism_label") or "").strip()
        or "unknown"
    )
    canonical_pool_closed = any(str((closure or {}).get("status") or "") == "closed" for closure in closures.values())
    legal_candidates: List[str] = []
    closed_labels = [label for label, closure in closures.items() if str((closure or {}).get("status") or "") == "closed"]
    provisional_labels = [label for label, closure in closures.items() if str((closure or {}).get("status") or "") == "provisional"]
    if closed_labels:
        legal_candidates.extend(closed_labels)
    else:
        legal_candidates.extend(provisional_labels)
        if allow_other_label and str(active_profile or "").upper() in {"R2", "R3"} and bool(residual_eval.get("admissible")):
            legal_candidates.append("other")
        legal_candidates.append("unknown")
    legal_candidates = _dedupe_strings(legal_candidates)
    if not legal_candidates:
        legal_candidates = ["unknown"]
    return {
        "active_profile": str(active_profile or "").upper(),
        "llm_primary_label": llm_primary_label,
        "candidate_labels": _dedupe_strings(
            [llm_primary_label, *standard_labels, *(['other'] if allow_other_label else []), "unknown"]
        ),
        "allow_other_label": bool(allow_other_label),
        "legal_candidates": legal_candidates,
        "standard_candidate_closures": closures,
        "canonical_pool_closed": bool(canonical_pool_closed),
        "residual_other_admissible": bool(residual_eval.get("admissible")),
        "residual_other_reasons": list(residual_eval.get("reasons") or []),
        "residual_other_qualifying_signals": list(residual_eval.get("qualifying_signals") or []),
        "novelty_candidate": bool(novelty_eval.get("is_novelty_candidate")),
        "novelty_basis": list(novelty_eval.get("basis") or []),
        "active_conflict_count": active_conflict_count,
        "master_confidence": _to_float((claim or {}).get("confidence")) or 0.0,
        "top_standard_label": closed_labels[0] if closed_labels else (provisional_labels[0] if provisional_labels else None),
    }


def build_final_label_adjudication(
    *,
    context: Dict[str, Any],
    llm_choice: Optional[Dict[str, Any]] = None,
    provisional_confidence_cap: float = 0.32,
) -> Dict[str, Any]:
    active_profile = str(context.get("active_profile") or "R0").upper()
    llm_primary_label = str(context.get("llm_primary_label") or "unknown")
    legal_candidates = _dedupe_strings(context.get("legal_candidates") or [])
    allow_other_label = bool(context.get("allow_other_label", "other" in legal_candidates))
    canonical_pool_closed = bool(context.get("canonical_pool_closed"))
    residual_other_admissible = bool(context.get("residual_other_admissible"))
    novelty_candidate = bool(context.get("novelty_candidate"))
    novelty_basis = [str(x) for x in (context.get("novelty_basis") or []) if str(x)]
    top_standard_label = str(context.get("top_standard_label") or "") or None
    base_conf = _to_float(context.get("master_confidence"))
    if base_conf is None:
        base_conf = 0.05

    adjudicated_label = "unknown"
    decision_state = "insufficient_evidence"
    reason_codes: List[str] = []

    llm_label = str((llm_choice or {}).get("adjudicated_label") or "").strip()
    if llm_label and llm_label in legal_candidates:
        chosen_label = llm_label
        reason_codes.append("llm_final_adjudication")
    else:
        chosen_label = ""
        if llm_choice and llm_label and llm_label not in legal_candidates:
            reason_codes.append("llm_choice_outside_legal_candidates")

    if canonical_pool_closed:
        adjudicated_label = chosen_label or (top_standard_label or llm_primary_label or "unknown")
        if adjudicated_label not in legal_candidates:
            adjudicated_label = top_standard_label or legal_candidates[0]
        decision_state = "closed_known"
    elif allow_other_label and llm_primary_label == "other" and residual_other_admissible and active_profile in {"R2", "R3"}:
        adjudicated_label = chosen_label or "other"
        if adjudicated_label not in legal_candidates:
            adjudicated_label = "other"
        decision_state = "residual_supported"
    elif llm_primary_label not in STANDARD_LABEL_BLOCKLIST and top_standard_label and llm_primary_label in legal_candidates:
        adjudicated_label = chosen_label or llm_primary_label
        if adjudicated_label not in legal_candidates:
            adjudicated_label = llm_primary_label
        decision_state = "provisional_known"
        reason_codes.append("provisional_standard_retained")
    elif top_standard_label and top_standard_label in legal_candidates:
        adjudicated_label = chosen_label or top_standard_label
        if adjudicated_label not in legal_candidates:
            adjudicated_label = top_standard_label
        decision_state = "provisional_known"
        reason_codes.append("top_provisional_standard_selected")
    elif allow_other_label and residual_other_admissible and "other" in legal_candidates and active_profile in {"R2", "R3"}:
        adjudicated_label = chosen_label or "other"
        if adjudicated_label not in legal_candidates:
            adjudicated_label = "other"
        decision_state = "residual_supported"
        reason_codes.append("residual_other_selected")
    else:
        adjudicated_label = "unknown"
        decision_state = "insufficient_evidence"
        reason_codes.append("insufficient_evidence")

    conf_delta = _to_float((llm_choice or {}).get("confidence_adjustment_delta"))
    if conf_delta is None:
        conf_delta = 0.0
    adjudicated_confidence = max(0.05, min(0.95, float(base_conf) + float(conf_delta)))
    if decision_state == "provisional_known":
        adjudicated_confidence = min(adjudicated_confidence, float(provisional_confidence_cap))
    elif decision_state == "insufficient_evidence":
        adjudicated_confidence = min(adjudicated_confidence, 0.18)

    why_not_other = str((llm_choice or {}).get("why_not_other") or "").strip()
    if not why_not_other:
        if not allow_other_label:
            why_not_other = "The active benchmark label pool disables 'other'; unresolved cases must remain within standard labels or unknown."
        elif residual_other_admissible:
            why_not_other = "Residual 'other' remained admissible, but a standard candidate stayed provisionally preferred."
        else:
            why_not_other = "Residual 'other' was not admissible under the final-round gate."
    why_not_unknown = str((llm_choice or {}).get("why_not_unknown") or "").strip()
    if not why_not_unknown:
        if decision_state == "insufficient_evidence":
            why_not_unknown = "Unknown remained the only admissible unresolved outcome."
        else:
            why_not_unknown = "Final adjudication retained a legal non-unknown outcome."
    why_not_top_standard = str((llm_choice or {}).get("why_not_top_standard") or "").strip()
    if not why_not_top_standard:
        if canonical_pool_closed:
            why_not_top_standard = "A canonical standard label achieved closure."
        elif top_standard_label:
            why_not_top_standard = "No standard label achieved full closure; only provisional support remained."
        else:
            why_not_top_standard = "No standard candidate reached a decisive support state."

    return {
        "llm_primary_label": llm_primary_label or None,
        "adjudicated_label": adjudicated_label,
        "decision_state": decision_state,
        "canonical_pool_closed": canonical_pool_closed,
        "allow_other_label": allow_other_label,
        "residual_other_admissible": residual_other_admissible,
        "novelty_candidate": novelty_candidate,
        "novelty_basis": novelty_basis,
        "reason_codes": _dedupe_strings(reason_codes + [str(x) for x in ((llm_choice or {}).get("reason_codes") or []) if str(x)]),
        "confidence_adjustment_delta": round(float(conf_delta), 6),
        "adjudicated_confidence": round(float(adjudicated_confidence), 6),
        "why_not_other": why_not_other,
        "why_not_unknown": why_not_unknown,
        "why_not_top_standard": why_not_top_standard,
        "legal_candidates": legal_candidates,
        "top_standard_label": top_standard_label,
    }


def apply_final_label_adjudication(
    *,
    master_output: Dict[str, Any],
    adjudication: Dict[str, Any],
) -> Dict[str, Any]:
    out = deepcopy(master_output) if isinstance(master_output, dict) else {}
    claim = out.get("mechanism_claim") if isinstance(out.get("mechanism_claim"), dict) else {}
    primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
    llm_primary_label = str((adjudication.get("llm_primary_label") or primary.get("mechanism_label") or "")).strip()
    adjudicated_label = str(adjudication.get("adjudicated_label") or llm_primary_label or "unknown")
    primary["mechanism_label"] = adjudicated_label
    claim["confidence"] = float(adjudication.get("adjudicated_confidence") or claim.get("confidence") or 0.05)
    out["mechanism_claim"] = claim
    note = ""
    if llm_primary_label and adjudicated_label and llm_primary_label != adjudicated_label:
        note = (
            f"Adjudication note: final structured label changed from {llm_primary_label} to {adjudicated_label} "
            f"because {', '.join(_dedupe_strings(adjudication.get('reason_codes') or ['final_adjudication']))}."
        )
    nlm = str(primary.get("natural_language_mechanism") or "").strip()
    if note and note not in nlm:
        primary["natural_language_mechanism"] = f"{note}\n\n{nlm}" if nlm else note
    meta = out.get("__meta") if isinstance(out.get("__meta"), dict) else {}
    meta["final_label_adjudication"] = deepcopy(adjudication)
    meta["adjudicated_label"] = adjudicated_label
    meta["normalized_primary_label"] = adjudicated_label
    meta["decision_state"] = adjudication.get("decision_state")
    meta["canonical_pool_closed"] = bool(adjudication.get("canonical_pool_closed", False))
    meta["allow_other_label"] = bool(adjudication.get("allow_other_label", False))
    meta["residual_other_admissible"] = bool(adjudication.get("residual_other_admissible", False))
    meta["novelty_candidate"] = bool(adjudication.get("novelty_candidate", False))
    meta["novelty_basis"] = [str(x) for x in (adjudication.get("novelty_basis") or []) if str(x)]
    meta["reason_codes"] = [str(x) for x in (adjudication.get("reason_codes") or []) if str(x)]
    meta["normalization_reason_codes"] = [str(x) for x in (adjudication.get("reason_codes") or []) if str(x)]
    out["__meta"] = meta
    return out


def build_eval_report(
    *,
    case_json: Dict[str, Any],
    judged: Dict[str, Any],
    round_index: int,
    active_profile: str,
    run_lane: str,
    prev_confidence: float | None = None,
    info_gain: Dict[str, Any] | None = None,
    candidate_scorecard: Sequence[Dict[str, Any]] | None = None,
    normalization_summary: Dict[str, Any] | None = None,
    final_label_adjudication: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    evidence_readiness = case_json.get("evidence_readiness") or {}
    atb = evidence_readiness.get("atb") or {}
    offline_pdfs = ((case_json.get("inputs") or {}).get("offline_pdfs") or [])

    atb_available = str(atb.get("cache_status") or "").lower() == "success"
    offline_pdf_available = bool(offline_pdfs)
    literature_enabled = str(run_lane or "").lower() in {"offline_pdf", "full"}
    wetlab_enabled = str(run_lane or "").lower() in {"full"}

    constraints: List[str] = []
    if not literature_enabled:
        constraints.append("lane_disabled:literature")
    if not wetlab_enabled:
        constraints.append("lane_disabled:wetlab")
    if not offline_pdf_available:
        constraints.append("missing_input:offline_pdf")
    if not atb_available:
        constraints.append("atb_not_available")

    cap_values = [
        1.0 if atb_available else 0.0,
        1.0 if offline_pdf_available else 0.0,
        1.0 if literature_enabled else 0.0,
        1.0 if wetlab_enabled else 0.0,
    ]
    overall_score = round(sum(cap_values) / len(cap_values), 3)

    missing_evidence = list(judged.get("missing_evidence") or [])
    contradictions = list(judged.get("contradictions") or [])
    def _action_row(
        action: str,
        *,
        eig: float,
        feasible: bool,
        blocked_by: List[str],
        unblock_actions: List[str],
        rationale: str,
    ) -> Dict[str, Any]:
        feasibility_score = 1.0 if feasible else 0.0
        return {
            "action": action,
            "expected_information_gain": round(float(eig), 3),
            "feasible": bool(feasible),
            "feasibility_score": round(float(feasibility_score), 3),
            "blocked_by": blocked_by,
            "unblock_actions": unblock_actions,
            "priority_score": round(float(eig) * float(feasibility_score), 3),
            "rationale": rationale,
        }

    voi_rows = [
        _action_row(
            "switch_run_lane_offline_pdf",
            eig=0.65,
            feasible=str(run_lane or "").lower() == "atb_cache_only",
            blocked_by=[] if str(run_lane or "").lower() == "atb_cache_only" else ["lane_already_switched"],
            unblock_actions=[] if str(run_lane or "").lower() == "atb_cache_only" else ["continue_current_lane"],
            rationale="Enable offline PDF lane to unlock new discriminative evidence.",
        ),
        _action_row(
            "provide_offline_pdf",
            eig=0.60,
            feasible=not offline_pdf_available,
            blocked_by=[] if not offline_pdf_available else ["already_available"],
            unblock_actions=[] if not offline_pdf_available else ["run_offline_pdf_extractor"],
            rationale="Provide a PDF input so offline extraction can run.",
        ),
        _action_row(
            "run_master_reasoner",
            eig=0.35,
            feasible=True,
            blocked_by=[],
            unblock_actions=[],
            rationale="Re-run reasoning after profile update to reduce uncertainty.",
        ),
        _action_row(
            "run_offline_pdf_extractor",
            eig=0.55,
            feasible=offline_pdf_available and literature_enabled,
            blocked_by=(
                [] if (offline_pdf_available and literature_enabled) else
                ([x for x in ["missing_input:offline_pdf", "lane_disabled:literature"] if x in constraints])
            ),
            unblock_actions=(
                [] if (offline_pdf_available and literature_enabled) else
                ["request_manual_pdf"] if not offline_pdf_available else ["enable_literature_lane"]
            ),
            rationale="Extract literature cues to reduce mechanism ambiguity.",
        ),
        _action_row(
            "request_min_experiment_emission",
            eig=0.70,
            feasible=wetlab_enabled,
            blocked_by=[] if wetlab_enabled else ["lane_disabled:wetlab"],
            unblock_actions=[] if wetlab_enabled else ["enable_wetlab_lane"],
            rationale="Emission readouts provide strongest discriminators among competing hypotheses.",
        ),
    ]
    # Keep all rows but sort by priority score desc.
    voi_rows = sorted(voi_rows, key=lambda x: (-float(x.get("priority_score") or 0.0), str(x.get("action") or "")))

    if voi_rows and not bool(voi_rows[0].get("feasible")):
        # If top is infeasible and a feasible row exists, move first feasible to head.
        for i, row in enumerate(voi_rows):
            if bool(row.get("feasible")):
                voi_rows.insert(0, voi_rows.pop(i))
                break

    master = case_json.get("master_reasoning") or {}
    mechanism_claim = master.get("mechanism_claim") if isinstance(master, dict) else {}
    master_conf = _to_float((mechanism_claim or {}).get("confidence"))
    if master_conf is None:
        master_conf = _to_float(judged.get("confidence"))
    if master_conf is None:
        master_conf = 0.0
    prev = _to_float(prev_confidence)
    if prev is None:
        prev = master_conf
    new_conf = master_conf
    conf_delta = round(float(new_conf) - float(prev), 6)

    next_profile = _next_round_profile(active_profile, literature_enabled=literature_enabled)
    if next_profile == "R3" and not literature_enabled:
        next_profile = "R2"

    info = info_gain if isinstance(info_gain, dict) else {}
    decision_state = str((normalization_summary or {}).get("decision_state") or "").strip().lower()
    novelty_candidate = bool((normalization_summary or {}).get("novelty_candidate"))
    count_added = int(info.get("count_added") or 0)
    if "count_effective_added" in info and info.get("count_effective_added") is not None:
        count_effective_added = int(info.get("count_effective_added"))
    else:
        count_effective_added = count_added
    confidence_delta = _to_float(info.get("confidence_delta"))
    if confidence_delta is None:
        confidence_delta = 0.0
    hypothesis_changed = bool(info.get("hypothesis_changed"))
    profile_repeated = bool(info.get("profile_repeated")) if "profile_repeated" in info else (str(next_profile) == str(active_profile))
    max_profile_in_lane = "R3" if literature_enabled else "R2"

    should_stop = False
    reason_code = "continue"
    effective_gain = bool(count_effective_added > 0 or hypothesis_changed or abs(float(confidence_delta)) >= 0.02)
    lane_atb_only = str(run_lane or "").lower() == "atb_cache_only"
    if decision_state == "closed_known" and len(contradictions) == 0:
        should_stop = True
        reason_code = "closed_known"
        next_profile = "NONE"
    elif decision_state == "residual_supported" and (lane_atb_only or str(active_profile or "").upper() in {"R2", "R3"}):
        should_stop = True
        reason_code = "residual_supported"
        next_profile = "NONE"
        if novelty_candidate:
            voi_rows = sorted(
                voi_rows,
                key=lambda row: (
                    0 if str(row.get("action") or "") in {"switch_run_lane_offline_pdf", "provide_offline_pdf"} else 1,
                    -float(row.get("priority_score") or 0.0),
                    str(row.get("action") or ""),
                ),
            )
    elif lane_atb_only and str(active_profile or "").upper() == "R1" and count_effective_added == 0:
        should_stop = True
        reason_code = "no_new_evidence_available_in_lane"
        next_profile = "NONE"
    elif lane_atb_only and str(active_profile or "").upper() in {"R2", "R3"} and count_effective_added == 0:
        should_stop = True
        reason_code = "no_new_evidence_available_in_lane"
        next_profile = "NONE"
    elif not effective_gain and profile_repeated:
        should_stop = True
        reason_code = "stagnation_no_new_evidence"
        next_profile = "NONE"
    elif not effective_gain and str(next_profile) != str(active_profile) and str(next_profile) == max_profile_in_lane:
        should_stop = True
        reason_code = "no_new_evidence_available_in_lane"
        next_profile = "NONE"
    elif next_profile == "NONE":
        should_stop = True
        reason_code = "profile_exhausted"
    elif not any(bool(x.get("feasible")) for x in voi_rows):
        should_stop = True
        reason_code = "no_feasible_actions"
    elif (master.get("status") or "") == "ok" and master_conf >= 0.70 and len(contradictions) == 0:
        should_stop = True
        reason_code = "confidence_sufficient"

    status = str(judged.get("status") or "ok")
    report = {
        "round_index": int(round_index),
        "status": status,
        "candidate_scorecard": [
            dict(row)
            for row in (candidate_scorecard or [])
            if isinstance(row, dict)
        ],
        "master_candidate_scorecard": [
            dict(row)
            for row in (candidate_scorecard or [])
            if isinstance(row, dict)
        ],
        "normalization_summary": {
            "llm_primary_label": str((normalization_summary or {}).get("llm_primary_label") or "") or None,
            "normalized_primary_label": str((normalization_summary or {}).get("normalized_primary_label") or "") or None,
            "decision_state": str((normalization_summary or {}).get("decision_state") or "") or None,
            "canonical_pool_closed": bool((normalization_summary or {}).get("canonical_pool_closed", False)),
            "standard_label_closure": str((normalization_summary or {}).get("standard_label_closure") or "") or None,
            "residual_other_admissible": bool((normalization_summary or {}).get("residual_other_admissible", False)),
            "novelty_candidate": bool((normalization_summary or {}).get("novelty_candidate", False)),
            "novelty_basis": [
                str(x) for x in ((normalization_summary or {}).get("novelty_basis") or []) if str(x)
            ],
            "normalization_reason_codes": [
                str(x) for x in ((normalization_summary or {}).get("normalization_reason_codes") or []) if str(x)
            ],
        },
        "final_label_adjudication": deepcopy(final_label_adjudication or {}),
        "evidence_scorecard": [
            {
                "dimension": "atb_baseline",
                "score": 1.0 if atb_available else 0.0,
                "gaps": [] if atb_available else ["target_atb_missing"],
                "supporting_evidence_ids": [],
            },
            {
                "dimension": "external_discriminators",
                "score": 1.0 if (offline_pdf_available or wetlab_enabled) else 0.0,
                "gaps": missing_evidence,
                "supporting_evidence_ids": [],
            },
        ],
        "conflict_adjudication": [
            {
                "conflict_id": f"C{i+1}",
                "status": "unresolved",
                "rationale": text,
                "evidence_ids": [],
            }
            for i, text in enumerate(contradictions)
        ],
        "voi_ranked_actions": voi_rows,
        "next_round_profile": next_profile,
        "stop_recommendation": {
            "should_stop": bool(should_stop),
            "reason_code": reason_code,
            "explanation": f"active_profile={active_profile}; next_profile={next_profile}; feasibility={overall_score}",
        },
        "confidence_update": {
            "prev": prev,
            "delta": conf_delta,
            "new": new_conf,
            "basis": "master_confidence" if (case_json.get("master_reasoning") or {}) else "judge_confidence",
        },
        "information_gain": {
            "count_added": count_added,
            "count_effective_added": count_effective_added,
            "effective_gain": effective_gain,
            "hypothesis_changed": hypothesis_changed,
            "confidence_delta": confidence_delta,
            "profile_repeated": profile_repeated,
        },
        "feasibility": {
            "lane_capabilities": {
                "atb_available": atb_available,
                "offline_pdf_available": offline_pdf_available,
                "literature_enabled": literature_enabled,
                "wetlab_enabled": wetlab_enabled,
            },
            "constraints": constraints,
            "overall_score": overall_score,
        },
    }
    return report


def build_post_uq_from_eval(eval_report: Dict[str, Any]) -> Dict[str, Any]:
    eval_copy = deepcopy(eval_report if isinstance(eval_report, dict) else {})
    contradictions = [
        str((row or {}).get("rationale") or "")
        for row in (eval_copy.get("conflict_adjudication") or [])
        if isinstance(row, dict)
    ]
    missing = []
    for row in (eval_copy.get("evidence_scorecard") or []):
        if not isinstance(row, dict):
            continue
        for g in (row.get("gaps") or []):
            txt = str(g or "").strip()
            if txt and txt not in missing:
                missing.append(txt)
    recommended = []
    for row in (eval_copy.get("voi_ranked_actions") or []):
        if not isinstance(row, dict):
            continue
        act = str(row.get("action") or "").strip()
        if act and act not in recommended:
            recommended.append(act)
    conf = _to_float(((eval_copy.get("confidence_update") or {}).get("new")))
    confidence_adjustment = eval_copy.get("confidence_adjustment")
    if not isinstance(confidence_adjustment, dict):
        confidence_adjustment = {}
    return {
        "status": str(eval_copy.get("status") or "not_started"),
        "confidence": conf,
        "contradictions": contradictions,
        "missing_evidence": missing,
        "recommended_actions": recommended,
        "confidence_adjustment": confidence_adjustment,
    }


def apply_evaluator_confidence_adjustment(
    *,
    eval_report: Dict[str, Any],
    config: Dict[str, Any],
    master_confidence: float,
    cap: float,
    added_ids: Sequence[str],
    count_added: int,
    resolved_conflicts: Sequence[str],
    scorecard_improved: bool,
    feasibility_improved: bool,
    conflicts_increased: bool,
) -> Dict[str, Any]:
    out = deepcopy(eval_report if isinstance(eval_report, dict) else {})
    cfg = config if isinstance(config, dict) else {}
    enabled = bool(cfg.get("enabled", False))
    max_abs = float(cfg.get("max_abs_delta", 0.05) or 0.05)
    max_abs = max(0.0, min(0.2, max_abs))
    require_new = bool(cfg.get("require_new_evidence", True))
    high_weight = {str(x).strip() for x in (cfg.get("high_weight_evidence_ids") or []) if str(x).strip()}

    added_norm = [str(x).strip() for x in added_ids if str(x).strip()]
    high_weight_added = [x for x in added_norm if x in high_weight] if high_weight else list(added_norm)
    has_resolved_conflicts = bool([str(x).strip() for x in resolved_conflicts if str(x).strip()])
    cond_new_high_weight = int(count_added or 0) > 0 and bool(high_weight_added)
    trigger = bool(cond_new_high_weight or has_resolved_conflicts)

    if not enabled:
        out["confidence_adjustment"] = {
            "enabled": False,
            "applied": False,
            "reason": "disabled",
        }
        return out
    if require_new and not trigger:
        out["confidence_adjustment"] = {
            "enabled": True,
            "applied": False,
            "reason": "trigger_not_met",
            "triggered_by": {"added_ids": added_norm, "resolved_conflicts": list(resolved_conflicts or [])},
            "bounded_by": {"max_abs_delta": max_abs, "cap": float(cap)},
        }
        return out

    positive = int(bool(scorecard_improved)) + int(bool(feasibility_improved)) + int(has_resolved_conflicts)
    negative = int(bool(conflicts_increased))
    if positive > negative:
        direction = 1.0
    elif negative > positive:
        direction = -1.0
    else:
        direction = 1.0 if cond_new_high_weight and not conflicts_increased else 0.0

    magnitude = max_abs if has_resolved_conflicts else (max_abs * 0.6)
    delta = round(direction * magnitude, 6)
    prev = _to_float(((out.get("confidence_update") or {}).get("new")))
    if prev is None:
        prev = float(master_confidence)
    new_conf = max(0.05, min(float(cap), float(prev) + float(delta)))
    applied_delta = round(float(new_conf) - float(prev), 6)

    cu = out.get("confidence_update")
    if not isinstance(cu, dict):
        cu = {}
    cu["new"] = float(new_conf)
    cu["delta"] = round(float(new_conf) - float(_to_float(cu.get("prev")) if _to_float(cu.get("prev")) is not None else prev), 6)
    cu["basis"] = "master_confidence+evaluator_adjustment"
    out["confidence_update"] = cu
    out["confidence_adjustment"] = {
        "enabled": True,
        "applied": bool(abs(applied_delta) > 0.0),
        "delta": float(applied_delta),
        "prev": float(prev),
        "new": float(new_conf),
        "triggered_by": {
            "added_ids": added_norm,
            "high_weight_added_ids": high_weight_added,
            "resolved_conflicts": [str(x) for x in (resolved_conflicts or []) if str(x).strip()],
        },
        "reason": (
            "positive_signal_from_new_evidence_or_resolved_conflicts"
            if applied_delta >= 0
            else "negative_signal_from_conflict_or_feasibility_drop"
        ),
        "bounded_by": {"max_abs_delta": max_abs, "cap": float(cap)},
    }
    return out


class JudgeAgent(CaseAgent):
    name = "judge_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/post_uq",
        "/post_uq/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def __init__(self, *, use_llm: bool = False) -> None:
        self.use_llm = bool(use_llm)

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        master_reasoning = case.get("master_reasoning")
        if not isinstance(master_reasoning, dict):
            master_reasoning = ((case.get("reasoning") or {}).get("master_output") or {})
        master_status = case.get("master_reasoning_status")
        if master_status is None:
            master_status = ((case.get("reasoning") or {}).get("status"))
        return {
            "case_id": case.get("case_id"),
            "gate": case.get("current_gate") or {},
            "target_fields": case.get("target_fields") or {},
            "master_reasoning": master_reasoning,
            "master_reasoning_status": master_status,
            "risk_scores": case.get("risk_scores") or {},
            "run_lane": ctx.run_lane,
            "active_profile": str(((case.get("iterative") or {}).get("active_profile") or "R0")),
            "round_index": int(((case.get("iterative") or {}).get("current_round") or 0)),
            "prev_confidence": _to_float((case.get("post_uq") or {}).get("confidence")),
        }

    def _heuristic_judge(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        master = inputs.get("master_reasoning") or {}
        has_reasoning = bool(master)
        missing = []
        if not inputs.get("target_fields"):
            missing.append("emission_fields_missing")
        if not has_reasoning:
            missing.append("reasoning_output_missing")
        status = "needs_followup" if missing else "ok"
        return {
            "status": status,
            "confidence": 0.65 if not missing else 0.4,
            "contradictions": [],
            "missing_evidence": missing,
            "recommended_actions": ["manual_extract"] if missing else ["run_master_reasoner"],
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        raw: Dict[str, Any] = {}
        warnings: List[str] = []
        judged: Dict[str, Any]

        if self.use_llm:
            try:
                llm = ResponsesLLMClient(
                    base_url=ctx.base_url,
                    model=ctx.model,
                    api_key_env=ctx.llm_api_key_env,
                    max_output_tokens=ctx.llm_max_output_tokens,
                    reasoning_effort=ctx.llm_reasoning_effort,
                )
                prompt = (
                    "You are a post-UQ judge agent.\n"
                    "Assess consistency and missing evidence from the case context.\n"
                    "Do not modify gate. Output strict JSON.\n\n"
                    f"Context:\n{inputs}"
                )
                out = llm.responses_json(
                    instructions="Return strict JSON only.",
                    input_text=prompt,
                    schema_name="judge_post_uq_v1",
                    schema=_judge_schema(),
                )
                judged = out["parsed"]
                raw["llm_request"] = out["request"]
                raw["llm_response"] = out["response"]
                raw["llm_trace_path"] = write_agent_response_trace(
                    ctx=ctx,
                    agent_name=self.name,
                    payload={
                        "run_id": ctx.run_id,
                        "case_id": inputs.get("case_id"),
                        "agent": self.name,
                        "model": ctx.model,
                        "reasoning_effort": ctx.llm_reasoning_effort,
                        "status": "completed",
                        "request": out.get("request"),
                        "response_raw": out.get("response"),
                        "parsed": judged,
                    },
                )
            except LLMClientError as exc:
                warnings.append(f"judge_llm_failed:{exc}")
                judged = self._heuristic_judge(inputs)
                raw["llm_trace_path"] = write_agent_response_trace(
                    ctx=ctx,
                    agent_name=self.name,
                    payload={
                        "run_id": ctx.run_id,
                        "case_id": inputs.get("case_id"),
                        "agent": self.name,
                        "model": ctx.model,
                        "reasoning_effort": ctx.llm_reasoning_effort,
                        "status": "failed_llm",
                        "error": f"{exc}",
                    },
                )
        else:
            judged = self._heuristic_judge(inputs)

        eval_report = build_eval_report(
            case_json=case,
            judged=judged,
            round_index=int(inputs.get("round_index") or 0),
            active_profile=str(inputs.get("active_profile") or "R0"),
            run_lane=str(inputs.get("run_lane") or ctx.run_lane),
            prev_confidence=_to_float(inputs.get("prev_confidence")),
        )
        patch = [{"op": "add", "path": "/post_uq", "value": build_post_uq_from_eval(eval_report)}]
        return AgentResult(
            patch=patch,
            status="success",
            warnings=warnings,
            raw_outputs={"judge_output": judged, "eval_report": eval_report, **raw},
        )

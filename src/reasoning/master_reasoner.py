"""
Pure-function master reasoner core.

Inputs:
- case_json
- reasoning_config

Outputs:
- reasoning_pack
- master_prompt_bundle
- master_output (strict JSON)
- master_patch (RFC6902 patch preview)
- replay-friendly metadata
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core.hashing import canonical_json_bytes, sha256_json
from src.reasoning.evidence_profiles import resolve_evidence_profiles
from src.reasoning.atb_ct_proxy_profile import compute_atb_ct_proxy_profile
from src.reasoning.charge_redistribution_profile import compute_charge_redistribution_profile
from src.reasoning.atb_shape_rigidity_profile import compute_atb_shape_rigidity_profile
from src.reasoning.atb_structural_relaxation_profile import compute_atb_structural_relaxation_profile
from src.reasoning.atb_trend_profile import compute_atb_trend_profile
from src.reasoning.atb_trends_self import compute_atb_trends_self
from src.reasoning.emission_observation_profile import compute_emission_observation_profile
from src.reasoning.neighbor_atb_stats import (
    ATB_DELTA_FIELDS,
    compact_neighbor_atb_rows,
    compute_neighbor_atb_stats_by_label,
)
from src.reasoning.r0_prior_profiles import (
    MAIN_PRIOR_LABELS,
    compute_candidate_slate_v2,
    compute_prior_reliability_profile,
    compute_structure_fact_sheet,
)
from src.reasoning.reasoning_config import build_allowed_mechanism_labels, build_reasoning_policy
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.tools.llm_client import ResponsesLLMClient


MASTER_PACK_VERSION = "master_pack_v1"
MASTER_PROMPT_BUNDLE_VERSION = "master_prompt_bundle_v1"
MASTER_OUTPUT_SCHEMA_VERSION_V1 = "master_output_schema_v1"
MASTER_OUTPUT_SCHEMA_VERSION_V2 = "master_output_schema_v2"
MASTER_OUTPUT_SCHEMA_VERSION_V3 = "master_output_schema_v3"
MASTER_OUTPUT_SCHEMA_VERSION = MASTER_OUTPUT_SCHEMA_VERSION_V3
MAX_PACK_BYTES = 24 * 1024
EVIDENCE_ID_PATTERN = re.compile(r"^(?:E[0-9]+|E_ATB_TREND_[1-4])$")
EVIDENCE_TOKEN_PATTERN = re.compile(r"\b(?:E_ATB_TREND_[1-4]|E[0-9]+)\b", flags=re.IGNORECASE)
STRONG_THRESHOLD_TRIGGER_PATTERN = re.compile(r"(?i)\b(?:threshold|cutoff)\b")
WEAK_THRESHOLD_TRIGGER_PATTERN = re.compile(r"(?i)\b(?:range|band)\b")
COMPARISON_PATTERN = re.compile(r"(?:<=|>=|<|>)")
INTERVAL_PATTERN = re.compile(r"-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?")
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
NUMERIC_CONTEXT_TOKEN_PATTERN = re.compile(
    r"(?i)(?:<=|>=|<|>|~|±|\bbetween\b|\bfrom\b|\bto\b|\bapprox(?:\.|imately)?\b)"
)
STANDARD_LIMIT_CONSERVATIVE = (
    "Conservative mode: mechanism assignment is tentative and should be interpreted with uncertainty."
)
STANDARD_LIMIT_NO_EMISSION = (
    "No emission evidence: emission_aggr_nm and emission_solid_or_film_nm are missing, so no direct emission-field confirmation is available."
)
STANDARD_LIMIT_LANE_DISABLED = (
    "Literature/experiment lane is disabled in this run; mechanism confidence is limited by missing external verification."
)
FORBIDDEN_MASTER_RISK_PATHS = {
    "/risk_scores/mechanism_hint",
    "/risk_scores/hint_confidence",
}
MASTER_NOTE_MAX_CHARS = 180
MASTER_MAX_SUPPORTING_CHAIN_ITEMS = 4
MASTER_MAX_PREDICTIONS_ITEMS = 3
MASTER_MAX_COMPETING_ITEMS = 3
MASTER_MAX_EVIDENCE_USED_ITEMS = 10
MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS = 3200
MASTER_DEFAULT_TEMPERATURE = 0.2
MASTER_OUTPUT_MODE_TAGGED_REPAIR = "tagged_repair"
MASTER_OUTPUT_MODE_STRICT_SCHEMA = "strict_schema"
TAGGED_SECTION_ORDER = [
    "TEMPLATE_USED",
    "STATUS",
    "PRIMARY_LABEL",
    "PRIMARY_CONFIDENCE",
    "PRIMARY",
    "COMPETING",
    "EVIDENCE",
    "PREDICTIONS",
    "LIMITS",
    "NEXT_ACTIONS",
]
TAGGED_SECTION_ALIASES = {
    "TEMPLATE": "TEMPLATE_USED",
    "NEXT": "NEXT_ACTIONS",
}
ATB_TREND_EVIDENCE_IDS = (
    "E_ATB_TREND_1",
    "E_ATB_TREND_2",
    "E_ATB_TREND_3",
    "E_ATB_TREND_4",
)
ATB_TREND_PROFILE_EVIDENCE_IDS = ("E31", "E32", "E33", "E34")
ATB_ENRICHMENT_EVIDENCE_IDS = ("E35", "E36", "E37", "E38", "E39")
AOP_COMPACT_EVIDENCE_IDS = ("E60", "E61", "E62", "E63")
TARGET_OBSERVATION_EVIDENCE_IDS = ("E70", "E71", "E72", "E73")
STRUCTURE_PRIOR_EVIDENCE_IDS = ("E40", "E41", "E42", "E43", "E44")
STRUCTURE_AGENT_EVIDENCE_IDS = ("E50", "E51", "E52", "E53", "E54", "E55", "E56")
BACKGROUND_PRIOR_EVIDENCE_IDS = ("E1", "E2", "E3", "E4", "E5", "E6")
COMPARATIVE_TRANSFERABILITY_EVIDENCE_IDS = ("E21", "E22", "E23", "E24")
COMPACT_REGISTRY_PRIORITY = (
    *COMPARATIVE_TRANSFERABILITY_EVIDENCE_IDS,
    *TARGET_OBSERVATION_EVIDENCE_IDS,
    *ATB_TREND_PROFILE_EVIDENCE_IDS,
    *ATB_ENRICHMENT_EVIDENCE_IDS,
    *AOP_COMPACT_EVIDENCE_IDS,
    *STRUCTURE_AGENT_EVIDENCE_IDS,
    "E40",
    "E41",
    "E42",
    "E44",
    "E2",
    "E4",
    "E6",
    "E1",
    "E3",
    "E5",
    "E43",
    *ATB_TREND_EVIDENCE_IDS,
)
ELECTRONIC_REDISTRIBUTION_EVIDENCE_IDS = ("E32", "E35", "E36", "E60", "E61", "E62", "E63", "E_ATB_TREND_2")
STRUCTURAL_RELAXATION_EVIDENCE_IDS = (
    "E31",
    "E33",
    "E34",
    "E37",
    "E38",
    "E_ATB_TREND_1",
    "E_ATB_TREND_3",
    "E_ATB_TREND_4",
)
SHAPE_RIGIDITY_EVIDENCE_IDS = ("E39",)
AXIS_EVIDENCE_ID_GROUPS: Dict[str, Tuple[str, ...]] = {
    "target_observation": TARGET_OBSERVATION_EVIDENCE_IDS,
    "electronic_redistribution": ELECTRONIC_REDISTRIBUTION_EVIDENCE_IDS,
    "structural_relaxation": STRUCTURAL_RELAXATION_EVIDENCE_IDS,
    "shape_rigidity": SHAPE_RIGIDITY_EVIDENCE_IDS,
    "structure_prior": STRUCTURE_PRIOR_EVIDENCE_IDS + STRUCTURE_AGENT_EVIDENCE_IDS,
    "comparative_transferability": COMPARATIVE_TRANSFERABILITY_EVIDENCE_IDS,
    "background_prior": BACKGROUND_PRIOR_EVIDENCE_IDS,
}
GOVERNING_PRIMARY_AXES = (
    "target_observation",
    "electronic_redistribution",
    "structural_relaxation",
    "shape_rigidity",
    "structure_prior",
)
TARGET_SIDE_PRIMARY_AXES = (
    "target_observation",
    "electronic_redistribution",
    "structural_relaxation",
    "shape_rigidity",
)


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_size_bytes(obj: Any) -> int:
    return len(canonical_json_bytes(obj))


def _json_pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _resolve_pointer(doc: Any, path: str) -> Tuple[bool, Any]:
    if path == "":
        return False, None
    if path == "/":
        return True, doc
    if not isinstance(path, str) or not path.startswith("/"):
        return False, None
    cur = doc
    for tok in path.split("/")[1:]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if tok not in cur:
                return False, None
            cur = cur[tok]
            continue
        if isinstance(cur, list):
            try:
                idx = int(tok)
            except Exception:
                return False, None
            if idx < 0 or idx >= len(cur):
                return False, None
            cur = cur[idx]
            continue
        return False, None
    return True, cur


def _axis_for_evidence_id(evidence_id: str) -> Optional[str]:
    token = str(evidence_id or "").strip()
    if not token:
        return None
    for axis_name, members in AXIS_EVIDENCE_ID_GROUPS.items():
        if token in members:
            return axis_name
    return None


def _collect_governing_evidence_ids(master_output: Dict[str, Any]) -> List[str]:
    evidence_ids: List[str] = []

    def _collect(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "context").strip().lower()
            if role not in {"support", "context"}:
                continue
            evidence_id = str(row.get("evidence_id") or "").strip()
            if evidence_id:
                evidence_ids.append(evidence_id)

    _collect(master_output.get("evidence_used"))
    for row in master_output.get("supporting_chain") or []:
        if isinstance(row, dict):
            _collect(row.get("evidence_used"))
    return evidence_ids


def _axis_support_summary(evidence_ids: Iterable[str]) -> Dict[str, List[str]]:
    summary: Dict[str, List[str]] = {axis_name: [] for axis_name in AXIS_EVIDENCE_ID_GROUPS}
    for evidence_id in evidence_ids:
        axis_name = _axis_for_evidence_id(str(evidence_id or ""))
        if not axis_name:
            continue
        if evidence_id not in summary[axis_name]:
            summary[axis_name].append(str(evidence_id))
    return summary


def _axis_role_summary(rows: Any) -> Dict[str, Dict[str, List[str]]]:
    summary: Dict[str, Dict[str, List[str]]] = {
        axis_name: {"support": [], "weakening": [], "context": []}
        for axis_name in AXIS_EVIDENCE_ID_GROUPS
    }
    if not isinstance(rows, list):
        return summary
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id") or "").strip()
        axis_name = _axis_for_evidence_id(evidence_id)
        if not axis_name:
            continue
        role = str(row.get("role") or "context").strip().lower()
        bucket = "support"
        if role == "counter":
            bucket = "weakening"
        elif role != "support":
            bucket = "context"
        if evidence_id and evidence_id not in summary[axis_name][bucket]:
            summary[axis_name][bucket].append(evidence_id)
    return summary


def _merge_axis_role_summary(*summaries: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    merged: Dict[str, Dict[str, List[str]]] = {
        axis_name: {"support": [], "weakening": [], "context": []}
        for axis_name in AXIS_EVIDENCE_ID_GROUPS
    }
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for axis_name, buckets in summary.items():
            if axis_name not in merged or not isinstance(buckets, dict):
                continue
            for bucket in ("support", "weakening", "context"):
                for evidence_id in buckets.get(bucket) or []:
                    token = str(evidence_id or "").strip()
                    if token and token not in merged[axis_name][bucket]:
                        merged[axis_name][bucket].append(token)
    return merged


def evaluate_standard_label_closure(
    *,
    primary_axes: Sequence[str],
    min_positive_axes: int,
    requires_target_axis: bool,
) -> Dict[str, Any]:
    dedup_axes: List[str] = []
    for axis_name in primary_axes:
        token = str(axis_name or "").strip()
        if token and token not in dedup_axes:
            dedup_axes.append(token)
    target_side_axes = [axis_name for axis_name in dedup_axes if axis_name in TARGET_SIDE_PRIMARY_AXES]
    if len(dedup_axes) >= int(max(1, min_positive_axes)) and (
        not requires_target_axis or bool(target_side_axes)
    ):
        status = "closed"
    elif dedup_axes:
        status = "provisional"
    else:
        status = "unsupported"
    return {
        "status": status,
        "positive_axes": dedup_axes,
        "target_side_axes": target_side_axes,
    }


def evaluate_residual_other_admissibility(
    *,
    active_profile: str,
    has_target_side_support: bool,
    standard_candidates_in_play: Sequence[str],
    standard_label_closed: bool,
    primary_axes: Sequence[str],
    weakening_axes: Sequence[str],
    active_conflict_count: int,
    min_standard_candidates: int,
    min_conflicts: int,
) -> Dict[str, Any]:
    reasons: List[str] = []
    qualifying_signals: List[str] = []
    profile = str(active_profile or "").upper()
    standard_pool = [str(x or "").strip() for x in standard_candidates_in_play if str(x or "").strip()]
    if profile not in {"R2", "R3"}:
        reasons.append("pre_residual_round")
    if not has_target_side_support:
        reasons.append("missing_target_side_evidence")
    if len(standard_pool) < int(max(1, min_standard_candidates)):
        reasons.append("insufficient_standard_candidates")
    if standard_label_closed:
        reasons.append("standard_label_closed")
    if primary_axes:
        qualifying_signals.append("primary_axis_present")
    if active_conflict_count >= int(max(1, min_conflicts)):
        qualifying_signals.append("conflict_threshold_met")
    if len(standard_pool) >= int(max(1, min_standard_candidates)) and weakening_axes:
        qualifying_signals.append("standard_set_remains_weakened")
    admissible = not reasons and bool(qualifying_signals)
    if not admissible and not reasons:
        reasons.append("insufficient_residual_signals")
    return {
        "admissible": admissible,
        "reasons": reasons,
        "qualifying_signals": qualifying_signals,
        "standard_candidates_in_play": standard_pool,
    }


def evaluate_novelty_candidate(
    *,
    reasoning_pack: Dict[str, Any],
    residual_other_admissible: bool,
    active_conflict_count: int,
) -> Dict[str, Any]:
    risk = reasoning_pack.get("risk_scores") if isinstance(reasoning_pack, dict) else {}
    policy = _policy(reasoning_pack if isinstance(reasoning_pack, dict) else {})
    novelty_struct = _to_float((risk or {}).get("novelty_struct")) if isinstance(risk, dict) else None
    mechanism_entropy = _to_float((risk or {}).get("mechanism_entropy")) if isinstance(risk, dict) else None
    basis: List[str] = []
    if novelty_struct is not None and novelty_struct >= float(policy.get("novelty_candidate_struct_threshold") or 0.60):
        basis.append("novelty_struct_high")
    if mechanism_entropy is not None and mechanism_entropy >= float(policy.get("novelty_candidate_entropy_threshold") or 0.75):
        basis.append("mechanism_entropy_high")
    if residual_other_admissible and active_conflict_count >= int(policy.get("residual_other_min_conflicts") or 2):
        basis.append("late_round_residual_conflict")
    return {
        "is_novelty_candidate": bool(basis),
        "basis": basis,
        "novelty_struct": novelty_struct,
        "mechanism_entropy": mechanism_entropy,
    }


def resolve_final_label_and_decision_state(
    *,
    active_profile: str,
    llm_primary_label: str,
    standard_label_closure: Optional[str],
    residual_other_admissible: bool,
) -> Dict[str, Any]:
    profile = str(active_profile or "").upper()
    raw_label = str(llm_primary_label or "").strip()
    closure = str(standard_label_closure or "unsupported")
    normalized_label = raw_label or "unknown"
    decision_state = "insufficient_evidence"
    reason_codes: List[str] = []

    if profile == "R0" and raw_label not in {"", "unknown", "other"}:
        normalized_label = raw_label
        decision_state = "provisional_known"
        reason_codes.append("r0_prior_only_decision")
    elif raw_label == "other":
        if profile in {"R2", "R3"} and residual_other_admissible:
            normalized_label = "other"
            decision_state = "residual_supported"
        else:
            normalized_label = "unknown"
            decision_state = "insufficient_evidence"
            reason_codes.append("other_without_residual_admissibility")
    elif raw_label in {"", "unknown"}:
        normalized_label = "unknown"
        decision_state = "insufficient_evidence"
        if raw_label == "unknown":
            reason_codes.append("llm_unknown_retained")
        else:
            reason_codes.append("missing_primary_label")
    elif closure == "closed":
        normalized_label = raw_label
        decision_state = "closed_known"
    elif closure == "provisional":
        normalized_label = raw_label
        decision_state = "provisional_known"
        reason_codes.append("standard_label_provisional")
    else:
        normalized_label = "unknown"
        decision_state = "insufficient_evidence"
        reason_codes.append("standard_label_unsupported")

    return {
        "normalized_primary_label": normalized_label,
        "decision_state": decision_state,
        "reason_codes": reason_codes,
        "canonical_pool_closed": bool(closure == "closed"),
    }


def _role_ids_from_rows(rows: Any) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {"support": [], "weakening": [], "context": []}
    if not isinstance(rows, list):
        return grouped
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        role = str(row.get("role") or "context").strip().lower()
        bucket = "support"
        if role == "counter":
            bucket = "weakening"
        elif role != "support":
            bucket = "context"
        if evidence_id not in grouped[bucket]:
            grouped[bucket].append(evidence_id)
    return grouped


def _role_ids_from_master_output(master_output: Dict[str, Any]) -> Dict[str, List[str]]:
    grouped = _role_ids_from_rows(master_output.get("evidence_used"))
    for row in master_output.get("supporting_chain") or []:
        if not isinstance(row, dict):
            continue
        row_grouped = _role_ids_from_rows(row.get("evidence_used"))
        for bucket in ("support", "weakening", "context"):
            for evidence_id in row_grouped[bucket]:
                if evidence_id not in grouped[bucket]:
                    grouped[bucket].append(evidence_id)
    return grouped


def _normalize_candidate_label(raw: Any, *, allowed_labels: Sequence[str]) -> Optional[str]:
    txt = str(raw or "").strip()
    if not txt:
        return None
    lookup = {str(x).strip().lower(): str(x).strip() for x in allowed_labels if str(x).strip()}
    return lookup.get(txt.lower())


def _candidate_pool_from_context(
    reasoning_pack: Dict[str, Any],
    master_output: Optional[Dict[str, Any]] = None,
    *,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    allowed_labels = resolve_allowed_mechanism_labels(reasoning_pack, {})
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _push(label: Any, probability: Any, *, source: str) -> None:
        normalized = _normalize_candidate_label(label, allowed_labels=allowed_labels)
        if not normalized or normalized in seen:
            return
        prob = _to_float(probability)
        out.append(
            {
                "label": normalized,
                "probability": float(prob) if prob is not None else None,
                "source": source,
            }
        )
        seen.add(normalized)

    ctx = reasoning_pack.get("mechanism_context") if isinstance(reasoning_pack, dict) else {}
    if isinstance(ctx, dict):
        for row in ctx.get("candidate_mechanisms_topk") or []:
            if not isinstance(row, dict):
                continue
            _push(row.get("mechanism_id"), row.get("probability"), source="mechanism_context")

    if isinstance(master_output, dict):
        claim = master_output.get("mechanism_claim") if isinstance(master_output.get("mechanism_claim"), dict) else {}
        primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
        _push(primary.get("mechanism_label"), claim.get("confidence"), source="master_primary")
        for row in master_output.get("competing_hypotheses") or []:
            if not isinstance(row, dict):
                continue
            _push(row.get("name"), row.get("confidence"), source="master_competing")

    if len(out) < max_items:
        risk = reasoning_pack.get("risk_scores") if isinstance(reasoning_pack, dict) else {}
        structure_dist = (risk or {}).get("candidate_slate_v2") if isinstance(risk, dict) else {}
        source_name = "candidate_slate_v2"
        if not isinstance(structure_dist, dict) or not structure_dist:
            structure_dist = (risk or {}).get("structure_candidate_distribution") if isinstance(risk, dict) else {}
            source_name = "structure_candidate_distribution"
        if isinstance(structure_dist, dict):
            for row in (
                structure_dist.get("top_candidates")
                or structure_dist.get("top3")
                or []
            ):
                if not isinstance(row, dict):
                    continue
                _push(row.get("label") or row.get("mechanism_id"), row.get("prob") or row.get("probability"), source=source_name)
                if len(out) >= max_items:
                    break

    if not out:
        out.append({"label": "unknown", "probability": None, "source": "fallback"})
    return out[: max(1, int(max_items))]


def build_candidate_scorecard(
    *,
    reasoning_pack: Dict[str, Any],
    master_output: Dict[str, Any],
    prev_scorecard: Optional[Sequence[Dict[str, Any]]] = None,
    new_evidence_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(master_output, dict):
        return []

    previous: Dict[str, Dict[str, Any]] = {}
    for row in prev_scorecard or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if label:
            previous[label] = row

    candidates = _candidate_pool_from_context(reasoning_pack, master_output, max_items=5)
    master_claim = master_output.get("mechanism_claim") if isinstance(master_output.get("mechanism_claim"), dict) else {}
    primary = master_claim.get("primary_hypothesis") if isinstance(master_claim.get("primary_hypothesis"), dict) else {}
    primary_label = str(primary.get("mechanism_label") or "").strip()
    primary_confidence = _to_float(master_claim.get("confidence"))
    competing_rows = [row for row in (master_output.get("competing_hypotheses") or []) if isinstance(row, dict)]
    competing_lookup = {str(row.get("name") or "").strip(): row for row in competing_rows if str(row.get("name") or "").strip()}

    global_axis_summary = _merge_axis_role_summary(
        _axis_role_summary(master_output.get("evidence_used")),
        *[
            _axis_role_summary((row or {}).get("evidence_used"))
            for row in (master_output.get("supporting_chain") or [])
            if isinstance(row, dict)
        ],
    )
    primary_support_axes = [
        axis_name
        for axis_name in GOVERNING_PRIMARY_AXES
        if global_axis_summary.get(axis_name, {}).get("support")
    ]
    context_axes = [
        axis_name
        for axis_name in GOVERNING_PRIMARY_AXES + ("comparative_transferability",)
        if global_axis_summary.get(axis_name, {}).get("context")
    ]
    new_ids = {str(x) for x in (new_evidence_ids or []) if str(x)}
    primary_role_ids = _role_ids_from_master_output(master_output)

    scorecard: List[Dict[str, Any]] = []
    for idx, row in enumerate(candidates, start=1):
        label = str(row.get("label") or "").strip()
        prev_row = previous.get(label) or {}
        prior_rank = prev_row.get("current_rank") if prev_row.get("current_rank") is not None else idx
        prior_confidence = _to_float(prev_row.get("current_confidence"))
        if prior_confidence is None:
            prior_confidence = _to_float(row.get("probability"))

        current_rank: Optional[int] = None
        current_confidence: Optional[float] = None
        support_axes: List[str] = []
        weakening_axes: List[str] = []
        unresolved_axes: List[str] = []
        new_support_ids: List[str] = []
        new_weakening_ids: List[str] = []

        if label and label == primary_label:
            current_rank = 1
            current_confidence = primary_confidence
            support_axes = list(primary_support_axes)
            unresolved_axes = list(context_axes)
            new_support_ids = sorted(new_ids.intersection(primary_role_ids.get("support") or []))
            new_weakening_ids = sorted(new_ids.intersection(primary_role_ids.get("weakening") or []))
        else:
            comp = competing_lookup.get(label) or {}
            if comp:
                current_rank = competing_rows.index(comp) + 2
                current_confidence = _to_float(comp.get("confidence"))
                comp_axis_summary = _axis_role_summary(comp.get("evidence_used"))
                comp_role_ids = _role_ids_from_rows(comp.get("evidence_used"))
                support_axes = [
                    axis_name
                    for axis_name in GOVERNING_PRIMARY_AXES
                    if comp_axis_summary.get(axis_name, {}).get("support")
                ]
                unresolved_axes = [
                    axis_name
                    for axis_name in GOVERNING_PRIMARY_AXES + ("comparative_transferability",)
                    if comp_axis_summary.get(axis_name, {}).get("context")
                ]
                if primary_support_axes:
                    weakening_axes = [axis for axis in primary_support_axes if axis not in support_axes]
                new_support_ids = sorted(new_ids.intersection(comp_role_ids.get("support") or []))
                new_weakening_ids = sorted(new_ids.intersection(comp_role_ids.get("weakening") or []))
                if not new_weakening_ids and new_ids and weakening_axes:
                    new_weakening_ids = sorted(new_ids.intersection(primary_role_ids.get("support") or []))
            else:
                current_rank = idx
                current_confidence = _to_float(row.get("probability"))
                unresolved_axes = list(context_axes or primary_support_axes)

        if current_rank is None:
            current_rank = idx
        if current_confidence is None:
            current_confidence = prior_confidence
        if current_confidence is None:
            current_confidence = 0.0

        if prior_rank is None:
            net_direction = "flat"
        elif int(current_rank) < int(prior_rank):
            net_direction = "up"
        elif int(current_rank) > int(prior_rank):
            net_direction = "down"
        else:
            delta = float(current_confidence) - float(prior_confidence or current_confidence)
            if delta > 0.03:
                net_direction = "up"
            elif delta < -0.03:
                net_direction = "down"
            else:
                net_direction = "flat"

        commentary: str
        if label == primary_label:
            if support_axes:
                commentary = f"Current leading candidate supported by {', '.join(support_axes)}."
            else:
                commentary = "Current leading candidate remains provisional; primary support is still limited."
        elif weakening_axes:
            commentary = f"Candidate remains in play but is weakened relative to {primary_label or 'the current lead'}."
        else:
            commentary = "Candidate remains unresolved under current evidence and stays in the negotiation set."

        scorecard.append(
            {
                "label": label,
                "prior_rank": int(prior_rank),
                "prior_confidence": round(float(prior_confidence or 0.0), 4),
                "current_rank": int(current_rank),
                "current_confidence": round(float(current_confidence or 0.0), 4),
                "support_axes": support_axes,
                "weakening_axes": weakening_axes,
                "unresolved_axes": unresolved_axes,
                "new_support_evidence_ids": new_support_ids,
                "new_weakening_evidence_ids": new_weakening_ids,
                "net_direction": net_direction,
                "commentary": commentary,
            }
        )

    scorecard.sort(key=lambda row: (int(row.get("current_rank") or 999), -float(row.get("current_confidence") or 0.0), str(row.get("label") or "")))
    return scorecard


def _collect_paths(prefix: str, value: Any) -> Set[str]:
    out: Set[str] = set()
    if prefix == "":
        return out
    out.add(prefix)
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{prefix}/{_json_pointer_escape(str(k))}"
            out.update(_collect_paths(child, v))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            child = f"{prefix}/{i}"
            out.update(_collect_paths(child, v))
    return out


def _truncate_text(value: Any, max_chars: int = 300) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _json_only_contract_text(*, required_keys: Sequence[str], array_caps: Dict[str, int]) -> str:
    caps = ", ".join([f"{k}<={v}" for k, v in array_caps.items()])
    keys = ", ".join(required_keys)
    return (
        "JSON-only contract:\n"
        "- Output must start with '{' and end with '}'.\n"
        "- Do not output explanations, markdown, code fences, or any prefix/suffix text.\n"
        "- Output valid JSON only (no non-JSON text).\n"
        f"- Required top-level keys: {keys}.\n"
        f"- Array size caps: {caps}.\n"
        f"- Each evidence note must be <= {MASTER_NOTE_MAX_CHARS} chars."
    )


def _has_any_token(lines_lower: Sequence[str], tokens: Sequence[str]) -> bool:
    for line in lines_lower:
        for tok in tokens:
            if tok in line:
                return True
    return False


def _normalize_limits(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for row in value:
            txt = str(row or "").strip()
            if txt:
                out.append(txt)
        return out
    if isinstance(value, str):
        txt = value.strip()
        return [txt] if txt else []
    return []


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _policy(reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    return build_reasoning_policy(reasoning_config.get("policy") if isinstance(reasoning_config, dict) else None)


def _thresholds(reasoning_config: Dict[str, Any]) -> Dict[str, float]:
    cfg = reasoning_config if isinstance(reasoning_config, dict) else {}
    user = cfg.get("thresholds")
    if isinstance(user, dict):
        out: Dict[str, float] = {}
        for k, v in user.items():
            fv = _to_float(v)
            if fv is not None:
                out[str(k)] = fv
        if out:
            return out
    policy = _policy(cfg)
    return {
        "neighbor_support_min_sim": float(policy["neighbor_support_min_sim"]),
        "atb_dihedral_thresh_none": float(policy["atb_dihedral_thresh_none"]),
        "atb_dihedral_thresh_strong": float(policy["atb_dihedral_thresh_strong"]),
        "atb_dihedral_flat_eps": float(policy.get("atb_dihedral_flat_eps", 1.0e-6)),
        "atb_gap_flat_eps": float(policy.get("atb_gap_flat_eps", 0.05)),
        "atb_gap_weak": float(policy.get("atb_gap_weak", 0.2)),
        "atb_gap_strong": float(policy.get("atb_gap_strong", 0.6)),
        "atb_dipole_flat_eps": float(policy.get("atb_dipole_flat_eps", 0.05)),
        "atb_dipole_weak": float(policy.get("atb_dipole_weak", 0.2)),
        "atb_dipole_strong": float(policy.get("atb_dipole_strong", 0.6)),
        "charge_redis_total_abs_low": float(policy.get("charge_redis_total_abs_low", 0.2190)),
        "charge_redis_total_abs_high": float(policy.get("charge_redis_total_abs_high", 0.4805)),
        "charge_redis_top3_share_low": float(policy.get("charge_redis_top3_share_low", 0.1908)),
        "charge_redis_top3_share_high": float(policy.get("charge_redis_top3_share_high", 0.3195)),
        "charge_redis_hetero_share_low": float(policy.get("charge_redis_hetero_share_low", 0.1046)),
        "charge_redis_hetero_share_high": float(policy.get("charge_redis_hetero_share_high", 0.2620)),
        "atb_vol_flat_eps": float(policy.get("atb_vol_flat_eps", 0.1)),
        "atb_vol_weak": float(policy.get("atb_vol_weak", 0.5)),
        "atb_vol_strong": float(policy.get("atb_vol_strong", 2.0)),
        "atb_bonds_weak": float(policy.get("atb_bonds_weak", 0.02)),
        "atb_bonds_strong": float(policy.get("atb_bonds_strong", 0.08)),
        "atb_angles_weak": float(policy.get("atb_angles_weak", 0.2)),
        "atb_angles_strong": float(policy.get("atb_angles_strong", 0.8)),
        "atb_asymmetry_weak": float(policy.get("atb_asymmetry_weak", 0.05)),
        "atb_asymmetry_strong": float(policy.get("atb_asymmetry_strong", 0.2)),
        "atb_rotconst_rel_weak": float(policy.get("atb_rotconst_rel_weak", 0.05)),
        "atb_rotconst_rel_strong": float(policy.get("atb_rotconst_rel_strong", 0.15)),
        "top1_sim_low": float(policy["top1_sim_low"]),
        "entropy_high": float(policy["entropy_high"]),
        "global_confidence_cap": float(policy.get("global_confidence_cap", 0.95)),
        "r0_penalty_factor": float(policy.get("r0_penalty_factor", 0.90)),
        "conservative_confidence_cap": float(cfg.get("conservative_confidence_cap", 0.65)),
    }


def _threshold_values(reasoning_config: Dict[str, Any]) -> Set[float]:
    values: Set[float] = set()
    for v in _thresholds(reasoning_config).values():
        fv = _to_float(v)
        if fv is not None:
            values.add(round(float(fv), 6))
    return values


def _atb_support_level_from_features(
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> str:
    fs = (((reasoning_pack.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    if not isinstance(fs, dict):
        return "none"
    dihedral = _to_float(fs.get("delta_dihedral"))
    if dihedral is None:
        return "none"
    policy = _policy(reasoning_config)
    abs_dihedral = abs(dihedral)
    if abs_dihedral < float(policy["atb_dihedral_thresh_none"]):
        return "none"
    if abs_dihedral < float(policy["atb_dihedral_thresh_strong"]):
        return "weak"
    return "strong"


def _separation_score(reasoning_pack: Dict[str, Any]) -> Optional[float]:
    stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats_by_label")
    if not isinstance(stats, dict):
        stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats")
    if not isinstance(stats, dict):
        return None
    score = _to_float(stats.get("separation_score"))
    if score is None:
        return None
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _separation_reliability(reasoning_pack: Dict[str, Any]) -> str:
    stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats_by_label")
    if not isinstance(stats, dict):
        stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats")
    if not isinstance(stats, dict):
        return "low"
    return str(stats.get("reliability") or "low").lower()


def _soft_confidence(
    *,
    raw_confidence: Optional[float],
    template_used: str,
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    policy = _policy(reasoning_config)
    tpl = str(template_used or "mixture").lower()
    base_defaults = {
        "stable": float(policy.get("confidence_base_stable", 0.62)),
        "mixture": float(policy.get("confidence_base_mixture", 0.52)),
        "novelty": float(policy.get("confidence_base_novelty", 0.45)),
    }
    base = base_defaults.get(tpl, base_defaults["mixture"])
    raw = raw_confidence if raw_confidence is not None else base
    raw = max(0.0, min(1.0, float(raw)))

    risk = reasoning_pack.get("risk_scores") or {}
    top1 = _to_float(risk.get("top1_sim"))
    entropy = _to_float(risk.get("mechanism_entropy"))
    top1_low = float(policy.get("top1_sim_low", 0.5))
    entropy_high = float(policy.get("entropy_high", 0.75))
    sim_strength = float(policy.get("penalty_sim_strength", 0.25))
    ent_strength = float(policy.get("penalty_entropy_strength", 0.25))

    sim_factor = 1.0
    if top1 is not None and top1 < top1_low:
        ratio = (top1_low - top1) / max(top1_low, 1e-9)
        sim_factor = max(0.55, 1.0 - sim_strength * max(0.0, ratio))

    entropy_factor = 1.0
    if entropy is not None and entropy > entropy_high:
        ratio = (entropy - entropy_high) / max(1.0 - entropy_high, 1e-9)
        entropy_factor = max(0.55, 1.0 - ent_strength * max(0.0, ratio))

    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    mode_factor = float(policy.get("penalty_mode_conservative", 0.86)) if gate_mode == "conservative" else 1.0

    separation = _separation_score(reasoning_pack)
    separation_rel = _separation_reliability(reasoning_pack)
    separation_boost = 1.0
    if separation is not None and separation_rel in {"medium", "high"}:
        center = float(policy.get("separation_center", 0.45))
        strength = float(policy.get("separation_boost_strength", 0.22))
        delta = separation - center
        separation_boost = max(0.8, min(1.25, 1.0 + strength * delta))

    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    round_index_raw = reasoning_config.get("round_index") if isinstance(reasoning_config, dict) else None
    try:
        round_index = int(round_index_raw) if round_index_raw is not None else None
    except Exception:
        round_index = None
    r0_penalty_factor = float(policy.get("r0_penalty_factor", 0.90))
    apply_r0_penalty = bool(active_profile == "R0" or round_index == 0)

    final_pre_cap = raw * sim_factor * entropy_factor * mode_factor * separation_boost
    if apply_r0_penalty:
        final_pre_cap *= r0_penalty_factor

    global_cap = float(policy.get("global_confidence_cap", 0.95))
    cap_value = max(0.05, min(0.95, global_cap))
    cap_reason = "global_cap"
    if gate_mode == "conservative":
        cap_value = min(cap_value, float(reasoning_config.get("conservative_confidence_cap", 0.65)))
        cap_reason = "conservative_cap"

    final = min(final_pre_cap, cap_value)
    final = max(0.05, min(0.95, final))

    components = {
        "raw_confidence_from_model": raw,
        "base_conf": base,
        "top1_sim": top1,
        "mechanism_entropy": entropy,
        "sim_factor": round(sim_factor, 6),
        "ent_factor": round(entropy_factor, 6),
        "mode_factor": round(mode_factor, 6),
        "separation_score": separation,
        "separation_reliability": separation_rel,
        "neighbor_factor": round(separation_boost, 6),
        "final_conf_pre_cap": round(float(final_pre_cap), 6),
        "final_conf_post_cap": round(float(final), 6),
        "cap_value": round(float(cap_value), 6),
        "cap_reason": cap_reason,
        "r0_penalty_applied": apply_r0_penalty,
        "r0_penalty_factor": round(float(r0_penalty_factor), 6),
        "confidence_formula_version": "soft_v1",
    }
    return round(float(final), 6), components


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _err(err_type: str, code: str, path: str, detail: str) -> Dict[str, str]:
    return {
        "type": err_type,
        "code": code,
        "path": path,
        "detail": detail,
    }


def _warn(code: str, path: str, detail: str) -> Dict[str, str]:
    return _err("warning", code, path, detail)


def _llm_failure_reason_from_exc(exc: BaseException) -> str:
    code = str(getattr(exc, "code", "") or "").strip().lower()
    if code in {"no_message_output", "json_parse_error", "json_repair_used"}:
        return code
    text = str(exc).lower()
    if "responses_empty_output_text" in text:
        return "no_message_output"
    if "responses_invalid_json" in text or "unterminated string" in text or "expecting property name enclosed in double quotes" in text:
        return "json_parse_error"
    return "llm_error"


def _llm_error_payload(exc: BaseException) -> Dict[str, Any]:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        return details
    return {}


def _parse_json_candidate_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates: List[str] = [raw]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    for block in fenced:
        b = str(block or "").strip()
        if b:
            candidates.append(b)
    l_brace = raw.find("{")
    r_brace = raw.rfind("}")
    if l_brace != -1 and r_brace > l_brace:
        candidates.append(raw[l_brace : r_brace + 1].strip())
    seen: Set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_tagged_sections(text: str) -> Dict[str, str]:
    raw = str(text or "")
    sections: Dict[str, str] = {}
    all_tags = list(TAGGED_SECTION_ORDER) + list(TAGGED_SECTION_ALIASES.keys())
    tag_alt = "|".join(sorted({re.escape(x) for x in all_tags}, key=len, reverse=True))
    patt = re.compile(rf"(?mi)^({tag_alt}):\s*(.*)$")
    matches = list(patt.finditer(raw))
    if not matches:
        primary = raw.strip()
        if primary:
            sections["PRIMARY"] = primary
        return sections
    for i, m in enumerate(matches):
        raw_key = str(m.group(1) or "").upper()
        key = TAGGED_SECTION_ALIASES.get(raw_key, raw_key)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        head = str(m.group(2) or "").strip()
        body = raw[start:end].strip()
        content = (head + ("\n" + body if body else "")).strip()
        sections[key] = content
    return sections


def _extract_evidence_ids(text: str) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for m in EVIDENCE_TOKEN_PATTERN.findall(str(text or "")):
        eid = str(m).upper()
        if eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    return out


def _candidate_set_labels(reasoning_pack: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ctx = reasoning_pack.get("mechanism_context")
    if not isinstance(ctx, dict):
        return out
    rows = ctx.get("candidate_mechanisms_topk") or ctx.get("candidate_mechanisms_top3")
    if not isinstance(rows, list):
        return out
    for row in rows:
        label: Optional[str] = None
        if isinstance(row, dict):
            raw = row.get("mechanism_id") or row.get("label") or row.get("name")
            label = str(raw or "").strip() if raw is not None else None
        elif isinstance(row, str):
            label = row.strip()
        if label and label not in out:
            out.append(label)
    return out


def resolve_allowed_mechanism_labels(
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> List[str]:
    cfg_labels = None
    allow_other_label = None
    if isinstance(reasoning_config, dict):
        cfg_labels = reasoning_config.get("allowed_mechanism_labels")
        allow_other_label = reasoning_config.get("allow_other_label")
        if allow_other_label is None:
            policy = reasoning_config.get("policy")
            if isinstance(policy, dict):
                allow_other_label = policy.get("allow_other_label")
    if allow_other_label is None and isinstance(reasoning_pack, dict):
        runtime = reasoning_pack.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("allow_other_label"), bool):
            allow_other_label = runtime.get("allow_other_label")
    out = build_allowed_mechanism_labels(cfg_labels, include_other=allow_other_label)
    for label in _candidate_set_labels(reasoning_pack):
        if allow_other_label is False and label == "other":
            continue
        if label not in out:
            out.append(label)
    return out


def _parse_role(text: str, default: str = "context") -> str:
    t = str(text or "").lower()
    if "counter" in t or "against" in t:
        return "counter"
    if "support" in t or "evidence for" in t:
        return "support"
    if default in {"support", "counter", "context"}:
        return default
    return "context"


def _pick_registry_id_by_suffix(reasoning_pack: Dict[str, Any], suffix: str) -> Optional[str]:
    for row in reasoning_pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("case_path") or "")
        eid = str(row.get("evidence_id") or "")
        if path.endswith(suffix) and eid:
            return eid
    return None


def _fallback_evidence_ids(reasoning_pack: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    registry = _registry_map(reasoning_pack.get("evidence_registry") or [])
    for eid in ATB_ENRICHMENT_EVIDENCE_IDS:
        if eid in registry and eid not in seen:
            seen.add(eid)
            out.append(eid)
    for eid in ATB_TREND_EVIDENCE_IDS:
        if eid in registry and eid not in seen:
            seen.add(eid)
            out.append(eid)

    prefs = [
        "/evidence_readiness/atb/features_summary/delta_dihedral",
        "/evidence_readiness/atb/features_summary/delta_gap",
        "/evidence_readiness/atb/features_summary/delta_volume",
        "/risk_scores/top1_sim",
    ]
    for suffix in prefs:
        eid = _pick_registry_id_by_suffix(reasoning_pack, suffix)
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    for row in reasoning_pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("evidence_id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
        if len(out) >= MASTER_MAX_EVIDENCE_USED_ITEMS:
            break
    return out


def _parse_template_value(text: str, fallback: str) -> str:
    t = str(text or "").strip().lower()
    if t in {"stable", "mixture", "novelty"}:
        return t
    for v in ("stable", "mixture", "novelty"):
        if v in t:
            return v
    return fallback if fallback in {"stable", "mixture", "novelty"} else "mixture"


def _parse_status_value(text: str) -> str:
    t = str(text or "").strip().lower()
    if t == "ok":
        return "ok"
    if "insufficient" in t:
        return "insufficient_evidence"
    return "insufficient_evidence"


def _normalize_primary_label(
    raw_label: str,
    label_map: Dict[str, str],
) -> Optional[str]:
    raw = str(raw_label or "").strip()
    if not raw:
        return None
    direct = label_map.get(raw.lower())
    if direct:
        return direct
    # Allow lightweight normalization from annotated label text without generic keyword scanning.
    candidates: List[str] = [raw]
    for sep in ("(", ":", ";", ",", "/", "|"):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if head:
                candidates.append(head)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]*", raw)
    for tok in tokens:
        candidates.append(tok.strip())
    seen: Set[str] = set()
    for cand in candidates:
        key = cand.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized = label_map.get(key)
        if normalized:
            return normalized
    return None


def _parse_lines(text: Any) -> List[str]:
    out: List[str] = []
    for row in str(text or "").splitlines():
        line = row.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line:
            out.append(line)
    return out


def _parse_first_float(text: Any) -> Optional[float]:
    m = NUMBER_PATTERN.search(str(text or ""))
    if not m:
        return None
    return _to_float(m.group(0))


def _tagged_text_to_master_output(
    *,
    raw_text: str,
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
    template_fallback: str,
) -> Dict[str, Any]:
    sections = _parse_tagged_sections(raw_text)
    template_used = _parse_template_value(
        sections.get("TEMPLATE_USED") or sections.get("TEMPLATE"),
        template_fallback,
    )
    status = _parse_status_value(sections.get("STATUS"))
    required_sections = ["STATUS", "PRIMARY_LABEL", "PRIMARY_CONFIDENCE", "PRIMARY"]
    missing_sections = [s for s in required_sections if not str(sections.get(s) or "").strip()]
    if missing_sections:
        status = "invalid"
    primary_text = str(sections.get("PRIMARY") or "").strip()
    competing_text = str(sections.get("COMPETING") or "").strip()
    evidence_text = str(sections.get("EVIDENCE") or "").strip()
    predictions_text = str(sections.get("PREDICTIONS") or "").strip()
    limits_text = str(sections.get("LIMITS") or "").strip()
    next_text = str(sections.get("NEXT_ACTIONS") or sections.get("NEXT") or "").strip()

    fallback_ids = _fallback_evidence_ids(reasoning_pack)
    evidence_lines = _parse_lines(evidence_text)
    evidence_ids = _extract_evidence_ids(evidence_text)
    if not evidence_ids:
        evidence_ids = _extract_evidence_ids(primary_text)
    if not evidence_ids:
        evidence_ids = list(fallback_ids[:3])
    evidence_ids = evidence_ids[:MASTER_MAX_EVIDENCE_USED_ITEMS]

    evidence_used: List[Dict[str, Any]] = []
    for i, eid in enumerate(evidence_ids):
        note = f"tagged evidence reference {eid}"
        if i < len(evidence_lines):
            note = evidence_lines[i][:MASTER_NOTE_MAX_CHARS]
        evidence_used.append({"evidence_id": eid, "note": note[:MASTER_NOTE_MAX_CHARS], "role": _parse_role(note)})
    if not evidence_used and fallback_ids:
        evidence_used.append(
            {
                "evidence_id": fallback_ids[0],
                "note": "fallback evidence from registry",
                "role": "context",
            }
        )

    allowed_labels = resolve_allowed_mechanism_labels(reasoning_pack, reasoning_config)
    label_map = {str(x).lower(): str(x) for x in allowed_labels}
    primary_label_raw = str(sections.get("PRIMARY_LABEL") or "").strip()
    primary_label = _normalize_primary_label(primary_label_raw, label_map) or "unknown"

    raw_confidence = _parse_first_float(sections.get("PRIMARY_CONFIDENCE"))
    final_confidence, conf_components = _soft_confidence(
        raw_confidence=raw_confidence,
        template_used=template_used,
        reasoning_pack=reasoning_pack,
        reasoning_config=reasoning_config,
    )

    atb_level = _atb_support_level_from_features(reasoning_pack, reasoning_config)
    mechanism_claim = {
        "primary_hypothesis": {
            "mechanism_label": primary_label,
            "aie_rationale_type": template_used,
            "natural_language_mechanism": primary_text or "Insufficient direct evidence; provisional mechanism summary.",
            "atb_support_level": atb_level,
        },
        "confidence": float(round(final_confidence, 4)),
        "reasoning_mode_used": str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "normal"),
    }

    def _ev_one(default_idx: int, default_note: str, default_role: str = "context") -> List[Dict[str, Any]]:
        if evidence_used:
            src = dict(evidence_used[min(default_idx, len(evidence_used) - 1)])
            src["role"] = default_role
            src["note"] = default_note[:MASTER_NOTE_MAX_CHARS]
            return [src]
        if fallback_ids:
            return [{"evidence_id": fallback_ids[0], "note": default_note[:MASTER_NOTE_MAX_CHARS], "role": default_role}]
        return []

    supporting_chain = [
        {
            "step_id": "A",
            "step_name": "torsion_access",
            "claim": "Excited-state structural access is inferred from available aTB cues.",
            "evidence_used": _ev_one(0, "aTB structural cue", "support"),
        },
        {
            "step_id": "B",
            "step_name": "ct_family",
            "claim": "An electronic redistribution or nonradiative-channel hypothesis is updated from current target cues.",
            "evidence_used": _ev_one(1, "channel context cue", "context"),
        },
        {
            "step_id": "C",
            "step_name": "aIE_bridge",
            "claim": "Aggregation/rigidification may suppress nonradiative pathways.",
            "evidence_used": _ev_one(2, "aggregation bridge cue", "context"),
        },
        {
            "step_id": "D",
            "step_name": "discriminators",
            "claim": "Discriminator tests are needed to separate top competing hypotheses.",
            "evidence_used": _ev_one(0, "discriminator context", "context"),
        },
    ]

    competing_hypotheses: List[Dict[str, Any]] = []
    for i, line in enumerate(_parse_lines(competing_text)[:MASTER_MAX_COMPETING_ITEMS]):
        name = line.split(":", 1)[0].strip() or f"alt_hyp_{i+1}"
        line_conf = _parse_first_float(line)
        cand_conf = float(line_conf) if line_conf is not None else float(max(0.1, round(0.35 - 0.1 * i, 3)))
        cand_conf = max(0.0, min(1.0, cand_conf))
        competing_hypotheses.append(
            {
                "name": name[:120],
                "confidence": cand_conf,
                "atb_support_level": atb_level,
                "evidence_used": _ev_one(i, f"competing hypothesis context: {name}", "context"),
            }
        )

    predictions: List[Dict[str, Any]] = []
    prediction_lines = _parse_lines(predictions_text)
    if not prediction_lines:
        prediction_lines = _parse_lines(next_text)
    for i, line in enumerate(prediction_lines[:MASTER_MAX_PREDICTIONS_ITEMS]):
        predictions.append(
            {
                "prediction": line[:180],
                "expected_signal": "discriminator readout",
                "evidence_used": _ev_one(i, "prediction context", "context"),
            }
        )
    while len(predictions) < MASTER_MAX_PREDICTIONS_ITEMS:
        idx = len(predictions) + 1
        predictions.append(
            {
                "prediction": f"discriminator_test_{idx}",
                "expected_signal": "mechanism-separating trend",
                "evidence_used": _ev_one(idx - 1, f"prediction {idx} context", "context"),
            }
        )

    next_lines = _parse_lines(next_text)
    rec_next: List[str] = [x[:120] for x in next_lines[:5]]
    if not rec_next:
        rec_next = ["provide_offline_pdf", "switch_run_lane_offline_pdf"]

    limits = _parse_lines(limits_text)
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    if gate_mode == "conservative":
        limits.append(STANDARD_LIMIT_CONSERVATIVE)
    limits.append("Tagged natural-language output converted to structured master_output.")
    if primary_label == "unknown" and primary_label_raw:
        limits.append(f"PRIMARY_LABEL '{primary_label_raw}' was normalized to unknown (not in allowed mechanism labels).")
    if missing_sections:
        limits.append(f"Tagged response missing required sections: {', '.join(missing_sections)}.")
    limits.append(
        "Confidence is computed from raw PRIMARY_CONFIDENCE via soft penalty (sim/entropy/mode/separation)."
    )
    limits = limits[:6]

    return {
        "status": status,
        "template_used": template_used,
        "mechanism_claim": mechanism_claim,
        "supporting_chain": supporting_chain[:MASTER_MAX_SUPPORTING_CHAIN_ITEMS],
        "competing_hypotheses": competing_hypotheses[:MASTER_MAX_COMPETING_ITEMS],
        "predictions": predictions[:MASTER_MAX_PREDICTIONS_ITEMS],
        "limits": limits,
        "evidence_used": evidence_used[:MASTER_MAX_EVIDENCE_USED_ITEMS],
        "recommended_next_actions": rec_next[:5],
        "__meta": {
            "raw_confidence_from_model": conf_components.get("raw_confidence_from_model"),
            "final_confidence": conf_components.get("final_conf_post_cap"),
            "confidence_components": {
                "base_conf": conf_components.get("base_conf"),
                "sim_factor": conf_components.get("sim_factor"),
                "ent_factor": conf_components.get("ent_factor"),
                "mode_factor": conf_components.get("mode_factor"),
                "neighbor_factor": conf_components.get("neighbor_factor"),
                "final_conf_pre_cap": conf_components.get("final_conf_pre_cap"),
                "final_conf_post_cap": conf_components.get("final_conf_post_cap"),
                "cap_value": conf_components.get("cap_value"),
                "cap_reason": conf_components.get("cap_reason"),
                "r0_penalty_applied": conf_components.get("r0_penalty_applied"),
                "r0_penalty_factor": conf_components.get("r0_penalty_factor"),
            },
            "penalty_components": {
                "base_conf": conf_components.get("base_conf"),
                "sim_factor": conf_components.get("sim_factor"),
                "ent_factor": conf_components.get("ent_factor"),
                "mode_factor": conf_components.get("mode_factor"),
                "neighbor_factor": conf_components.get("neighbor_factor"),
                "final_conf_pre_cap": conf_components.get("final_conf_pre_cap"),
                "final_conf_post_cap": conf_components.get("final_conf_post_cap"),
                "cap_value": conf_components.get("cap_value"),
                "cap_reason": conf_components.get("cap_reason"),
                "r0_penalty_applied": conf_components.get("r0_penalty_applied"),
                "r0_penalty_factor": conf_components.get("r0_penalty_factor"),
            },
            "allowed_mechanism_labels": allowed_labels,
            "missing_required_sections": missing_sections,
            "confidence_formula_version": conf_components.get("confidence_formula_version"),
        },
    }


def _max_budgeted_items(rows: Any, budget: int) -> bool:
    return isinstance(rows, list) and len(rows) <= int(budget)


def _weak_trigger_in_numeric_context(text: str, window_chars: int = 40) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    for m in WEAK_THRESHOLD_TRIGGER_PATTERN.finditer(raw):
        lo = max(0, m.start() - window_chars)
        hi = min(len(raw), m.end() + window_chars)
        snippet = raw[lo:hi]
        if NUMBER_PATTERN.search(snippet):
            return True
        if COMPARISON_PATTERN.search(snippet):
            return True
        if INTERVAL_PATTERN.search(snippet):
            return True
        if NUMERIC_CONTEXT_TOKEN_PATTERN.search(snippet):
            return True
    return False


def _risk_scores_subset(
    case_json: Dict[str, Any],
    *,
    include_neighbor_summary: bool,
    include_neighbor_feature_rows: bool,
) -> Dict[str, Any]:
    src = case_json.get("risk_scores") or {}
    keep = [
        "top1_sim",
        "mean_topk_sim",
        "novelty_struct",
        "mechanism_entropy",
        "atb_neighbor_consistency",
        "structure_fact_sheet",
        "prior_reliability_profile",
        "candidate_slate_v2",
        "structure_prior_profile",
        "structure_motif_profile",
        "structure_retrieval_profile",
        "structure_candidate_distribution",
    ]
    if include_neighbor_summary and include_neighbor_feature_rows:
        keep.append("atb_neighbor_features_all")
    out = {k: src.get(k) for k in keep if k in src}
    # Keep mechanism hint in case for debug/routing only; exclude from master reasoning pack.
    out.pop("mechanism_hint", None)
    out.pop("hint_confidence", None)
    if include_neighbor_summary and include_neighbor_feature_rows:
        out["atb_neighbor_features_all"] = compact_neighbor_atb_rows(src.get("atb_neighbor_features_all"))
    elif "atb_neighbor_features_all" in out:
        out["atb_neighbor_features_all"] = []
    return out


def _sanitize_features_summary_for_reasoning(features_summary: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(features_summary, dict):
        return None
    out = deepcopy(features_summary)
    raw_delta_dipole = out.get("delta_dipole")
    if isinstance(raw_delta_dipole, dict):
        out.pop("delta_dipole", None)
    return out


def _evidence_readiness_subset(
    case_json: Dict[str, Any],
    *,
    include_target_atb_summary: bool,
    include_target_atb_full: bool,
    include_literature_status: bool,
    include_experiment_status: bool,
) -> Dict[str, Any]:
    er = case_json.get("evidence_readiness") or {}
    atb = er.get("atb") or {}
    lit = er.get("literature") or {}
    exp = er.get("experiment") or {}
    return {
        "atb": {
            "cache_status": atb.get("cache_status"),
            "features_summary": _sanitize_features_summary_for_reasoning(atb.get("features_summary"))
            if include_target_atb_summary
            else None,
            "features": atb.get("features") if include_target_atb_full else None,
            "missing_fields": atb.get("missing_fields"),
        },
        "literature": (
            {
                "status": lit.get("status"),
                "notes": lit.get("notes"),
            }
            if include_literature_status
            else {"status": None, "notes": None}
        ),
        "experiment": (
            {
                "status": exp.get("status"),
                "notes": exp.get("notes"),
            }
            if include_experiment_status
            else {"status": None, "notes": None}
        ),
    }


def _neighbors_topk(case_json: Dict[str, Any], k: int = 10) -> List[Dict[str, Any]]:
    rows = []
    for idx, n in enumerate((case_json.get("neighbors") or [])[:k]):
        if not isinstance(n, dict):
            continue
        rows.append(
            {
                "case_index": idx,
                "rank": n.get("rank"),
                "sim": n.get("sim"),
                "neighbor_inchikey": n.get("neighbor_inchikey"),
                "neighbor_mechanism_label": n.get("neighbor_mechanism_label"),
            }
        )
    return rows


def _neighbor_label_lookup(case_json: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in case_json.get("neighbors") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("neighbor_mechanism_label") or "").strip()
        if not label:
            continue
        inchikey = str(row.get("neighbor_inchikey") or "").strip()
        if inchikey:
            out[inchikey] = label
        rank = row.get("rank")
        if isinstance(rank, int):
            out[f"rank:{rank}"] = label
    return out


def _mechanism_context(case_json: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "candidate_mechanisms_top3": [],
        "candidate_mechanisms_topk": [],
        "mechanism_signatures_top3": [],
    }
    candidates = case_json.get("candidate_mechanisms")
    if isinstance(candidates, list):
        for idx, row in enumerate(candidates[:5]):
            if not isinstance(row, dict):
                continue
            payload = {
                "mechanism_id": row.get("mechanism_id") or row.get("label") or row.get("name"),
                "probability": row.get("probability") or row.get("confidence"),
            }
            out["candidate_mechanisms_topk"].append(payload)
            if idx < 3:
                out["candidate_mechanisms_top3"].append(payload)
    if not out["candidate_mechanisms_top3"]:
        slate_v2 = (case_json.get("risk_scores") or {}).get("candidate_slate_v2") or {}
        structure_dist = slate_v2.get("top_candidates") or slate_v2.get("top3") or []
        if isinstance(structure_dist, list):
            for idx, row in enumerate(structure_dist[:5]):
                if not isinstance(row, dict):
                    continue
                payload = {
                    "mechanism_id": row.get("label") or row.get("mechanism_id") or row.get("name"),
                    "probability": row.get("prob") or row.get("probability") or row.get("confidence"),
                }
                out["candidate_mechanisms_topk"].append(payload)
                if idx < 3:
                    out["candidate_mechanisms_top3"].append(payload)
    if not out["candidate_mechanisms_top3"]:
        structure_candidate_dist = (case_json.get("risk_scores") or {}).get("structure_candidate_distribution") or {}
        structure_dist = (
            structure_candidate_dist.get("top_candidates")
            or structure_candidate_dist.get("top3")
            or []
        )
        if isinstance(structure_dist, list):
            for idx, row in enumerate(structure_dist[:5]):
                if not isinstance(row, dict):
                    continue
                payload = {
                    "mechanism_id": row.get("label") or row.get("mechanism_id") or row.get("name"),
                    "probability": row.get("prob") or row.get("probability") or row.get("confidence"),
                }
                out["candidate_mechanisms_topk"].append(payload)
                if idx < 3:
                    out["candidate_mechanisms_top3"].append(payload)

    signatures = case_json.get("mechanism_signatures")
    if isinstance(signatures, dict):
        for i, (name, val) in enumerate(signatures.items()):
            if i >= 3:
                break
            out["mechanism_signatures_top3"].append(
                {
                    "name": str(name),
                    "signature": _truncate_text(val, max_chars=300),
                }
            )
    return out


def _build_evidence_registry(
    case_json: Dict[str, Any],
    neighbors_topk: Sequence[Dict[str, Any]],
    *,
    structure_prior_profile: Optional[Dict[str, Any]],
    structure_motif_profile: Optional[Dict[str, Any]],
    structure_fact_sheet: Optional[Dict[str, Any]],
    prior_reliability_profile: Optional[Dict[str, Any]],
    candidate_slate_v2: Optional[Dict[str, Any]],
    structure_retrieval_profile: Optional[Dict[str, Any]],
    structure_candidate_distribution: Optional[Dict[str, Any]],
    emission_observation_profile: Optional[Dict[str, Any]],
    use_r0_prior_stack: bool,
    include_target_atb_signals: bool,
    atb_trend_profile: Optional[Dict[str, Any]],
    charge_redistribution_profile: Optional[Dict[str, Any]],
    atb_ct_proxy_profile: Optional[Dict[str, Any]],
    atb_structural_relaxation_profile: Optional[Dict[str, Any]],
    atb_shape_rigidity_profile: Optional[Dict[str, Any]],
    include_literature_status: bool,
    include_experiment_status: bool,
    include_atb_trends_self: bool,
    atb_trends_self: Optional[Dict[str, Any]],
    include_neighbor_atb_stats: bool,
    neighbor_atb_stats: Optional[Dict[str, Any]],
    max_items: int = 24,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    def _add(path: str, label: str, role_hint: str, note_hint: str) -> None:
        if any(row.get("case_path") == path for row in entries):
            return
        found, value = _resolve_pointer(case_json, path)
        if not found or _is_empty_value(value):
            return
        entries.append(
            {
                "source_type": "case",
                "case_path": path,
                "label": label,
                "value_preview": value,
                "role_hint": role_hint,
                "note_hint": note_hint,
            }
        )

    target_features_summary = (((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    if not isinstance(target_features_summary, dict):
        target_features_summary = {}

    # Gate core
    _add("/current_gate/state", "gate state", "context", "gate state")
    _add("/current_gate/reasoning_mode", "gate reasoning mode", "context", "reasoning mode")
    _add("/current_gate/reason", "gate reason", "context", "gate rationale")

    # Risk priors
    _add("/risk_scores/top1_sim", "top1 similarity", "context", "closest-neighbor similarity prior")
    _add("/risk_scores/mean_topk_sim", "mean top-k similarity", "context", "local neighborhood density")
    _add("/risk_scores/mechanism_entropy", "neighbor mechanism entropy", "context", "neighbor label uncertainty")
    _add("/risk_scores/novelty_struct", "structural novelty", "context", "structural novelty score")
    _add("/risk_scores/structure_prior_profile/overall_structure_prior", "overall structure prior", "context", "structure-only prior summary")

    # aTB evidence keys (R1+ by default; excluded from R0 prior-only stage).
    if include_target_atb_signals:
        _add("/evidence_readiness/atb/cache_status", "aTB cache status", "context", "target aTB cache readiness")
        _add(
            "/evidence_readiness/atb/features_summary/delta_dihedral",
            "aTB delta dihedral",
            "support",
            "excited-state torsional accessibility",
        )
        _add(
            "/evidence_readiness/atb/features_summary/delta_gap",
            "aTB delta gap",
            "context",
            "electronic redistribution context",
        )
        _add(
            "/evidence_readiness/atb/features_summary/delta_volume",
            "aTB delta volume",
            "context",
            "packing/rigidification proxy",
        )
        _add(
            "/evidence_readiness/atb/features_summary/excitation_energy",
            "aTB excitation energy",
            "context",
            "excited-state energy context",
        )

    # Neighbors: top-2 sim + label as prior
    for i, row in enumerate(neighbors_topk[:2]):
        if not isinstance(row, dict):
            continue
        case_index = row.get("case_index")
        if not isinstance(case_index, int):
            continue
        _add(
            f"/neighbors/{case_index}/sim",
            f"neighbor {i+1} similarity",
            "context",
            f"neighbor {i+1} prior similarity",
        )
        _add(
            f"/neighbors/{case_index}/neighbor_mechanism_label",
            f"neighbor {i+1} mechanism label",
            "context",
            f"neighbor {i+1} mechanism prior label",
        )

    # Downstream status signals
    if include_literature_status:
        _add("/evidence_readiness/literature/status", "literature status", "context", "literature readiness status")
    if include_experiment_status:
        _add("/evidence_readiness/experiment/status", "experiment status", "context", "experiment readiness status")

    reg: List[Dict[str, Any]] = []
    for idx, row in enumerate(entries[:max_items], start=1):
        reg.append(
            {
                "evidence_id": f"E{idx}",
                "source_type": row.get("source_type") or "case",
                "case_path": row["case_path"],
                "label": row["label"],
                "value_preview": row["value_preview"],
                "role_hint": row["role_hint"],
                "note_hint": row["note_hint"],
            }
        )

    if isinstance(atb_trend_profile, dict):
        buckets = atb_trend_profile.get("buckets") if isinstance(atb_trend_profile.get("buckets"), dict) else {}
        direction = atb_trend_profile.get("direction") if isinstance(atb_trend_profile.get("direction"), dict) else {}
        reliability = str(atb_trend_profile.get("reliability") or "unknown")
        motion = str(atb_trend_profile.get("overall_motion_proxy") or "unknown")
        reg.extend(
            [
                {
                    "evidence_id": "E31",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_dihedral",
                    "label": "aTB torsion trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_dihedral"),
                        "direction": direction.get("delta_dihedral"),
                    },
                    "role_hint": "support",
                    "note_hint": f"torsion trend bucket={buckets.get('delta_dihedral')} direction={direction.get('delta_dihedral')}",
                },
                {
                    "evidence_id": "E32",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_gap",
                    "label": "aTB electronic redistribution trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_gap"),
                        "direction": direction.get("delta_gap"),
                    },
                    "role_hint": "context",
                    "note_hint": f"redistribution bucket={buckets.get('delta_gap')} direction={direction.get('delta_gap')}",
                },
                {
                    "evidence_id": "E33",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_volume",
                    "label": "aTB volume trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_volume"),
                        "direction": direction.get("delta_volume"),
                    },
                    "role_hint": "context",
                    "note_hint": f"volume trend bucket={buckets.get('delta_volume')} direction={direction.get('delta_volume')}",
                },
                {
                    "evidence_id": "E34",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/atb_trend_profile/overall_motion_proxy",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_dihedral",
                        "/evidence_readiness/atb/features_summary/delta_gap",
                        "/evidence_readiness/atb/features_summary/delta_volume",
                    ],
                    "label": "aTB overall motion proxy",
                    "value_preview": {"overall_motion_proxy": motion, "reliability": reliability},
                    "role_hint": "context",
                    "note_hint": "self-only motion proxy from bucketized aTB trend profile",
                },
            ]
        )

    def _append_registry_entry(row: Dict[str, Any]) -> None:
        preview = row.get("value_preview")
        if _is_empty_value(preview):
            return
        reg.append(row)

    if isinstance(emission_observation_profile, dict) and str(emission_observation_profile.get("coverage") or "none") != "none":
        aggr_val = ((case_json.get("target_fields") or {}).get("emission_aggr_nm"))
        solid_val = ((case_json.get("target_fields") or {}).get("emission_solid_or_film_nm"))
        if not _is_empty_value(aggr_val):
            _append_registry_entry(
                {
                    "evidence_id": "E70",
                    "source_type": "case",
                    "case_path": "/target_fields/emission_aggr_nm",
                    "label": "aggregate-state emission observation",
                    "value_preview": {"emission_aggr_nm": aggr_val},
                    "role_hint": "support",
                    "note_hint": "target aggregate-state emission observation",
                }
            )
        if not _is_empty_value(solid_val):
            _append_registry_entry(
                {
                    "evidence_id": "E71",
                    "source_type": "case",
                    "case_path": "/target_fields/emission_solid_or_film_nm",
                    "label": "solid/film emission observation",
                    "value_preview": {"emission_solid_or_film_nm": solid_val},
                    "role_hint": "support",
                    "note_hint": "target solid/film emission observation",
                }
            )
        _append_registry_entry(
            {
                "evidence_id": "E72",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/emission_observation_profile",
                "derived_from_case_paths": [
                    "/target_fields/emission_aggr_nm",
                    "/target_fields/emission_solid_or_film_nm",
                ],
                "label": "emission shift summary",
                "value_preview": {
                    "coverage": emission_observation_profile.get("coverage"),
                    "shift_nm": emission_observation_profile.get("shift_nm"),
                    "shift_direction": emission_observation_profile.get("shift_direction"),
                    "shift_magnitude_bucket": emission_observation_profile.get("shift_magnitude_bucket"),
                },
                "role_hint": "support",
                "note_hint": "compact target emission observation shift summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E73",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/emission_observation_profile",
                "derived_from_case_paths": [
                    "/target_fields/emission_aggr_nm",
                    "/target_fields/emission_solid_or_film_nm",
                    "/target_fields_provenance/emission_aggr_nm",
                    "/target_fields_provenance/emission_solid_or_film_nm",
                ],
                "label": "emission observation reliability summary",
                "value_preview": {
                    "coverage": emission_observation_profile.get("coverage"),
                    "reliability": emission_observation_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "emission observation coverage and provenance reliability summary",
            }
        )

    if isinstance(charge_redistribution_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E35",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/charge_redistribution_profile/redistribution_magnitude_bucket",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/charge_redis_total_abs",
                    "/evidence_readiness/atb/features_summary/charge_redis_top3_abs_share",
                    "/evidence_readiness/atb/features_summary/charge_redis_heteroatom_abs_share",
                ],
                "label": "aTB electronic redistribution magnitude cue",
                "value_preview": {
                    "source": charge_redistribution_profile.get("source"),
                    "redistribution_magnitude_bucket": charge_redistribution_profile.get("redistribution_magnitude_bucket"),
                    "redistribution_localization": charge_redistribution_profile.get("redistribution_localization"),
                    "heteroatom_involvement": charge_redistribution_profile.get("heteroatom_involvement"),
                    "reliability": charge_redistribution_profile.get("reliability"),
                },
                "role_hint": "support",
                "note_hint": "compact target-only electronic redistribution magnitude cue",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E36",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/charge_redistribution_profile/redistribution_score",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/charge_redis_total_abs",
                    "/evidence_readiness/atb/features_summary/delta_gap",
                ],
                "label": "aTB electronic redistribution summary",
                "value_preview": {
                    "redistribution_score": charge_redistribution_profile.get("redistribution_score"),
                    "delta_gap_bucket": charge_redistribution_profile.get("delta_gap_bucket"),
                    "source": charge_redistribution_profile.get("source"),
                    },
                "role_hint": "support",
                "note_hint": "compact electronic redistribution summary",
            }
        )

    if isinstance(atb_structural_relaxation_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E37",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/atb_structural_relaxation_profile/relaxation_proxy_score",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/delta_dihedral",
                    "/evidence_readiness/atb/features_summary/delta_bonds",
                    "/evidence_readiness/atb/features_summary/delta_angles",
                    "/evidence_readiness/atb/features_summary/delta_volume",
                ],
                "label": "aTB structural relaxation summary",
                "value_preview": {
                    "relaxation_proxy_score": atb_structural_relaxation_profile.get("relaxation_proxy_score"),
                    "delta_dihedral_bucket": ((atb_structural_relaxation_profile.get("buckets") or {}).get("delta_dihedral")),
                    "delta_volume_bucket": ((atb_structural_relaxation_profile.get("buckets") or {}).get("delta_volume")),
                },
                "role_hint": "support",
                "note_hint": "combined structural relaxation from torsion, bonds, angles, and volume",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E38",
                "source_type": "case",
                "case_path": "/evidence_readiness/atb/features_summary/exciting_path_mean_volume",
                "label": "aTB excited-path volume cue",
                "value_preview": {
                    "exciting_path_mean_volume": (
                        ((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary", {})
                    ).get("exciting_path_mean_volume"),
                },
                "role_hint": "context",
                "note_hint": "excited-path volume cue from cached aTB output",
            }
        )

    if isinstance(atb_shape_rigidity_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E39",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/atb_shape_rigidity_profile/rigidity_proxy",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/s0_rays_asymmetry_parameter",
                    "/evidence_readiness/atb/features_summary/s1_rays_asymmetry_parameter",
                    "/evidence_readiness/atb/features_summary/s0_rotational_constant_a",
                    "/evidence_readiness/atb/features_summary/s1_rotational_constant_a",
                ],
                "label": "aTB shape-rigidity summary",
                "value_preview": {
                    "rigidity_proxy": atb_shape_rigidity_profile.get("rigidity_proxy"),
                    "reliability": atb_shape_rigidity_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "auxiliary shape/rigidity cue from asymmetry and rotational changes",
            }
        )

    if include_target_atb_signals:
        def _fs_num(name: str) -> Optional[float]:
            value = target_features_summary.get(name)
            if value is None:
                return None
            try:
                out = float(value)
            except (TypeError, ValueError):
                return None
            if out != out:
                return None
            return out

        aop_reliability = _fs_num("aop_compact_reliability_score")
        s1_electric_dip = _fs_num("s1_transition_electric_dip_au")
        s1_osc = _fs_num("s1_oscillator_strength_f")
        s1_wave = _fs_num("s1_excitation_wavelength_nm")
        delta_perm = _fs_num("delta_perm_dipole_tot_debye")
        s1_rotatory = _fs_num("s1_rotatory_strength_cgs")

        if s1_electric_dip is not None:
            _append_registry_entry(
                {
                    "evidence_id": "E60",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/s1_transition_electric_dip_au",
                    "label": "aTB S1 transition electric dipole cue",
                    "value_preview": {
                        "s1_transition_electric_dip_au": s1_electric_dip,
                        "aop_reliability_score": aop_reliability,
                    },
                    "role_hint": "context",
                    "note_hint": "compact S1 transition electric dipole magnitude from final excit aop block",
                }
            )
        if s1_osc is not None:
            _append_registry_entry(
                {
                    "evidence_id": "E61",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/s1_oscillator_strength_f",
                    "label": "aTB S1 oscillator/excitation cue",
                    "value_preview": {
                        "s1_oscillator_strength_f": s1_osc,
                        "s1_excitation_wavelength_nm": s1_wave,
                        "aop_reliability_score": aop_reliability,
                    },
                    "role_hint": "context",
                    "note_hint": "compact S1 oscillator and excitation-wavelength cue from final excit aop block",
                }
            )
        if delta_perm is not None:
            _append_registry_entry(
                {
                    "evidence_id": "E62",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_perm_dipole_tot_debye",
                    "label": "aTB permanent dipole delta cue",
                    "value_preview": {
                        "delta_perm_dipole_tot_debye": delta_perm,
                        "aop_reliability_score": aop_reliability,
                    },
                    "role_hint": "context",
                    "note_hint": "compact S0/S1 permanent dipole delta from final opt/excit aop blocks",
                }
            )
        if s1_rotatory is not None:
            _append_registry_entry(
                {
                    "evidence_id": "E63",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/s1_rotatory_strength_cgs",
                    "label": "aTB S1 rotatory-strength cue",
                    "value_preview": {
                        "s1_rotatory_strength_cgs": s1_rotatory,
                        "aop_reliability_score": aop_reliability,
                    },
                    "role_hint": "context",
                    "note_hint": "compact S1 rotatory-strength cue from final excit aop block",
                }
            )

    if use_r0_prior_stack and isinstance(structure_fact_sheet, dict):
        _append_registry_entry(
            {
                "evidence_id": "E50",
                "source_type": "case",
                "case_path": "/risk_scores/structure_fact_sheet/donor_acceptor_fragment_balance",
                "label": "R0 structure fact: donor-acceptor topology",
                "value_preview": {
                    "donor_acceptor_fragment_balance": structure_fact_sheet.get("donor_acceptor_fragment_balance"),
                    "donor_acceptor_separation_regime": structure_fact_sheet.get("donor_acceptor_separation_regime"),
                    "reliability": structure_fact_sheet.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "phenomenon-level donor/acceptor topology fact sheet entry",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E51",
                "source_type": "case",
                "case_path": "/risk_scores/structure_fact_sheet/intramolecular_hbond_geometry",
                "label": "R0 structure fact: local proton-transfer geometry",
                "value_preview": {
                    "intramolecular_hbond_geometry": structure_fact_sheet.get("intramolecular_hbond_geometry"),
                    "proton_transfer_local_geometry": structure_fact_sheet.get("proton_transfer_local_geometry"),
                },
                "role_hint": "context",
                "note_hint": "local H-bond and proton-transfer geometry fact sheet entry",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E52",
                "source_type": "case",
                "case_path": "/risk_scores/structure_fact_sheet/tautomerizable_subgraph_strength",
                "label": "R0 structure fact: tautomer and proton-transfer topology",
                "value_preview": {
                    "tautomerizable_subgraph_strength": structure_fact_sheet.get("tautomerizable_subgraph_strength"),
                    "proton_transfer_topology_candidate": structure_fact_sheet.get("proton_transfer_topology_candidate"),
                },
                "role_hint": "context",
                "note_hint": "tautomerizable subgraph and proton-transfer topology fact sheet entry",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E53",
                "source_type": "case",
                "case_path": "/risk_scores/structure_fact_sheet/aromatic_core_connectivity",
                "label": "R0 structure fact: aromatic-core connectivity",
                "value_preview": {
                    "aromatic_core_connectivity": structure_fact_sheet.get("aromatic_core_connectivity"),
                    "fused_aromatic_core_strength": structure_fact_sheet.get("fused_aromatic_core_strength"),
                    "conjugation_continuity": structure_fact_sheet.get("conjugation_continuity"),
                },
                "role_hint": "context",
                "note_hint": "aromatic-core connectivity and conjugation continuity fact sheet entry",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E54",
                "source_type": "case",
                "case_path": "/risk_scores/structure_fact_sheet/global_flexibility_vs_core_rigidity",
                "label": "R0 structure fact: rigidity and planarity summary",
                "value_preview": {
                    "global_flexibility_vs_core_rigidity": structure_fact_sheet.get("global_flexibility_vs_core_rigidity"),
                    "planarity_proxy": structure_fact_sheet.get("planarity_proxy"),
                    "heteroatom_cluster_pattern": structure_fact_sheet.get("heteroatom_cluster_pattern"),
                },
                "role_hint": "context",
                "note_hint": "global flexibility/rigidity and planarity fact sheet entry",
            }
        )
        if isinstance(prior_reliability_profile, dict):
            _append_registry_entry(
                {
                    "evidence_id": "E55",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/prior_reliability_profile",
                    "label": "R0 prior reliability summary",
                    "value_preview": {
                        "feature_consensus_strength": prior_reliability_profile.get("feature_consensus_strength"),
                        "scaffold_consensus_strength": prior_reliability_profile.get("scaffold_consensus_strength"),
                        "neighbor_consensus_strength": prior_reliability_profile.get("neighbor_consensus_strength"),
                        "cross_source_agreement": prior_reliability_profile.get("cross_source_agreement"),
                        "prior_reliability": prior_reliability_profile.get("prior_reliability"),
                        "ambiguity_level": prior_reliability_profile.get("ambiguity_level"),
                    },
                    "role_hint": "context",
                    "note_hint": "R0 prior reliability summary from feature, scaffold, and ECFP agreement",
                }
            )
        if isinstance(candidate_slate_v2, dict):
            _append_registry_entry(
                {
                    "evidence_id": "E56",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/candidate_slate_v2",
                    "label": "R0 candidate slate summary",
                    "value_preview": {
                        "top_candidates": candidate_slate_v2.get("top_candidates"),
                        "slate_confidence": candidate_slate_v2.get("slate_confidence"),
                    },
                    "role_hint": "context",
                    "note_hint": "R0 candidate slate synthesized from structure facts and prior reliability",
                }
            )
    elif isinstance(structure_prior_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E40",
                "source_type": "case",
                "case_path": "/risk_scores/structure_prior_profile/donor_acceptor_topology",
                "label": "donor-acceptor topology summary",
                "value_preview": {
                    "donor_acceptor_topology": structure_prior_profile.get("donor_acceptor_topology"),
                    "reliability": structure_prior_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "structure-prior topology cue from canonical SMILES and RDKit descriptors",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E41",
                "source_type": "case",
                "case_path": "/risk_scores/structure_prior_profile/intramolecular_hbond_candidates",
                "label": "intramolecular H-bond candidate summary",
                "value_preview": {
                    "intramolecular_hbond_candidates": structure_prior_profile.get("intramolecular_hbond_candidates"),
                },
                "role_hint": "context",
                "note_hint": "structure-prior H-bond candidate cue",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E42",
                "source_type": "case",
                "case_path": "/risk_scores/structure_prior_profile/aromatic_core_density",
                "label": "aromatic-core and conjugation summary",
                "value_preview": {
                    "aromatic_core_density": structure_prior_profile.get("aromatic_core_density"),
                    "conjugation_proxy": structure_prior_profile.get("conjugation_proxy"),
                },
                "role_hint": "context",
                "note_hint": "structure-prior aromatic-core/conjugation cue",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E43",
                "source_type": "case",
                "case_path": "/risk_scores/structure_prior_profile/flexibility_proxy",
                "label": "flexibility summary",
                "value_preview": {
                    "flexibility_proxy": structure_prior_profile.get("flexibility_proxy"),
                },
                "role_hint": "context",
                "note_hint": "structure-prior flexibility cue from rotatable-bond and topology summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E44",
                "source_type": "case",
                "case_path": "/risk_scores/structure_prior_profile/overall_structure_prior",
                "label": "overall structure prior summary",
                "value_preview": {
                    "overall_structure_prior": structure_prior_profile.get("overall_structure_prior"),
                    "reliability": structure_prior_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "structure-prior overall summary",
            }
        )

    if not use_r0_prior_stack and isinstance(structure_motif_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E50",
                "source_type": "case",
                "case_path": "/risk_scores/structure_motif_profile/donor_acceptor_path_strength",
                "label": "donor-acceptor path summary",
                "value_preview": {
                    "donor_acceptor_path_strength": structure_motif_profile.get("donor_acceptor_path_strength"),
                    "reliability": structure_motif_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "structure-motif donor/acceptor path summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E51",
                "source_type": "case",
                "case_path": "/risk_scores/structure_motif_profile/intramolecular_hbond_motif",
                "label": "intramolecular H-bond motif summary",
                "value_preview": {
                    "intramolecular_hbond_motif": structure_motif_profile.get("intramolecular_hbond_motif"),
                    "possible_intramolecular_hbond_pairs": structure_motif_profile.get("possible_intramolecular_hbond_pairs"),
                },
                "role_hint": "context",
                "note_hint": "structure-motif intramolecular H-bond summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E52",
                "source_type": "case",
                "case_path": "/risk_scores/structure_motif_profile/tautomerizable_motif",
                "label": "tautomerizable motif summary",
                "value_preview": {
                    "tautomerizable_motif": structure_motif_profile.get("tautomerizable_motif"),
                    "tautomerizable_motif_candidates": structure_motif_profile.get("tautomerizable_motif_candidates"),
                },
                "role_hint": "context",
                "note_hint": "structure-motif tautomerizable substructure summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E53",
                "source_type": "case",
                "case_path": "/risk_scores/structure_motif_profile/aromatic_scaffold_type",
                "label": "aromatic and conjugation scaffold summary",
                "value_preview": {
                    "aromatic_scaffold_type": structure_motif_profile.get("aromatic_scaffold_type"),
                    "conjugation_span_bucket": structure_motif_profile.get("conjugation_span_bucket"),
                    "fused_aromatic_core": structure_motif_profile.get("fused_aromatic_core"),
                },
                "role_hint": "context",
                "note_hint": "structure-motif aromatic scaffold and conjugation summary",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E54",
                "source_type": "case",
                "case_path": "/risk_scores/structure_motif_profile/flexibility_regime",
                "label": "flexibility and rigidity summary",
                "value_preview": {
                    "flexibility_regime": structure_motif_profile.get("flexibility_regime"),
                    "motif_density": structure_motif_profile.get("motif_density"),
                    "reliability": structure_motif_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "structure-motif flexibility and rigidity summary",
            }
        )

    if not use_r0_prior_stack and isinstance(structure_retrieval_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E55",
                "source_type": "case",
                "case_path": "/risk_scores/structure_retrieval_profile/retrieval_consensus_strength",
                "label": "structure retrieval prior summary",
                "value_preview": {
                    "retrieval_consensus_strength": structure_retrieval_profile.get("retrieval_consensus_strength"),
                    "feature_neighbor_label_distribution": structure_retrieval_profile.get("feature_neighbor_label_distribution"),
                    "scaffold_neighbor_label_distribution": structure_retrieval_profile.get("scaffold_neighbor_label_distribution"),
                },
                "role_hint": "context",
                "note_hint": "structure retrieval prior from feature and scaffold neighbors",
            }
        )

    if not use_r0_prior_stack and isinstance(structure_candidate_distribution, dict):
        candidate_case_path = "/risk_scores/structure_candidate_distribution/top3"
        candidate_preview = structure_candidate_distribution.get("top3")
        if isinstance(structure_candidate_distribution.get("top_candidates"), list):
            candidate_case_path = "/risk_scores/structure_candidate_distribution/top_candidates"
            candidate_preview = structure_candidate_distribution.get("top_candidates")
        _append_registry_entry(
            {
                "evidence_id": "E56",
                "source_type": "case",
                "case_path": candidate_case_path,
                "label": "structure candidate distribution summary",
                "value_preview": {
                    "top_candidates": candidate_preview,
                    "top3": structure_candidate_distribution.get("top3"),
                    "calibration": structure_candidate_distribution.get("calibration"),
                },
                "role_hint": "context",
                "note_hint": "structure candidate distribution summary from calibrated structure prior model",
            }
        )

    if include_atb_trends_self and isinstance(atb_trends_self, dict):
        if bool(atb_trends_self.get("enabled")) or str(atb_trends_self.get("reliability") or "").lower() in {"low", "medium", "high"}:
            reg_seed = [
                (
                    "E_ATB_TREND_1",
                    "/risk_scores/atb_trends_self/delta_dihedral_bucket",
                    "aTB self trend: delta_dihedral",
                    {
                        "delta_dihedral_abs_deg": atb_trends_self.get("delta_dihedral_abs_deg"),
                        "delta_dihedral_bucket": atb_trends_self.get("delta_dihedral_bucket"),
                        "delta_dihedral_direction": atb_trends_self.get("delta_dihedral_direction"),
                        "delta_dihedral_percentile_global": atb_trends_self.get("delta_dihedral_percentile_global"),
                    },
                    "support",
                    "Target-only torsional self trend bucket.",
                ),
                (
                    "E_ATB_TREND_2",
                    "/risk_scores/atb_trends_self/delta_gap_bucket",
                    "aTB self trend: delta_gap",
                    {
                        "delta_gap_direction": atb_trends_self.get("delta_gap_direction"),
                        "delta_gap_bucket": atb_trends_self.get("delta_gap_bucket"),
                        "delta_gap_percentile_global": atb_trends_self.get("delta_gap_percentile_global"),
                    },
                    "context",
                    "Target-only gap trend direction and magnitude bucket.",
                ),
                (
                    "E_ATB_TREND_3",
                    "/risk_scores/atb_trends_self/delta_volume_bucket",
                    "aTB self trend: delta_volume",
                    {
                        "delta_volume_direction": atb_trends_self.get("delta_volume_direction"),
                        "delta_volume_bucket": atb_trends_self.get("delta_volume_bucket"),
                        "delta_volume_percentile_global": atb_trends_self.get("delta_volume_percentile_global"),
                    },
                    "context",
                    "Target-only volume trend direction and magnitude bucket.",
                ),
                (
                    "E_ATB_TREND_4",
                    "/risk_scores/atb_trends_self/overall_motion_proxy",
                    "aTB self trend: overall motion proxy",
                    {
                        "overall_motion_proxy": atb_trends_self.get("overall_motion_proxy"),
                        "reliability": atb_trends_self.get("reliability"),
                    },
                    "context",
                    "Self-trend summary reliability and motion proxy.",
                ),
            ]
            for evidence_id, pack_path, label, value_preview, role_hint, note_hint in reg_seed:
                reg.append(
                    {
                        "evidence_id": evidence_id,
                        "source_type": "derived_pack",
                        "pack_path": pack_path,
                        "derived_from_case_paths": [
                            "/evidence_readiness/atb/features_summary/delta_dihedral",
                            "/evidence_readiness/atb/features_summary/delta_gap",
                            "/evidence_readiness/atb/features_summary/delta_volume",
                            "/evidence_readiness/atb/features_summary/excitation_energy",
                        ],
                        "label": label,
                        "value_preview": value_preview,
                        "role_hint": role_hint,
                        "note_hint": note_hint,
                    }
                )

    if include_neighbor_atb_stats and isinstance(neighbor_atb_stats, dict):
        fields = neighbor_atb_stats.get("fields") if isinstance(neighbor_atb_stats.get("fields"), dict) else {}
        reliability = str(neighbor_atb_stats.get("reliability") or "").strip()
        by_label = neighbor_atb_stats.get("by_label") if isinstance(neighbor_atb_stats.get("by_label"), dict) else {}

        def _preview(field_name: str) -> Optional[Dict[str, Any]]:
            row = fields.get(field_name)
            if not isinstance(row, dict):
                return None
            preview = {
                "target": row.get("target"),
                "neighbors_median": row.get("neighbors_median"),
                "neighbors_iqr": row.get("neighbors_iqr"),
                "target_percentile": row.get("target_percentile"),
                "z_robust": row.get("z_robust"),
            }
            if all(v is None for v in preview.values()):
                return None
            return preview

        e21 = _preview("abs_delta_dihedral")
        if e21 is not None:
            reg.append(
                {
                    "evidence_id": "E21",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/fields/abs_delta_dihedral",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_dihedral",
                        "/risk_scores/atb_neighbor_features_all",
                    ],
                    "label": "target abs_delta_dihedral vs neighbor distribution",
                    "value_preview": e21,
                    "role_hint": "support",
                    "note_hint": "R2 comparative torsional evidence",
                }
            )

        e22 = _preview("delta_gap")
        if e22 is not None:
            reg.append(
                {
                    "evidence_id": "E22",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/fields/delta_gap",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_gap",
                        "/risk_scores/atb_neighbor_features_all",
                    ],
                    "label": "target delta_gap vs neighbor distribution",
                    "value_preview": e22,
                    "role_hint": "context",
                    "note_hint": "R2 comparative electronic-redistribution context",
                }
            )

        if by_label:
            reg.append(
                {
                    "evidence_id": "E23",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/by_label",
                    "derived_from_case_paths": ["/risk_scores/atb_neighbor_features_all"],
                    "label": "label-stratified neighbor aTB comparison",
                    "value_preview": by_label,
                    "role_hint": "context",
                    "note_hint": "R2 label-conditioned neighbor comparison",
                }
            )

        if reliability:
            reg.append(
                {
                    "evidence_id": "E24",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/reliability",
                    "derived_from_case_paths": ["/risk_scores/atb_neighbor_features_all"],
                    "label": "neighbor comparative reliability",
                    "value_preview": {
                        "reliability": reliability,
                        "sample_size": neighbor_atb_stats.get("sample_size"),
                        "separation_score": neighbor_atb_stats.get("separation_score"),
                    },
                    "role_hint": "context",
                    "note_hint": "R2 comparative reliability level",
                }
            )
    reg = _compact_registry(
        reg,
        max_items=max_items,
        prefer_comparative=include_neighbor_atb_stats and isinstance(neighbor_atb_stats, dict),
    )
    return reg


def _registry_map(evidence_registry: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(evidence_registry, dict):
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in evidence_registry.items():
            if isinstance(v, dict):
                evidence_id = str(v.get("evidence_id") or k)
                out[evidence_id] = dict(v)
        return out
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(evidence_registry, list):
        for row in evidence_registry:
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("evidence_id") or "").strip()
            if not evidence_id:
                continue
            out[evidence_id] = dict(row)
    return out


def _compact_registry(
    reg: List[Dict[str, Any]],
    *,
    max_items: int,
    prefer_comparative: bool,
) -> List[Dict[str, Any]]:
    if len(reg) <= max_items:
        return reg
    id_to_row: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []
    for row in reg:
        eid = str(row.get("evidence_id") or "")
        if not eid or eid in id_to_row:
            continue
        id_to_row[eid] = row
        ordered_ids.append(eid)
    priority_order: List[str] = []
    if prefer_comparative:
        priority_order.extend(COMPACT_REGISTRY_PRIORITY)
    else:
        # Keep non-comparative rounds aligned with the same compact priority so
        # target aTB enrichments and compact .aop cues survive pack-size trimming.
        priority_order.extend(COMPACT_REGISTRY_PRIORITY)
    trimmed: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for eid in priority_order:
        if len(trimmed) >= max_items:
            break
        row = id_to_row.get(eid)
        if row is None or eid in seen:
            continue
        trimmed.append(row)
        seen.add(eid)
    for eid in ordered_ids:
        if len(trimmed) >= max_items:
            break
        if eid in seen:
            continue
        trimmed.append(id_to_row[eid])
        seen.add(eid)
    return trimmed


def build_reasoning_pack(case_json: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    query = case_json.get("query") or {}
    runtime = case_json.get("runtime") or {}
    emission_cfg = ((case_json.get("evidence_acquire") or {}).get("emission") or {})
    active_profile, active_profile_cfg, profiles_cfg = resolve_evidence_profiles(reasoning_config)
    gate_mode = str(((case_json.get("current_gate") or {}).get("reasoning_mode") or "")).lower()
    default_neighbor_k = 5 if gate_mode == "conservative" else 10
    neighbor_k = int(active_profile_cfg.get("neighbor_topk", default_neighbor_k) or 0)
    include_neighbor_summary = bool(active_profile_cfg.get("include_neighbor_summary", True))
    include_atb_trends_self = bool(active_profile_cfg.get("include_atb_trends_self", active_profile in {"R1", "R2", "R3"}))
    include_neighbor_atb_stats = bool(
        active_profile_cfg.get("include_neighbor_atb_stats_by_label")
        if "include_neighbor_atb_stats_by_label" in active_profile_cfg
        else active_profile_cfg.get("include_neighbor_atb_stats", True)
    )
    include_neighbor_feature_rows = bool(active_profile_cfg.get("include_neighbor_feature_rows", False))
    include_structure_fact_sheet = bool(active_profile_cfg.get("include_structure_fact_sheet", active_profile == "R0"))
    include_prior_reliability_profile = bool(active_profile_cfg.get("include_prior_reliability_profile", active_profile == "R0"))
    include_candidate_slate_v2 = bool(active_profile_cfg.get("include_candidate_slate_v2", active_profile == "R0"))
    include_structure_prior_profile = bool(active_profile_cfg.get("include_structure_prior_profile", True))
    include_structure_motif_profile = bool(active_profile_cfg.get("include_structure_motif_profile", active_profile in {"R0", "R1", "R2", "R3"}))
    include_structure_retrieval_profile = bool(active_profile_cfg.get("include_structure_retrieval_profile", active_profile in {"R0", "R1", "R2", "R3"}))
    include_structure_candidate_distribution = bool(active_profile_cfg.get("include_structure_candidate_distribution", active_profile in {"R0", "R1", "R2", "R3"}))
    include_emission_observation_profile = bool(
        active_profile_cfg.get("include_emission_observation_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_target_atb_summary = bool(active_profile_cfg.get("include_target_atb_summary", True))
    include_target_atb_full = bool(active_profile_cfg.get("include_target_atb_full", False))
    include_atb_trend_profile = bool(
        active_profile_cfg.get("include_atb_trend_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_ct_proxy_profile = bool(
        active_profile_cfg.get("include_atb_ct_proxy_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_structural_relaxation_profile = bool(
        active_profile_cfg.get("include_atb_structural_relaxation_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_shape_rigidity_profile = bool(
        active_profile_cfg.get("include_atb_shape_rigidity_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_literature_status = bool(active_profile_cfg.get("include_literature_status", True))
    include_experiment_status = bool(active_profile_cfg.get("include_experiment_status", True))
    registry_max_items = int(active_profile_cfg.get("registry_max_items", 20) or 20)

    neighbors_topk = _neighbors_topk(case_json, k=neighbor_k) if include_neighbor_summary else []
    neighbor_rows_compact = compact_neighbor_atb_rows(((case_json.get("risk_scores") or {}).get("atb_neighbor_features_all") or []))
    risk_subset = _risk_scores_subset(
        case_json,
        include_neighbor_summary=include_neighbor_summary,
        include_neighbor_feature_rows=include_neighbor_feature_rows,
    )
    if active_profile == "R0":
        for key in (
            "structure_prior_profile",
            "structure_motif_profile",
            "structure_retrieval_profile",
            "structure_candidate_distribution",
        ):
            risk_subset.pop(key, None)
    structure_prior_profile: Optional[Dict[str, Any]] = None
    if include_structure_prior_profile:
        existing_structure_prior = ((case_json.get("risk_scores") or {}).get("structure_prior_profile") or {})
        if isinstance(existing_structure_prior, dict) and existing_structure_prior:
            structure_prior_profile = deepcopy(existing_structure_prior)
        else:
            canonical_smiles = str((query.get("canonical_smiles") or query.get("input_smiles") or "")).strip()
            if canonical_smiles:
                structure_prior_profile = compute_structure_prior_profile(canonical_smiles)
        if isinstance(structure_prior_profile, dict) and active_profile != "R0":
            risk_subset["structure_prior_profile"] = structure_prior_profile
    structure_motif_profile = None
    if include_structure_motif_profile:
        existing_structure_motif = ((case_json.get("risk_scores") or {}).get("structure_motif_profile") or {})
        if isinstance(existing_structure_motif, dict) and existing_structure_motif:
            structure_motif_profile = deepcopy(existing_structure_motif)
            if active_profile != "R0":
                risk_subset["structure_motif_profile"] = structure_motif_profile
    structure_fact_sheet: Optional[Dict[str, Any]] = None
    if include_structure_fact_sheet and isinstance(structure_prior_profile, dict) and isinstance(structure_motif_profile, dict):
        existing_fact_sheet = ((case_json.get("risk_scores") or {}).get("structure_fact_sheet") or {})
        if isinstance(existing_fact_sheet, dict) and existing_fact_sheet:
            structure_fact_sheet = deepcopy(existing_fact_sheet)
        else:
            structure_fact_sheet = compute_structure_fact_sheet(structure_prior_profile, structure_motif_profile)
        risk_subset["structure_fact_sheet"] = structure_fact_sheet
    structure_retrieval_profile = None
    if include_structure_retrieval_profile:
        existing_structure_retrieval = ((case_json.get("risk_scores") or {}).get("structure_retrieval_profile") or {})
        if isinstance(existing_structure_retrieval, dict) and existing_structure_retrieval:
            structure_retrieval_profile = deepcopy(existing_structure_retrieval)
            if active_profile != "R0":
                risk_subset["structure_retrieval_profile"] = structure_retrieval_profile
    structure_candidate_distribution = None
    if include_structure_candidate_distribution:
        existing_structure_candidate_distribution = ((case_json.get("risk_scores") or {}).get("structure_candidate_distribution") or {})
        if isinstance(existing_structure_candidate_distribution, dict) and existing_structure_candidate_distribution:
            structure_candidate_distribution = deepcopy(existing_structure_candidate_distribution)
            if active_profile != "R0":
                risk_subset["structure_candidate_distribution"] = structure_candidate_distribution
    prior_reliability_profile: Optional[Dict[str, Any]] = None
    candidate_slate_v2: Optional[Dict[str, Any]] = None
    if active_profile == "R0":
        if include_prior_reliability_profile:
            prior_reliability_profile = compute_prior_reliability_profile(
                structure_retrieval_profile=structure_retrieval_profile or {},
                neighbors=case_json.get("neighbors") or [],
                top1_sim=((case_json.get("risk_scores") or {}).get("top1_sim")),
                mechanism_entropy=((case_json.get("risk_scores") or {}).get("mechanism_entropy")),
                novelty_struct=((case_json.get("risk_scores") or {}).get("novelty_struct")),
                allowed_labels=MAIN_PRIOR_LABELS,
            )
            risk_subset["prior_reliability_profile"] = prior_reliability_profile
        if include_candidate_slate_v2:
            candidate_slate_v2 = compute_candidate_slate_v2(
                structure_retrieval_profile=structure_retrieval_profile or {},
                neighbors=case_json.get("neighbors") or [],
                top1_sim=((case_json.get("risk_scores") or {}).get("top1_sim")),
                mechanism_entropy=((case_json.get("risk_scores") or {}).get("mechanism_entropy")),
                novelty_struct=((case_json.get("risk_scores") or {}).get("novelty_struct")),
                allowed_labels=MAIN_PRIOR_LABELS,
            )
            risk_subset["candidate_slate_v2"] = candidate_slate_v2
    emission_observation_profile: Optional[Dict[str, Any]] = None
    if include_emission_observation_profile and active_profile in {"R1", "R2", "R3"}:
        target_fields = case_json.get("target_fields") or {}
        target_fields_provenance = case_json.get("target_fields_provenance") or {}
        emission_observation_profile = compute_emission_observation_profile(
            target_fields if isinstance(target_fields, dict) else {},
            target_fields_provenance if isinstance(target_fields_provenance, dict) else {},
        )
        if str(emission_observation_profile.get("coverage") or "none") != "none":
            risk_subset["emission_observation_profile"] = emission_observation_profile
    atb_status = str((((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("cache_status") or "")).lower()
    target_summary = (((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    if include_atb_trends_self and active_profile in {"R1", "R2", "R3"}:
        atb_trends_self = compute_atb_trends_self(
            target_atb_features_summary=target_summary if isinstance(target_summary, dict) else {},
            thresholds=_thresholds(reasoning_config),
        )
        if atb_status != "success":
            atb_trends_self["enabled"] = False
            atb_trends_self["reliability"] = "low"
            notes = list(atb_trends_self.get("notes") or [])
            notes.append(f"atb cache_status is {atb_status or 'unknown'}; self-trend is informational only.")
            atb_trends_self["notes"] = notes[:4]
    else:
        atb_trends_self = {
            "enabled": False,
            "fields_used": ["delta_dihedral", "delta_gap", "delta_volume", "excitation_energy"],
            "delta_dihedral_abs_deg": None,
            "delta_dihedral_bucket": "unknown",
            "delta_dihedral_direction": "unknown",
            "delta_gap_direction": "unknown",
            "delta_gap_bucket": "unknown",
            "delta_volume_direction": "unknown",
            "delta_volume_bucket": "unknown",
            "overall_motion_proxy": "unknown",
            "reliability": "low",
            "notes": ["atb_trends_self disabled by profile"],
        }
    risk_subset["atb_trends_self"] = atb_trends_self
    atb_trend_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_trend_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_trend_profile = compute_atb_trend_profile(target_summary)
        risk_subset["atb_trend_profile"] = atb_trend_profile
    atb_ct_proxy_profile: Optional[Dict[str, Any]] = None
    charge_redistribution_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_ct_proxy_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        charge_redistribution_profile = compute_charge_redistribution_profile(
            target_summary,
            thresholds=_thresholds(reasoning_config),
        )
        atb_ct_proxy_profile = compute_atb_ct_proxy_profile(target_summary, thresholds=_thresholds(reasoning_config))
        risk_subset["charge_redistribution_profile"] = charge_redistribution_profile
        risk_subset["atb_ct_proxy_profile"] = atb_ct_proxy_profile
    atb_structural_relaxation_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_structural_relaxation_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_structural_relaxation_profile = compute_atb_structural_relaxation_profile(
            target_summary,
            thresholds=_thresholds(reasoning_config),
        )
        risk_subset["atb_structural_relaxation_profile"] = atb_structural_relaxation_profile
    atb_shape_rigidity_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_shape_rigidity_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_shape_rigidity_profile = compute_atb_shape_rigidity_profile(
            target_summary,
            thresholds=_thresholds(reasoning_config),
        )
        risk_subset["atb_shape_rigidity_profile"] = atb_shape_rigidity_profile
    if include_neighbor_atb_stats and active_profile in {"R2", "R3"}:
        neighbor_atb_stats = compute_neighbor_atb_stats_by_label(
            target_features_summary=target_summary if isinstance(target_summary, dict) else {},
            neighbor_atb_features_all=neighbor_rows_compact,
            neighbor_label_lookup=_neighbor_label_lookup(case_json),
        )
    else:
        neighbor_atb_stats = {
            "sample_size": 0,
            "fields": {},
            "by_label": {},
            "summary": ["neighbor_atb_stats disabled by profile"],
            "reliability": "low",
        }
    risk_subset["neighbor_atb_stats_by_label"] = neighbor_atb_stats
    risk_subset["neighbor_atb_stats"] = neighbor_atb_stats

    mechanism_context = _mechanism_context(case_json)
    if active_profile == "R0" and isinstance(candidate_slate_v2, dict):
        slate_rows = candidate_slate_v2.get("top_candidates") or candidate_slate_v2.get("top3") or []
        if isinstance(slate_rows, list) and slate_rows:
            mechanism_context["candidate_mechanisms_topk"] = []
            mechanism_context["candidate_mechanisms_top3"] = []
            for idx, row in enumerate(slate_rows[:5]):
                if not isinstance(row, dict):
                    continue
                payload = {
                    "mechanism_id": row.get("label") or row.get("mechanism_id") or row.get("name"),
                    "probability": row.get("prob") or row.get("probability") or row.get("confidence"),
                }
                mechanism_context["candidate_mechanisms_topk"].append(payload)
                if idx < 3:
                    mechanism_context["candidate_mechanisms_top3"].append(payload)

    pack = {
        "pack_version": MASTER_PACK_VERSION,
        "query": {
            "input_smiles": query.get("input_smiles"),
            "canonical_smiles": query.get("canonical_smiles"),
            "inchikey": query.get("inchikey"),
            "aliases": query.get("aliases") or [],
            "code": query.get("code"),
            "reference": query.get("reference"),
        },
        "runtime": {
            "run_lane": runtime.get("run_lane") or reasoning_config.get("run_lane"),
            "emission_mode": emission_cfg.get("mode"),
            "emission_strictness": emission_cfg.get("strictness"),
            "allow_other_label": runtime.get("allow_other_label"),
            "label_pool_name": runtime.get("label_pool_name"),
        },
        "evidence_profile": {
            "active_profile": active_profile,
            "config": active_profile_cfg,
            "profiles": profiles_cfg.get("profiles"),
        },
        "gate": deepcopy(case_json.get("current_gate") or {}),
        "neighbors_topk": neighbors_topk,
        "risk_scores": risk_subset,
        "neighbor_atb_stats_by_label": neighbor_atb_stats,
        "neighbor_atb_stats": neighbor_atb_stats,
        "atb_trends_self": atb_trends_self,
        "evidence_readiness": _evidence_readiness_subset(
            case_json,
            include_target_atb_summary=include_target_atb_summary,
            include_target_atb_full=include_target_atb_full,
            include_literature_status=include_literature_status,
            include_experiment_status=include_experiment_status,
        ),
        "target_fields": deepcopy(case_json.get("target_fields") or {}),
        "target_fields_provenance": deepcopy(case_json.get("target_fields_provenance") or {}),
        "mechanism_context": mechanism_context,
    }
    pack["evidence_registry"] = _build_evidence_registry(
        case_json,
        neighbors_topk,
        structure_prior_profile=structure_prior_profile,
        structure_motif_profile=structure_motif_profile,
        structure_fact_sheet=structure_fact_sheet,
        prior_reliability_profile=prior_reliability_profile,
        candidate_slate_v2=candidate_slate_v2,
        structure_retrieval_profile=structure_retrieval_profile,
        structure_candidate_distribution=structure_candidate_distribution,
        emission_observation_profile=emission_observation_profile,
        use_r0_prior_stack=active_profile == "R0",
        include_target_atb_signals=include_target_atb_summary and active_profile in {"R1", "R2", "R3"},
        atb_trend_profile=atb_trend_profile,
        charge_redistribution_profile=charge_redistribution_profile,
        atb_ct_proxy_profile=atb_ct_proxy_profile,
        atb_structural_relaxation_profile=atb_structural_relaxation_profile,
        atb_shape_rigidity_profile=atb_shape_rigidity_profile,
        include_literature_status=include_literature_status,
        include_experiment_status=include_experiment_status,
        include_atb_trends_self=include_atb_trends_self and active_profile in {"R1", "R2", "R3"},
        atb_trends_self=atb_trends_self,
        include_neighbor_atb_stats=include_neighbor_atb_stats and active_profile in {"R2", "R3"},
        neighbor_atb_stats=neighbor_atb_stats,
        max_items=registry_max_items,
    )

    if _safe_json_size_bytes(pack) > MAX_PACK_BYTES:
        # deterministic shrink strategy
        pack["neighbors_topk"] = pack["neighbors_topk"][:5]
        pack["mechanism_context"]["mechanism_signatures_top3"] = (
            pack["mechanism_context"].get("mechanism_signatures_top3") or []
        )[:2]
        pack["evidence_registry"] = _build_evidence_registry(
            case_json,
            pack["neighbors_topk"],
            structure_prior_profile=structure_prior_profile,
            structure_motif_profile=structure_motif_profile,
            structure_fact_sheet=structure_fact_sheet,
            prior_reliability_profile=prior_reliability_profile,
            candidate_slate_v2=candidate_slate_v2,
            structure_retrieval_profile=structure_retrieval_profile,
            structure_candidate_distribution=structure_candidate_distribution,
            emission_observation_profile=emission_observation_profile,
            use_r0_prior_stack=active_profile == "R0",
            include_target_atb_signals=include_target_atb_summary and active_profile in {"R1", "R2", "R3"},
            atb_trend_profile=atb_trend_profile,
            charge_redistribution_profile=charge_redistribution_profile,
            atb_ct_proxy_profile=atb_ct_proxy_profile,
            atb_structural_relaxation_profile=atb_structural_relaxation_profile,
            atb_shape_rigidity_profile=atb_shape_rigidity_profile,
            include_literature_status=include_literature_status,
            include_experiment_status=include_experiment_status,
            include_atb_trends_self=include_atb_trends_self and active_profile in {"R1", "R2", "R3"},
            atb_trends_self=atb_trends_self,
            include_neighbor_atb_stats=include_neighbor_atb_stats and active_profile in {"R2", "R3"},
            neighbor_atb_stats=neighbor_atb_stats,
            max_items=min(registry_max_items, 20),
        )
    return pack


def _choose_template(reasoning_pack: Dict[str, Any]) -> str:
    gate = reasoning_pack.get("gate") or {}
    mode = str(gate.get("reasoning_mode") or "").lower()
    risk = reasoning_pack.get("risk_scores") or {}
    atb_nc = (risk.get("atb_neighbor_consistency") or {}) if isinstance(risk.get("atb_neighbor_consistency"), dict) else {}
    novelty = risk.get("novelty_struct")
    entropy = risk.get("mechanism_entropy")

    if mode == "conservative":
        if isinstance(entropy, (int, float)) and entropy >= 0.55:
            return "mixture"
        return "stable"

    if atb_nc.get("flag") == "outlier" and atb_nc.get("reliability") in {"medium", "high"}:
        return "novelty"
    if isinstance(novelty, (int, float)) and novelty >= 0.60:
        return "novelty"
    if isinstance(entropy, (int, float)) and entropy >= 0.55:
        return "mixture"
    if isinstance(novelty, (int, float)) and novelty >= 0.35:
        return "mixture"
    return "stable"


def _build_prompt_payload(reasoning_pack: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep model-facing payload compact:
    - prioritize chemical signal fields,
    - reduce traceability token overhead while preserving strict server-side validation.
    """
    payload = deepcopy(reasoning_pack)
    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    risk_scores = payload.get("risk_scores")
    if isinstance(risk_scores, dict):
        # Keep prompt compact: neighbor raw rows stay out of model-facing payload.
        risk_scores.pop("atb_neighbor_features_all", None)
        if active_profile == "R0":
            for key in (
                "structure_prior_profile",
                "structure_motif_profile",
                "structure_retrieval_profile",
                "structure_candidate_distribution",
            ):
                risk_scores.pop(key, None)
    payload["validation_note"] = "Use only evidence_id keys from evidence_registry for citations."
    payload["candidate_set_text"] = _candidate_set_text(reasoning_pack, reasoning_config)
    payload["reasoning_config"] = {"thresholds": _thresholds(reasoning_config)}
    return payload


def _candidate_set_text(reasoning_pack: Dict[str, Any], reasoning_config: Optional[Dict[str, Any]] = None) -> str:
    ctx = reasoning_pack.get("mechanism_context") or {}
    rows = ctx.get("candidate_mechanisms_topk") or ctx.get("candidate_mechanisms_top3")
    if not rows:
        slate_v2 = (((reasoning_pack.get("risk_scores") or {}).get("candidate_slate_v2")) or {})
        rows = slate_v2.get("top_candidates") or slate_v2.get("top3")
    allowed_lookup = {
        str(label).strip().lower(): str(label).strip()
        for label in resolve_allowed_mechanism_labels(reasoning_pack, reasoning_config or {})
        if str(label).strip()
    }
    labels: List[str] = []
    residual_labels: List[str] = []
    if isinstance(rows, list):
        for row in rows:
            label: Optional[str] = None
            if isinstance(row, dict):
                raw = row.get("mechanism_id") or row.get("label") or row.get("name")
                if isinstance(raw, str):
                    label = raw.strip()
            elif isinstance(row, str):
                label = row.strip()
            if label:
                label = allowed_lookup.get(label.lower())
            if label and label not in labels and label not in residual_labels:
                if label == "other":
                    residual_labels.append(label)
                else:
                    labels.append(label)
    labels.extend(residual_labels)
    if labels:
        return f"Top competing mechanisms (from candidate slate): {', '.join(labels)}."
    return "Top competing mechanisms are uncertain; propose plausible hypotheses from evidence."


def _available_evidence_ids(
    reasoning_pack: Dict[str, Any],
    candidate_ids: Sequence[str],
) -> List[str]:
    registry = reasoning_pack.get("evidence_registry")
    if not isinstance(registry, list):
        return []
    available = {
        str(row.get("evidence_id") or "").strip()
        for row in registry
        if isinstance(row, dict) and str(row.get("evidence_id") or "").strip()
    }
    return [eid for eid in candidate_ids if eid in available]


def build_master_prompt_bundle(reasoning_pack: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    template = _choose_template(reasoning_pack)
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "normal").lower()
    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    policy = _policy(reasoning_config)
    schema_version = str(reasoning_config.get("master_output_schema_version") or "v3").lower()
    output_mode = str(reasoning_config.get("master_output_mode") or MASTER_OUTPUT_MODE_TAGGED_REPAIR).strip().lower()
    thresholds = _thresholds(reasoning_config)
    candidate_set_text = _candidate_set_text(reasoning_pack, reasoning_config)
    allowed_labels = resolve_allowed_mechanism_labels(reasoning_pack, reasoning_config)
    allow_other_label = "other" in allowed_labels
    available_trend_ids = _available_evidence_ids(reasoning_pack, ATB_TREND_PROFILE_EVIDENCE_IDS)
    available_enrichment_ids = _available_evidence_ids(reasoning_pack, ATB_ENRICHMENT_EVIDENCE_IDS)
    available_emission_ids = _available_evidence_ids(reasoning_pack, TARGET_OBSERVATION_EVIDENCE_IDS)
    available_structure_prior_ids = _available_evidence_ids(reasoning_pack, STRUCTURE_PRIOR_EVIDENCE_IDS)
    available_structure_agent_ids = _available_evidence_ids(reasoning_pack, STRUCTURE_AGENT_EVIDENCE_IDS)
    available_comparative_ids = _available_evidence_ids(reasoning_pack, COMPARATIVE_TRANSFERABILITY_EVIDENCE_IDS)
    available_aop_compact_ids = _available_evidence_ids(reasoning_pack, AOP_COMPACT_EVIDENCE_IDS)

    output_line = (
        "Respond in natural language using tagged sections only.\n"
        if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
        else "Return strict JSON that matches the provided schema.\n"
    )
    system = (
        "You are the master reasoner for AIE mechanism discovery.\n"
        "Use ONLY the provided reasoning_pack JSON.\n"
        "Do not fabricate evidence or facts.\n"
        "Every evidence reference must use evidence_id from evidence_registry only.\n"
        f"{output_line}"
        "Neighbors are priors/context by default. Use aTB features as primary support evidence.\n"
        "DO NOT invent numeric thresholds or bands. If a threshold is needed, use only values from reasoning_config.thresholds or evidence_registry."
    )
    instructions = [
        "Template rubric:",
        f"- template_used should be {template}.",
        "- stable: pick one dominant mechanism with conservative uncertainty.",
        "- mixture: discuss multiple plausible mechanisms and tradeoffs.",
        "- novelty: emphasize uncertainty and verification path.",
        "Evidence Weighting Policy:",
        f"- Neighbors are context/prior unless top1_sim >= {policy['neighbor_support_min_sim']:.2f}.",
        f"- Target observation IDs available this round: {', '.join(available_emission_ids) if available_emission_ids else 'none'}.",
        "- aTB features are support evidence when available.",
        f"- Self-trend IDs available this round: {', '.join(available_trend_ids) if available_trend_ids else 'none'}.",
        f"- Target aTB enrichment IDs available this round: {', '.join(available_enrichment_ids) if available_enrichment_ids else 'none'}.",
        f"- Compact .aop transition IDs available this round: {', '.join(available_aop_compact_ids) if available_aop_compact_ids else 'none'}.",
        f"- Structure-prior IDs available this round: {', '.join(available_structure_prior_ids) if available_structure_prior_ids else 'none'}.",
        f"- Structure-agent IDs available this round: {', '.join(available_structure_agent_ids) if available_structure_agent_ids else 'none'}.",
        f"- Comparative IDs available this round: {', '.join(available_comparative_ids) if available_comparative_ids else 'none'}.",
        "- Cite only IDs that actually appear in evidence_registry for this round.",
        "- Do not use raw absolute aTB values as standalone mechanism verdicts; cite self-trend buckets/directions first.",
        f"- {candidate_set_text}",
        "aTB discriminative rubric:",
        "- Assign atb_support_level by comparing abs(delta_dihedral) against reasoning_config.thresholds.atb_dihedral_thresh_none and reasoning_config.thresholds.atb_dihedral_thresh_strong.",
        "- If you mention threshold logic in text, you must cite exact key=value from reasoning_config.thresholds.",
        "- Otherwise use relative wording only (e.g., modest/large) and avoid threshold/range/band/cutoff terms.",
        "- Treat electronic redistribution as one generic axis described in phenomenon-level terms only.",
        "- Treat target observation (emission observations and their compact summary) as a target-side axis that is stronger than neighbor comparative context when present.",
        "- For target-only aTB evidence, prefer direction/bucket wording from atb_trend_profile (or legacy atb_trends_self) over raw numeric thresholds.",
        "- Treat the electronic redistribution profile as a compact cue derived from atom-wise charge-variation summaries and gap change.",
        "- It is not a true dipole-moment measurement and should be interpreted only as one evidence axis among others.",
        "- Treat structural relaxation as a combined signal from torsion, bond, angle, and volume changes; do not rely on delta_dihedral alone when E37 is available.",
        "- Treat shape-rigidity (E39) as auxiliary context that may reinforce or weaken cross-axis consistency.",
        "- Treat structure prior (E40..E44) as a generic topology/context axis that stays label-agnostic and auditable when those IDs are present.",
        "- Treat structure-agent evidence (E50..E56) as candidate-generation context from the R0 fact sheet, prior reliability, and candidate slate; it does not by itself determine the final mechanism.",
        "- Comparative transferability evidence (E21..E24) may refine target-only interpretation but cannot determine the winning label by itself.",
        "supporting_chain must contain exactly 4 ordered steps A->B->C->D:",
        "- A excited-state structural access (aTB features)",
        "- B electronic redistribution or nonradiative-channel interpretation (legacy schema key ct_family)",
        "- C aggregation/rigidification suppressing nonradiative channel",
        "- D discriminative predictions to separate the top competing mechanisms listed above (or the top hypotheses you propose if none are provided).",
        "- step_name must be chosen from: ct_family, torsion_access, aIE_bridge, neighbor_priors, discriminators, limits",
        "- Use ct_family only as the legacy schema key for the electronic redistribution axis; legacy schema key: ct_family; it is not privileged support for any mechanism label.",
        "If constraints cannot be satisfied, set status=insufficient_evidence and still return predictions.",
        "When citing evidence, use only evidence_id keys (E1, E2, ..., E31..E34, plus legacy E_ATB_TREND_1..4 if present).",
        "Additional cache-derived evidence IDs may appear as E35..E63; prefer these summaries over raw field-by-field narration when they are present.",
        "Never output case_path anywhere in the JSON (including evidence_used, supporting_chain, competing_hypotheses, predictions).",
        f"PRIMARY_LABEL must be exactly one mechanism token from this set: {', '.join(allowed_labels)}. Do not add explanation text in PRIMARY_LABEL.",
        "Hard output budgets:",
        f"- supporting_chain max {MASTER_MAX_SUPPORTING_CHAIN_ITEMS} items (must still be A->B->C->D).",
        f"- predictions max {MASTER_MAX_PREDICTIONS_ITEMS} items.",
        f"- competing_hypotheses max {MASTER_MAX_COMPETING_ITEMS} items.",
        f"- evidence_used max {MASTER_MAX_EVIDENCE_USED_ITEMS} items.",
        f"- each evidence note max {MASTER_NOTE_MAX_CHARS} chars.",
        "Top-level evidence_used should stay compact and prioritize uncertainty bounds plus the active-profile aTB/structure IDs that are present in evidence_registry.",
        "When profile is R2/R3 and E21/E22 exist, cite at least one of them in supporting_chain to ground comparative neighbor-vs-target interpretation.",
        "When profile is R2/R3 and any of E60..E63 are present, cite at least one compact .aop transition cue in evidence_used.",
        "natural_language_mechanism should be a three-paragraph narrative in one string: best hypothesis, unresolved boundary among top competing mechanisms, and falsifiable next tests.",
        "Do not cite neighbors_topk fields directly.",
        "Hard rule: DO NOT invent numeric thresholds/bands. If threshold mention is necessary, reference reasoning_config.thresholds key/value exactly.",
        f"Configured thresholds (authoritative): {json.dumps(thresholds, ensure_ascii=False, sort_keys=True)}",
    ]
    if active_profile == "R0":
        instructions.extend(
            [
                "Round contract (R0 prior-only):",
                "- Read the R0 prior stack in this order: structure facts (E50..E54 when present), prior reliability (E55), then candidate slate (E56).",
                "- Treat neighbor/similarity/novelty signals as prior reliability context, not as a direct verdict.",
                "- Focus on a ranked candidate slate, not a final verdict.",
                "- Structure facts describe phenomena only; they do not directly determine mechanism labels.",
                "- Candidate slate is a suggestion layer synthesized from multiple priors; do not treat it as ground truth.",
                "- Do not restate raw feature/murcko/ECFP disagreements unless they are already summarized by prior reliability or candidate slate.",
                "- Keep at least two competing mechanisms visible unless the candidate set is genuinely empty.",
                "- If prior reliability is low or ambiguity is high, keep at least three competing mechanisms visible and avoid a high-confidence top1.",
                "- Do NOT output a high-confidence final verdict; keep status=insufficient_evidence if uncertainty remains.",
                "- Explicitly state what target-specific evidence would move the leading candidate up or down in later rounds.",
            ]
        )
        if allow_other_label:
            instructions.append("- Do not use 'other' as the top primary label in R0; treat it as a residual late-round outcome only.")
    elif active_profile == "R1":
        instructions.extend(
            [
                "Round contract (R1 target-constraint):",
                f"- Use target observation IDs first when present: {', '.join(available_emission_ids) if available_emission_ids else 'none'}.",
                f"- Use self-trend IDs as primary target aTB evidence when present: {', '.join(available_trend_ids) if available_trend_ids else 'none'}.",
                f"- Use available target-aTB enrichment IDs for gain/loss updates: {', '.join(available_enrichment_ids) if available_enrichment_ids else 'none'}.",
                f"- Use available structure-prior IDs for gain/loss updates: {', '.join(available_structure_prior_ids) if available_structure_prior_ids else 'none'}.",
                "- Interpret target observations before target aTB when both are available.",
                "- If E70..E73 are present, cite at least one of them in supporting_chain or top-level evidence_used.",
                "- Explain which candidate mechanisms gain/lose weight under target self-trend evidence.",
                "- Prefer bucket/direction/percentile_global wording; do not make absolute-value threshold verdicts.",
            ]
        )
    elif active_profile == "R2":
        instructions.extend(
            [
                "Round contract (R2 comparative-control):",
                f"- Keep target observation IDs ({', '.join(available_emission_ids) if available_emission_ids else 'none'}) in view as the target-side observation anchor when available.",
                f"- Use comparative evidence IDs ({', '.join(available_comparative_ids) if available_comparative_ids else 'none'}) to assess neighbor transferability vs outlier behavior.",
                f"- Keep target-only enrichment IDs ({', '.join(available_enrichment_ids) if available_enrichment_ids else 'none'}) and structure-prior IDs ({', '.join(available_structure_prior_ids) if available_structure_prior_ids else 'none'}) in view when comparative evidence is weak or mixed.",
                f"- If compact .aop transition IDs are present ({', '.join(available_aop_compact_ids) if available_aop_compact_ids else 'none'}), cite at least one to ground excited-state transition context.",
                "- Comparative evidence can refine but must not override target observation evidence when target observations are available.",
                "- If comparative evidence is weak/unavailable, state limited information gain and avoid over-updating claims.",
            ]
        )
        if allow_other_label:
            instructions.append("- If standard-label support remains single-axis and residual ambiguity persists, keep a residual outcome in play instead of force-fitting to a standard label.")
        else:
            instructions.append("- If evidence stays ambiguous after target-side and comparative review, keep the leading standard label provisional or output unknown instead of inventing an out-of-pool label.")
    elif active_profile == "R3":
        instructions.extend(
            [
                "Round contract (R3 external-evidence):",
                "- Incorporate literature/experiment readiness with explicit strictness limits.",
                "- Distinguish plausible narrative from externally verifiable support.",
            ]
        )
        if allow_other_label:
            instructions.append("- A late-round residual outcome ('other') remains valid when standard labels stay weakened or unresolved under target-side evidence.")
        else:
            instructions.append("- If no standard label closes under the available evidence, use unknown to signal unresolved mechanism attribution.")
    if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA:
        instructions.extend(
            [
                "Response format for this turn: natural language only.",
                "Use EXACT tagged section prefixes in this order (one line each to start):",
                "TEMPLATE_USED:, STATUS:, PRIMARY_LABEL:, PRIMARY_CONFIDENCE:, PRIMARY:, COMPETING:, EVIDENCE:, PREDICTIONS:, LIMITS:, NEXT_ACTIONS:",
                "You may write multiple lines under each tagged section.",
                "In each section, cite evidence_id inline when relevant (for example: E11, E12).",
                f"Hard limits: COMPETING <= {MASTER_MAX_COMPETING_ITEMS}, EVIDENCE <= {MASTER_MAX_EVIDENCE_USED_ITEMS}, NEXT <= 5.",
                "Anything after NEXT section may be ignored by parser.",
                "Do NOT output raw JSON in this response.",
            ]
        )
    if gate_mode == "conservative":
        instructions.append(
            "- Conservative mode: keep confidence capped and explicitly list evidence limitations."
        )
    instructions.extend(
        [
            "Confidence policy:",
            "- Use continuous soft-penalty factors from reasoning_config thresholds/policy; avoid hard step caps.",
            "- Apply one final cap only at the end (global cap, and conservative cap when mode is conservative).",
            "- In R0, apply the configured r0_penalty_factor as a soft multiplier rather than hard clipping.",
        ]
    )

    if schema_version == "v1":
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V1
    elif schema_version == "v2":
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V2
    else:
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V3
    schema = master_output_schema(schema_version=schema_version)
    if output_mode == MASTER_OUTPUT_MODE_STRICT_SCHEMA:
        contract = _json_only_contract_text(
            required_keys=list(schema.get("required") or []),
            array_caps={
                "supporting_chain": MASTER_MAX_SUPPORTING_CHAIN_ITEMS,
                "predictions": MASTER_MAX_PREDICTIONS_ITEMS,
                "competing_hypotheses": MASTER_MAX_COMPETING_ITEMS,
                "evidence_used": MASTER_MAX_EVIDENCE_USED_ITEMS,
            },
        )
        instructions.append(contract)
    return {
        "prompt_bundle_version": MASTER_PROMPT_BUNDLE_VERSION,
        "template_version": f"{template}_v1",
        "template_used": template,
        "output_mode": output_mode,
        "system": system,
        "instructions": "\n".join(instructions),
        "user_payload": _build_prompt_payload(reasoning_pack, reasoning_config),
        "reasoning_policy": policy,
        "output_schema_name": schema_name,
        "output_schema": schema,
    }


def _evidence_item_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidence_id": {"type": "string", "pattern": "^(?:E[0-9]+|E_ATB_TREND_[1-4])$"},
            "note": {"type": "string"},
            "role": {"type": "string", "enum": ["support", "counter", "context"]},
        },
        "required": ["evidence_id", "note", "role"],
    }


def _base_master_output_schema(*, schema_version: str) -> Dict[str, Any]:
    ver = str(schema_version).lower()
    is_v2_plus = ver in {"v2", "v3"}
    is_v3 = ver == "v3"
    evidence_item = _evidence_item_schema()
    primary_props: Dict[str, Any] = {
        "mechanism_label": {"type": "string"},
        "aie_rationale_type": {"type": "string", "enum": ["stable", "mixture", "novelty"]},
        "natural_language_mechanism": {"type": "string"},
    }
    primary_required = ["mechanism_label", "aie_rationale_type", "natural_language_mechanism"]
    if is_v2_plus:
        primary_props["atb_support_level"] = {"type": "string", "enum": ["none", "weak", "strong"]}
        primary_required.append("atb_support_level")

    competing_props: Dict[str, Any] = {
        "name": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_used": {"type": "array", "items": evidence_item},
    }
    competing_required = ["name", "confidence", "evidence_used"]
    if is_v2_plus:
        competing_props["atb_support_level"] = {"type": "string", "enum": ["none", "weak", "strong"]}
        competing_required.append("atb_support_level")

    chain_props: Dict[str, Any] = {
        "claim": {"type": "string"},
        "evidence_used": {"type": "array", "items": evidence_item},
    }
    chain_required = ["claim", "evidence_used"]
    if is_v2_plus:
        chain_props["step_id"] = {"type": "string", "enum": ["A", "B", "C", "D"]}
        if is_v3:
            chain_props["step_name"] = {
                "type": "string",
                "enum": [
                    "ct_family",
                    "torsion_access",
                    "aIE_bridge",
                    "neighbor_priors",
                    "discriminators",
                    "limits",
                ],
            }
        else:
            chain_props["step_name"] = {"type": "string"}
        chain_required = ["step_id", "step_name", "claim", "evidence_used"]

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["ok", "insufficient_evidence"]},
            "template_used": {"type": "string", "enum": ["stable", "mixture", "novelty"]},
            "mechanism_claim": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "primary_hypothesis": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": primary_props,
                        "required": primary_required,
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reasoning_mode_used": {"type": "string", "enum": ["normal", "conservative"]},
                },
                "required": ["primary_hypothesis", "confidence", "reasoning_mode_used"],
            },
            "supporting_chain": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": chain_props,
                    "required": chain_required,
                },
            },
            "competing_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": competing_props,
                    "required": competing_required,
                },
            },
            "predictions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "prediction": {"type": "string"},
                        "expected_signal": {"type": "string"},
                        "evidence_used": {"type": "array", "items": evidence_item},
                    },
                    "required": ["prediction", "expected_signal", "evidence_used"],
                },
            },
            "limits": {"type": "array", "items": {"type": "string"}},
            "evidence_used": {"type": "array", "items": evidence_item},
            "recommended_next_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "status",
            "template_used",
            "mechanism_claim",
            "supporting_chain",
            "competing_hypotheses",
            "predictions",
            "limits",
            "evidence_used",
            "recommended_next_actions",
        ],
    }


def master_output_schema_v1() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v1")


def master_output_schema_v2() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v2")


def master_output_schema_v3() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v3")


def master_output_schema(schema_version: str = "v3") -> Dict[str, Any]:
    ver = str(schema_version).lower()
    if ver == "v1":
        return master_output_schema_v1()
    if ver == "v2":
        return master_output_schema_v2()
    return master_output_schema_v3()


def _collect_all_evidence_entries(master_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    top = master_output.get("evidence_used")
    if isinstance(top, list):
        rows.extend([x for x in top if isinstance(x, dict)])

    for chain in master_output.get("supporting_chain") or []:
        if not isinstance(chain, dict):
            continue
        ev = chain.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    for comp in master_output.get("competing_hypotheses") or []:
        if not isinstance(comp, dict):
            continue
        ev = comp.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    for pred in master_output.get("predictions") or []:
        if not isinstance(pred, dict):
            continue
        ev = pred.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    return rows


def _schema_validate_value(
    value: Any,
    schema: Dict[str, Any],
    *,
    path: str,
    errors: List[Dict[str, str]],
) -> None:
    if not isinstance(schema, dict):
        return
    typ = schema.get("type")
    if typ == "object":
        if not isinstance(value, dict):
            errors.append(_err("schema", "type_mismatch", path, "expected object"))
            return
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(_err("schema", "missing_required", f"{path}/{key}", f"missing required key: {key}"))
        # Keep local validation lightweight: allow extra keys to reduce brittleness.
        for key, subschema in props.items():
            if key in value:
                _schema_validate_value(value[key], subschema, path=f"{path}/{key}", errors=errors)
        return
    if typ == "array":
        if not isinstance(value, list):
            errors.append(_err("schema", "type_mismatch", path, "expected array"))
            return
        item_schema = schema.get("items")
        for idx, row in enumerate(value):
            _schema_validate_value(row, item_schema, path=f"{path}/{idx}", errors=errors)
        return
    if typ == "string":
        if not isinstance(value, str):
            errors.append(_err("schema", "type_mismatch", path, "expected string"))
            return
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(_err("schema", "enum_violation", path, f"value '{value}' not in enum"))
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            if re.match(pattern, value) is None:
                errors.append(_err("schema", "pattern_mismatch", path, f"value '{value}' does not match pattern {pattern}"))
        return
    if typ == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(_err("schema", "type_mismatch", path, "expected number"))
            return
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and float(value) < float(minimum):
            errors.append(_err("schema", "minimum_violation", path, f"{value} < {minimum}"))
        if isinstance(maximum, (int, float)) and float(value) > float(maximum):
            errors.append(_err("schema", "maximum_violation", path, f"{value} > {maximum}"))
        return
    # Unknown/no type in schema: skip strict local check, rely on provider-side strict schema.


def _validate_master_output_schema(
    master_output: Dict[str, Any],
    *,
    schema_version: str,
) -> List[Dict[str, str]]:
    if not isinstance(master_output, dict):
        return [_err("schema", "root_not_object", "$", "master_output must be a JSON object")]
    schema = master_output_schema(schema_version=schema_version)
    errors: List[Dict[str, str]] = []
    _schema_validate_value(master_output, schema, path="$", errors=errors)
    return errors


def _is_neighbor_path(case_path: str) -> bool:
    return case_path.startswith("/neighbors/") or case_path.startswith("/neighbors_topk/")


def resolve_evidence_id(
    evidence_id: str,
    reasoning_pack: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    registry = _registry_map(reasoning_pack.get("evidence_registry") or {})
    row = registry.get(str(evidence_id))
    if not isinstance(row, dict):
        return None, None
    source_type = str(row.get("source_type") or "case")
    if source_type == "derived_pack":
        case_path = row.get("pack_path")
    else:
        case_path = row.get("case_path")
    label = row.get("label")
    return (str(case_path) if isinstance(case_path, str) else None, str(label) if isinstance(label, str) else None)


def _resolve_evidence_entry(
    entry: Dict[str, Any],
    evidence_registry: Dict[str, Dict[str, Any]],
    case_json: Dict[str, Any],
    reasoning_pack: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    if "case_path" in entry:
        return False, None, "evidence_case_path_forbidden", None
    evidence_id = str(entry.get("evidence_id") or "").strip()
    if not evidence_id:
        return False, None, "evidence_used_missing_evidence_id", None
    if not EVIDENCE_ID_PATTERN.match(evidence_id):
        return False, None, f"evidence_id_format_invalid:{evidence_id}", None
    reg = evidence_registry.get(evidence_id)
    if not isinstance(reg, dict):
        return False, None, f"evidence_id_not_found:{evidence_id}", None
    source_type = str(reg.get("source_type") or "case").strip().lower()
    if source_type not in {"case", "derived_pack"}:
        return False, None, f"unsupported_source_type:{source_type}", None

    if source_type == "case":
        case_path = str(reg.get("case_path") or "").strip()
        if not case_path:
            return False, None, f"evidence_id_missing_case_path:{evidence_id}", None
        if case_path in FORBIDDEN_MASTER_RISK_PATHS:
            return False, None, f"forbidden_hint_reference:{case_path}", None
        found, value = _resolve_pointer(case_json, case_path)
        if not found:
            return False, None, f"evidence_path_not_found:{case_path}", None
        if _is_empty_value(value):
            return False, None, f"evidence_path_empty_value:{case_path}", None
        return True, case_path, None, {
            "value": value,
            "registry_entry": reg,
            "source_type": "case",
            "resolved_case_paths": [case_path],
        }

    pack_path = str(reg.get("pack_path") or "").strip()
    if not pack_path:
        return False, None, f"derived_pack_path_missing:{evidence_id}", None
    found, value = _resolve_pointer(reasoning_pack, pack_path)
    if not found:
        return False, None, f"derived_pack_path_not_found:{pack_path}", None
    if _is_empty_value(value):
        return False, None, f"derived_pack_value_empty:{pack_path}", None

    derived_paths: List[str] = []
    for p in reg.get("derived_from_case_paths") or []:
        if isinstance(p, str) and p.strip():
            derived_paths.append(p.strip())
    primary_path = derived_paths[0] if derived_paths else f"pack:{pack_path}"
    return True, primary_path, None, {
        "value": value,
        "registry_entry": reg,
        "source_type": "derived_pack",
        "pack_path": pack_path,
        "resolved_case_paths": derived_paths,
    }


def _validate_supporting_chain_structure(
    out: Dict[str, Any],
) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    chain = out.get("supporting_chain")
    if not isinstance(chain, list):
        return [_err("evidence", "supporting_chain_not_list", "/supporting_chain", "supporting_chain must be a list")]
    if len(chain) != 4:
        errors.append(
            _err("evidence", "supporting_chain_length_invalid", "/supporting_chain", f"expected 4 steps, got {len(chain)}")
        )
        return errors
    expected_steps = ["A", "B", "C", "D"]
    for idx, expected in enumerate(expected_steps):
        row = chain[idx]
        if not isinstance(row, dict):
            errors.append(_err("evidence", "supporting_chain_step_not_object", f"/supporting_chain/{idx}", "step must be object"))
            continue
        step_id = str(row.get("step_id") or "")
        if step_id != expected:
            errors.append(
                _err(
                    "evidence",
                    "supporting_chain_step_order_invalid",
                    f"/supporting_chain/{idx}/step_id",
                    f"expected {expected}, got {step_id}",
                )
            )
        ev = row.get("evidence_used")
        if not isinstance(ev, list) or len(ev) == 0:
            errors.append(
                _err(
                    "evidence",
                    "supporting_chain_step_missing_evidence",
                    f"/supporting_chain/{idx}/evidence_used",
                    f"missing evidence in step {expected}",
                )
            )
    # Step D discriminator requirement: keep minimal and deterministic.
    preds = out.get("predictions")
    if not isinstance(preds, list) or len(preds) < 3:
        errors.append(
            _err(
                "evidence",
                "supporting_chain_step_d_requires_predictions_gte3",
                "/predictions",
                "predictions must contain at least 3 items",
            )
        )
    return errors


def validate_master_output(
    master_output: Dict[str, Any],
    reasoning_pack: Dict[str, Any],
    case_json: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Tuple[bool, List[Dict[str, str]], Dict[str, Any], List[str], List[str], List[Dict[str, Any]]]:
    """
    Semantic validations after schema parse.
    Returns: (ok, errors, normalized_output, used_case_paths, used_evidence_ids, used_evidence_expanded)
    """
    if not isinstance(master_output, dict):
        return (
            False,
            [_err("schema", "root_not_object", "$", "master_output must be a JSON object")],
            {},
            [],
            [],
            [],
        )

    out = deepcopy(master_output)
    if isinstance(out.get("__meta"), dict):
        out.pop("__meta", None)
    structural_errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    used_paths: List[str] = []
    used_evidence_ids: List[str] = []
    used_evidence: List[Dict[str, Any]] = []
    evidence_registry = _registry_map(reasoning_pack.get("evidence_registry") or {})
    policy = _policy(reasoning_config)
    top1_sim = _to_float((reasoning_pack.get("risk_scores") or {}).get("top1_sim"))
    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    trend_ids_in_registry = {
        eid
        for eid in (*ATB_TREND_EVIDENCE_IDS, *ATB_TREND_PROFILE_EVIDENCE_IDS)
        if eid in evidence_registry
    }
    emission_ids_in_registry = {
        eid
        for eid in TARGET_OBSERVATION_EVIDENCE_IDS
        if eid in evidence_registry
    }
    schema_version = str(reasoning_config.get("master_output_schema_version") or "v3").lower()

    # Phase A: structural validation (hard fail).
    structural_errors.extend(_validate_master_output_schema(out, schema_version=schema_version))
    if structural_errors:
        return False, structural_errors[:5], out, [], [], []

    threshold_values = _threshold_values(reasoning_config)
    threshold_keys = {str(k).lower() for k in _thresholds(reasoning_config).keys()}

    def _text_nodes(value: Any, path: str = "$") -> Iterable[Tuple[str, str]]:
        if isinstance(value, str):
            yield path, value
            return
        if isinstance(value, dict):
            for k, v in value.items():
                yield from _text_nodes(v, f"{path}/{k}")
            return
        if isinstance(value, list):
            for i, row in enumerate(value):
                yield from _text_nodes(row, f"{path}/{i}")

    def _contains_unapproved_threshold(text: str) -> bool:
        raw = str(text or "")
        lower = raw.lower()
        strong_trigger = bool(STRONG_THRESHOLD_TRIGGER_PATTERN.search(raw))
        weak_trigger_numeric = _weak_trigger_in_numeric_context(raw)
        threshold_like = bool(
            strong_trigger
            or weak_trigger_numeric
            or COMPARISON_PATTERN.search(raw)
            or INTERVAL_PATTERN.search(raw)
        )
        if not threshold_like:
            return False
        has_key = any(k in lower for k in threshold_keys)
        nums = [round(float(m), 6) for m in NUMBER_PATTERN.findall(raw)]
        has_allowed_num = any(
            any(abs(n - allow) <= 1e-6 for allow in threshold_values)
            for n in nums
        )
        return not (has_key and has_allowed_num)

    for text_path, text_value in _text_nodes(out):
        if _contains_unapproved_threshold(text_value):
            warnings.append(
                _warn(
                    "invented_threshold_not_allowed",
                    text_path,
                    "threshold/range text must use configured reasoning_config.thresholds",
                )
            )

    # Hard output budgets (prompt-constrained + validator-enforced).
    if isinstance(out.get("supporting_chain"), list) and len(out["supporting_chain"]) > MASTER_MAX_SUPPORTING_CHAIN_ITEMS:
        out["supporting_chain"] = out["supporting_chain"][:MASTER_MAX_SUPPORTING_CHAIN_ITEMS]
        warnings.append(_warn("supporting_chain_budget_trimmed", "/supporting_chain", "trimmed to budget"))
    if isinstance(out.get("predictions"), list) and len(out["predictions"]) > MASTER_MAX_PREDICTIONS_ITEMS:
        out["predictions"] = out["predictions"][:MASTER_MAX_PREDICTIONS_ITEMS]
        warnings.append(_warn("predictions_budget_trimmed", "/predictions", "trimmed to budget"))
    if isinstance(out.get("competing_hypotheses"), list) and len(out["competing_hypotheses"]) > MASTER_MAX_COMPETING_ITEMS:
        out["competing_hypotheses"] = out["competing_hypotheses"][:MASTER_MAX_COMPETING_ITEMS]
        warnings.append(_warn("competing_hypotheses_budget_trimmed", "/competing_hypotheses", "trimmed to budget"))
    if isinstance(out.get("evidence_used"), list) and len(out["evidence_used"]) > MASTER_MAX_EVIDENCE_USED_ITEMS:
        out["evidence_used"] = out["evidence_used"][:MASTER_MAX_EVIDENCE_USED_ITEMS]
        warnings.append(_warn("evidence_used_budget_trimmed", "/evidence_used", "trimmed to budget"))

    def _validate_and_collect(entry: Dict[str, Any], entry_path: str) -> Optional[str]:
        if "case_path" in entry:
            warnings.append(
                _warn(
                    "evidence_case_path_forbidden",
                    f"{entry_path}/case_path",
                    "case_path is forbidden in evidence_id mode; removed",
                )
            )
            entry.pop("case_path", None)
            return None
        ok, case_path, err, resolved = _resolve_evidence_entry(entry, evidence_registry, case_json, reasoning_pack)
        if not ok:
            code = str(err).split(":", 1)[0]
            warnings.append(_warn(code, entry_path, str(err)))
            entry["__drop__"] = True
            return None
        role = str(entry.get("role") or "").strip().lower()
        if (
            top1_sim is not None
            and top1_sim < float(policy["neighbor_support_min_sim"])
            and _is_neighbor_path(str(case_path))
            and role == "support"
        ):
            warnings.append(
                _warn(
                    "neighbor_support_disallowed_low_similarity",
                    str(case_path),
                    f"top1_sim={top1_sim} < neighbor_support_min_sim={policy['neighbor_support_min_sim']}; role downgraded to context",
                )
            )
            role = "context"
            entry["role"] = "context"
        evidence_id = str(entry.get("evidence_id") or "").strip()
        note = entry.get("note")
        if not isinstance(note, str):
            warnings.append(
                _warn(
                    "evidence_note_type_invalid",
                    f"{entry_path}/note",
                    "note coerced to string",
                )
            )
            note = str(note or "")
            entry["note"] = note
        if len(note) > MASTER_NOTE_MAX_CHARS:
            entry["note"] = note[:MASTER_NOTE_MAX_CHARS]
            warnings.append(_warn("evidence_note_trimmed", f"{entry_path}/note", "trimmed to note budget"))
        resolved_paths = []
        if isinstance(resolved, dict):
            for p in resolved.get("resolved_case_paths") or []:
                if isinstance(p, str) and p.strip():
                    resolved_paths.append(p.strip())
        if resolved_paths:
            used_paths.extend(resolved_paths)
        else:
            used_paths.append(str(case_path))
        used_evidence_ids.append(evidence_id)
        if isinstance(resolved, dict):
            reg = resolved.get("registry_entry") if isinstance(resolved.get("registry_entry"), dict) else {}
            used_evidence.append(
                {
                    "evidence_id": evidence_id,
                    "case_path": str(case_path),
                    "source_type": resolved.get("source_type"),
                    "pack_path": resolved.get("pack_path"),
                    "value_preview": resolved.get("value"),
                    "label": reg.get("label"),
                    "role": role,
                    "note": entry.get("note"),
                }
            )
        return str(case_path)

    # supporting_chain contract
    if schema_version != "v1":
        for row in _validate_supporting_chain_structure(out):
            if isinstance(row, dict):
                warnings.append(_warn(str(row.get("code") or "supporting_chain_warning"), str(row.get("path") or "/supporting_chain"), str(row.get("detail") or "")))
    chain = out.get("supporting_chain") if isinstance(out.get("supporting_chain"), list) else []
    step_a_atb = 0
    atb_citations = 0
    atb_support_citations = 0
    step_semantics = {
        "A": ("excited", "struct", "geometry", "dihedral", "atb"),
        "B": ("nonradiative", "channel", "redistribution", "electronic", "torsion", "relax"),
        "C": ("aggregation", "rigid", "rim", "suppress", "packing"),
        "D": ("discrimin", "test", "measure", "compare", "separate", "prediction"),
    }
    if isinstance(chain, list):
        for idx, row in enumerate(chain):
            if not isinstance(row, dict):
                continue
            step_id = str(row.get("step_id") or "")
            evidence_used = row.get("evidence_used")
            if isinstance(evidence_used, list):
                for j, ev in enumerate(evidence_used):
                    if not isinstance(ev, dict):
                        continue
                    case_path = _validate_and_collect(ev, f"/supporting_chain/{idx}/evidence_used/{j}")
                    if not case_path:
                        continue
                    role = str(ev.get("role") or "").lower()
                    if case_path.startswith("/evidence_readiness/atb/features_summary/"):
                        atb_citations += 1
                        if role == "support":
                            atb_support_citations += 1
                        if idx == 0:
                            step_a_atb += 1
            claim_text = f"{row.get('step_name') or ''} {row.get('claim') or ''}".lower()
            if step_id in step_semantics and not _has_any_token([claim_text], step_semantics[step_id]):
                warnings.append(
                    _warn(
                        "supporting_chain_step_semantics_missing",
                        f"/supporting_chain/{idx}",
                        f"semantic tokens for step {step_id} are missing",
                    )
                )

    if schema_version != "v1":
        if atb_citations < 2:
            warnings.append(
                _warn(
                    "supporting_chain_atb_citations_insufficient",
                    "/supporting_chain",
                    f"found {atb_citations}, require >=2",
                )
            )
        if atb_support_citations < 1:
            warnings.append(
                _warn(
                    "supporting_chain_atb_support_citations_insufficient",
                    "/supporting_chain",
                    f"found {atb_support_citations}, require >=1 support citation",
                )
            )
        if step_a_atb < 1:
            warnings.append(
                _warn(
                    "supporting_chain_step_a_missing_atb_citation",
                    "/supporting_chain/0",
                    "step A requires at least one aTB citation",
                )
            )

    # Validate non-chain evidence lists.
    top_evidence = out.get("evidence_used")
    if isinstance(top_evidence, list):
        for i, ev in enumerate(top_evidence):
            if isinstance(ev, dict):
                _validate_and_collect(ev, f"/evidence_used/{i}")
    for i, row in enumerate(out.get("competing_hypotheses") or []):
        if not isinstance(row, dict):
            continue
        ev_list = row.get("evidence_used")
        if isinstance(ev_list, list):
            for j, ev in enumerate(ev_list):
                if isinstance(ev, dict):
                    _validate_and_collect(ev, f"/competing_hypotheses/{i}/evidence_used/{j}")
    for i, row in enumerate(out.get("predictions") or []):
        if not isinstance(row, dict):
            continue
        ev_list = row.get("evidence_used")
        if isinstance(ev_list, list):
            for j, ev in enumerate(ev_list):
                if isinstance(ev, dict):
                    _validate_and_collect(ev, f"/predictions/{i}/evidence_used/{j}")

    # Ensure compact .aop transition cues participate in R2/R3 reasoning when available.
    aop_ids_in_registry = [eid for eid in AOP_COMPACT_EVIDENCE_IDS if eid in evidence_registry]
    if active_profile in {"R2", "R3"} and aop_ids_in_registry:
        used_ids_snapshot = {str(x) for x in used_evidence_ids if str(x)}
        if used_ids_snapshot.isdisjoint(aop_ids_in_registry):
            selected_eid = aop_ids_in_registry[0]
            auto_row = {
                "evidence_id": selected_eid,
                "role": "context",
                "note": "compact aop transition cue from target aTB cache",
            }
            ev_list = out.get("evidence_used")
            if not isinstance(ev_list, list):
                ev_list = []
            ev_list.insert(0, auto_row)
            out["evidence_used"] = ev_list[:MASTER_MAX_EVIDENCE_USED_ITEMS]
            _validate_and_collect(out["evidence_used"][0], "/evidence_used/0")
            warnings.append(
                _warn(
                    "r2_missing_aop_compact_citation_auto_added",
                    "/evidence_used",
                    f"added {selected_eid} because compact .aop evidence is available in this round",
                )
            )

    # Uniform multi-axis governance.
    used_ids_set = {str(x) for x in used_evidence_ids}
    governing_ids = _collect_governing_evidence_ids(out)
    axis_support_summary = _axis_support_summary(governing_ids)
    axis_role_summary = _merge_axis_role_summary(
        _axis_role_summary(out.get("evidence_used")),
        *[
            _axis_role_summary((row or {}).get("evidence_used"))
            for row in (out.get("supporting_chain") or [])
            if isinstance(row, dict)
        ],
    )
    primary_axes = [
        axis_name
        for axis_name in GOVERNING_PRIMARY_AXES
        if axis_role_summary.get(axis_name, {}).get("support")
    ]
    weakening_axes = [
        axis_name
        for axis_name in GOVERNING_PRIMARY_AXES
        if axis_role_summary.get(axis_name, {}).get("weakening")
    ]
    axis_count = len(primary_axes)
    single_axis_penalty_applied = False
    conflict_penalty_applied = False
    comparative_only_adjust_applied = False
    other_residual_support_applied = False
    r0_prior_only_penalty_applied = False

    def _apply_confidence_rule(*, factor: float = 1.0, cap: Optional[float] = None) -> None:
        mech = out.get("mechanism_claim")
        if not isinstance(mech, dict):
            return
        conf_val = _to_float(mech.get("confidence"))
        if conf_val is None:
            return
        new_conf = conf_val * float(factor)
        if cap is not None:
            new_conf = min(float(cap), new_conf)
        mech["confidence"] = max(0.05, min(0.95, new_conf))

    if bool(policy.get("comparative_axis_can_only_adjust")) and active_profile in {"R2", "R3"}:
        if axis_support_summary.get("comparative_transferability") and not primary_axes:
            warnings.append(
                _warn(
                    "comparative_only_support_not_allowed",
                    "/supporting_chain",
                    "comparative evidence can refine but cannot independently determine the mechanism label",
                )
            )
            out["status"] = "insufficient_evidence"
            if str(out.get("template_used") or "").strip().lower() == "stable":
                out["template_used"] = "mixture"
            _apply_confidence_rule(
                factor=float(policy.get("comparative_only_support_penalty_factor") or 0.85)
            )
            limits = _normalize_limits(out.get("limits"))
            msg = "Comparative evidence can refine but cannot independently determine the mechanism label."
            if msg not in limits:
                limits.append(msg)
            out["limits"] = limits
            comparative_only_adjust_applied = True

    if axis_count == 1:
        warnings.append(
            _warn(
                "single_axis_support_only",
                "/supporting_chain",
                f"winning claim is supported by one primary evidence axis only: {primary_axes[0]}",
            )
        )
        if str(out.get("status") or "").strip().lower() == "ok":
            out["status"] = "insufficient_evidence"
        if str(out.get("template_used") or "").strip().lower() == "stable":
            out["template_used"] = "mixture"
        _apply_confidence_rule(
            factor=float(policy.get("single_axis_penalty_factor") or 0.80),
            cap=float(policy.get("single_axis_confidence_cap") or 0.38),
        )
        limits = _normalize_limits(out.get("limits"))
        msg = "Primary claim is supported by only one evidence axis, so mechanism resolution remains underdetermined."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits
        single_axis_penalty_applied = True

    if active_profile == "R1" and trend_ids_in_registry and used_ids_set.isdisjoint(trend_ids_in_registry):
        warnings.append(
            _warn(
                "r1_missing_atb_self_trend_citation",
                "/supporting_chain",
                "R1 requires at least one self-trend evidence citation (E31..E34 or legacy E_ATB_TREND_*) when available",
            )
        )
        out["status"] = "insufficient_evidence"
        limits = _normalize_limits(out.get("limits"))
        msg = "R1 output lacks self-trend citation; confidence kept conservative until trend evidence is used."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits

    if active_profile == "R1" and emission_ids_in_registry and used_ids_set.isdisjoint(emission_ids_in_registry):
        warnings.append(
            _warn(
                "r1_missing_emission_observation_citation",
                "/supporting_chain",
                "R1 requires at least one emission observation evidence citation (E70..E73) when available",
            )
        )
        out["status"] = "insufficient_evidence"
        limits = _normalize_limits(out.get("limits"))
        msg = "R1 output lacks emission observation citation; target observation remains unused."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits

    warning_codes = {str(row.get("code") or "") for row in warnings if isinstance(row, dict)}
    unresolved_codes = {
        "single_axis_support_only",
        "comparative_only_support_not_allowed",
        "r1_missing_atb_self_trend_citation",
        "r1_missing_emission_observation_citation",
    }
    active_conflict_count = 0
    mech = out.get("mechanism_claim")
    primary_confidence = _to_float((mech or {}).get("confidence")) if isinstance(mech, dict) else None
    if primary_confidence is not None:
        for row in out.get("competing_hypotheses") or []:
            if not isinstance(row, dict):
                continue
            rival_conf = _to_float(row.get("confidence"))
            if rival_conf is None:
                continue
            if rival_conf >= max(0.30, primary_confidence - 0.15):
                active_conflict_count += 1
    conflict_factor = 1.0
    if active_conflict_count >= 2:
        conflict_factor *= float(policy.get("multi_active_conflict_penalty") or 0.80)
    elif active_conflict_count == 1:
        conflict_factor *= float(policy.get("one_active_conflict_penalty") or 0.90)
    if str(out.get("template_used") or "").strip().lower() == "mixture":
        conflict_factor *= float(policy.get("mixture_conflict_penalty") or 0.92)
    if warning_codes.intersection(unresolved_codes):
        conflict_factor *= float(policy.get("unresolved_warning_penalty") or 0.90)
    if conflict_factor < 0.999:
        _apply_confidence_rule(factor=conflict_factor)
        conflict_penalty_applied = True

    claim = out.get("mechanism_claim") if isinstance(out.get("mechanism_claim"), dict) else {}
    primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
    llm_primary_label = str(primary.get("mechanism_label") or "").strip()
    primary_label = llm_primary_label
    normalization_reason_codes: List[str] = []
    standard_candidate_names = [
        str((row or {}).get("name") or "").strip()
        for row in (out.get("competing_hypotheses") or [])
        if isinstance(row, dict) and str((row or {}).get("name") or "").strip() not in {"", "other", "unknown"}
    ]
    standard_candidates_in_play = list(standard_candidate_names)
    if primary_label and primary_label not in {"other", "unknown"} and primary_label not in standard_candidates_in_play:
        standard_candidates_in_play.insert(0, primary_label)
    target_side_support_ids = {
        *TARGET_OBSERVATION_EVIDENCE_IDS,
        *ATB_TREND_PROFILE_EVIDENCE_IDS,
        *ATB_ENRICHMENT_EVIDENCE_IDS,
        *AOP_COMPACT_EVIDENCE_IDS,
    }
    has_target_side_support = bool(used_ids_set.intersection(target_side_support_ids))
    standard_label_closure = None
    residual_other_admissible = False

    if active_profile == "R0":
        if str(out.get("template_used") or "").strip().lower() == "stable":
            warnings.append(
                _warn(
                    "r0_stable_template_forbidden",
                    "/template_used",
                    "R0 is a candidate-generation round; template changed from stable to mixture",
                )
            )
            out["template_used"] = "mixture"
        out["status"] = "insufficient_evidence"
        r0_cap = float(policy.get("r0_candidate_confidence_cap") or 0.30)
        _apply_confidence_rule(cap=r0_cap)
        if primary_label == "other":
            warnings.append(
                _warn(
                    "r0_other_primary_forbidden",
                    "/mechanism_claim/primary_hypothesis/mechanism_label",
                    "R0 cannot use 'other' as the primary label; converted to unknown",
                )
            )
            primary["mechanism_label"] = "unknown"
            primary_label = "unknown"
        competing = out.get("competing_hypotheses")
        if not isinstance(competing, list):
            competing = []
        existing_names = {
            str((row or {}).get("name") or "").strip()
            for row in competing
            if isinstance(row, dict)
        }
        candidate_fill = _candidate_pool_from_context(reasoning_pack, out, max_items=5)
        next_rank = len(competing) + 1
        for row in candidate_fill:
            cand_label = str(row.get("label") or "").strip()
            if not cand_label or cand_label in existing_names or cand_label == primary_label:
                continue
            competing.append(
                {
                    "name": cand_label,
                    "confidence": max(0.08, min(0.28, float(_to_float(row.get("probability")) or 0.18))),
                    "atb_support_level": str(primary.get("atb_support_level") or "none"),
                    "evidence_used": [],
                }
            )
            existing_names.add(cand_label)
            next_rank += 1
            if len(competing) >= 2:
                break
        out["competing_hypotheses"] = competing[:MASTER_MAX_COMPETING_ITEMS]
        limits = _normalize_limits(out.get("limits"))
        msg = "R0 is prior-only and keeps multiple candidates open until target-specific evidence arrives."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits[:6]
        r0_prior_only_penalty_applied = True

    if primary_label not in {"", "unknown", "other"}:
        closure_eval = evaluate_standard_label_closure(
            primary_axes=primary_axes,
            min_positive_axes=int(policy.get("standard_label_min_positive_axes") or 2),
            requires_target_axis=bool(policy.get("standard_label_requires_target_axis", True)),
        )
        standard_label_closure = str(closure_eval.get("status") or "unsupported")
    residual_eval = evaluate_residual_other_admissibility(
        active_profile=active_profile,
        has_target_side_support=has_target_side_support,
        standard_candidates_in_play=standard_candidates_in_play,
        standard_label_closed=bool(standard_label_closure == "closed"),
        primary_axes=primary_axes,
        weakening_axes=weakening_axes,
        active_conflict_count=active_conflict_count,
        min_standard_candidates=int(policy.get("residual_other_min_standard_candidates") or 2),
        min_conflicts=int(policy.get("residual_other_min_conflicts") or 2),
    )
    residual_other_admissible = bool(residual_eval.get("admissible"))
    novelty_eval = evaluate_novelty_candidate(
        reasoning_pack=reasoning_pack,
        residual_other_admissible=residual_other_admissible,
        active_conflict_count=active_conflict_count,
    )
    novelty_candidate = bool(novelty_eval.get("is_novelty_candidate"))
    novelty_basis = [str(x) for x in (novelty_eval.get("basis") or []) if str(x)]
    normalized_primary_label = primary_label or "unknown"
    canonical_pool_closed = bool(standard_label_closure == "closed")
    if active_profile == "R0" and primary_label not in {"", "unknown", "other"}:
        decision_state = "provisional_known"
        normalization_reason_codes.append("r0_prior_only_decision")
    elif primary_label == "other":
        if active_profile in {"R2", "R3"} and residual_other_admissible:
            decision_state = "residual_supported"
        else:
            decision_state = "insufficient_evidence"
            normalization_reason_codes.append("other_without_residual_admissibility")
    elif primary_label in {"", "unknown"}:
        decision_state = "insufficient_evidence"
        normalization_reason_codes.append("llm_unknown_retained" if primary_label == "unknown" else "missing_primary_label")
    elif standard_label_closure == "closed":
        decision_state = "closed_known"
    elif standard_label_closure == "provisional":
        decision_state = "provisional_known"
        normalization_reason_codes.append("standard_label_provisional")
    else:
        decision_state = "insufficient_evidence"
        normalization_reason_codes.append("standard_label_unsupported")

    if decision_state == "provisional_known":
        if str(out.get("status") or "").strip().lower() == "ok":
            out["status"] = "insufficient_evidence"
        if str(out.get("template_used") or "").strip().lower() == "stable":
            out["template_used"] = "mixture"
        _apply_confidence_rule(
            cap=float(
                policy.get("late_round_provisional_standard_confidence_cap")
                or policy.get("late_round_single_axis_standard_confidence_cap")
                or 0.32
            ),
        )
        limits = _normalize_limits(out.get("limits"))
        msg = "Current standard-label lead remains provisional until at least two primary axes close on the same mechanism."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits[:6]
    elif decision_state == "insufficient_evidence":
        if str(out.get("status") or "").strip().lower() == "ok":
            out["status"] = "insufficient_evidence"
        if str(out.get("template_used") or "").strip().lower() == "stable":
            out["template_used"] = "mixture"
        if primary_label == "other":
            limits = _normalize_limits(out.get("limits"))
            msg = "Residual 'other' remains provisional until late-round admissibility is confirmed."
            if msg not in limits:
                limits.append(msg)
            out["limits"] = limits[:6]
            _apply_confidence_rule(cap=0.18)
            other_residual_support_applied = True
        elif primary_label not in {"", "unknown"}:
            limits = _normalize_limits(out.get("limits"))
            msg = "Current lead remains unsupported under the available evidence; final adjudication may still resolve to unknown."
            if msg not in limits:
                limits.append(msg)
            out["limits"] = limits[:6]
            _apply_confidence_rule(cap=0.18)

    out_meta = out.get("__meta") if isinstance(out.get("__meta"), dict) else {}
    out_meta["axis_support_summary"] = {k: list(v) for k, v in axis_support_summary.items() if v}
    out_meta["axis_support_polarity_summary"] = {
        axis_name: {bucket: list(values) for bucket, values in buckets.items() if values}
        for axis_name, buckets in axis_role_summary.items()
        if any(buckets.get(bucket) for bucket in ("support", "weakening", "context"))
    }
    out_meta["axis_count"] = axis_count
    out_meta["positive_axis_count"] = len(primary_axes)
    out_meta["weakening_axis_count"] = len(weakening_axes)
    out_meta["single_axis_penalty_applied"] = single_axis_penalty_applied
    out_meta["active_conflict_count"] = active_conflict_count
    out_meta["conflict_penalty_applied"] = conflict_penalty_applied
    out_meta["comparative_only_adjust_applied"] = comparative_only_adjust_applied
    out_meta["other_residual_support_applied"] = other_residual_support_applied
    out_meta["r0_prior_only_penalty_applied"] = r0_prior_only_penalty_applied
    out_meta["llm_primary_label"] = llm_primary_label or None
    out_meta["normalized_primary_label"] = normalized_primary_label or None
    out_meta["standard_label_closure"] = standard_label_closure
    out_meta["decision_state"] = decision_state
    out_meta["canonical_pool_closed"] = canonical_pool_closed
    out_meta["residual_other_admissible"] = residual_other_admissible
    out_meta["novelty_candidate"] = novelty_candidate
    out_meta["novelty_basis"] = novelty_basis
    out_meta["normalization_reason_codes"] = list(dict.fromkeys(normalization_reason_codes))
    out_meta["residual_other_reasons"] = list(residual_eval.get("reasons") or [])
    out_meta["residual_other_qualifying_signals"] = list(residual_eval.get("qualifying_signals") or [])
    out["__meta"] = out_meta

    def _prune_evidence_list(rows: Any) -> List[Dict[str, Any]]:
        out_rows: List[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return out_rows
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.pop("__drop__", False):
                continue
            out_rows.append(row)
        return out_rows

    out["evidence_used"] = _prune_evidence_list(out.get("evidence_used"))
    for row in out.get("supporting_chain") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))
    for row in out.get("competing_hypotheses") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))
    for row in out.get("predictions") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))

    # Confidence sanity (single cap is applied in _soft_confidence upstream).
    confidence = (
        (out.get("mechanism_claim") or {}).get("confidence")
        if isinstance(out.get("mechanism_claim"), dict)
        else None
    )
    try:
        conf_val = float(confidence)
        if conf_val < 0.05 or conf_val > 0.95:
            bounded = max(0.05, min(0.95, conf_val))
            warnings.append(
                _warn(
                    "confidence_out_of_bounds",
                    "/mechanism_claim/confidence",
                    f"{conf_val} outside [0.05,0.95]; normalized to {bounded}",
                )
            )
            if isinstance(out.get("mechanism_claim"), dict):
                out["mechanism_claim"]["confidence"] = float(bounded)
    except Exception:
        warnings.append(
            _warn(
                "mechanism_claim_confidence_invalid",
                "/mechanism_claim/confidence",
                "confidence coerced to 0.05",
            )
        )
        if isinstance(out.get("mechanism_claim"), dict):
            out["mechanism_claim"]["confidence"] = 0.05

    # Conservative constraints
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    if gate_mode == "conservative":
        tpl = str(out.get("template_used") or "").lower()
        if tpl == "novelty":
            warnings.append(
                _warn(
                    "conservative_mode_template_novelty_forbidden",
                    "/template_used",
                    "template changed from novelty to mixture in conservative mode",
                )
            )
            out["template_used"] = "mixture"

        limits = _normalize_limits(out.get("limits"))
        out["limits"] = limits
        limits_lower = [x.lower() for x in limits]
        conservative_tokens = [
            "conservative",
            "uncertain",
            "uncertainty",
            "tentative",
            "cautious",
            "not definitive",
            "ambig",
            "low similarity",
            "weak evidence",
            "insufficient",
        ]
        if not _has_any_token(limits_lower, conservative_tokens):
            out["limits"].append(STANDARD_LIMIT_CONSERVATIVE)
            limits_lower.append(STANDARD_LIMIT_CONSERVATIVE.lower())

        tf = reasoning_pack.get("target_fields") or {}
        no_emission = tf.get("emission_aggr_nm") is None and tf.get("emission_solid_or_film_nm") is None
        no_emission_tokens = [
            "no emission evidence",
            "without emission",
            "emission missing",
            "missing emission",
            "no direct emission",
            "emission fields missing",
            "emission not available",
            "no emission-field confirmation",
        ]
        if no_emission and not _has_any_token(limits_lower, no_emission_tokens):
            out["limits"].append(STANDARD_LIMIT_NO_EMISSION)

    runtime = reasoning_pack.get("runtime") or {}
    run_lane = str(runtime.get("run_lane") or "").lower()
    literature = (reasoning_pack.get("evidence_readiness") or {}).get("literature") or {}
    experiment = (reasoning_pack.get("evidence_readiness") or {}).get("experiment") or {}
    lit_disabled = "lane_disabled" in str(literature.get("notes") or "").lower() or run_lane == "atb_cache_only"
    exp_disabled = "lane_disabled" in str(experiment.get("notes") or "").lower() or run_lane == "atb_cache_only"
    if lit_disabled or exp_disabled:
        limits = _normalize_limits(out.get("limits"))
        limits_lower = [x.lower() for x in limits]
        lane_tokens = ["lane is disabled", "missing external verification", "literature", "experiment"]
        if not _has_any_token(limits_lower, lane_tokens):
            out["limits"] = limits + [STANDARD_LIMIT_LANE_DISABLED]

    # aTB support-level consistency
    if schema_version != "v1":
        expected_level = _atb_support_level_from_features(reasoning_pack, reasoning_config)
        mech = out.get("mechanism_claim") if isinstance(out.get("mechanism_claim"), dict) else {}
        primary_raw = mech.get("primary_hypothesis") if isinstance(mech, dict) else {}
        primary = primary_raw if isinstance(primary_raw, dict) else {}
        observed_level = str(primary.get("atb_support_level") or "none")
        if expected_level == "none" and observed_level in {"weak", "strong"}:
            warnings.append(
                _warn(
                    "atb_support_level_inconsistent",
                    "/mechanism_claim/primary_hypothesis/atb_support_level",
                    f"observed {observed_level}, expected <= none; corrected",
                )
            )
            primary["atb_support_level"] = "none"
        elif expected_level == "weak" and observed_level == "strong":
            warnings.append(
                _warn(
                    "atb_support_level_inconsistent",
                    "/mechanism_claim/primary_hypothesis/atb_support_level",
                    "observed strong, expected <= weak; corrected",
                )
            )
            primary["atb_support_level"] = "weak"
        elif expected_level == "strong" and observed_level == "none":
            limits = _normalize_limits(out.get("limits"))
            warn = "aTB delta_dihedral suggests strong torsional access; claim keeps conservative atb_support_level=none."
            if warn not in limits:
                limits.append(warn)
            out["limits"] = limits

    used_paths = sorted(set(used_paths))
    def _eid_sort_key(eid: str) -> Tuple[int, int, str]:
        token = str(eid or "")
        if token.startswith("E") and token[1:].isdigit():
            return (0, int(token[1:]), token)
        if token.startswith("E_ATB_TREND_") and token.split("_")[-1].isdigit():
            return (1, int(token.split("_")[-1]), token)
        return (2, 0, token)

    used_evidence_ids = sorted(set(used_evidence_ids), key=_eid_sort_key)
    dedup_used_evidence: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for row in used_evidence:
        key = (str(row.get("evidence_id") or ""), str(row.get("case_path") or ""))
        if key in seen:
            continue
        seen.add(key)
        dedup_used_evidence.append(row)
    issues = warnings
    return len(structural_errors) == 0, issues, out, used_paths, used_evidence_ids, dedup_used_evidence


def _set_or_replace_op(case_json: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    found, _ = _resolve_pointer(case_json, path)
    return {"op": "replace" if found else "add", "path": path, "value": value}


def build_master_patch(
    case_json: Dict[str, Any],
    normalized_output: Optional[Dict[str, Any]],
    *,
    status: str,
    used_paths: Sequence[str],
    used_evidence_ids: Sequence[str],
    used_evidence: Sequence[Dict[str, Any]],
    meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    patch: List[Dict[str, Any]] = []
    if not isinstance(case_json.get("reasoning"), dict):
        patch.append({"op": "add", "path": "/reasoning", "value": {}})
    if normalized_output is not None:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", normalized_output))
        patch.append(_set_or_replace_op(case_json, "/reasoning/master_reasoning", normalized_output))
    else:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", None))
        patch.append(_set_or_replace_op(case_json, "/reasoning/master_reasoning", None))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_status", status))
    patch.append(_set_or_replace_op(case_json, "/reasoning/status", status))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_used_evidence_paths", list(used_paths)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence_paths", list(used_paths)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence_ids", list(used_evidence_ids)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence", list(used_evidence)))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_meta", meta))
    patch.append(_set_or_replace_op(case_json, "/reasoning/meta", meta))
    return patch


def _resolve_master_llm_params(
    reasoning_config: Dict[str, Any],
    llm_client: ResponsesLLMClient,
) -> Tuple[Optional[str], int, float, bool]:
    master_cfg = reasoning_config.get("master") if isinstance(reasoning_config.get("master"), dict) else {}
    effort: Optional[str]
    if "reasoning_effort" in master_cfg and master_cfg.get("reasoning_effort") is not None:
        effort = str(master_cfg.get("reasoning_effort"))
    elif reasoning_config.get("reasoning_effort") is not None:
        effort = str(reasoning_config.get("reasoning_effort"))
    elif llm_client.reasoning_effort is not None:
        effort = str(llm_client.reasoning_effort)
    else:
        effort = "medium"

    if effort in {"xhigh", "high"} and reasoning_config.get("reasoning_effort") is None and "reasoning_effort" not in master_cfg:
        effort = "medium"

    max_output_tokens = int(master_cfg.get("max_output_tokens") or llm_client.max_output_tokens)
    temp_raw = master_cfg.get("temperature", reasoning_config.get("temperature", llm_client.temperature))
    temperature = float(temp_raw) if isinstance(temp_raw, (int, float)) else float(MASTER_DEFAULT_TEMPERATURE)
    use_json_schema = bool(
        master_cfg.get("use_json_schema")
        if "use_json_schema" in master_cfg
        else reasoning_config.get("use_json_schema", False)
    )
    return effort, max_output_tokens, temperature, use_json_schema


def _clone_llm_client(
    llm_client: ResponsesLLMClient,
    *,
    reasoning_effort: Optional[str],
    max_output_tokens: int,
    temperature: Optional[float],
    model: Optional[str] = None,
) -> ResponsesLLMClient:
    return ResponsesLLMClient(
        base_url=llm_client.base_url,
        model=str(model or llm_client.model),
        api_key_env=llm_client.api_key_env,
        max_output_tokens=int(max_output_tokens),
        reasoning_effort=reasoning_effort,
        temperature=temperature,
    )


def _repair_json_only(
    *,
    llm_client: ResponsesLLMClient,
    raw_text: str,
    schema_name: str,
    schema: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Dict[str, Any]:
    _ = llm_client, schema_name, schema
    pack = reasoning_config.get("__repair_reasoning_pack") if isinstance(reasoning_config, dict) else None
    if not isinstance(pack, dict):
        pack = {}
    template_fallback = str(reasoning_config.get("__repair_template") or "mixture")
    out_obj = _tagged_text_to_master_output(
        raw_text=str(raw_text or ""),
        reasoning_pack=pack,
        reasoning_config=reasoning_config if isinstance(reasoning_config, dict) else {},
        template_fallback=template_fallback,
    )
    return {
        "parsed": out_obj,
        "request": {
            "mode": "local_tagged_repair",
            "schema_name": MASTER_OUTPUT_SCHEMA_VERSION_V3,
        },
        "response": {
            "mode": "local_tagged_repair",
            "raw_text": str(raw_text or "")[:2000],
        },
        "model": "local_tagged_repair",
        "reasoning_effort": "none",
    }


def run_master_reasoner_once(
    case_json: Dict[str, Any],
    reasoning_config: Dict[str, Any],
    llm_client: ResponsesLLMClient,
    reasoning_pack: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pack = deepcopy(reasoning_pack) if isinstance(reasoning_pack, dict) else build_reasoning_pack(case_json, reasoning_config)
    pack_hash = sha256_json(pack)
    prompt_bundle = build_master_prompt_bundle(pack, reasoning_config)
    input_text = json.dumps(prompt_bundle.get("user_payload"), ensure_ascii=False, indent=2)
    output_mode = str(prompt_bundle.get("output_mode") or MASTER_OUTPUT_MODE_TAGGED_REPAIR).strip().lower()
    instructions = f"{prompt_bundle.get('system')}\n\n{prompt_bundle.get('instructions')}"
    schema_name = str(prompt_bundle.get("output_schema_name") or MASTER_OUTPUT_SCHEMA_VERSION)
    schema = prompt_bundle.get("output_schema") or master_output_schema()

    effort, max_tokens, temperature, use_json_schema = _resolve_master_llm_params(reasoning_config, llm_client)
    primary_client = _clone_llm_client(
        llm_client,
        reasoning_effort=effort,
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    llm_failure_reason: Optional[str] = None
    parsed: Dict[str, Any] = {}
    llm_request: Any = None
    llm_response_raw: Any = None

    def _failed_out(*, failure_reason: str, detail: str, request_obj: Any, response_obj: Any) -> Dict[str, Any]:
        return {
            "reasoning_pack": pack,
            "pack_hash": pack_hash,
            "prompt_bundle": prompt_bundle,
            "template_used": prompt_bundle.get("template_used"),
            "llm_request": request_obj,
            "llm_response_raw": response_obj,
            "master_output_raw": response_obj,
            "master_output_parsed": {},
            "normalized_output": None,
            "validation_errors": [
                _err("evidence", "llm_error", "$", detail),
            ],
            "used_case_paths": [],
            "used_evidence_ids": [],
            "used_evidence": [],
            "status": "failed_llm",
            "llm_failure_reason": failure_reason,
            "confidence_meta": {},
        }

    def _invoke_once(client: ResponsesLLMClient, *, call_instructions: str, out_tokens: int) -> Dict[str, Any]:
        if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA and hasattr(client, "responses_text"):
            try:
                out = client.responses_text(
                    instructions=call_instructions,
                    input_text=input_text,
                    max_output_tokens=out_tokens,
                    temperature=temperature,
                )
            except Exception as exc:
                # Backward-compatible test path: if text call cannot initialize client, allow json fallback.
                if "missing_api_key_env" in str(exc) and hasattr(client, "responses_json"):
                    return client.responses_json(
                        instructions=call_instructions,
                        input_text=input_text,
                        schema_name=schema_name,
                        schema=schema,
                        max_output_tokens=out_tokens,
                        temperature=temperature,
                        use_json_schema=use_json_schema,
                    )
                raise
            text = str(out.get("text") or "")
            if text.strip():
                parsed_text = _tagged_text_to_master_output(
                    raw_text=text,
                    reasoning_pack=pack,
                    reasoning_config=reasoning_config,
                    template_fallback=str(prompt_bundle.get("template_used") or "mixture"),
                )
                return {
                    "request": out.get("request"),
                    "response": out.get("response"),
                    "text": text,
                    "parsed": parsed_text,
                }
            raise RuntimeError(
                json.dumps(
                    {
                        "code": "no_message_output",
                        "details": {
                            "last_request": out.get("request"),
                            "last_response": out.get("response"),
                            "last_text": text,
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return client.responses_json(
            instructions=call_instructions,
            input_text=input_text,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=out_tokens,
            temperature=temperature,
            use_json_schema=use_json_schema,
        )

    def _parse_runtime_exc(exc: BaseException) -> Tuple[str, Dict[str, Any]]:
        if isinstance(exc, Exception):
            payload = _llm_error_payload(exc)
            if payload:
                return _llm_failure_reason_from_exc(exc), payload
        raw = str(exc or "")
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                code = str(obj.get("code") or "json_parse_error")
                details = obj.get("details") if isinstance(obj.get("details"), dict) else {}
                return code, details
        except Exception:
            pass
        return _llm_failure_reason_from_exc(exc), {}

    first_text = ""
    try:
        first_out = _invoke_once(
            primary_client,
            call_instructions=(
                instructions
                + (
                    "\n\nOutput mode reminder: respond in tagged natural language sections only."
                    if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
                    else "\n\nReturn JSON only."
                )
            ),
            out_tokens=max_tokens,
        )
        llm_request = first_out.get("request")
        llm_response_raw = first_out.get("response")
        first_text = str(first_out.get("text") or "")
        if first_text:
            llm_response_raw = {"primary_response": llm_response_raw, "primary_text": first_text}
        parsed = first_out.get("parsed") or {}
    except Exception as first_exc:
        llm_failure_reason, first_payload = _parse_runtime_exc(first_exc)
        first_request = first_payload.get("last_request")
        first_response = first_payload.get("last_response")
        first_text = str(first_payload.get("last_text") or "")
        if llm_failure_reason not in {"no_message_output", "json_parse_error"}:
            return _failed_out(
                failure_reason=llm_failure_reason,
                detail=str(first_exc),
                request_obj=first_request,
                response_obj=first_response,
            )

        retry_client = _clone_llm_client(
            llm_client,
            reasoning_effort=effort,
            max_output_tokens=max(max_tokens, MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS),
            temperature=temperature,
        )
        retry_instructions = (
            instructions
            + (
                "\n\nRetry instruction: respond with concise natural language only. Avoid markdown/fences."
                if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
                else "\n\nRetry instruction: return JSON only; no trailing text."
            )
        )
        try:
            retry_out = _invoke_once(
                retry_client,
                call_instructions=retry_instructions,
                out_tokens=max(max_tokens, MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS),
            )
            llm_request = retry_out.get("request")
            llm_response_raw = retry_out.get("response")
            parsed = retry_out.get("parsed") or {}
            second_text = str(retry_out.get("text") or "")
            if second_text:
                llm_response_raw = {"retry_response": llm_response_raw, "retry_text": second_text}
        except Exception as second_exc:
            second_reason, second_payload = _parse_runtime_exc(second_exc)
            second_text = str(second_payload.get("last_text") or "")
            raw_repair_text = second_text or first_text
            if bool(reasoning_config.get("json_repair_enabled", True)) and raw_repair_text.strip():
                try:
                    repair_out = _repair_json_only(
                        llm_client=llm_client,
                        raw_text=raw_repair_text,
                        schema_name=schema_name,
                        schema=schema,
                        reasoning_config={
                            **(reasoning_config or {}),
                            "__repair_reasoning_pack": pack,
                            "__repair_template": str(prompt_bundle.get("template_used") or "mixture"),
                        },
                    )
                    parsed = repair_out.get("parsed") or {}
                    llm_request = {
                        "primary_request": first_request,
                        "retry_request": second_payload.get("last_request"),
                        "repair_request": repair_out.get("request"),
                    }
                    llm_response_raw = {
                        "primary_response": first_response,
                        "primary_text": first_text,
                        "retry_response": second_payload.get("last_response"),
                        "retry_text": second_text,
                        "repair_response": repair_out.get("response"),
                    }
                    llm_failure_reason = "json_repair_used"
                except Exception as repair_exc:
                    detail = (
                        f"retry_failed:{second_exc}; "
                        f"repair_failed:{repair_exc}"
                    )
                    return _failed_out(
                        failure_reason=second_reason,
                        detail=detail,
                        request_obj={
                            "primary_request": first_request,
                            "retry_request": second_payload.get("last_request"),
                        },
                        response_obj={
                            "primary_response": first_response,
                            "primary_text": first_text,
                            "retry_response": second_payload.get("last_response"),
                            "retry_text": second_text,
                        },
                    )
            else:
                return _failed_out(
                    failure_reason=second_reason,
                    detail=str(second_exc),
                    request_obj={
                        "primary_request": first_request,
                        "retry_request": second_payload.get("last_request"),
                    },
                    response_obj={
                        "primary_response": first_response,
                        "primary_text": first_text,
                        "retry_response": second_payload.get("last_response"),
                        "retry_text": second_text,
                    },
                )

    try:
        confidence_meta = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("__meta"), dict):
            confidence_meta = dict(parsed.get("__meta") or {})
        ok, errors, normalized_output, used_paths, used_evidence_ids, used_evidence = validate_master_output(
            parsed,
            pack,
            case_json,
            reasoning_config,
        )
    except Exception as validate_exc:  # pragma: no cover - defensive
        return {
            "reasoning_pack": pack,
            "pack_hash": pack_hash,
            "prompt_bundle": prompt_bundle,
            "template_used": prompt_bundle.get("template_used"),
            "llm_request": llm_request,
            "llm_response_raw": llm_response_raw,
            "master_output_raw": llm_response_raw,
            "master_output_parsed": parsed if isinstance(parsed, dict) else {},
            "normalized_output": None,
            "validation_errors": [
                _err("evidence", "internal_error", "$", str(validate_exc)),
            ],
            "used_case_paths": [],
            "used_evidence_ids": [],
            "used_evidence": [],
            "status": "failed_schema_validation",
            "llm_failure_reason": llm_failure_reason,
            "confidence_meta": {},
        }
    errors = errors[:5]
    return {
        "reasoning_pack": pack,
        "pack_hash": pack_hash,
        "prompt_bundle": prompt_bundle,
        "template_used": prompt_bundle.get("template_used"),
        "llm_request": llm_request,
        "llm_response_raw": llm_response_raw,
        "master_output_raw": llm_response_raw,
        "master_output_parsed": parsed,
        "normalized_output": normalized_output,
        "validation_errors": errors,
        "used_case_paths": used_paths,
        "used_evidence_ids": used_evidence_ids,
        "used_evidence": used_evidence,
        "status": "success" if ok else "failed_schema_validation",
        "llm_failure_reason": llm_failure_reason,
        "confidence_meta": confidence_meta,
    }

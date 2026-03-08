"""
Iterative reasoning closure runner (Round0 -> RoundN).

R0 default behavior (dryrun_then_commit): write only /iterative/* state.
R1+ may write reasoning/post_uq outputs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.agents.judge_agent import (
    apply_evaluator_confidence_adjustment,
    build_eval_report,
    build_post_uq_from_eval,
)
from src.agents.judge_agent import JudgeAgent
from src.agents.llm_evaluator import LLMEvaluator, merge_eval_report_with_llm_layer
from src.agents.reasoning_agent import ReasoningAgent
from src.core.hashing import sha256_json
from src.core.patching import apply_patch, validate_patch
from src.core.types import AgentContext
from src.reasoning.evidence_profiles import ROUND_PROFILE_ORDER, default_evidence_profiles, next_profile_name
from src.reasoning.master_reasoner import build_master_patch, run_master_reasoner_once
from src.reasoning.reasoning_config import build_allowed_mechanism_labels, build_reasoning_policy
from src.tools.llm_client import LLMClientError, ResponsesLLMClient
from src.tools.llm_trace_store import (
    resolve_rounds_trace_dir,
    write_agent_response_trace,
    write_eval_report,
    write_master_round_report,
    write_round_state,
)
from src.orchestration.run_status import (
    atomic_write_json,
    emit_error_summary,
    emit_progress_event,
    now_iso8601,
    summarize_errors,
)


ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT = "dryrun_then_commit"
ROUND_RUNNER_MODE_COMMIT_ALL = "commit_all_rounds"
ROUND_RUNNER_MODES = {ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, ROUND_RUNNER_MODE_COMMIT_ALL}
R2_DISCRIMINATIVE_EVIDENCE_IDS = {"E21", "E22", "E23", "E24"}


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _resolve_round_profile(name: str) -> str:
    n = str(name or "R0").upper()
    return n if n in ROUND_PROFILE_ORDER else "R0"


def _build_reasoning_config(
    ctx: AgentContext,
    *,
    active_profile: str,
    round_index: int,
    profiles_cfg: Dict[str, Any],
    neighbor_topk: int = 10,
    evaluator_use_llm: bool = False,
    master_model: Optional[str] = None,
    master_reasoning_effort: Optional[str] = None,
    evaluator_model: Optional[str] = None,
    evaluator_reasoning_effort: Optional[str] = None,
    evaluator_confidence_adjustment_enabled: bool = False,
    evaluator_confidence_adjustment_max_abs_delta: float = 0.05,
) -> Dict[str, Any]:
    policy = build_reasoning_policy()
    resolved_master_model = str(master_model or ctx.model)
    resolved_master_effort = master_reasoning_effort if master_reasoning_effort is not None else (ctx.llm_reasoning_effort or "medium")
    resolved_neighbor_topk = max(1, int(neighbor_topk or 10))
    profiles_copy = deepcopy((profiles_cfg or {}).get("profiles") or {})
    for name, row in profiles_copy.items():
        if not isinstance(row, dict):
            continue
        if str(name).upper() in {"R0", "R1", "R2", "R3"}:
            row["neighbor_topk"] = resolved_neighbor_topk

    return {
        "run_lane": ctx.run_lane,
        # Backward-compatible top-level mirrors master defaults.
        "model": resolved_master_model,
        "reasoning_effort": resolved_master_effort,
        "temperature": float(ctx.llm_temperature),
        "use_json_schema": bool(ctx.llm_use_json_schema),
        "master_output_mode": "tagged_repair",
        "round_index": int(round_index),
        "allowed_mechanism_labels": build_allowed_mechanism_labels(),
        "master": {
            "model": resolved_master_model,
            "reasoning_effort": resolved_master_effort,
            "temperature": float(ctx.llm_temperature),
            "use_json_schema": bool(ctx.llm_use_json_schema),
        },
        "pack_version": "master_pack_v1",
        "prompt_bundle_version": "master_prompt_bundle_v1",
        "master_output_schema_version": "v3",
        "conservative_confidence_cap": 0.65,
        "policy": policy,
        "thresholds": {
            "neighbor_support_min_sim": policy["neighbor_support_min_sim"],
            "atb_dihedral_thresh_none": policy["atb_dihedral_thresh_none"],
            "atb_dihedral_thresh_strong": policy["atb_dihedral_thresh_strong"],
            "atb_dihedral_flat_eps": policy.get("atb_dihedral_flat_eps", 1.0e-6),
            "atb_gap_flat_eps": policy.get("atb_gap_flat_eps", 0.05),
            "atb_gap_weak": policy.get("atb_gap_weak", 0.2),
            "atb_gap_strong": policy.get("atb_gap_strong", 0.6),
            "atb_vol_flat_eps": policy.get("atb_vol_flat_eps", 0.1),
            "atb_vol_weak": policy.get("atb_vol_weak", 0.5),
            "atb_vol_strong": policy.get("atb_vol_strong", 2.0),
            "top1_sim_low": policy["top1_sim_low"],
            "entropy_high": policy["entropy_high"],
            "global_confidence_cap": policy.get("global_confidence_cap", 0.95),
            "r0_penalty_factor": policy.get("r0_penalty_factor", 0.90),
            "conservative_confidence_cap": 0.65,
        },
        "evaluator_confidence_adjustment": {
            "enabled": bool(evaluator_confidence_adjustment_enabled),
            "max_abs_delta": float(max(0.0, min(0.2, evaluator_confidence_adjustment_max_abs_delta))),
            "require_new_evidence": True,
            "high_weight_evidence_ids": ["E21", "E22", "E23", "E24"],
        },
        "evidence_profiles": {
            "active_profile": active_profile,
            "profiles": profiles_copy,
        },
        "evaluator": {
            "use_llm": bool(evaluator_use_llm),
            # None means inherit from master.
            "model": evaluator_model,
            "reasoning_effort": evaluator_reasoning_effort,
            "use_json_schema": bool(ctx.llm_use_json_schema),
        },
    }


def _llm_client(
    ctx: AgentContext,
    *,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> ResponsesLLMClient:
    return ResponsesLLMClient(
        base_url=ctx.base_url,
        model=str(model or ctx.model),
        api_key_env=ctx.llm_api_key_env,
        max_output_tokens=ctx.llm_max_output_tokens,
        reasoning_effort=reasoning_effort if reasoning_effort is not None else ctx.llm_reasoning_effort,
        temperature=ctx.llm_temperature,
    )


def _resolve_master_llm_config(reasoning_config: Dict[str, Any], ctx: AgentContext) -> Tuple[str, Optional[str]]:
    master_cfg = reasoning_config.get("master") if isinstance(reasoning_config.get("master"), dict) else {}
    model = str(master_cfg.get("model") or reasoning_config.get("model") or ctx.model)
    if "reasoning_effort" in master_cfg:
        effort = master_cfg.get("reasoning_effort")
    else:
        effort = reasoning_config.get("reasoning_effort", ctx.llm_reasoning_effort)
    return model, effort


def _resolve_evaluator_llm_config(reasoning_config: Dict[str, Any], ctx: AgentContext) -> Tuple[str, Optional[str]]:
    master_model, master_effort = _resolve_master_llm_config(reasoning_config, ctx)
    eval_cfg = reasoning_config.get("evaluator") if isinstance(reasoning_config.get("evaluator"), dict) else {}
    model = str(eval_cfg.get("model") or master_model)
    if "reasoning_effort" in eval_cfg and eval_cfg.get("reasoning_effort") is not None:
        effort = eval_cfg.get("reasoning_effort")
    else:
        effort = master_effort
    return model, effort


def _extract_mechanism_claim(master_out: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(master_out, dict):
        return {}
    claim = master_out.get("mechanism_claim")
    return claim if isinstance(claim, dict) else {}


def _build_master_round_report(
    *,
    round_index: int,
    active_profile: str,
    status: str,
    master_out: Dict[str, Any],
    validation_errors: Sequence[Dict[str, Any]],
    used_evidence_ids: Sequence[str],
    used_case_paths: Sequence[str],
    llm_failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    claim = _extract_mechanism_claim(master_out)
    primary = claim.get("primary_hypothesis") if isinstance(claim.get("primary_hypothesis"), dict) else {}
    chain = master_out.get("supporting_chain") if isinstance(master_out.get("supporting_chain"), list) else []
    conflict_points = [
        {
            "code": str(row.get("code") or ""),
            "path": str(row.get("path") or ""),
            "detail": str(row.get("detail") or ""),
        }
        for row in validation_errors[:5]
        if isinstance(row, dict)
    ]
    return {
        "round_index": int(round_index),
        "active_profile": active_profile,
        "status": status,
        "hypothesis": {
            "mechanism_label": primary.get("mechanism_label"),
            "aie_rationale_type": primary.get("aie_rationale_type"),
            "atb_support_level": primary.get("atb_support_level"),
            "template_used": master_out.get("template_used"),
        },
        "claim_set": [
            {
                "step_id": row.get("step_id"),
                "step_name": row.get("step_name"),
                "claim": row.get("claim"),
            }
            for row in chain
            if isinstance(row, dict)
        ],
        "conflict_points": conflict_points,
        "confidence": _to_float(claim.get("confidence")),
        "used_evidence_ids": list(used_evidence_ids),
        "used_case_paths": list(used_case_paths),
        "llm_failure_reason": llm_failure_reason,
        "updated_at": _now_iso8601(),
    }


def _apply_scoped_patch(
    case_json: Dict[str, Any],
    patch_ops: List[Dict[str, Any]],
    *,
    allowed_prefixes: Sequence[str],
    append_only_prefixes: Sequence[str] = (),
) -> Dict[str, Any]:
    validate_patch(
        patch_ops,
        allowed_prefixes=tuple(allowed_prefixes),
        append_only_prefixes=tuple(append_only_prefixes),
    )
    return apply_patch(case_json, patch_ops)


def _iterative_patch(
    *,
    mode: str,
    round_index: int,
    active_profile: str,
    last_round_status: str,
    last_round_state_path: str,
) -> List[Dict[str, Any]]:
    return [
        {"op": "add", "path": "/iterative/enabled", "value": True},
        {"op": "add", "path": "/iterative/mode", "value": mode},
        {"op": "add", "path": "/iterative/current_round", "value": int(round_index)},
        {"op": "add", "path": "/iterative/active_profile", "value": active_profile},
        {"op": "add", "path": "/iterative/last_round_status", "value": last_round_status},
        {"op": "add", "path": "/iterative/last_round_state_path", "value": str(last_round_state_path)},
        {"op": "add", "path": "/iterative/updated_at", "value": _now_iso8601()},
    ]


def _round_state_payload(
    *,
    round_index: int,
    active_profile: str,
    master_report: Dict[str, Any],
    eval_report: Dict[str, Any],
    chosen_next_round_profile: str,
    profile_adjustment_reason: str,
    prev_master_report: Optional[Dict[str, Any]],
    prev_used_ids: Sequence[str],
    effective_added_ids: Sequence[str],
    prev_conflict_ids: Sequence[str],
) -> Dict[str, Any]:
    prev = prev_master_report or {}
    prev_hyp = (prev.get("hypothesis") or {}) if isinstance(prev, dict) else {}
    cur_hyp = master_report.get("hypothesis") or {}

    prev_label = str(prev_hyp.get("mechanism_label") or "")
    cur_label = str(cur_hyp.get("mechanism_label") or "")
    prev_tpl = str(prev_hyp.get("template_used") or "")
    cur_tpl = str(cur_hyp.get("template_used") or "")
    prev_conf = _to_float(prev.get("confidence")) if isinstance(prev, dict) else None
    cur_conf = _to_float(master_report.get("confidence"))
    if prev_conf is None:
        prev_conf = cur_conf
    if cur_conf is None:
        cur_conf = prev_conf
    if prev_conf is None:
        prev_conf = 0.0
    if cur_conf is None:
        cur_conf = 0.0
    conf_delta = round(float(cur_conf) - float(prev_conf), 6)
    hypothesis_changed = (prev_label != cur_label) or (prev_tpl != cur_tpl)

    prev_ids = set(str(x) for x in prev_used_ids if str(x))
    cur_ids = set(str(x) for x in (master_report.get("used_evidence_ids") or []) if str(x))
    added_ids = sorted(cur_ids - prev_ids)
    removed_ids = sorted(prev_ids - cur_ids)

    prev_conf_set = set(str(x) for x in prev_conflict_ids if str(x))
    cur_conf_set = set(
        str((row or {}).get("conflict_id") or "")
        for row in (eval_report.get("conflict_adjudication") or [])
        if isinstance(row, dict) and str((row or {}).get("status") or "").lower() != "resolved"
    )
    cur_conf_set.discard("")

    return {
        "round_index": int(round_index),
        "active_profile": active_profile,
        "llm_failure_reason": str(master_report.get("llm_failure_reason") or "") or None,
        "hypothesis_delta": {
            "changed": bool(hypothesis_changed),
            "primary_label_before": prev_label or None,
            "primary_label_after": cur_label or None,
            "confidence_before": prev_conf,
            "confidence_after": cur_conf,
            "confidence_delta": conf_delta,
            "template_before": prev_tpl or None,
            "template_after": cur_tpl or None,
        },
        "new_evidence_used": {
            "added_ids": added_ids,
            "effective_added_ids": [str(x) for x in effective_added_ids if str(x)],
            "removed_ids": removed_ids,
            "added_paths": [
                str(x)
                for x in (master_report.get("used_case_paths") or [])
                if isinstance(x, str)
            ],
            "count_added": len(added_ids),
            "count_effective_added": len([str(x) for x in effective_added_ids if str(x)]),
        },
        "conflict_delta": {
            "new_conflicts": sorted(cur_conf_set - prev_conf_set),
            "resolved_conflicts": sorted(prev_conf_set - cur_conf_set),
            "unresolved_before": len(prev_conf_set),
            "unresolved_after": len(cur_conf_set),
        },
        "feasibility_snapshot": {
            "overall_score": _to_float(((eval_report.get("feasibility") or {}).get("overall_score"))),
            "constraints": list(((eval_report.get("feasibility") or {}).get("constraints") or [])),
            "lane_capabilities": deepcopy(((eval_report.get("feasibility") or {}).get("lane_capabilities") or {})),
        },
        "chosen_next_round_profile": chosen_next_round_profile,
        "profile_adjustment_reason": profile_adjustment_reason,
        "updated_at": _now_iso8601(),
    }


def _neighbor_stats_reliability_from_pack(reasoning_pack: Dict[str, Any]) -> str:
    risk_scores = reasoning_pack.get("risk_scores") if isinstance(reasoning_pack, dict) else {}
    if not isinstance(risk_scores, dict):
        return "unknown"
    stats = risk_scores.get("neighbor_atb_stats_by_label")
    if not isinstance(stats, dict):
        stats = risk_scores.get("neighbor_atb_stats")
    if not isinstance(stats, dict):
        return "unknown"
    return str(stats.get("reliability") or "unknown").strip().lower()


def _effective_added_ids(
    *,
    active_profile: str,
    newly_seen_global: Sequence[str],
    reasoning_pack: Dict[str, Any],
) -> List[str]:
    added = [str(x) for x in newly_seen_global if str(x)]
    if not added:
        return []
    # In R2/R3, discriminative E21-E24 are only effective when reliability is medium/high.
    if str(active_profile).upper() in {"R2", "R3"}:
        reliability = _neighbor_stats_reliability_from_pack(reasoning_pack)
        if reliability not in {"medium", "high"}:
            added = [x for x in added if x not in R2_DISCRIMINATIVE_EVIDENCE_IDS]
    return sorted(set(added))


def run_iterative_rounds(
    *,
    case_json: Dict[str, Any],
    ctx: AgentContext,
    mode: str = ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
    max_rounds: int = 4,
    start_profile: str = "R0",
    run_master_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    eval_report_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    status_path: Optional[Path] = None,
    evaluator_use_llm: bool = False,
    master_model: Optional[str] = None,
    master_reasoning_effort: Optional[str] = None,
    evaluator_model: Optional[str] = None,
    evaluator_reasoning_effort: Optional[str] = None,
    evaluator_confidence_adjustment_enabled: bool = False,
    evaluator_confidence_adjustment_max_abs_delta: float = 0.05,
    pre_r2_failure_recovery_mode: str = "force_r2",
    neighbor_topk: int = 10,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if mode not in ROUND_RUNNER_MODES:
        raise ValueError(f"unsupported_round_runner_mode:{mode}")
    total_rounds = max(1, int(max_rounds))
    current = deepcopy(case_json)
    profiles_cfg = default_evidence_profiles()
    active_profile = _resolve_round_profile(start_profile)
    profiles_cfg["active_profile"] = active_profile

    master_runner = run_master_fn
    if master_runner is None:

        def _default_master_runner(*, case_json: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
            model, effort = _resolve_master_llm_config(reasoning_config, ctx)
            llm = _llm_client(ctx, model=model, reasoning_effort=effort)
            return run_master_reasoner_once(
                case_json=case_json,
                reasoning_config=reasoning_config,
                llm_client=llm,
            )

        master_runner = _default_master_runner

    evaluator = eval_report_fn or build_eval_report
    llm_evaluator = (
        LLMEvaluator(
            base_url=ctx.base_url,
            api_key_env=ctx.llm_api_key_env,
            max_output_tokens=ctx.llm_max_output_tokens,
            default_model=ctx.model,
            default_reasoning_effort=ctx.llm_reasoning_effort,
        )
        if evaluator_use_llm
        else None
    )

    rounds: List[Dict[str, Any]] = []
    stop_reason = ""
    prev_master_report: Optional[Dict[str, Any]] = None
    prev_used_ids: List[str] = []
    seen_used_ids_global: set[str] = set()
    prev_conflict_ids: List[str] = []
    prev_feasibility_score: Optional[float] = None
    prev_scorecard_total: Optional[float] = None
    invalid_master_streak = 0
    pre_r2_recovery_used = False
    rounds_root = resolve_rounds_trace_dir(ctx, create=True).resolve()

    def _update_status(
        *,
        round_index: int,
        active_profile: str,
        stage: str,
        last_event: str,
        errors: Optional[List[Dict[str, str]]] = None,
        latest_eval_report: Optional[str] = None,
    ) -> None:
        if status_path is None:
            return
        payload: Dict[str, Any] = {
            "run_id": ctx.run_id,
            "case_id": str(current.get("case_id") or case_json.get("case_id") or ""),
            "round_index": int(round_index),
            "max_rounds": int(total_rounds),
            "active_profile": str(active_profile),
            "round_runner_mode": mode,
            "stage": stage,
            "last_event": last_event,
            "last_updated_at": now_iso8601(),
            "errors": list(errors or []),
            "round_dir": str(rounds_root),
            "latest_eval_report": latest_eval_report,
        }
        atomic_write_json(Path(status_path), payload)

    def _log_stage(
        *,
        round_index: int,
        active_profile: str,
        stage: str,
        status: str,
        t0: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        elapsed_ms = int((perf_counter() - t0) * 1000) if t0 is not None else 0
        emit_progress_event(
            round_index=round_index,
            max_rounds=total_rounds,
            active_profile=active_profile,
            stage=stage,
            status=status,
            elapsed_ms=elapsed_ms,
            extra=extra,
        )

    for round_index in range(total_rounds):
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="round_start",
            status="running",
            t0=None,
        )
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="round_start",
            last_event="round_start",
            errors=[],
            latest_eval_report=None,
        )
        reasoning_config = _build_reasoning_config(
            ctx,
            active_profile=active_profile,
            round_index=round_index,
            profiles_cfg=profiles_cfg,
            neighbor_topk=neighbor_topk,
            evaluator_use_llm=evaluator_use_llm,
            master_model=master_model,
            master_reasoning_effort=master_reasoning_effort,
            evaluator_model=evaluator_model,
            evaluator_reasoning_effort=evaluator_reasoning_effort,
            evaluator_confidence_adjustment_enabled=evaluator_confidence_adjustment_enabled,
            evaluator_confidence_adjustment_max_abs_delta=evaluator_confidence_adjustment_max_abs_delta,
        )
        master_status = "failed_schema_validation"
        run_out: Dict[str, Any]
        t_master = perf_counter()
        try:
            run_out = master_runner(case_json=current, reasoning_config=reasoning_config)
            master_status = "completed" if str(run_out.get("status")) == "success" else "failed_schema_validation"
        except LLMClientError as exc:
            run_out = {
                "status": "failed_llm",
                "master_output_parsed": {},
                "normalized_output": None,
                "validation_errors": [{"code": "llm_error", "detail": str(exc), "path": "$", "type": "evidence"}],
                "used_case_paths": [],
                "used_evidence_ids": [],
                "used_evidence": [],
                "llm_request": None,
                "llm_response_raw": None,
                "pack_hash": None,
                "prompt_bundle": {},
                "reasoning_pack": {},
            }
            master_status = "failed_llm"
        except Exception as exc:  # pragma: no cover - defensive
            run_out = {
                "status": "failed_internal",
                "master_output_parsed": {},
                "normalized_output": None,
                "validation_errors": [{"code": "internal_error", "detail": str(exc), "path": "$", "type": "evidence"}],
                "used_case_paths": [],
                "used_evidence_ids": [],
                "used_evidence": [],
                "llm_request": None,
                "llm_response_raw": None,
                "pack_hash": None,
                "prompt_bundle": {},
                "reasoning_pack": {},
            }
            master_status = "failed_schema_validation"
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="master_call",
            status=master_status,
            t0=t_master,
        )
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="master_call",
            last_event=f"master_{master_status}",
            errors=[],
            latest_eval_report=None,
        )

        parsed_master = run_out.get("master_output_parsed") if isinstance(run_out.get("master_output_parsed"), dict) else {}
        validation_errors = list(run_out.get("validation_errors") or [])
        error_summary = summarize_errors(validation_errors)
        validate_status = "passed" if master_status == "completed" and not error_summary else "failed"
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="validate",
            status=validate_status,
            t0=None,
        )
        if master_status in {"failed_llm", "failed_schema_validation"} and error_summary:
            emit_error_summary(
                round_index=round_index,
                max_rounds=total_rounds,
                active_profile=active_profile,
                stage="validate",
                errors=error_summary,
            )
            _update_status(
                round_index=round_index,
                active_profile=active_profile,
                stage="validate",
                last_event="validation_failed",
                errors=error_summary,
                latest_eval_report=None,
            )
        master_report = _build_master_round_report(
            round_index=round_index,
            active_profile=active_profile,
            status=master_status,
            master_out=parsed_master,
            validation_errors=validation_errors,
            used_evidence_ids=list(run_out.get("used_evidence_ids") or []),
            used_case_paths=list(run_out.get("used_case_paths") or []),
            llm_failure_reason=str(run_out.get("llm_failure_reason") or "") or None,
        )

        judged = {
            "status": parsed_master.get("status") if master_status == "completed" else "needs_followup",
            "confidence": (_to_float((_extract_mechanism_claim(parsed_master) or {}).get("confidence")) or 0.0),
            "contradictions": [str(x.get("detail") or "") for x in validation_errors if isinstance(x, dict)],
            "missing_evidence": [] if master_status == "completed" else ["master_output_invalid"],
                "recommended_actions": list(parsed_master.get("recommended_next_actions") or []),
            }
        cur_used_ids = set(str(x) for x in (master_report.get("used_evidence_ids") or []) if str(x))
        prev_used_id_set = set(str(x) for x in prev_used_ids if str(x))
        count_added = len(cur_used_ids - prev_used_id_set)
        newly_seen_global = sorted(cur_used_ids - seen_used_ids_global)
        effective_added_ids = _effective_added_ids(
            active_profile=active_profile,
            newly_seen_global=newly_seen_global,
            reasoning_pack=deepcopy((run_out.get("reasoning_pack") or {})),
        )
        seen_used_ids_global |= cur_used_ids
        prev_hyp = (prev_master_report or {}).get("hypothesis") if isinstance(prev_master_report, dict) else {}
        cur_hyp = master_report.get("hypothesis") if isinstance(master_report, dict) else {}
        hypothesis_changed = (
            str((prev_hyp or {}).get("mechanism_label") or "") != str((cur_hyp or {}).get("mechanism_label") or "")
            or str((prev_hyp or {}).get("template_used") or "") != str((cur_hyp or {}).get("template_used") or "")
        )
        prev_conf = _to_float((prev_master_report or {}).get("confidence")) if isinstance(prev_master_report, dict) else None
        cur_conf = _to_float(master_report.get("confidence"))
        if prev_conf is None:
            prev_conf = cur_conf
        if cur_conf is None:
            cur_conf = prev_conf
        if prev_conf is None:
            prev_conf = 0.0
        if cur_conf is None:
            cur_conf = 0.0
        confidence_delta = round(float(cur_conf) - float(prev_conf), 6)
        t_judge = perf_counter()
        eval_report = evaluator(
            case_json=current,
            judged=judged,
            round_index=round_index,
            active_profile=active_profile,
            run_lane=ctx.run_lane,
            prev_confidence=_to_float(((current.get("post_uq") or {}).get("confidence"))),
            info_gain={
                "count_added": count_added,
                "count_effective_added": len(effective_added_ids),
                "effective_added_ids": list(effective_added_ids),
                "hypothesis_changed": bool(hypothesis_changed),
                "confidence_delta": confidence_delta,
            },
        )
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="judge",
            status="completed",
            t0=t_judge,
        )
        if bool(((reasoning_config.get("evaluator") or {}).get("use_llm"))) and llm_evaluator is not None:
            t_judge_llm = perf_counter()
            try:
                eval_model, eval_effort = _resolve_evaluator_llm_config(reasoning_config, ctx)
                llm_out = llm_evaluator.run(
                    reasoning_pack=deepcopy((run_out.get("reasoning_pack") or {})),
                    master_output_parsed=deepcopy(parsed_master),
                    policy=deepcopy(reasoning_config.get("policy") or {}),
                    thresholds=deepcopy(reasoning_config.get("thresholds") or {}),
                    run_lane_capabilities=deepcopy(((eval_report.get("feasibility") or {}).get("lane_capabilities") or {})),
                    model=eval_model,
                    reasoning_effort=eval_effort,
                    use_json_schema=bool(((reasoning_config.get("evaluator") or {}).get("use_json_schema"))),
                )
                eval_report = merge_eval_report_with_llm_layer(
                    eval_report=eval_report,
                    llm_output=llm_out.get("parsed") if isinstance(llm_out, dict) else None,
                )
                eval_report.setdefault("llm_layer", {})
                if isinstance(eval_report["llm_layer"], dict):
                    eval_report["llm_layer"]["request"] = llm_out.get("request")
                    eval_report["llm_layer"]["response"] = llm_out.get("response")
                    if llm_out.get("llm_failure_reason"):
                        eval_report["llm_layer"]["llm_failure_reason"] = llm_out.get("llm_failure_reason")
                _log_stage(
                    round_index=round_index,
                    active_profile=active_profile,
                    stage="judge_llm",
                    status="completed",
                    t0=t_judge_llm,
                )
            except Exception as exc:
                eval_report = merge_eval_report_with_llm_layer(eval_report=eval_report, llm_output=None)
                eval_report.setdefault("llm_layer", {})
                if isinstance(eval_report["llm_layer"], dict):
                    eval_report["llm_layer"]["status"] = "failed"
                    eval_report["llm_layer"]["error"] = str(exc)
                _log_stage(
                    round_index=round_index,
                    active_profile=active_profile,
                    stage="judge_llm",
                    status="failed",
                    t0=t_judge_llm,
                )

        current_unresolved_conflicts = set(
            str((row or {}).get("conflict_id") or "")
            for row in (eval_report.get("conflict_adjudication") or [])
            if isinstance(row, dict) and str((row or {}).get("status") or "").lower() != "resolved"
        )
        current_unresolved_conflicts.discard("")
        prev_conflict_set = set(str(x) for x in prev_conflict_ids if str(x))
        resolved_conflicts = sorted(prev_conflict_set - current_unresolved_conflicts)
        new_conflicts = sorted(current_unresolved_conflicts - prev_conflict_set)

        current_feasibility = _to_float(((eval_report.get("feasibility") or {}).get("overall_score")))
        if current_feasibility is None:
            current_feasibility = 0.0
        scorecard_total = 0.0
        for row in (eval_report.get("evidence_scorecard") or []):
            if not isinstance(row, dict):
                continue
            scorecard_total += float(_to_float(row.get("score")) or 0.0)
        scorecard_improved = (
            prev_scorecard_total is not None and float(scorecard_total) > float(prev_scorecard_total) + 1.0e-12
        )
        feasibility_improved = (
            prev_feasibility_score is not None and float(current_feasibility) > float(prev_feasibility_score) + 1.0e-12
        )
        claim_conf = _to_float((_extract_mechanism_claim(parsed_master) or {}).get("confidence"))
        if claim_conf is None:
            claim_conf = _to_float(((eval_report.get("confidence_update") or {}).get("new")))
        if claim_conf is None:
            claim_conf = 0.05
        global_cap = float(((reasoning_config.get("thresholds") or {}).get("global_confidence_cap") or 0.95))
        cap_value = max(0.05, min(0.95, global_cap))
        gate_mode = str((current.get("current_gate") or {}).get("reasoning_mode") or "").lower()
        if gate_mode == "conservative":
            cap_value = min(cap_value, float(reasoning_config.get("conservative_confidence_cap", 0.65)))

        eval_report = apply_evaluator_confidence_adjustment(
            eval_report=eval_report,
            config=deepcopy(reasoning_config.get("evaluator_confidence_adjustment") or {}),
            master_confidence=float(claim_conf),
            cap=float(cap_value),
            added_ids=list(effective_added_ids),
            count_added=int(count_added),
            resolved_conflicts=resolved_conflicts,
            scorecard_improved=bool(scorecard_improved),
            feasibility_improved=bool(feasibility_improved),
            conflicts_increased=bool(new_conflicts),
        )

        suggested_next = str(eval_report.get("next_round_profile") or "NONE").upper()
        chosen_next = suggested_next
        profile_adjustment_reason = "as_recommended"
        if chosen_next not in set(ROUND_PROFILE_ORDER) | {"NONE"}:
            chosen_next = next_profile_name(active_profile)
            profile_adjustment_reason = "invalid_recommendation_fallback"
        if chosen_next == "R3" and not bool(((eval_report.get("feasibility") or {}).get("lane_capabilities") or {}).get("literature_enabled")):
            chosen_next = "R2"
            profile_adjustment_reason = "literature_lane_disabled"

        # Pre-R2 failure guard: do not stop early before at least one R2 attempt.
        failure_reasons = {"failed_llm", "failed_schema_validation", "failed_internal"}
        in_pre_r2 = active_profile in {"R0", "R1"}
        if (
            master_status in failure_reasons
            and in_pre_r2
            and not pre_r2_recovery_used
        ):
            recovery_mode = str(pre_r2_failure_recovery_mode or "force_r2").lower()
            if recovery_mode == "degraded_retry":
                chosen_next = active_profile
                profile_adjustment_reason = "pre_r2_failure_recovery_degraded_retry"
            else:
                chosen_next = "R2"
                profile_adjustment_reason = "pre_r2_failure_recovery_force_r2"
            pre_r2_recovery_used = True
            eval_report["next_round_profile"] = chosen_next
            eval_report["stop_recommendation"] = {
                "should_stop": False,
                "reason_code": "pre_r2_failure_recovery",
                "explanation": f"master failed in {active_profile}; forcing one recovery round via {profile_adjustment_reason}",
            }

        round_state = _round_state_payload(
            round_index=round_index,
            active_profile=active_profile,
            master_report=master_report,
            eval_report=eval_report,
            chosen_next_round_profile=chosen_next,
            profile_adjustment_reason=profile_adjustment_reason,
            prev_master_report=prev_master_report,
            prev_used_ids=prev_used_ids,
            effective_added_ids=effective_added_ids,
            prev_conflict_ids=prev_conflict_ids,
        )

        reasoning_trace_path = write_agent_response_trace(
            ctx=ctx,
            agent_name=f"reasoning_agent.round{round_index:02d}",
            payload={
                "run_id": ctx.run_id,
                "case_id": current.get("case_id"),
                "agent": "reasoning_agent",
                "round_index": round_index,
                "status": master_status,
                "validation_errors": validation_errors[:5],
                "request": run_out.get("llm_request"),
                "response_raw": run_out.get("llm_response_raw"),
                "parsed": parsed_master,
            },
        )
        eval_trace_path = write_agent_response_trace(
            ctx=ctx,
            agent_name=f"evaluator.round{round_index:02d}",
            payload={
                "run_id": ctx.run_id,
                "case_id": current.get("case_id"),
                "agent": "evaluator",
                "round_index": round_index,
                "report": eval_report,
            },
        )
        master_report_path = write_master_round_report(ctx=ctx, round_index=round_index, payload=master_report)
        eval_report_path = write_eval_report(ctx=ctx, round_index=round_index, payload=eval_report)
        round_state_path = write_round_state(ctx=ctx, round_index=round_index, payload=round_state)
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="judge",
            last_event="eval_report_written",
            errors=error_summary if master_status in {"failed_llm", "failed_schema_validation"} else [],
            latest_eval_report=eval_report_path,
        )

        should_commit_round = (mode == ROUND_RUNNER_MODE_COMMIT_ALL) or (round_index > 0)
        t_apply_patch = perf_counter()
        if should_commit_round:
            meta = {
                "run_id": ctx.run_id,
                "round_index": round_index,
                "active_profile": active_profile,
                "inputs_hash": sha256_json(reasoning_config),
                "pack_hash": run_out.get("pack_hash"),
                "pack_version": reasoning_config.get("pack_version"),
                "prompt_bundle_version": reasoning_config.get("prompt_bundle_version"),
                "template_version": ((run_out.get("prompt_bundle") or {}).get("template_version")),
                "model": ctx.model,
                "status": master_status,
                "llm_failure_reason": str(run_out.get("llm_failure_reason") or "") or None,
                "errors": validation_errors[:5],
                "llm_trace_path": reasoning_trace_path,
                "updated_at": _now_iso8601(),
            }
            master_patch = build_master_patch(
                current,
                run_out.get("normalized_output") if master_status == "completed" else None,
                status=master_status,
                used_paths=list(run_out.get("used_case_paths") or []),
                used_evidence_ids=list(run_out.get("used_evidence_ids") or []),
                used_evidence=list(run_out.get("used_evidence") or []),
                meta=meta,
            )
            current = _apply_scoped_patch(
                current,
                master_patch,
                allowed_prefixes=ReasoningAgent.allowed_patch_prefixes,
                append_only_prefixes=(),
            )
            post_uq_patch = [{"op": "add", "path": "/post_uq", "value": build_post_uq_from_eval(eval_report)}]
            current = _apply_scoped_patch(
                current,
                post_uq_patch,
                allowed_prefixes=JudgeAgent.allowed_patch_prefixes,
                append_only_prefixes=(),
            )
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="apply_patch",
            status="applied" if should_commit_round else "skipped",
            t0=t_apply_patch,
        )
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="apply_patch",
            last_event="patch_applied" if should_commit_round else "patch_skipped",
            errors=error_summary if master_status in {"failed_llm", "failed_schema_validation"} else [],
            latest_eval_report=eval_report_path,
        )

        iterative_patch = _iterative_patch(
            mode=mode,
            round_index=round_index,
            active_profile=active_profile,
            last_round_status=master_status,
            last_round_state_path=str(Path(round_state_path).resolve()),
        )
        current = _apply_scoped_patch(
            current,
            iterative_patch,
            allowed_prefixes=("/iterative", "/iterative/"),
            append_only_prefixes=(),
        )

        if master_status != "completed":
            invalid_master_streak += 1
        else:
            invalid_master_streak = 0

        stop_info = eval_report.get("stop_recommendation") or {}
        eval_should_stop = bool(stop_info.get("should_stop"))
        eval_reason_code = str(stop_info.get("reason_code") or "").strip()
        feasibility_score = _to_float(((eval_report.get("feasibility") or {}).get("overall_score")))
        if feasibility_score is None:
            feasibility_score = 0.0

        stop_now = False
        if eval_should_stop:
            stop_now = True
            if eval_reason_code:
                stop_reason = eval_reason_code
            elif chosen_next == "NONE":
                stop_reason = "profile_exhausted"
            else:
                stop_reason = "stop_recommended"
        elif chosen_next == "NONE":
            stop_now = True
            stop_reason = "profile_exhausted"
        elif invalid_master_streak >= 2 and (len(eval_report.get("voi_ranked_actions") or []) == 0):
            stop_now = True
            stop_reason = "repeated_invalid_master_output"
        elif feasibility_score < 0.2 and len(eval_report.get("voi_ranked_actions") or []) == 0:
            stop_now = True
            stop_reason = "stop_recommended_low_feasibility"

        if profile_adjustment_reason.startswith("pre_r2_failure_recovery"):
            stop_now = False
            stop_reason = ""
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="stop",
            status=stop_reason if stop_now else "continue",
            t0=None,
        )

        rounds.append(
            {
                "round_index": round_index,
                "active_profile": active_profile,
                "master_status": master_status,
                "eval_status": str(eval_report.get("status") or "unknown"),
                "commit_applied": bool(should_commit_round),
                "master_round_report_path": master_report_path,
                "eval_report_path": eval_report_path,
                "round_state_path": round_state_path,
                "reasoning_trace_path": reasoning_trace_path,
                "evaluator_trace_path": eval_trace_path,
                "llm_failure_reason": str(run_out.get("llm_failure_reason") or "") or None,
                "stop": stop_now,
                "stop_reason": stop_reason if stop_now else "",
            }
        )
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="stop",
            last_event=stop_reason if stop_now else "continue",
            errors=error_summary if master_status in {"failed_llm", "failed_schema_validation"} else [],
            latest_eval_report=eval_report_path,
        )
        _log_stage(
            round_index=round_index,
            active_profile=active_profile,
            stage="round_end",
            status="stopped" if stop_now else "completed",
            t0=None,
        )
        _update_status(
            round_index=round_index,
            active_profile=active_profile,
            stage="round_end",
            last_event="round_stopped" if stop_now else "round_completed",
            errors=error_summary if master_status in {"failed_llm", "failed_schema_validation"} else [],
            latest_eval_report=eval_report_path,
        )

        prev_master_report = master_report
        prev_used_ids = list(master_report.get("used_evidence_ids") or [])
        prev_conflict_ids = [
            str((row or {}).get("conflict_id") or "")
            for row in (eval_report.get("conflict_adjudication") or [])
            if isinstance(row, dict) and str((row or {}).get("status") or "").lower() != "resolved"
        ]
        prev_conflict_ids = [x for x in prev_conflict_ids if x]
        prev_feasibility_score = _to_float(((eval_report.get("feasibility") or {}).get("overall_score")))
        if prev_feasibility_score is None:
            prev_feasibility_score = 0.0
        prev_scorecard_total = 0.0
        for row in (eval_report.get("evidence_scorecard") or []):
            if not isinstance(row, dict):
                continue
            prev_scorecard_total += float(_to_float(row.get("score")) or 0.0)

        if stop_now:
            break
        active_profile = chosen_next if chosen_next in ROUND_PROFILE_ORDER else _resolve_round_profile(next_profile_name(active_profile))

    summary = {
        "mode": mode,
        "max_rounds": total_rounds,
        "executed_rounds": len(rounds),
        "stopped": bool(stop_reason),
        "stop_reason": stop_reason,
        "rounds": rounds,
    }
    return current, summary

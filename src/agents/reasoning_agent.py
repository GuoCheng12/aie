"""
Reasoning Agent (master) for case-level hypothesis generation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.agents.base import CaseAgent
from src.core.hashing import sha256_json
from src.core.types import AgentContext, AgentResult, SKIPPED_REASON_NOT_APPLICABLE
from src.reasoning.master_reasoner import (
    MASTER_PACK_VERSION,
    MASTER_PROMPT_BUNDLE_VERSION,
    build_master_patch,
    build_master_prompt_bundle,
    build_reasoning_pack,
    run_master_reasoner_once,
)
from src.reasoning.reasoning_config import build_allowed_mechanism_labels, build_reasoning_policy
from src.tools.llm_client import LLMClientError, ResponsesLLMClient
from src.tools.llm_trace_store import (
    build_reasoning_five_signals,
    write_agent_response_trace,
    write_reasoning_five_signals,
)


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _action_plan_has_master_action(action_plan: Any, *, require_top1: bool) -> bool:
    if not isinstance(action_plan, list):
        return False
    candidates = {"run_master_reasoner", "run_master_reasoner_stub"}
    rows = [x for x in action_plan if isinstance(x, dict)]
    if not rows:
        return False
    if require_top1:
        pending = [x for x in rows if str(x.get("status") or "pending") in {"pending", "not_started"}]
        if not pending:
            pending = rows
        top = sorted(pending, key=lambda x: int(x.get("priority") or 10**9))[0]
        return str(top.get("action") or "") in candidates
    return any(str(x.get("action") or "") in candidates for x in rows)


class ReasoningAgent(CaseAgent):
    name = "reasoning_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/master_reasoning",
        "/master_reasoning_meta",
        "/master_reasoning_status",
        "/master_reasoning_used_evidence_paths",
        "/reasoning",
        "/reasoning/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def __init__(self, *, use_llm: bool = True, require_top1_for_master: bool = False) -> None:
        self.use_llm = bool(use_llm)
        self.require_top1_for_master = bool(require_top1_for_master)

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        policy = build_reasoning_policy()
        reasoning_config = {
            "run_lane": ctx.run_lane,
            "model": ctx.model,
            "reasoning_effort": ctx.llm_reasoning_effort or "medium",
            "temperature": float(ctx.llm_temperature),
            "use_json_schema": bool(ctx.llm_use_json_schema),
            "master_output_mode": "tagged_repair",
            "allowed_mechanism_labels": build_allowed_mechanism_labels(),
            "master": {
                "model": ctx.model,
                "reasoning_effort": ctx.llm_reasoning_effort or "medium",
                "temperature": float(ctx.llm_temperature),
                "use_json_schema": bool(ctx.llm_use_json_schema),
            },
            "pack_version": MASTER_PACK_VERSION,
            "prompt_bundle_version": MASTER_PROMPT_BUNDLE_VERSION,
            "master_output_schema_version": "v3",
            "require_top1_for_master": self.require_top1_for_master,
            "conservative_confidence_cap": 0.65,
            "policy": policy,
            "thresholds": {
                "neighbor_support_min_sim": policy["neighbor_support_min_sim"],
                "atb_dihedral_thresh_none": policy["atb_dihedral_thresh_none"],
                "atb_dihedral_thresh_strong": policy["atb_dihedral_thresh_strong"],
                "top1_sim_low": policy["top1_sim_low"],
                "entropy_high": policy["entropy_high"],
                # Backward-compatible keys for prompt/explainability paths.
                # New policy uses soft penalty + final cap, but these keys may still be read.
                "conf_cap_top1_sim_low": policy.get("conf_cap_top1_sim_low", 0.45),
                "conf_cap_entropy_high": policy.get("conf_cap_entropy_high", 0.50),
                "conf_cap_both": policy.get("conf_cap_both", 0.42),
                "global_confidence_cap": policy.get("global_confidence_cap", 0.95),
                "r0_penalty_factor": policy.get("r0_penalty_factor", 0.90),
                "conservative_confidence_cap": 0.65,
            },
        }
        reasoning_pack = build_reasoning_pack(case, reasoning_config)
        prompt_bundle = build_master_prompt_bundle(reasoning_pack, reasoning_config)
        gate = case.get("current_gate") or {}
        action_plan = case.get("action_plan") or []
        has_master_action = _action_plan_has_master_action(
            action_plan,
            require_top1=self.require_top1_for_master,
        )
        return {
            "case_id": case.get("case_id"),
            "ready_for_reasoning": bool(gate.get("ready_for_reasoning") is True or str(gate.get("state") or "") in {"ready_for_reasoning", "ready_conservative"}),
            "has_master_action": has_master_action,
            "reasoning_config": reasoning_config,
            "pack_hash": sha256_json(reasoning_pack),
            "template_version": prompt_bundle.get("template_version"),
            "reasoning_pack": reasoning_pack,
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        if not bool(inputs.get("ready_for_reasoning")):
            return AgentResult(
                patch=[],
                status="skipped",
                status_reason_code=SKIPPED_REASON_NOT_APPLICABLE,
                warnings=["reasoning_not_ready"],
                raw_outputs={"skip": {"reason": "reasoning_not_ready"}},
            )
        if not bool(inputs.get("has_master_action")):
            return AgentResult(
                patch=[],
                status="skipped",
                status_reason_code=SKIPPED_REASON_NOT_APPLICABLE,
                warnings=["master_action_missing"],
                raw_outputs={"skip": {"reason": "master_action_missing"}},
            )

        reasoning_config = dict(inputs.get("reasoning_config") or {})
        raw: Dict[str, Any] = {}
        if not self.use_llm:
            meta = {
                "run_id": ctx.run_id,
                "inputs_hash": inputs.get("pack_hash"),
                "pack_hash": inputs.get("pack_hash"),
                "pack_version": reasoning_config.get("pack_version"),
                "prompt_bundle_version": reasoning_config.get("prompt_bundle_version"),
                "template_version": inputs.get("template_version"),
                "model": ctx.model,
                "status": "stubbed",
                "updated_at": _now_iso8601(),
            }
            patch = build_master_patch(
                case,
                None,
                status="stubbed",
                used_paths=[],
                used_evidence_ids=[],
                used_evidence=[],
                meta=meta,
            )
            return AgentResult(patch=patch, status="stubbed", raw_outputs={"reasoning_stub": meta})

        try:
            llm = ResponsesLLMClient(
                base_url=ctx.base_url,
                model=ctx.model,
                api_key_env=ctx.llm_api_key_env,
                max_output_tokens=ctx.llm_max_output_tokens,
                reasoning_effort=ctx.llm_reasoning_effort,
                temperature=ctx.llm_temperature,
            )
            run_out = run_master_reasoner_once(
                case_json=case,
                reasoning_config=reasoning_config,
                llm_client=llm,
                reasoning_pack=inputs.get("reasoning_pack"),
            )
            raw_status = str(run_out.get("status") or "failed_schema_validation")
            if raw_status == "success":
                status = "completed"
            elif raw_status == "failed_llm":
                status = "failed_llm"
            else:
                status = "failed_schema_validation"
            llm_trace_payload = {
                "run_id": ctx.run_id,
                "case_id": case.get("case_id"),
                "agent": self.name,
                "model": ctx.model,
                "reasoning_effort": ctx.llm_reasoning_effort,
                "status": status,
                "llm_failure_reason": run_out.get("llm_failure_reason"),
                "validation_errors": run_out.get("validation_errors") or [],
                "request": run_out.get("llm_request"),
                "response_raw": run_out.get("llm_response_raw"),
                "parsed": run_out.get("master_output_parsed"),
            }
            llm_trace_path = write_agent_response_trace(
                ctx=ctx,
                agent_name=self.name,
                payload=llm_trace_payload,
            )
            summary5_path = write_reasoning_five_signals(
                ctx=ctx,
                payload=build_reasoning_five_signals(
                    run_id=ctx.run_id,
                    case_id=str(case.get("case_id") or ""),
                    status=status,
                    model=ctx.model,
                    reasoning_effort=ctx.llm_reasoning_effort,
                    parsed=run_out.get("master_output_parsed"),
                ),
            )
            confidence_meta = run_out.get("confidence_meta") if isinstance(run_out.get("confidence_meta"), dict) else {}
            meta = {
                "run_id": ctx.run_id,
                "inputs_hash": inputs.get("pack_hash"),
                "pack_hash": run_out.get("pack_hash"),
                "pack_version": reasoning_config.get("pack_version"),
                "prompt_bundle_version": reasoning_config.get("prompt_bundle_version"),
                "template_version": run_out.get("prompt_bundle", {}).get("template_version"),
                "model": ctx.model,
                "status": status,
                "llm_failure_reason": run_out.get("llm_failure_reason"),
                "errors": run_out.get("validation_errors") or [],
                "llm_trace_path": llm_trace_path,
                "summary5_path": summary5_path,
                "raw_confidence_from_model": confidence_meta.get("raw_confidence_from_model"),
                "final_confidence": confidence_meta.get("final_confidence"),
                "confidence_components": confidence_meta.get("confidence_components"),
                "penalty_components": confidence_meta.get("penalty_components"),
                "confidence_formula_version": confidence_meta.get("confidence_formula_version"),
                "updated_at": _now_iso8601(),
            }
            patch = build_master_patch(
                case,
                run_out.get("normalized_output") if run_out["status"] == "success" else None,
                status=status,
                used_paths=run_out.get("used_case_paths") or [],
                used_evidence_ids=run_out.get("used_evidence_ids") or [],
                used_evidence=run_out.get("used_evidence") or [],
                meta=meta,
            )
            raw.update(
                {
                    "master_prompt_bundle": run_out.get("prompt_bundle"),
                    "reasoning_pack": run_out.get("reasoning_pack"),
                    "llm_request": run_out.get("llm_request"),
                    "llm_response_raw": run_out.get("llm_response_raw"),
                    "master_output_raw": run_out.get("master_output_raw"),
                    "master_output_parsed": run_out.get("master_output_parsed"),
                    "master_patch_preview": patch,
                    "validation_errors": run_out.get("validation_errors") or [],
                    "llm_failure_reason": run_out.get("llm_failure_reason"),
                    "llm_trace_path": llm_trace_path,
                    "summary5_path": summary5_path,
                }
            )
            if raw_status == "success":
                return AgentResult(patch=patch, status="success", raw_outputs=raw)
            if raw_status == "failed_llm":
                return AgentResult(
                    patch=patch,
                    status="partial",
                    warnings=["reasoning_llm_failed"],
                    raw_outputs=raw,
                )
            return AgentResult(
                patch=patch,
                status="partial",
                warnings=["master_output_validation_failed"],
                raw_outputs=raw,
            )
        except LLMClientError as exc:
            llm_trace_payload = {
                "run_id": ctx.run_id,
                "case_id": case.get("case_id"),
                "agent": self.name,
                "model": ctx.model,
                "reasoning_effort": ctx.llm_reasoning_effort,
                "status": "failed_llm",
                "error": f"{exc}",
            }
            llm_trace_path = write_agent_response_trace(
                ctx=ctx,
                agent_name=self.name,
                payload=llm_trace_payload,
            )
            summary5_path = write_reasoning_five_signals(
                ctx=ctx,
                payload=build_reasoning_five_signals(
                    run_id=ctx.run_id,
                    case_id=str(case.get("case_id") or ""),
                    status="failed_llm",
                    model=ctx.model,
                    reasoning_effort=ctx.llm_reasoning_effort,
                    parsed=None,
                ),
            )
            meta = {
                "run_id": ctx.run_id,
                "inputs_hash": inputs.get("pack_hash"),
                "pack_hash": inputs.get("pack_hash"),
                "pack_version": reasoning_config.get("pack_version"),
                "prompt_bundle_version": reasoning_config.get("prompt_bundle_version"),
                "template_version": inputs.get("template_version"),
                "model": ctx.model,
                "status": "failed_llm",
                "errors": [f"llm_error:{exc}"],
                "llm_trace_path": llm_trace_path,
                "summary5_path": summary5_path,
                "updated_at": _now_iso8601(),
            }
            patch = build_master_patch(
                case,
                None,
                status="failed_llm",
                used_paths=[],
                used_evidence_ids=[],
                used_evidence=[],
                meta=meta,
            )
            return AgentResult(
                patch=patch,
                status="partial",
                warnings=[f"reasoning_llm_failed:{exc}"],
                raw_outputs={
                    "validation_errors": [f"llm_error:{exc}"],
                    "master_patch_preview": patch,
                    "llm_trace_path": llm_trace_path,
                    "summary5_path": summary5_path,
                },
            )

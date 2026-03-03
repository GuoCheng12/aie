"""
Judge Agent: post-reasoning critique and next-action suggestions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
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


def build_eval_report(
    *,
    case_json: Dict[str, Any],
    judged: Dict[str, Any],
    round_index: int,
    active_profile: str,
    run_lane: str,
    prev_confidence: float | None = None,
    info_gain: Dict[str, Any] | None = None,
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
    count_added = int(info.get("count_added") or 0)
    count_effective_added = int(info.get("count_effective_added") or count_added)
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
    if lane_atb_only and str(active_profile or "").upper() == "R1" and count_added == 0:
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
    return {
        "status": str(eval_copy.get("status") or "not_started"),
        "confidence": conf,
        "contradictions": contradictions,
        "missing_evidence": missing,
        "recommended_actions": recommended,
    }


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

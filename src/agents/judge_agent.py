"""
Judge Agent: post-reasoning critique and next-action suggestions.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.tools.llm_client import LLMClientError, ResponsesLLMClient


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
            except LLMClientError as exc:
                warnings.append(f"judge_llm_failed:{exc}")
                judged = self._heuristic_judge(inputs)
        else:
            judged = self._heuristic_judge(inputs)

        patch = [{"op": "add", "path": "/post_uq", "value": judged}]
        return AgentResult(
            patch=patch,
            status="success",
            warnings=warnings,
            raw_outputs={"judge_output": judged, **raw},
        )

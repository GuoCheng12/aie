"""
Reasoning Agent (master) for case-level hypothesis generation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.tools.llm_client import LLMClientError, ResponsesLLMClient


def _reasoning_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence_basis": {"type": "string"},
                    },
                    "required": ["name", "confidence", "evidence_basis"],
                },
            },
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "hypotheses", "limitations"],
    }


class ReasoningAgent(CaseAgent):
    name = "reasoning_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/reasoning/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def __init__(self, *, use_llm: bool = True) -> None:
        self.use_llm = bool(use_llm)

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        return {
            "case_id": case.get("case_id"),
            "inchikey": (case.get("query") or {}).get("inchikey"),
            "risk_scores": case.get("risk_scores") or {},
            "target_fields": case.get("target_fields") or {},
            "mechanism_hint": (case.get("risk_scores") or {}).get("mechanism_hint"),
            "neighbors_top3": (case.get("neighbors") or [])[:3],
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        raw: Dict[str, Any] = {}
        if not self.use_llm:
            stub = {
                "summary": "Reasoning stub executed.",
                "hypotheses": [],
                "limitations": ["llm_disabled"],
            }
            patch = [
                {"op": "add", "path": "/reasoning/status", "value": "stubbed"},
                {"op": "add", "path": "/reasoning/master_output", "value": stub},
            ]
            return AgentResult(patch=patch, status="stubbed", raw_outputs={"reasoning_stub": stub})

        try:
            llm = ResponsesLLMClient(
                base_url=ctx.base_url,
                model=ctx.model,
                api_key_env=ctx.llm_api_key_env,
                max_output_tokens=ctx.llm_max_output_tokens,
                reasoning_effort=ctx.llm_reasoning_effort,
            )
            prompt = (
                "You are the master reasoner for AIE mechanism discovery.\n"
                "Use only the provided structured case context.\n"
                "Do not claim certainty if evidence is missing.\n\n"
                f"Case context:\n{inputs}"
            )
            out = llm.responses_json(
                instructions="Return strict JSON only.",
                input_text=prompt,
                schema_name="reasoning_master_output_v1",
                schema=_reasoning_schema(),
            )
            parsed = out["parsed"]
            raw["llm_request"] = out["request"]
            raw["llm_response"] = out["response"]
            patch = [
                {"op": "add", "path": "/reasoning/status", "value": "completed"},
                {"op": "add", "path": "/reasoning/master_output", "value": parsed},
            ]
            return AgentResult(patch=patch, status="success", raw_outputs=raw)
        except LLMClientError as exc:
            stub = {
                "summary": "Reasoning fallback stub due to LLM error.",
                "hypotheses": [],
                "limitations": [f"llm_error:{exc}"],
            }
            patch = [
                {"op": "add", "path": "/reasoning/status", "value": "stubbed"},
                {"op": "add", "path": "/reasoning/master_output", "value": stub},
            ]
            return AgentResult(
                patch=patch,
                status="partial",
                warnings=[f"reasoning_llm_failed:{exc}"],
                raw_outputs={"reasoning_stub": stub},
            )


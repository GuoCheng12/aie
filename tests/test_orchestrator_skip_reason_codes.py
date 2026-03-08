from pathlib import Path

from src.agents.base import CaseAgent
from src.core.types import (
    AgentContext,
    AgentResult,
    SKIPPED_REASON_GATE_BLOCKED_REASONING,
    SKIPPED_REASON_NOT_APPLICABLE,
)
from src.orchestration.orchestrator import Orchestrator


class _ReasoningAgent(CaseAgent):
    name = "reasoning_agent"
    version = "test"
    allowed_patch_prefixes = ("/reasoning/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[{"op": "add", "path": "/reasoning/status", "value": "completed"}],
            status="success",
        )


class _SkippedWithoutReasonAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(status="skipped", patch=[])


def _base_case():
    return {
        "case_id": "CASE-SKIP",
        "query": {"input_smiles": "C", "canonical_smiles": None, "inchikey": None, "created_at": "2026-02-24T00:00:00Z"},
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
        "agent_runs": [],
    }


def test_reasoning_gate_skip_sets_standard_reason_code(tmp_path: Path):
    ctx = AgentContext(
        run_id="run-skip-gate",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    _, summary = Orchestrator(agents=[_ReasoningAgent()], ctx=ctx).run(_base_case())
    assert summary["steps"][0]["status"] == "skipped"
    assert summary["steps"][0]["status_reason_code"] == SKIPPED_REASON_GATE_BLOCKED_REASONING


def test_skipped_without_reason_gets_not_applicable(tmp_path: Path):
    ctx = AgentContext(
        run_id="run-skip-default",
        run_dir=tmp_path / "artifacts2",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    case_after, summary = Orchestrator(agents=[_SkippedWithoutReasonAgent()], ctx=ctx).run(_base_case())
    assert summary["steps"][0]["status"] == "skipped"
    assert summary["steps"][0]["status_reason_code"] == SKIPPED_REASON_NOT_APPLICABLE
    assert case_after["agent_runs"][-1]["status_reason_code"] == SKIPPED_REASON_NOT_APPLICABLE

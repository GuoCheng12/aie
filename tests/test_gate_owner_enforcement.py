from pathlib import Path

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.orchestration.orchestrator import Orchestrator


class _BadGateWriter(CaseAgent):
    name = "chem_agent"
    version = "test"
    allowed_patch_prefixes = (
        "/evidence_readiness/atb/",
        "/current_gate/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "add", "path": "/evidence_readiness/atb/cache_status", "value": "success"},
                {"op": "replace", "path": "/current_gate/state", "value": "ready_for_reasoning"},
            ],
            status="success",
        )


class _TailAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(patch=[{"op": "add", "path": "/query/ran_tail", "value": True}], status="success")


def _base_case():
    return {
        "case_id": "CASE-GATE-OWNER",
        "query": {"input_smiles": "C", "canonical_smiles": None, "inchikey": None, "created_at": "2026-02-24T00:00:00Z"},
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
        "agent_runs": [],
    }


def test_non_ready_agent_writing_gate_fails_fast(tmp_path: Path):
    ctx = AgentContext(
        run_id="run-gate-owner",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    case_after, summary = Orchestrator(agents=[_BadGateWriter(), _TailAgent()], ctx=ctx).run(_base_case())

    assert len(summary["steps"]) == 1
    assert summary["steps"][0]["status"] == "failed"
    assert any("gate_owner_violation" in w for w in summary["steps"][0]["warnings"])
    assert case_after["current_gate"]["state"] == "needs_manual"
    assert case_after["agent_runs"][-1]["status"] == "failed"

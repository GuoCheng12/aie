from pathlib import Path

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult, SKIPPED_REASON_IDEMPOTENCY_HIT
from src.orchestration.orchestrator import Orchestrator


class _IdemAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[{"op": "add", "path": "/query/stable_field", "value": 42}],
            status="success",
        )


def _base_case():
    return {
        "case_id": "CASE-IDEM",
        "query": {"input_smiles": "C", "canonical_smiles": None, "inchikey": None, "created_at": "2026-02-24T00:00:00Z"},
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
        "agent_runs": [],
    }


def test_orchestrator_idempotency_skip_has_reason_code(tmp_path: Path):
    ctx1 = AgentContext(
        run_id="run-1",
        run_dir=tmp_path / "artifacts1",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    orch1 = Orchestrator(agents=[_IdemAgent()], ctx=ctx1)
    case_after_first, _ = orch1.run(_base_case())
    assert case_after_first["query"]["stable_field"] == 42
    assert len(case_after_first["agent_runs"]) == 1

    ctx2 = AgentContext(
        run_id="run-2",
        run_dir=tmp_path / "artifacts2",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    orch2 = Orchestrator(agents=[_IdemAgent()], ctx=ctx2)
    case_after_second, summary2 = orch2.run(case_after_first)

    assert case_after_second["query"]["stable_field"] == 42
    assert len(case_after_second["agent_runs"]) == 2
    assert summary2["steps"][0]["status"] == "skipped"
    assert summary2["steps"][0]["status_reason_code"] == SKIPPED_REASON_IDEMPOTENCY_HIT

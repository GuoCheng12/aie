from pathlib import Path

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.orchestration.orchestrator import Orchestrator


class _ReplayAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[{"op": "replace", "path": "/query/canonical_smiles", "value": "C"}],
            status="success",
            raw_outputs={"replay_probe": {"ok": True}},
        )


def _base_case():
    return {
        "case_id": "CASE-REPLAY",
        "query": {"input_smiles": "C", "canonical_smiles": None, "inchikey": None, "created_at": "2026-02-24T00:00:00Z"},
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
    }


def test_orchestrator_replay_contract(tmp_path: Path):
    ctx = AgentContext(
        run_id="run-replay",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    orch = Orchestrator(agents=[_ReplayAgent()], ctx=ctx)
    _, summary = orch.run(_base_case())

    step_dir = Path(summary["steps"][0]["step_dir"])
    required = {
        "00_input_snapshot.json",
        "01_raw_outputs.json",
        "03_patch.json",
        "04_case_before.json",
        "05_case_after.json",
        "06_case_diff.json",
        "manifest.json",
    }
    existing = {p.name for p in step_dir.iterdir() if p.is_file()}
    assert required.issubset(existing)

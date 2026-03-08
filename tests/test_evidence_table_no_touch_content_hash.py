from pathlib import Path

from src.agents.base import CaseAgent
from src.core.hashing import sha256_file
from src.core.types import AgentContext, AgentResult
from src.orchestration.orchestrator import Orchestrator


class _NoTouchAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(patch=[{"op": "add", "path": "/query/no_touch_probe", "value": True}], status="success")


def _base_case():
    return {
        "case_id": "CASE-NO-TOUCH",
        "query": {"input_smiles": "C", "canonical_smiles": None, "inchikey": None, "created_at": "2026-02-24T00:00:00Z"},
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
        "agent_runs": [],
    }


def test_evidence_table_content_hash_unchanged(tmp_path: Path):
    evidence_path = Path("data/evidence_table.parquet")
    before_exists = evidence_path.exists()
    before_hash = sha256_file(evidence_path) if before_exists else None

    ctx = AgentContext(
        run_id="run-no-touch-hash",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    Orchestrator(agents=[_NoTouchAgent()], ctx=ctx).run(_base_case())

    after_exists = evidence_path.exists()
    after_hash = sha256_file(evidence_path) if after_exists else None
    assert after_exists == before_exists
    assert after_hash == before_hash

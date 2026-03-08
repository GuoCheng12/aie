from pathlib import Path

from src.agents.ready_agent import ReadyAgent
from src.core.types import AgentContext
from src.orchestration.orchestrator import Orchestrator


def _base_case():
    return {
        "case_id": "CASE-READY-OWNER",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "VNWKTOKETHGBQD-UHFFFAOYSA-N", "created_at": "2026-02-24T00:00:00Z"},
        "inputs": {"offline_pdfs": [{"path_or_id": "/tmp/paper.pdf"}]},
        "target_fields": {
            "emission_aggr_nm": 520.0,
        },
        "target_fields_provenance": {
            "emission_aggr_nm": {
                "source_ref": "paper.pdf",
                "source_locator": "Table 1",
                "confidence": 0.9,
                "identity_match": "exact",
                "identity_match_confidence": 0.9,
                "condition": "aggregation in water fraction",
            }
        },
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "init"},
        "action_plan": [],
        "agent_runs": [],
    }


def test_ready_agent_can_update_gate_fields(tmp_path: Path):
    ctx = AgentContext(
        run_id="run-ready-owner",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
    )
    case_after, summary = Orchestrator(agents=[ReadyAgent()], ctx=ctx).run(_base_case())
    assert summary["steps"][0]["status"] == "success"
    assert case_after["current_gate"]["state"] in {"ready_for_reasoning", "ready_conservative"}
    assert isinstance(case_after.get("action_rationale"), str)
    assert isinstance(case_after.get("action_plan"), list)

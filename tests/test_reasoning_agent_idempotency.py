from pathlib import Path

from src.agents.reasoning_agent import ReasoningAgent
from src.core.types import AgentContext
from src.orchestration.orchestrator import Orchestrator


def _ctx(tmp_path: Path, run_id: str) -> AgentContext:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return AgentContext(
        run_id=run_id,
        run_dir=run_dir,
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        run_lane="atb_cache_only",
    )


def _base_case():
    return {
        "case_id": "CID-1",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-1", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "neighbors": [{"rank": 1, "sim": 0.91, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {"top1_sim": 0.91, "mean_topk_sim": 0.88, "novelty_struct": 0.09, "mechanism_entropy": 0.2, "mechanism_hint": "ICT", "hint_confidence": 0.8},
        "evidence_readiness": {"atb": {"cache_status": "success"}, "literature": {"status": "not_started"}, "experiment": {"status": "not_requested"}},
        "target_fields": {},
        "target_fields_provenance": {},
        "current_gate": {"state": "ready_for_reasoning", "ready_for_reasoning": True, "reasoning_mode": "normal", "reason": "ok"},
        "action_plan": [{"action": "run_master_reasoner", "priority": 2, "status": "pending", "inputs": {}, "expected_outputs": [], "blocking": False, "notes": ""}],
        "agent_runs": [],
    }


def test_reasoning_idempotency_hit(monkeypatch, tmp_path: Path):
    def _fake_responses_json(self, *, instructions, input_text, schema_name, schema):
        return {
            "request": {"schema_name": schema_name},
            "response": {"id": "resp-1"},
            "parsed": {
                "status": "ok",
                "template_used": "stable",
                "mechanism_claim": {
                    "primary_hypothesis": {
                        "mechanism_label": "ICT",
                        "aie_rationale_type": "stable",
                        "natural_language_mechanism": "ICT dominates",
                    },
                    "confidence": 0.55,
                    "reasoning_mode_used": "normal",
                },
                "supporting_chain": [
                    {
                        "claim": "similarity support",
                        "evidence_used": [{"case_path": "/risk_scores/top1_sim", "note": "high sim", "role": "support"}],
                    }
                ],
                "competing_hypotheses": [],
                "predictions": [],
                "limits": ["normal mode"],
                "evidence_used": [{"case_path": "/risk_scores/top1_sim", "note": "high sim", "role": "support"}],
                "recommended_next_actions": [],
            },
        }

    monkeypatch.setattr("src.tools.llm_client.ResponsesLLMClient.responses_json", _fake_responses_json)

    agent = ReasoningAgent(use_llm=True)
    case0 = _base_case()

    c1, s1 = Orchestrator(agents=[agent], ctx=_ctx(tmp_path, "run1")).run(case0)
    assert s1["steps"][0]["status"] == "success"

    c2, s2 = Orchestrator(agents=[agent], ctx=_ctx(tmp_path, "run2")).run(c1)
    assert s2["steps"][0]["status"] == "skipped"
    assert s2["steps"][0]["status_reason_code"] == "idempotency_hit"

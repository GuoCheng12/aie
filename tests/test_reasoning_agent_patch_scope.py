from src.agents.reasoning_agent import ReasoningAgent
from src.core.patching import validate_patch
from src.core.types import AgentContext


def _ctx(tmp_path):
    return AgentContext(
        run_id="run1",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        run_lane="atb_cache_only",
    )


def _case_ready():
    return {
        "case_id": "C1",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK1", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "neighbors": [],
        "risk_scores": {"top1_sim": 0.8, "mean_topk_sim": 0.7, "novelty_struct": 0.2, "mechanism_entropy": 0.3, "mechanism_hint": "ICT", "hint_confidence": 0.6},
        "evidence_readiness": {"atb": {"cache_status": "success"}, "literature": {"status": "not_started"}, "experiment": {"status": "not_requested"}},
        "target_fields": {},
        "target_fields_provenance": {},
        "current_gate": {"state": "ready_for_reasoning", "ready_for_reasoning": True, "reasoning_mode": "normal", "reason": "ok"},
        "action_plan": [{"action": "run_master_reasoner", "priority": 2, "status": "pending", "inputs": {}, "expected_outputs": [], "blocking": False, "notes": ""}],
    }


def test_reasoning_agent_patch_paths_are_whitelisted(tmp_path):
    case = _case_ready()
    agent = ReasoningAgent(use_llm=False)
    ctx = _ctx(tmp_path)
    inputs = agent.build_inputs(case, ctx)
    result = agent.run(case, ctx, inputs)

    assert result.status == "stubbed"
    validate_patch(
        result.patch,
        allowed_prefixes=agent.allowed_patch_prefixes,
        append_only_prefixes=agent.append_only_prefixes,
    )
    assert all(
        p["path"].startswith("/master_reasoning") or p["path"].startswith("/reasoning/") or p["path"] == "/reasoning"
        for p in result.patch
    )

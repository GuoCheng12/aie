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
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 12.0, "delta_gap": 0.1, "delta_volume": 0.5}},
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
        "current_gate": {"state": "ready_for_reasoning", "ready_for_reasoning": True, "reasoning_mode": "normal", "reason": "ok"},
        "action_plan": [{"action": "run_master_reasoner", "priority": 2, "status": "pending", "inputs": {}, "expected_outputs": [], "blocking": False, "notes": ""}],
        "agent_runs": [],
    }


def test_reasoning_idempotency_hit(monkeypatch, tmp_path: Path):
    def _fake_responses_json(self, *, instructions, input_text, schema_name, schema, **kwargs):
        _ = kwargs
        import json
        payload = json.loads(input_text)
        registry = payload.get("evidence_registry") or {}

        def _eid(case_path: str) -> str:
            rows = registry if isinstance(registry, list) else list((registry or {}).values())
            for row in rows:
                if isinstance(row, dict) and row.get("case_path") == case_path and isinstance(row.get("evidence_id"), str):
                    return str(row.get("evidence_id"))
            raise AssertionError(f"missing evidence id for {case_path}")

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
                        "atb_support_level": "weak",
                    },
                    "confidence": 0.55,
                    "reasoning_mode_used": "normal",
                },
                "supporting_chain": [
                    {
                        "step_id": "A",
                        "step_name": "torsion_access",
                        "claim": "Excited-state structural torsion access from aTB.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "torsion", "role": "support"}],
                    },
                    {
                        "step_id": "B",
                        "step_name": "ct_family",
                        "claim": "Nonradiative CT/torsion channel is plausible.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "ct context", "role": "context"}],
                    },
                    {
                        "step_id": "C",
                        "step_name": "aIE_bridge",
                        "claim": "Aggregation rigidification may suppress channel.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "packing proxy", "role": "context"}],
                    },
                    {
                        "step_id": "D",
                        "step_name": "discriminators",
                        "claim": "Compare and test discriminative signatures.",
                        "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}],
                    },
                ],
                "competing_hypotheses": [{"name": "TICT", "confidence": 0.2, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}]}],
                "predictions": [
                    {"prediction": "measure TRPL", "expected_signal": "lifetime trend", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "torsion sensitivity", "role": "context"}]},
                    {"prediction": "compare solvent polarity", "expected_signal": "CT shift", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "ct", "role": "context"}]},
                    {"prediction": "compare aggregation state", "expected_signal": "channel suppression", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "aggregation proxy", "role": "context"}]},
                ],
                "limits": ["normal mode"],
                "evidence_used": [
                    {"evidence_id": _eid("/risk_scores/top1_sim"), "note": "high sim prior", "role": "context"},
                    {"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "support", "role": "support"},
                ],
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


def test_five_signals_generation_does_not_participate_in_validation(monkeypatch, tmp_path: Path):
    def _fake_responses_json(self, *, instructions, input_text, schema_name, schema, **kwargs):
        _ = kwargs
        import json
        payload = json.loads(input_text)
        registry = payload.get("evidence_registry") or {}

        def _eid(case_path: str) -> str:
            rows = registry if isinstance(registry, list) else list((registry or {}).values())
            for row in rows:
                if isinstance(row, dict) and row.get("case_path") == case_path and isinstance(row.get("evidence_id"), str):
                    return str(row.get("evidence_id"))
            raise AssertionError(f"missing evidence id for {case_path}")

        return {
            "request": {"schema_name": schema_name},
            "response": {"id": "resp-2"},
            "parsed": {
                "status": "ok",
                "template_used": "stable",
                "mechanism_claim": {
                    "primary_hypothesis": {
                        "mechanism_label": "ICT",
                        "aie_rationale_type": "stable",
                        "natural_language_mechanism": "ICT dominates",
                        "atb_support_level": "weak",
                    },
                    "confidence": 0.55,
                    "reasoning_mode_used": "normal",
                },
                "supporting_chain": [
                    {"step_id": "A", "step_name": "torsion_access", "claim": "Excited-state structural access.", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "a", "role": "support"}]},
                    {"step_id": "B", "step_name": "ct_family", "claim": "Nonradiative channel.", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "b", "role": "context"}]},
                    {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation rigidification suppress.", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "c", "role": "context"}]},
                    {"step_id": "D", "step_name": "discriminators", "claim": "Compare and test predictions.", "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "d", "role": "context"}]},
                ],
                "competing_hypotheses": [{"name": "TICT", "confidence": 0.2, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}]}],
                "predictions": [
                    {"prediction": "p1", "expected_signal": "s1", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "x", "role": "context"}]},
                    {"prediction": "p2", "expected_signal": "s2", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "y", "role": "context"}]},
                    {"prediction": "p3", "expected_signal": "s3", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "z", "role": "context"}]},
                ],
                "limits": ["normal mode"],
                "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}],
                "recommended_next_actions": [],
            },
        }

    monkeypatch.setattr("src.tools.llm_client.ResponsesLLMClient.responses_json", _fake_responses_json)
    # Force malformed/irrelevant summary payload; validation should still succeed because this runs post-validation.
    monkeypatch.setattr("src.agents.reasoning_agent.build_reasoning_five_signals", lambda **kwargs: {"bad": "not_master_schema"})

    agent = ReasoningAgent(use_llm=True)
    case0 = _base_case()
    c1, s1 = Orchestrator(agents=[agent], ctx=_ctx(tmp_path, "run3")).run(case0)
    assert s1["steps"][0]["status"] == "success"
    assert c1.get("master_reasoning_status") == "completed"

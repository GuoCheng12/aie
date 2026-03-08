import json
from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, run_iterative_rounds


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-llm-layer",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
    )


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-LLM-LAYER",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-LLM", "created_at": "2026-02-28T00:00:00Z"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 10.0, "delta_gap": 0.1, "delta_volume": 0.2}},
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
        "risk_scores": {"top1_sim": 0.7, "mean_topk_sim": 0.6, "mechanism_entropy": 0.33, "novelty_struct": 0.2},
        "neighbors": [],
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _master_stub(*, case_json: dict, reasoning_config: dict) -> dict:
    _ = case_json, reasoning_config
    parsed = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "unknown",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "llm-layer-test",
                "atb_support_level": "weak",
            },
            "confidence": 0.4,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [],
        "competing_hypotheses": [],
        "predictions": [],
        "limits": ["Conservative mode: mechanism assignment is tentative and should be interpreted with uncertainty."],
        "evidence_used": [],
        "recommended_next_actions": [],
    }
    return {
        "status": "success",
        "master_output_parsed": parsed,
        "normalized_output": parsed,
        "validation_errors": [],
        "used_case_paths": [],
        "used_evidence_ids": [],
        "used_evidence": [],
        "llm_request": {},
        "llm_response_raw": {},
        "pack_hash": "h",
        "prompt_bundle": {"template_version": "stable_v1"},
        "reasoning_pack": {"pack_version": "master_pack_v1"},
    }


def _rule_eval_stop(*, case_json: dict, judged: dict, round_index: int, active_profile: str, run_lane: str, prev_confidence=None, info_gain=None) -> dict:
    _ = case_json, judged, round_index, active_profile, run_lane, prev_confidence, info_gain
    return {
        "round_index": 0,
        "status": "ok",
        "evidence_scorecard": [],
        "conflict_adjudication": [],
        "voi_ranked_actions": [
            {
                "action": "run_master_reasoner",
                "expected_information_gain": 0.4,
                "feasibility_score": 1.0,
                "priority_score": 0.4,
                "feasible": True,
                "blocked_by": [],
                "unblock_actions": [],
            }
        ],
        "next_round_profile": "NONE",
        "stop_recommendation": {"should_stop": True, "reason_code": "profile_exhausted", "explanation": "done"},
        "confidence_update": {"prev": 0.4, "delta": 0.0, "new": 0.4, "basis": "master_confidence"},
        "feasibility": {
            "lane_capabilities": {
                "atb_available": True,
                "offline_pdf_available": False,
                "literature_enabled": False,
                "wetlab_enabled": False,
            },
            "constraints": ["lane_disabled:literature", "lane_disabled:wetlab"],
            "overall_score": 0.25,
        },
    }


def test_round_runner_llm_layer_merges_and_stop_policy_still_dominates(monkeypatch, tmp_path: Path):
    captured = {}

    def _fake_llm_eval_run(self, **kwargs):
        _ = self
        captured["model"] = kwargs.get("model")
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return {
            "parsed": {
                "critique_points": [{"title": "c", "severity": "medium", "note": "n", "evidence_ids": []}],
                "conflicts": [],
                "voi_ranked_actions": [{"action": "run_master_reasoner", "llm_priority_weight": 1.7, "rationale": "boost"}],
                "confidence_delta_suggestion": -0.05,
                "next_round_profile_suggestion": "R2",
            },
            "request": {"mock": True},
            "response": {"mock": True},
        }

    monkeypatch.setattr("src.orchestration.round_runner.LLMEvaluator.run", _fake_llm_eval_run)

    case_after, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=4,
        start_profile="R0",
        run_master_fn=_master_stub,
        eval_report_fn=_rule_eval_stop,
        evaluator_use_llm=True,
        status_path=tmp_path / "artifacts" / "run_status.json",
    )
    _ = case_after
    assert summary["executed_rounds"] == 1
    assert summary["stop_reason"] == "profile_exhausted"
    eval_report_path = summary["rounds"][0]["eval_report_path"]
    payload = json.loads(Path(eval_report_path).read_text(encoding="utf-8"))
    assert payload["llm_layer"]["enabled"] is True
    assert payload["voi_ranked_actions"][0]["llm_priority_weight"] == 1.7
    # evaluator config inherits master config (which defaults to ctx model/effort)
    assert captured["model"] == "gpt-test"
    assert captured["reasoning_effort"] == "medium"


def test_round_runner_llm_layer_can_override_evaluator_model_effort(monkeypatch, tmp_path: Path):
    captured = {}

    def _fake_llm_eval_run(self, **kwargs):
        _ = self
        captured["model"] = kwargs.get("model")
        captured["reasoning_effort"] = kwargs.get("reasoning_effort")
        return {
            "parsed": {
                "critique_points": [],
                "conflicts": [],
                "voi_ranked_actions": [],
                "confidence_delta_suggestion": 0.0,
                "next_round_profile_suggestion": "R1",
            },
            "request": {"mock": True},
            "response": {"mock": True},
        }

    monkeypatch.setattr("src.orchestration.round_runner.LLMEvaluator.run", _fake_llm_eval_run)

    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=1,
        start_profile="R0",
        run_master_fn=_master_stub,
        eval_report_fn=_rule_eval_stop,
        evaluator_use_llm=True,
        master_model="gpt-master",
        master_reasoning_effort="high",
        evaluator_model="gpt-evaluator",
        evaluator_reasoning_effort="low",
        status_path=tmp_path / "artifacts" / "run_status.json",
    )
    assert summary["executed_rounds"] == 1
    assert captured["model"] == "gpt-evaluator"
    assert captured["reasoning_effort"] == "low"

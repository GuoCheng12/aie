import json
from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, run_iterative_rounds


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-state",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
    )


def _base_case() -> dict:
    return {
        "case_id": "CASE-ROUND-STATE",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-STATE", "created_at": "2026-02-28T00:00:00Z"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {"state": "ready_conservative", "ready_for_reasoning": True, "reasoning_mode": "conservative", "reason": "ok"},
        "neighbors": [],
        "risk_scores": {"top1_sim": 0.61, "mean_topk_sim": 0.55, "mechanism_entropy": 0.4, "novelty_struct": 0.2},
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 9.1, "delta_gap": 0.1, "delta_volume": 0.2}},
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
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
                "natural_language_mechanism": "Round-state test hypothesis.",
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
        "recommended_next_actions": ["request_manual_pdf"],
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
        "reasoning_pack": {},
    }


def _eval_stub(*, case_json: dict, judged: dict, round_index: int, active_profile: str, run_lane: str, prev_confidence=None, info_gain=None) -> dict:
    _ = case_json, judged, active_profile, run_lane, prev_confidence, info_gain
    return {
        "round_index": round_index,
        "status": "ok",
        "evidence_scorecard": [],
        "conflict_adjudication": [{"conflict_id": "C1", "status": "unresolved", "rationale": "needs more evidence", "evidence_ids": []}],
        "voi_ranked_actions": [{"action": "run_master_reasoner", "feasible": True, "priority_score": 0.2}],
        "next_round_profile": "R1",
        "stop_recommendation": {"should_stop": False, "reason_code": "continue", "explanation": "continue"},
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


def test_round_state_delta_fields_present(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_base_case(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=1,
        start_profile="R0",
        run_master_fn=_master_stub,
        eval_report_fn=_eval_stub,
    )
    assert "llm_failure_reason" in summary["rounds"][0]
    round_state_path = Path(summary["rounds"][0]["round_state_path"])
    state = json.loads(round_state_path.read_text(encoding="utf-8"))
    assert "llm_failure_reason" in state
    assert "hypothesis_delta" in state
    assert "new_evidence_used" in state
    assert "conflict_delta" in state
    assert "feasibility_snapshot" in state
    assert "chosen_next_round_profile" in state
    assert "profile_adjustment_reason" in state

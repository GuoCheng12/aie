import json
from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, run_iterative_rounds


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-r0",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
    )


def _base_case() -> dict:
    return {
        "case_id": "CASE-R0",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-R0", "created_at": "2026-02-28T00:00:00Z"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {"delta_dihedral": 12.0, "delta_gap": 0.11, "delta_volume": 0.33},
            },
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
        "risk_scores": {"top1_sim": 0.72, "mean_topk_sim": 0.65, "mechanism_entropy": 0.31, "novelty_struct": 0.2},
        "neighbors": [],
        "target_fields": {},
        "target_fields_provenance": {},
        "agent_runs": [],
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
                "natural_language_mechanism": "Baseline hypothesis.",
                "atb_support_level": "weak",
            },
            "confidence": 0.42,
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
        "llm_request": {"mock": True},
        "llm_response_raw": {"mock": True},
        "pack_hash": "pack-hash",
        "prompt_bundle": {"template_version": "stable_v1"},
        "reasoning_pack": {"pack_version": "master_pack_v1"},
    }


def _eval_stub(*, case_json: dict, judged: dict, round_index: int, active_profile: str, run_lane: str, prev_confidence=None, info_gain=None) -> dict:
    _ = case_json, judged, prev_confidence, info_gain
    return {
        "round_index": round_index,
        "status": "ok",
        "evidence_scorecard": [],
        "conflict_adjudication": [],
        "voi_ranked_actions": [{"action": "run_master_reasoner", "feasible": True, "priority_score": 0.1}],
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


def test_round_runner_r0_minimal_writeback(tmp_path: Path):
    case_after, summary = run_iterative_rounds(
        case_json=_base_case(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=1,
        start_profile="R0",
        run_master_fn=_master_stub,
        eval_report_fn=_eval_stub,
    )

    assert summary["executed_rounds"] == 1
    assert summary["rounds"][0]["commit_applied"] is False
    assert "master_reasoning" not in case_after
    assert "post_uq" not in case_after
    assert (case_after.get("iterative") or {}).get("active_profile") == "R0"
    assert (case_after.get("iterative") or {}).get("current_round") == 0
    state_path = summary["rounds"][0]["round_state_path"]
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    assert "hypothesis_delta" in state
    assert "new_evidence_used" in state
    assert "conflict_delta" in state

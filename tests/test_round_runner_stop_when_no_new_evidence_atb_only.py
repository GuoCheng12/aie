from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, run_iterative_rounds


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-r1-r2-stop",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
    )


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-R1-R2-STOP",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-R1R2"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": [],
        "risk_scores": {"top1_sim": 0.65, "mean_topk_sim": 0.6, "mechanism_entropy": 0.2, "novelty_struct": 0.2},
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": 9.0,
                    "delta_gap": -0.2,
                    "delta_volume": 0.3,
                    "excitation_energy": 1.7,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _master_no_new_ids_stub(*, case_json: dict, reasoning_config: dict) -> dict:
    _ = case_json, reasoning_config
    parsed = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "unknown",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "stub",
                "atb_support_level": "weak",
            },
            "confidence": 0.4,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [],
        "competing_hypotheses": [],
        "predictions": [],
        "limits": [],
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


def test_round_runner_stops_r1_to_r2_when_no_new_evidence_in_atb_only(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=4,
        start_profile="R1",
        run_master_fn=_master_no_new_ids_stub,
    )
    assert summary["executed_rounds"] == 1
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "no_new_evidence_available_in_lane"


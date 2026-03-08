from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT, run_iterative_rounds


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-stagnation",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
    )


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-STAGNATION",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-STAG"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": [],
        "risk_scores": {"top1_sim": 0.6, "mean_topk_sim": 0.5, "mechanism_entropy": 0.3, "novelty_struct": 0.2},
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 9.0, "delta_gap": 0.1, "delta_volume": 0.2}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
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
                "natural_language_mechanism": "stagnation test",
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


def _master_fail_stub(*, case_json: dict, reasoning_config: dict) -> dict:
    _ = case_json, reasoning_config
    return {
        "status": "failed_schema_validation",
        "master_output_parsed": {},
        "normalized_output": None,
        "validation_errors": [{"type": "schema", "code": "missing_required", "path": "$", "detail": "forced fail"}],
        "used_case_paths": [],
        "used_evidence_ids": [],
        "used_evidence": [],
        "llm_request": {},
        "llm_response_raw": {},
        "pack_hash": "h",
        "prompt_bundle": {"template_version": "stable_v1"},
        "reasoning_pack": {"pack_version": "master_pack_v1"},
    }


def _master_r2_low_reliability_stub(*, case_json: dict, reasoning_config: dict) -> dict:
    _ = case_json, reasoning_config
    parsed = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "unknown",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "r2 low reliability",
                "atb_support_level": "weak",
            },
            "confidence": 0.33,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [],
        "competing_hypotheses": [],
        "predictions": [],
        "limits": [],
        "evidence_used": [{"evidence_id": "E21", "note": "neighbor stats cue", "role": "context"}],
        "recommended_next_actions": [],
    }
    return {
        "status": "success",
        "master_output_parsed": parsed,
        "normalized_output": parsed,
        "validation_errors": [],
        "used_case_paths": [],
        "used_evidence_ids": ["E21"],
        "used_evidence": [{"evidence_id": "E21"}],
        "llm_request": {},
        "llm_response_raw": {},
        "pack_hash": "h",
        "prompt_bundle": {"template_version": "mixture_v1"},
        "reasoning_pack": {
            "pack_version": "master_pack_v1",
            "risk_scores": {"neighbor_atb_stats_by_label": {"reliability": "low"}},
        },
    }


def test_round_runner_stops_when_profile_repeats_without_new_evidence(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=3,
        start_profile="R2",
        run_master_fn=_master_stub,
    )
    assert summary["executed_rounds"] == 1
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "no_new_evidence_available_in_lane"


def test_round_runner_stops_when_lane_has_no_higher_profile_and_no_new_evidence(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=3,
        start_profile="R1",
        run_master_fn=_master_stub,
    )
    assert summary["executed_rounds"] == 1
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "no_new_evidence_available_in_lane"


def test_pre_r2_failure_recovery_guard_forces_one_r2_attempt(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=3,
        start_profile="R0",
        run_master_fn=_master_fail_stub,
        pre_r2_failure_recovery_mode="force_r2",
    )
    assert summary["executed_rounds"] >= 2
    rounds = summary["rounds"]
    assert rounds[0]["active_profile"] == "R0"
    assert rounds[1]["active_profile"] == "R2"


def test_round_runner_r2_low_reliability_new_ids_do_not_count_as_effective_gain(tmp_path: Path):
    _, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        max_rounds=4,
        start_profile="R2",
        run_master_fn=_master_r2_low_reliability_stub,
    )
    # Round0 initializes hypothesis; Round1 should stop due to no effective gain in repeated R2 profile.
    assert summary["executed_rounds"] == 2
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "no_new_evidence_available_in_lane"

from pathlib import Path

from src.core.types import AgentContext
from src.orchestration.round_runner import (
    ROUND_RUNNER_MODE_COMMIT_ALL,
    ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
    run_iterative_rounds,
)


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


def _master_final_other_stub(*, case_json: dict, reasoning_config: dict) -> dict:
    _ = case_json, reasoning_config
    parsed = {
        "status": "insufficient_evidence",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Residual interpretation remains the strongest late-round explanation.",
                "atb_support_level": "weak",
            },
            "confidence": 0.41,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target-side evidence is present.", "evidence_used": [{"evidence_id": "E31", "note": "self-trend", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution stays contextual.", "evidence_used": [{"evidence_id": "E35", "note": "redistribution", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Comparative evidence does not recover a canonical label.", "evidence_used": [{"evidence_id": "E21", "note": "comparative", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Standard candidates stay unresolved.", "evidence_used": [{"evidence_id": "E56", "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ICT", "confidence": 0.31, "atb_support_level": "weak", "evidence_used": [{"evidence_id": "E40", "note": "structure prior", "role": "context"}]},
            {"name": "ESIPT", "confidence": 0.29, "atb_support_level": "none", "evidence_used": [{"evidence_id": "E51", "note": "motif context", "role": "context"}]},
        ],
        "predictions": [],
        "limits": [],
        "evidence_used": [{"evidence_id": "E31", "note": "self-trend", "role": "support"}],
        "recommended_next_actions": [],
        "__meta": {
            "llm_primary_label": "other",
            "normalized_primary_label": "other",
            "decision_state": "insufficient_evidence",
            "canonical_pool_closed": False,
            "residual_other_admissible": True,
            "novelty_candidate": False,
            "novelty_basis": [],
            "normalization_reason_codes": ["other_without_residual_admissibility"],
        },
    }
    return {
        "status": "success",
        "master_output_parsed": parsed,
        "normalized_output": parsed,
        "validation_errors": [],
        "used_case_paths": [],
        "used_evidence_ids": ["E31"],
        "used_evidence": [{"evidence_id": "E31"}],
        "llm_request": {},
        "llm_response_raw": {},
        "pack_hash": "h",
        "prompt_bundle": {"template_version": "mixture_v1"},
        "reasoning_pack": {
            "pack_version": "master_pack_v1",
            "risk_scores": {"novelty_struct": 0.4, "mechanism_entropy": 0.4},
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
    # With count_effective_added=0 in atb-only R2, stop should trigger immediately.
    assert summary["executed_rounds"] == 1
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "no_new_evidence_available_in_lane"


def test_round_runner_final_adjudication_writes_case_label_and_sidecars(tmp_path: Path):
    current, summary = run_iterative_rounds(
        case_json=_case_fixture(),
        ctx=_ctx(tmp_path),
        mode=ROUND_RUNNER_MODE_COMMIT_ALL,
        max_rounds=1,
        start_profile="R3",
        run_master_fn=_master_final_other_stub,
    )
    assert summary["executed_rounds"] == 1
    master = current.get("master_reasoning") or {}
    claim = master.get("mechanism_claim") or {}
    primary = claim.get("primary_hypothesis") or {}
    assert primary.get("mechanism_label") == "other"
    meta = master.get("__meta") or {}
    adjud = meta.get("final_label_adjudication") or {}
    assert adjud.get("adjudicated_label") == "other"
    assert adjud.get("decision_state") == "residual_supported"

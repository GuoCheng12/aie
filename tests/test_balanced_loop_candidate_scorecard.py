from src.agents.judge_agent import (
    build_eval_report,
    build_final_adjudication_context,
    build_final_label_adjudication,
)
from src.orchestration.round_runner import _round_state_payload
from src.reasoning.master_reasoner import build_candidate_scorecard, build_reasoning_pack, validate_master_output


def _base_case() -> dict:
    smiles = "Oc1ccccc1C=Nc2ccccc2"
    return {
        "query": {"input_smiles": smiles, "canonical_smiles": smiles, "inchikey": "IK-BALANCED", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "balanced_loop_test",
        },
        "neighbors": [
            {"rank": 1, "sim": 0.82, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"},
            {"rank": 2, "sim": 0.79, "neighbor_inchikey": "N2", "neighbor_mechanism_label": "TICT"},
        ],
        "risk_scores": {
            "top1_sim": 0.82,
            "mean_topk_sim": 0.78,
            "novelty_struct": 0.16,
            "mechanism_entropy": 0.31,
            "structure_prior_profile": {
                "donor_acceptor_topology": "mid",
                "intramolecular_hbond_candidates": "possible",
                "aromatic_core_density": "high",
                "flexibility_proxy": "mid",
                "conjugation_proxy": "high",
                "overall_structure_prior": "Conjugated aromatic topology with moderate flexibility.",
                "reliability": "high",
            },
            "structure_motif_profile": {
                "version": "structure_motif_v1",
                "intramolecular_hbond_motif": "possible",
                "intramolecular_hbond_geometry": "favorable",
                "tautomerizable_motif": "possible",
                "proton_transfer_topology_candidate": "possible",
                "tautomerizable_subgraph_strength": "mid",
                "donor_acceptor_path_strength": "strong",
                "donor_acceptor_separation_regime": "mid",
                "aromatic_scaffold_type": "fused",
                "aromatic_rigidity_signature": "high",
                "fused_aromatic_core_strength": "high",
                "planarity_proxy": "high",
                "conjugation_compactness": "high",
                "flexibility_regime": "low",
                "motif_density": "high",
                "reliability": "high",
                "notes": [
                    "Detected donor and acceptor sites with mid donor-acceptor separation.",
                    "Intramolecular H-bond geometry is favorable; tautomerizable subgraph strength is mid.",
                ],
            },
            "structure_retrieval_profile": {
                "version": "structure_retrieval_v1",
                "feature_morgan_topk": [{"case_index": 1, "sim": 0.83, "mechanism_label": "ICT"}],
                "murcko_topk": [{"case_index": 2, "sim": 1.0, "mechanism_label": "ESIPT"}],
                "feature_neighbor_label_distribution": {"ICT": 0.7, "ESIPT": 0.3},
                "scaffold_neighbor_label_distribution": {"ESIPT": 0.6, "ICT": 0.4},
                "retrieval_consensus_strength": "mid",
            },
            "structure_candidate_distribution": {
                "version": "structure_candidate_dist_v1",
                "label_probs": {"ICT": 0.38, "ESIPT": 0.29, "neutral aromatic": 0.17, "TICT": 0.10, "other": 0.06},
                "top_candidates": [
                    {"label": "ICT", "prob": 0.38},
                    {"label": "ESIPT", "prob": 0.29},
                    {"label": "neutral aromatic", "prob": 0.17},
                    {"label": "TICT", "prob": 0.10},
                    {"label": "other", "prob": 0.06},
                ],
                "top3": [{"label": "ICT", "prob": 0.38}, {"label": "ESIPT", "prob": 0.29}, {"label": "neutral aromatic", "prob": 0.17}],
                "calibration": {"method": "retrieval_fallback", "reliability": "low"},
            },
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": 9.5,
                    "delta_gap": -0.42,
                    "delta_volume": 1.2,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _eid(pack: dict, suffix: str) -> str:
    for row in pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        if row.get("evidence_id") == suffix:
            return suffix
    raise AssertionError(f"missing evidence id {suffix}")


def test_validate_master_output_keeps_r0_as_candidate_round():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R0"}})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Structure prior alone points to a residual bucket.",
                "atb_support_level": "none",
            },
            "confidence": 0.61,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Structure prior opens a candidate slate.", "evidence_used": [{"evidence_id": _eid(pack, "E40"), "note": "topology prior", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution remains unresolved.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation effects remain provisional.", "evidence_used": [{"evidence_id": _eid(pack, "E42"), "note": "aromatic context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Need target-specific evidence to separate candidates.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate distribution", "role": "context"}]},
        ],
        "competing_hypotheses": [],
        "predictions": [
            {"prediction": "collect target aTB self-trend", "expected_signal": "candidate reorder", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
            {"prediction": "compare gap trend", "expected_signal": "target-only constraint", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
            {"prediction": "measure aggregation response", "expected_signal": "candidate separation", "evidence_used": [{"evidence_id": _eid(pack, "E42"), "note": "aromatic context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E44"), "note": "overall structure prior", "role": "context"}],
        "recommended_next_actions": ["run_target_atb"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["status"] == "insufficient_evidence"
    assert normalized["template_used"] == "mixture"
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "unknown"
    assert float(normalized["mechanism_claim"]["confidence"]) <= 0.30
    assert len(normalized.get("competing_hypotheses") or []) >= 2
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "r0_other_primary_forbidden" in error_codes


def test_candidate_scorecard_drops_other_when_runtime_disables_it():
    case = _base_case()
    case["runtime"] = {
        "run_lane": "atb_cache_only",
        "reference_index_root": "data/reference_indices/split_levels_v2/views",
        "allow_other_label": False,
    }
    pack = build_reasoning_pack(
        case,
        {
            "run_lane": "atb_cache_only",
            "allowed_mechanism_labels": ["ICT", "TICT", "ESIPT", "neutral aromatic", "unknown"],
            "policy": {"allow_other_label": False},
            "evidence_profiles": {"active_profile": "R1"},
        },
    )
    scorecard = build_candidate_scorecard(
        reasoning_pack=pack,
        master_output={
            "mechanism_claim": {
                "primary_hypothesis": {"mechanism_label": "ICT"},
                "confidence": 0.41,
            },
            "competing_hypotheses": [{"name": "ESIPT", "confidence": 0.3, "evidence_used": []}],
            "evidence_used": [],
            "supporting_chain": [],
        },
    )
    assert scorecard
    assert "other" not in {str(row.get("label") or "") for row in scorecard}


def test_final_adjudication_excludes_other_when_active_label_pool_disables_it():
    case = _base_case()
    case["runtime"] = {
        "run_lane": "atb_cache_only",
        "reference_index_root": "data/reference_indices/split_levels_v2/views",
        "allow_other_label": False,
    }
    candidate_scorecard = [
        {
            "label": "neutral aromatic",
            "support_axes": ["target_observation"],
            "weakening_axes": [],
            "unresolved_axes": ["comparative_transferability"],
            "current_rank": 1,
            "current_confidence": 0.34,
        },
        {
            "label": "ICT",
            "support_axes": [],
            "weakening_axes": ["target_observation"],
            "unresolved_axes": ["comparative_transferability"],
            "current_rank": 2,
            "current_confidence": 0.29,
        },
    ]
    master_reasoning = {
        "mechanism_claim": {
            "primary_hypothesis": {"mechanism_label": "neutral aromatic"},
            "confidence": 0.34,
        },
        "__meta": {"llm_primary_label": "other"},
        "competing_hypotheses": [{"name": "ICT", "confidence": 0.29, "evidence_used": []}],
    }
    context = build_final_adjudication_context(
        case_json=case,
        master_reasoning=master_reasoning,
        active_profile="R2",
        candidate_scorecard=candidate_scorecard,
        normalization_summary={},
        eval_report={"conflict_adjudication": [{"status": "unresolved"}]},
        reasoning_config={
            "allowed_mechanism_labels": ["ICT", "TICT", "ESIPT", "neutral aromatic", "unknown"],
            "policy": {"allow_other_label": False},
        },
        used_evidence_ids=["E71"],
    )
    assert context["allow_other_label"] is False
    assert "other" not in context["legal_candidates"]
    adjudication = build_final_label_adjudication(context=context)
    assert adjudication["adjudicated_label"] == "neutral aromatic"
    assert adjudication["decision_state"] == "provisional_known"
    assert adjudication["why_not_other"].startswith("The active benchmark label pool disables 'other'")


def test_validate_master_output_keeps_late_round_other_with_target_side_support():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R2"}})
    out = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Late-round target evidence favors a residual interpretation while standard candidates remain weakened.",
                "atb_support_level": "weak",
            },
            "confidence": 0.52,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target aTB provides the main late-round support axis.", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend support", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution remains context rather than a decisive standard mechanism cue.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Comparative evidence does not pull the molecule back into a clear standard cluster.", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Residual outcome stays plausible after standard candidates remain unresolved.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ICT", "confidence": 0.34, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E40"), "note": "structure prior", "role": "context"}]},
            {"name": "ESIPT", "confidence": 0.31, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif geometry", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "seek emission-sensitive evidence", "expected_signal": "residual outcome remains or standard labels recover", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
            {"prediction": "re-check target self-trend consistency", "expected_signal": "support remains on target axis", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "trend support", "role": "support"}]},
            {"prediction": "probe redistribution sensitivity", "expected_signal": "standard candidates remain unresolved", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [
            {"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"},
            {"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"},
        ],
        "recommended_next_actions": ["collect_emission_context"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "other"
    meta = normalized.get("__meta") or {}
    assert meta.get("normalized_primary_label") == "other"
    assert meta.get("residual_other_admissible") is True
    assert meta.get("decision_state") == "residual_supported"
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "other_without_residual_admissibility" not in error_codes


def test_validate_master_output_still_demotes_r1_other_without_late_round_support():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}})
    out = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "R1 still only has provisional target evidence.",
                "atb_support_level": "weak",
            },
            "confidence": 0.44,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target aTB is still provisional at R1.", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend support", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution remains contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Structure prior keeps alternatives open.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Need comparative evidence before residual conclusions.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ICT", "confidence": 0.34, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E40"), "note": "structure prior", "role": "context"}]},
            {"name": "ESIPT", "confidence": 0.31, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif geometry", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "run comparative round", "expected_signal": "resolve residual ambiguity", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "trend support", "role": "support"}]},
            {"prediction": "probe motif-sensitive perturbation", "expected_signal": "standard candidates separate", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
            {"prediction": "check redistribution trend", "expected_signal": "context remains provisional", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}],
        "recommended_next_actions": ["run_r2_comparative"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "other"
    meta = normalized.get("__meta") or {}
    assert meta.get("decision_state") == "insufficient_evidence"
    assert meta.get("normalized_primary_label") == "other"
    assert meta.get("residual_other_admissible") is False


def test_validate_master_output_requires_r1_emission_citation_when_available():
    case = _base_case()
    case["target_fields"] = {
        "emission_aggr_nm": 520.0,
        "emission_solid_or_film_nm": 560.0,
    }
    case["target_fields_provenance"] = {
        "emission_aggr_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "aggregation",
            "condition_bucket": "aggregation",
        },
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}})
    out = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Target aTB starts to favor ICT but target observations were not cited.",
                "atb_support_level": "weak",
            },
            "confidence": 0.43,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target aTB adds structural access.", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend support", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution remains contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Structure prior remains in view.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Need comparative evidence later.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ESIPT", "confidence": 0.32, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif context", "role": "context"}]},
            {"name": "neutral aromatic", "confidence": 0.20, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E42"), "note": "aromatic context", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "collect comparative evidence", "expected_signal": "candidate ranking refines", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "trend support", "role": "support"}]},
            {"prediction": "check target observation consistency", "expected_signal": "observation remains available", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"prediction": "probe motif-sensitive perturbation", "expected_signal": "structure facts remain in play", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "motif context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}],
        "recommended_next_actions": ["run_r2_comparative"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "r1_missing_emission_observation_citation" in error_codes
    assert normalized["status"] == "insufficient_evidence"


def test_validate_master_output_keeps_single_axis_standard_label_provisional_in_late_rounds():
    case = _base_case()
    case["target_fields"] = {
        "emission_solid_or_film_nm": 450.0,
    }
    case["target_fields_provenance"] = {
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R3"}})
    out = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "neutral aromatic",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "A single target-observation axis keeps neutral aromatic provisionally ahead.",
                "atb_support_level": "weak",
            },
            "confidence": 0.35,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Solid-state emission anchors the target observation axis.", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution weakens standard alternatives rather than selecting one cleanly.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution weakens standard labels", "role": "counter"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Comparative evidence does not recover a clean standard cluster.", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Residual interpretation remains active late in the loop.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "other", "confidence": 0.30, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
            {"name": "ICT", "confidence": 0.28, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior", "role": "context"}]},
            {"name": "ESIPT", "confidence": 0.27, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif context", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "keep residual outcome in play", "expected_signal": "single-axis standard label remains provisional", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"}]},
            {"prediction": "collect stronger target-side corroboration", "expected_signal": "residual versus standard split resolves", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
            {"prediction": "re-check comparative drift", "expected_signal": "standard cluster either recovers or stays weakened", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [
            {"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"},
            {"evidence_id": _eid(pack, "E35"), "note": "redistribution weakening", "role": "counter"},
        ],
        "recommended_next_actions": ["collect_stronger_target_evidence"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "neutral aromatic"
    narrative = normalized["mechanism_claim"]["primary_hypothesis"]["natural_language_mechanism"]
    assert not narrative.startswith("Normalization note:")
    meta = normalized.get("__meta") or {}
    assert meta.get("llm_primary_label") == "neutral aromatic"
    assert meta.get("normalized_primary_label") == "neutral aromatic"
    assert meta.get("standard_label_closure") == "provisional"
    assert meta.get("residual_other_admissible") is True
    assert meta.get("decision_state") == "provisional_known"
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "late_round_single_axis_standard_prefers_residual_other" not in error_codes
    assert "other_without_residual_admissibility" not in error_codes


def test_validate_master_output_keeps_provisional_standard_label_when_residual_other_is_inadmissible():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R3"}})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "neutral aromatic",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "A single structural-relaxation axis provisionally favors a neutral aromatic outcome.",
                "atb_support_level": "weak",
            },
            "confidence": 0.41,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target self-trend gives the only positive axis.", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution stays contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Shape evidence is still contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E39"), "note": "shape context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Only one standard alternative remains in play.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
        ],
        "competing_hypotheses": [],
        "predictions": [
            {"prediction": "collect stronger corroboration", "expected_signal": "late-round provisional standard label either closes or collapses", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
            {"prediction": "re-check redistribution context", "expected_signal": "context remains non-decisive", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"prediction": "probe shape-rigidity corroboration", "expected_signal": "second axis either appears or stays absent", "evidence_used": [{"evidence_id": _eid(pack, "E39"), "note": "shape context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}],
        "recommended_next_actions": ["collect_stronger_target_evidence"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "neutral aromatic"
    assert normalized["status"] == "insufficient_evidence"
    assert normalized["template_used"] == "mixture"
    assert float(normalized["mechanism_claim"]["confidence"]) <= 0.32
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "late_round_single_axis_standard_prefers_residual_other" not in error_codes
    meta = normalized.get("__meta") or {}
    assert meta.get("standard_label_closure") == "provisional"
    assert meta.get("residual_other_admissible") is False
    assert meta.get("decision_state") == "provisional_known"


def test_validate_master_output_marks_unsupported_standard_label_as_unknown():
    case = _base_case()
    case["risk_scores"]["novelty_struct"] = 0.92
    case["risk_scores"]["mechanism_entropy"] = 0.81
    case["target_fields"] = {
        "emission_solid_or_film_nm": 450.0,
    }
    case["target_fields_provenance"] = {
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R3"}})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "No primary support axis closes ICT under the available evidence.",
                "atb_support_level": "none",
            },
            "confidence": 0.37,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target observation stays contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "context"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution remains contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Comparative evidence remains contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Structure prior does not close any standard label.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
        ],
            "competing_hypotheses": [
                {"name": "neutral aromatic", "confidence": 0.30, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
                {"name": "other", "confidence": 0.29, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "context"}]},
            ],
        "predictions": [
            {"prediction": "collect stronger target-side corroboration", "expected_signal": "a primary axis may appear", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "context"}]},
            {"prediction": "re-check comparative drift", "expected_signal": "standard labels remain unresolved", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative context", "role": "context"}]},
            {"prediction": "probe structure-sensitive perturbation", "expected_signal": "candidate pool remains open", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
        ],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "context"}],
        "recommended_next_actions": ["collect_stronger_target_evidence"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "ICT"
    narrative = normalized["mechanism_claim"]["primary_hypothesis"]["natural_language_mechanism"]
    assert not narrative.startswith("Normalization note:")
    meta = normalized.get("__meta") or {}
    assert meta.get("decision_state") == "insufficient_evidence"
    assert meta.get("standard_label_closure") == "unsupported"
    assert meta.get("novelty_candidate") is True
    assert set(meta.get("novelty_basis") or []) >= {"novelty_struct_high", "mechanism_entropy_high"}
    error_codes = {str(row.get("code") or "") for row in errors if isinstance(row, dict)}
    assert "other_without_residual_admissibility" not in error_codes


def test_final_label_adjudication_keeps_residual_other_when_admissible():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R3"}})
    master_output = {
        "status": "insufficient_evidence",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Residual interpretation remains the best late-round explanation.",
                "atb_support_level": "weak",
            },
            "confidence": 0.41,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target self-trend anchors the target-side support axis.", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Redistribution stays contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Comparative evidence fails to recover a canonical cluster.", "evidence_used": [{"evidence_id": _eid(pack, "E21"), "note": "comparative", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Standard candidates remain weakened or unresolved.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ICT", "confidence": 0.31, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E40"), "note": "structure prior", "role": "context"}]},
            {"name": "ESIPT", "confidence": 0.29, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif context", "role": "context"}]},
        ],
        "predictions": [],
        "limits": [],
        "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend", "role": "support"}],
        "recommended_next_actions": [],
    }
    ok, _, normalized, _, used_ids, _ = validate_master_output(
        master_output,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    scorecard = build_candidate_scorecard(reasoning_pack=pack, master_output=normalized, prev_scorecard=[], new_evidence_ids=["E31"])
    eval_report = build_eval_report(
        case_json=case,
        judged={"status": "ok", "confidence": 0.41, "contradictions": ["late conflict A", "late conflict B"], "missing_evidence": [], "recommended_actions": []},
        round_index=3,
        active_profile="R3",
        run_lane="atb_cache_only",
        prev_confidence=0.35,
        info_gain={"count_added": 1, "count_effective_added": 1, "hypothesis_changed": True, "confidence_delta": 0.06},
        candidate_scorecard=scorecard,
        normalization_summary=normalized.get("__meta") or {},
    )
    from src.agents.judge_agent import build_final_adjudication_context, build_final_label_adjudication

    context = build_final_adjudication_context(
        case_json=case,
        master_reasoning=normalized,
        active_profile="R3",
        candidate_scorecard=scorecard,
        normalization_summary=normalized.get("__meta") or {},
        eval_report=eval_report,
        reasoning_config={"policy": {"standard_label_min_positive_axes": 2, "standard_label_requires_target_axis": True, "residual_other_min_standard_candidates": 2, "residual_other_min_conflicts": 2, "novelty_candidate_entropy_threshold": 0.75, "novelty_candidate_struct_threshold": 0.60}},
        used_evidence_ids=used_ids,
    )
    adjud = build_final_label_adjudication(context=context)
    assert adjud["adjudicated_label"] == "other"
    assert adjud["decision_state"] == "residual_supported"


def test_candidate_scorecard_tracks_candidate_motion_and_sidecars():
    case = _base_case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}})
    master_output = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ESIPT",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Target evidence moves ESIPT upward but keeps alternatives open.",
                "atb_support_level": "weak",
            },
            "confidence": 0.41,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target aTB adds structural access.", "evidence_used": [{"evidence_id": "E31", "note": "target self-trend", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution stays moderate.", "evidence_used": [{"evidence_id": "E35", "note": "redistribution cue", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Structure motifs remain relevant.", "evidence_used": [{"evidence_id": "E51", "note": "motif geometry", "role": "support"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Need comparative evidence to separate ICT from ESIPT.", "evidence_used": [{"evidence_id": "E56", "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ICT", "confidence": 0.33, "atb_support_level": "weak", "evidence_used": [{"evidence_id": "E40", "note": "structure prior", "role": "support"}]},
            {"name": "neutral aromatic", "confidence": 0.18, "atb_support_level": "none", "evidence_used": [{"evidence_id": "E42", "note": "aromatic context", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "compare target aTB against neighbors", "expected_signal": "candidate separation", "evidence_used": [{"evidence_id": "E56", "note": "candidate context", "role": "context"}]},
            {"prediction": "check self-trend consistency", "expected_signal": "support shift", "evidence_used": [{"evidence_id": "E31", "note": "trend cue", "role": "support"}]},
            {"prediction": "probe H-bond-sensitive perturbation", "expected_signal": "motif-sensitive change", "evidence_used": [{"evidence_id": "E51", "note": "motif cue", "role": "support"}]},
        ],
        "limits": [],
        "evidence_used": [
            {"evidence_id": "E31", "note": "target self-trend", "role": "support"},
            {"evidence_id": "E51", "note": "motif geometry", "role": "support"},
        ],
        "recommended_next_actions": ["run_r2_comparative"],
    }
    prev_scorecard = [
        {"label": "ICT", "current_rank": 1, "current_confidence": 0.38},
        {"label": "ESIPT", "current_rank": 2, "current_confidence": 0.29},
        {"label": "neutral aromatic", "current_rank": 3, "current_confidence": 0.17},
    ]
    scorecard = build_candidate_scorecard(
        reasoning_pack=pack,
        master_output=master_output,
        prev_scorecard=prev_scorecard,
        new_evidence_ids=["E31", "E51"],
    )
    assert scorecard
    top = scorecard[0]
    assert top["label"] == "ESIPT"
    assert top["net_direction"] == "up"
    assert "structural_relaxation" in top["support_axes"]
    assert set(top["new_support_evidence_ids"]) == {"E31", "E51"}

    eval_report = build_eval_report(
        case_json=case,
        judged={"status": "ok", "confidence": 0.41, "contradictions": [], "missing_evidence": [], "recommended_actions": []},
        round_index=1,
        active_profile="R1",
        run_lane="atb_cache_only",
        prev_confidence=0.29,
        info_gain={"count_added": 2, "count_effective_added": 2, "hypothesis_changed": True, "confidence_delta": 0.12},
        candidate_scorecard=scorecard,
        normalization_summary={
            "llm_primary_label": "ESIPT",
            "normalized_primary_label": "ESIPT",
            "decision_state": "closed_known",
            "canonical_pool_closed": True,
            "standard_label_closure": "closed",
            "residual_other_admissible": False,
            "novelty_candidate": False,
            "novelty_basis": [],
            "normalization_reason_codes": [],
        },
    )
    assert eval_report["candidate_scorecard"][0]["label"] == "ESIPT"
    assert eval_report["normalization_summary"]["normalized_primary_label"] == "ESIPT"
    assert eval_report["normalization_summary"]["decision_state"] == "closed_known"

    round_state = _round_state_payload(
        round_index=1,
        active_profile="R1",
        master_report={
            "hypothesis": {"mechanism_label": "ESIPT", "template_used": "mixture"},
            "confidence": 0.41,
            "used_evidence_ids": ["E31", "E51"],
            "used_case_paths": ["/risk_scores/structure_motif_profile/intramolecular_hbond_motif"],
            "normalization_summary": {
                "llm_primary_label": "ESIPT",
                "normalized_primary_label": "ESIPT",
                "decision_state": "closed_known",
                "canonical_pool_closed": True,
                "standard_label_closure": "closed",
                "residual_other_admissible": False,
                "novelty_candidate": False,
                "novelty_basis": [],
                "normalization_reason_codes": [],
            },
            "llm_failure_reason": None,
        },
        eval_report=eval_report,
        chosen_next_round_profile="R2",
        profile_adjustment_reason="as_recommended",
        prev_master_report={"hypothesis": {"mechanism_label": "ICT", "template_used": "mixture"}, "confidence": 0.29},
        prev_used_ids=["E40"],
        effective_added_ids=["E31", "E51"],
        prev_conflict_ids=[],
        candidate_scorecard=scorecard,
        normalization_summary=eval_report.get("normalization_summary"),
    )
    assert round_state["candidate_scorecard"][0]["label"] == "ESIPT"
    assert round_state["normalization_summary"]["normalized_primary_label"] == "ESIPT"
    assert round_state["normalization_summary"]["decision_state"] == "closed_known"


def test_candidate_scorecard_tracks_target_observation_axis_when_emission_evidence_is_used():
    case = _base_case()
    case["target_fields"] = {
        "emission_aggr_nm": 520.0,
        "emission_solid_or_film_nm": 560.0,
    }
    case["target_fields_provenance"] = {
        "emission_aggr_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "aggregation",
            "condition_bucket": "aggregation",
        },
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}})
    master_output = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Target observation and target aTB together favor ICT while keeping alternatives visible.",
                "atb_support_level": "weak",
            },
            "confidence": 0.41,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Observed emission and target aTB constrain the candidate slate.", "evidence_used": [{"evidence_id": _eid(pack, "E70"), "note": "aggregate observation", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution remains one axis among several.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation/rigidification stays provisional.", "evidence_used": [{"evidence_id": _eid(pack, "E72"), "note": "emission shift summary", "role": "support"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Comparative evidence can refine later rounds.", "evidence_used": [{"evidence_id": _eid(pack, "E56"), "note": "candidate context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "ESIPT", "confidence": 0.32, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E51"), "note": "motif context", "role": "context"}]},
            {"name": "neutral aromatic", "confidence": 0.25, "atb_support_level": "none", "evidence_used": [{"evidence_id": _eid(pack, "E42"), "note": "aromatic context", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "collect neighbor comparative evidence", "expected_signal": "candidate ranking refines", "evidence_used": [{"evidence_id": _eid(pack, "E70"), "note": "aggregate observation", "role": "support"}]},
            {"prediction": "re-check target shift context", "expected_signal": "target observation stays anchored", "evidence_used": [{"evidence_id": _eid(pack, "E72"), "note": "shift summary", "role": "support"}]},
            {"prediction": "probe self-trend consistency", "expected_signal": "target-specific update remains coherent", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "self-trend support", "role": "support"}]},
        ],
        "limits": [],
        "evidence_used": [
            {"evidence_id": _eid(pack, "E70"), "note": "aggregate observation", "role": "support"},
            {"evidence_id": _eid(pack, "E72"), "note": "shift summary", "role": "support"},
        ],
        "recommended_next_actions": ["run_r2_comparative"],
    }
    ok, errors, normalized, _, used_ids, _ = validate_master_output(
        master_output,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert "E70" in used_ids
    assert "E72" in used_ids

    scorecard = build_candidate_scorecard(
        reasoning_pack=pack,
        master_output=normalized,
        prev_scorecard=[],
        new_evidence_ids=["E70", "E72"],
    )
    primary = next(row for row in scorecard if row["label"] == "ICT")
    assert "target_observation" in primary["support_axes"]
    assert set(primary["new_support_evidence_ids"]) == {"E70", "E72"}


def test_candidate_scorecard_new_support_ids_follow_role_aligned_primary_evidence():
    case = _base_case()
    case["target_fields"] = {
        "emission_solid_or_film_nm": 450.0,
    }
    case["target_fields_provenance"] = {
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=BAL",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R3"}})
    master_output = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "neutral aromatic",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Target observation remains the only positive axis.",
                "atb_support_level": "weak",
            },
            "confidence": 0.33,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Solid-state observation supports the current lead.", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"}]},
                {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution context remains non-decisive.", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
                {"step_id": "C", "step_name": "aIE_bridge", "claim": "Shape cue remains contextual.", "evidence_used": [{"evidence_id": _eid(pack, "E39"), "note": "shape context", "role": "context"}]},
                {"step_id": "D", "step_name": "discriminators", "claim": "Residual competition stays active.", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "other", "confidence": 0.29, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
            {"name": "ICT", "confidence": 0.25, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid(pack, "E50"), "note": "structure prior", "role": "context"}]},
        ],
        "predictions": [
            {"prediction": "collect stronger corroboration", "expected_signal": "single-axis support either holds or collapses", "evidence_used": [{"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"}]},
                {"prediction": "re-check redistribution context", "expected_signal": "context remains non-decisive", "evidence_used": [{"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"}]},
            {"prediction": "compare against residual candidate", "expected_signal": "residual competition persists", "evidence_used": [{"evidence_id": _eid(pack, "E31"), "note": "target self-trend", "role": "support"}]},
        ],
        "limits": [],
        "evidence_used": [
            {"evidence_id": _eid(pack, "E71"), "note": "solid emission observation", "role": "support"},
            {"evidence_id": _eid(pack, "E35"), "note": "redistribution context", "role": "context"},
            {"evidence_id": _eid(pack, "E39"), "note": "shape context", "role": "context"},
        ],
        "recommended_next_actions": ["collect_stronger_target_evidence"],
    }
    scorecard = build_candidate_scorecard(
        reasoning_pack=pack,
        master_output=master_output,
        prev_scorecard=[],
        new_evidence_ids=["E71", "E35", "E39"],
    )
    primary = next(row for row in scorecard if row["label"] == "neutral aromatic")
    assert primary["support_axes"] == ["target_observation"]
    assert primary["new_support_evidence_ids"] == ["E71"]

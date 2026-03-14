from src.reasoning.master_reasoner import build_master_prompt_bundle, build_reasoning_pack


def _case_fixture() -> dict:
    return {
        "query": {"input_smiles": "Oc1ccccc1N", "canonical_smiles": "Oc1ccccc1N", "inchikey": "IK-GEN", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "web_search", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "structure_prior_without_target_atb",
        },
        "neighbors": [{"rank": 1, "sim": 0.82, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.82,
            "mean_topk_sim": 0.77,
            "novelty_struct": 0.12,
            "mechanism_entropy": 0.35,
            "structure_prior_profile": {
                "version": "structure_prior_v1",
                "donor_acceptor_topology": "mixed",
                "intramolecular_hbond_candidates": "possible",
                "aromatic_core_density": "high",
                "flexibility_proxy": "low",
                "conjugation_proxy": "mid",
                "overall_structure_prior": "structure prior is mixed and should be combined with target-state evidence.",
                "reliability": "high",
                "notes": ["Generic structure prior only."],
            },
        },
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 0.5, "delta_gap": -0.2, "delta_volume": 0.3, "delta_dipole": 0.2}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_master_prompt_uses_generic_axes_wording():
    pack = build_reasoning_pack(_case_fixture(), {"run_lane": "atb_cache_only"})
    bundle = build_master_prompt_bundle(pack, {"run_lane": "atb_cache_only", "master_output_mode": "tagged_repair"})
    instructions = str(bundle.get("instructions") or "")
    assert "electronic redistribution" in instructions
    assert "structural relaxation" in instructions
    assert "shape-rigidity" in instructions
    assert "structure prior" in instructions
    assert "CT-family weak context" not in instructions
    assert "neutral-aromatic-like stability" not in instructions
    assert "supports ICT" not in instructions
    assert "supports TICT" not in instructions
    assert "delta_gap ... mechanism family" not in instructions
    assert "legacy schema key: ct_family" in instructions


def test_master_prompt_uses_round_available_evidence_ids_instead_of_fixed_r1_ids():
    case = _case_fixture()
    case["target_fields"] = {
        "emission_aggr_nm": 520.0,
        "emission_solid_or_film_nm": 560.0,
    }
    case["target_fields_provenance"] = {
        "emission_aggr_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=GEN",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "aggregation",
            "condition_bucket": "aggregation",
        },
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=GEN",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }
    pack = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}},
    )
    bundle = build_master_prompt_bundle(pack, {"run_lane": "atb_cache_only", "master_output_mode": "tagged_repair"})
    instructions = str(bundle.get("instructions") or "")
    assert "Use E35/E36 to express electronic-redistribution gain or loss of support when available." not in instructions
    assert "Target aTB enrichment IDs available this round:" in instructions
    assert "Target observation IDs available this round: E70, E71, E72, E73." in instructions
    assert "Use target observation IDs first when present" in instructions
    assert "If E70..E73 are present, cite at least one of them" in instructions
    assert "Cite only IDs that actually appear in evidence_registry for this round." in instructions

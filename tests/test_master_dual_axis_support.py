from src.reasoning.master_reasoner import build_reasoning_pack, validate_master_output


def _case() -> dict:
    return {
        "query": {"input_smiles": "CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1", "canonical_smiles": "CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1", "inchikey": "IK-DUAL", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "demo",
        },
        "neighbors": [{"rank": 1, "sim": 0.81, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.81,
            "mean_topk_sim": 0.77,
            "novelty_struct": 0.15,
            "mechanism_entropy": 0.28,
            "structure_prior_profile": {
                "version": "structure_prior_v1",
                "donor_acceptor_topology": "strong",
                "intramolecular_hbond_candidates": "possible",
                "aromatic_core_density": "mid",
                "flexibility_proxy": "mid",
                "conjugation_proxy": "high",
                "overall_structure_prior": "Conjugation is high; aromatic-core density is mid; flexibility is mid; donor/acceptor topology is strong; intramolecular H-bond candidates are possible; reliability is high.",
                "reliability": "high",
                "notes": ["Donor/acceptor topology is strong under the current topology heuristic."],
            },
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_gap": -0.42,
                    "delta_dihedral": 11.0,
                    "delta_volume": 0.9,
                    "delta_dipole": 0.35,
                    "delta_bonds": 0.03,
                    "delta_angles": 0.4,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_dual_axis_support_avoids_single_axis_penalty():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Two independent evidence axes support one interpretation.",
                "atb_support_level": "weak",
            },
            "confidence": 0.58,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Structural access is present.", "evidence_used": [{"evidence_id": "E31", "note": "self trend torsion", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution is present.", "evidence_used": [{"evidence_id": "E36", "note": "redistribution summary", "role": "support"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Relaxation and structural cues remain relevant.", "evidence_used": [{"evidence_id": "E37", "note": "relaxation summary", "role": "support"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Compare orthogonal discriminators.", "evidence_used": [{"evidence_id": "E40", "note": "structure prior context", "role": "context"}]},
        ],
        "competing_hypotheses": [{"name": "other", "confidence": 0.12, "atb_support_level": "none", "evidence_used": [{"evidence_id": "E40", "note": "background prior", "role": "context"}]}],
        "predictions": [{"prediction": "collect orthogonal discriminators", "expected_signal": "separate hypotheses", "evidence_used": [{"evidence_id": "E37", "note": "relaxation context", "role": "context"}]}],
        "limits": [],
        "evidence_used": [{"evidence_id": "E36", "note": "redistribution axis", "role": "support"}, {"evidence_id": "E37", "note": "relaxation axis", "role": "support"}],
        "recommended_next_actions": ["provide_offline_pdf"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(out, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert normalized["status"] == "ok"
    assert normalized["template_used"] == "stable"
    assert float(normalized["mechanism_claim"]["confidence"]) > 0.38
    assert not any(isinstance(row, dict) and row.get("code") == "single_axis_support_only" for row in errors)

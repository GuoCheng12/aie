from src.reasoning.master_reasoner import build_reasoning_pack, validate_master_output


def _case() -> dict:
    return {
        "query": {"input_smiles": "Oc1ccccc1N", "canonical_smiles": "Oc1ccccc1N", "inchikey": "IK-SINGLE", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "structure_prior_without_target_atb",
        },
        "neighbors": [{"rank": 1, "sim": 0.72, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.72,
            "mean_topk_sim": 0.68,
            "novelty_struct": 0.12,
            "mechanism_entropy": 0.33,
            "structure_prior_profile": {
                "version": "structure_prior_v1",
                "donor_acceptor_topology": "mixed",
                "intramolecular_hbond_candidates": "possible",
                "aromatic_core_density": "high",
                "flexibility_proxy": "low",
                "conjugation_proxy": "mid",
                "overall_structure_prior": "Conjugation is mid; aromatic-core density is high; flexibility is low; donor/acceptor topology is mixed; intramolecular H-bond candidates are possible; reliability is high.",
                "reliability": "high",
                "notes": ["Donor/acceptor topology is mixed under the current topology heuristic."],
            },
        },
        "evidence_readiness": {
            "atb": {"cache_status": "failed", "features_summary": {}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_single_axis_support_forces_insufficient_evidence():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Structure prior alone points to one explanation.",
                "atb_support_level": "none",
            },
            "confidence": 0.62,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Prior structural access remains uncertain.", "evidence_used": [{"evidence_id": "E40", "note": "prior topology context", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic interpretation remains tentative.", "evidence_used": [{"evidence_id": "E40", "note": "same prior axis", "role": "support"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation bridge is not resolved.", "evidence_used": [{"evidence_id": "E40", "note": "same prior axis", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Compare orthogonal discriminators.", "evidence_used": [{"evidence_id": "E40", "note": "same prior axis", "role": "context"}]},
        ],
        "competing_hypotheses": [],
        "predictions": [{"prediction": "collect more target-state evidence", "expected_signal": "orthogonal separation", "evidence_used": [{"evidence_id": "E40", "note": "prior-only", "role": "context"}]}],
        "limits": [],
        "evidence_used": [{"evidence_id": "E40", "note": "prior-only support", "role": "support"}],
        "recommended_next_actions": ["provide_offline_pdf"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(out, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert normalized["status"] == "insufficient_evidence"
    assert normalized["template_used"] == "mixture"
    assert float(normalized["mechanism_claim"]["confidence"]) <= 0.38
    assert any(isinstance(row, dict) and row.get("code") == "single_axis_support_only" for row in errors)

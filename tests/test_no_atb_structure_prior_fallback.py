from src.agents.ready_agent import review_case_and_patch, apply_ready_agent_patch
from src.reasoning.master_reasoner import build_reasoning_pack, validate_master_output


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-NO-ATB",
        "runtime": {"run_lane": "atb_cache_only"},
        "query": {"input_smiles": "Oc1ccccc1N", "canonical_smiles": "Oc1ccccc1N", "inchikey": "IK-NOATB"},
        "inputs": {"offline_pdfs": []},
        "neighbors": [{"rank": 1, "sim": 0.74, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.74,
            "mean_topk_sim": 0.70,
            "novelty_struct": 0.18,
            "mechanism_entropy": 0.42,
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
            "atb": {"cache_status": "failed", "features_summary": {}, "missing_fields": []},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
        "evidence_candidates_staging": [],
        "current_gate": {"state": "needs_manual", "ready_for_reasoning": False, "reasoning_mode": "blocked", "reason": "seed"},
        "action_plan": [],
    }


def test_ready_agent_allows_structure_prior_fallback_without_target_atb():
    case = _case_fixture()
    patch = review_case_and_patch(case)
    updated = apply_ready_agent_patch(case, patch)
    gate = updated["current_gate"]
    assert gate["state"] == "ready_conservative"
    assert gate["ready_for_reasoning"] is True
    assert gate["reasoning_mode"] == "conservative"
    assert "structure_prior_without_target_atb" in gate["reason"]


def test_no_atb_structure_prior_fallback_stays_low_confidence():
    case = _case_fixture()
    ready_patch = review_case_and_patch(case)
    ready_case = apply_ready_agent_patch(case, ready_patch)
    pack = build_reasoning_pack(ready_case, {"run_lane": "atb_cache_only"})
    out = {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Structure prior alone suggests one explanation.",
                "atb_support_level": "none",
            },
            "confidence": 0.61,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Target-only aTB is unavailable.", "evidence_used": [{"evidence_id": "E40", "note": "structure prior", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic interpretation remains provisional.", "evidence_used": [{"evidence_id": "E40", "note": "structure prior", "role": "support"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation bridge is unresolved.", "evidence_used": [{"evidence_id": "E41", "note": "H-bond context", "role": "context"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Compare orthogonal discriminators.", "evidence_used": [{"evidence_id": "E42", "note": "aromatic context", "role": "context"}]},
        ],
        "competing_hypotheses": [],
        "predictions": [{"prediction": "provide target-state evidence", "expected_signal": "mechanism separation", "evidence_used": [{"evidence_id": "E44", "note": "overall structure prior", "role": "context"}]}],
        "limits": [],
        "evidence_used": [{"evidence_id": "E40", "note": "structure prior", "role": "support"}],
        "recommended_next_actions": ["provide_offline_pdf"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        ready_case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert normalized["status"] == "insufficient_evidence"
    assert normalized["template_used"] == "mixture"
    assert float(normalized["mechanism_claim"]["confidence"]) <= 0.38
    assert any(isinstance(row, dict) and row.get("code") == "single_axis_support_only" for row in errors)

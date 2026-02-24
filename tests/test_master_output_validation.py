from src.reasoning.master_reasoner import (
    build_reasoning_pack,
    validate_master_output,
)


def _case(conservative: bool = True, with_emission: bool = False):
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-1", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative" if conservative else "ready_for_reasoning",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative" if conservative else "normal",
            "reason": "demo",
        },
        "neighbors": [{"rank": 1, "sim": 0.9, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.9,
            "mean_topk_sim": 0.85,
            "novelty_struct": 0.1,
            "mechanism_entropy": 0.3,
            "mechanism_hint": "ICT",
            "hint_confidence": 0.7,
            "atb_neighbor_consistency": {"flag": "inlier", "reliability": "medium"},
        },
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_gap": 0.1}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": None},
        },
        "target_fields": {
            "emission_aggr_nm": 520.0 if with_emission else None,
            "emission_solid_or_film_nm": None,
        },
        "target_fields_provenance": {},
    }


def _valid_output():
    ev = [{"case_path": "/risk_scores/top1_sim", "note": "top similarity", "role": "support"}]
    return {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "ICT dominated",
            },
            "confidence": 0.6,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [{"claim": "similar neighborhood", "evidence_used": ev}],
        "competing_hypotheses": [{"name": "TICT", "confidence": 0.3, "evidence_used": ev}],
        "predictions": [{"prediction": "blue shift", "expected_signal": "nm decreases", "evidence_used": ev}],
        "limits": ["conservative estimate; no emission evidence yet"],
        "evidence_used": ev,
        "recommended_next_actions": ["request_manual_pdf"],
    }


def test_validate_master_output_passes_for_valid_payload():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    ok, errors, normalized, used = validate_master_output(
        _valid_output(),
        pack,
        case,
        {"conservative_confidence_cap": 0.65},
    )
    assert ok is True
    assert errors == []
    assert normalized["status"] == "ok"
    assert used == ["/risk_scores/top1_sim"]


def test_validate_master_output_fails_on_invalid_evidence_path():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output()
    out["evidence_used"] = [{"case_path": "/not/allowed/path", "note": "bad", "role": "support"}]
    ok, errors, _, _ = validate_master_output(out, pack, case, {"conservative_confidence_cap": 0.65})
    assert ok is False
    assert any("evidence_path_not_allowed" in e for e in errors)


def test_validate_master_output_enforces_conservative_cap_and_limits():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output()
    out["mechanism_claim"]["confidence"] = 0.9
    out["limits"] = ["insufficient data"]
    ok, errors, _, _ = validate_master_output(out, pack, case, {"conservative_confidence_cap": 0.65})
    assert ok is False
    assert any("conservative_confidence_cap_exceeded" in e for e in errors)
    assert "missing_conservative_limit_statement" in errors
    assert "missing_no_emission_evidence_limit" in errors

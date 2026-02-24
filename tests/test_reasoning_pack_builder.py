from src.reasoning.master_reasoner import build_reasoning_pack


def _case_fixture():
    return {
        "case_id": "CASE-1",
        "query": {
            "input_smiles": "C",
            "canonical_smiles": "C",
            "inchikey": "IK-1",
            "aliases": ["demo"],
            "code": "DEMO",
            "reference": "ref",
        },
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": [
            {"rank": 1, "sim": 0.91, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"},
            {"rank": 2, "sim": 0.84, "neighbor_inchikey": "N2", "neighbor_mechanism_label": "TICT"},
        ],
        "risk_scores": {
            "top1_sim": 0.91,
            "mean_topk_sim": 0.88,
            "novelty_struct": 0.09,
            "mechanism_entropy": 0.48,
            "mechanism_hint": "ICT",
            "hint_confidence": 0.8,
            "atb_neighbor_consistency": {"flag": "inlier", "reliability": "medium", "sample_size": 8},
        },
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_gap": 0.1}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": None},
        },
        "target_fields": {"emission_aggr_nm": None, "emission_solid_or_film_nm": None},
        "target_fields_provenance": {},
        "candidate_mechanisms": [{"mechanism_id": "ICT", "probability": 0.8}],
        "mechanism_signatures": {"ICT": "signature text"},
    }


def test_build_reasoning_pack_contains_minimum_sections_and_paths():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})

    assert pack["pack_version"] == "master_pack_v1"
    assert "query" in pack
    assert "risk_scores" in pack
    assert "evidence_readiness" in pack
    assert "allowed_evidence_paths" in pack
    assert "path_map" in pack
    assert "/risk_scores/top1_sim" in pack["allowed_evidence_paths"]
    assert "/evidence_readiness/atb/cache_status" in pack["allowed_evidence_paths"]
    assert "/neighbors/0/sim" in pack["allowed_evidence_paths"]
    assert pack["path_map"]["/mechanism_context/mechanism_hint"] == "/risk_scores/mechanism_hint"


def test_build_reasoning_pack_is_deterministic_for_same_case():
    case = _case_fixture()
    pack1 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    pack2 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    assert pack1 == pack2

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
            "atb_neighbor_features_all": [
                {
                    "neighbor_inchikey": "N1",
                    "rank": 1,
                    "sim": 0.91,
                    "delta_gap": 0.1,
                    "delta_dihedral": -1.1,
                    "delta_volume": 0.5,
                    "features": {"delta_gap": 0.1, "delta_dihedral": -1.1, "delta_volume": 0.5, "excitation_energy": 2.2},
                }
            ],
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_gap": 0.1,
                    "delta_dihedral": -1.1,
                    "delta_volume": 0.5,
                    "excitation_energy": 2.2,
                    "delta_dipole": 0.35,
                    "delta_bonds": 0.03,
                    "delta_angles": 0.4,
                    "exciting_path_mean_volume": 3.4,
                    "s0_rays_asymmetry_parameter": 0.10,
                    "s1_rays_asymmetry_parameter": 0.13,
                    "s0_rotational_constant_a": 1.0,
                    "s1_rotational_constant_a": 0.97,
                    "s0_rotational_constant_b": 0.5,
                    "s1_rotational_constant_b": 0.48,
                    "s0_rotational_constant_c": 0.25,
                    "s1_rotational_constant_c": 0.24,
                },
                "features": {"delta_gap": 0.1, "delta_dihedral": -1.1, "delta_volume": 0.5, "excitation_energy": 2.2},
            },
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
    assert "evidence_registry" in pack
    assert "allowed_evidence_paths" not in pack
    assert "path_map" not in pack
    assert "mechanism_hint" not in (pack.get("risk_scores") or {})
    assert "hint_confidence" not in (pack.get("risk_scores") or {})
    assert all(
        (x.get("case_path") not in {"/risk_scores/mechanism_hint", "/risk_scores/hint_confidence"})
        for x in (pack.get("evidence_registry") or [])
        if isinstance(x, dict)
    )
    registry = pack["evidence_registry"]
    assert isinstance(registry, list)
    assert all(isinstance(x, dict) for x in registry)
    assert any(v.get("case_path") == "/risk_scores/top1_sim" for v in registry)
    assert any(v.get("case_path") == "/evidence_readiness/atb/features_summary/delta_dihedral" for v in registry)
    assert all(str(v.get("evidence_id") or "").startswith("E") for v in registry)
    assert "features" in pack["evidence_readiness"]["atb"]
    assert pack["evidence_readiness"]["atb"]["features"] is None
    assert isinstance(pack["evidence_readiness"]["atb"]["features_summary"], dict)
    stats = pack.get("neighbor_atb_stats") or {}
    assert "fields" in stats
    assert "abs_delta_dihedral" in (stats.get("fields") or {})
    assert "delta_dihedral" in (stats.get("fields") or {})
    assert "target_percentile" in (stats.get("fields") or {}).get("abs_delta_dihedral", {})
    assert "z_robust" in (stats.get("fields") or {}).get("abs_delta_dihedral", {})
    assert "summary" in stats
    assert "reliability" in stats
    risk = pack.get("risk_scores") or {}
    assert (risk.get("atb_ct_proxy_profile") or {}).get("version") == "atb_ct_proxy_v1"
    assert (risk.get("atb_structural_relaxation_profile") or {}).get("version") == "atb_structural_relaxation_v1"
    assert (risk.get("atb_shape_rigidity_profile") or {}).get("version") == "atb_shape_rigidity_v1"
    ids = {str(x.get("evidence_id")) for x in registry if isinstance(x, dict)}
    assert "E35" in ids
    assert "E36" in ids
    assert "E37" in ids
    assert "E38" in ids
    assert "E39" in ids


def test_build_reasoning_pack_is_deterministic_for_same_case():
    case = _case_fixture()
    pack1 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    pack2 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    assert pack1 == pack2


def test_reasoning_pack_registry_size_cap():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    assert len(pack.get("evidence_registry") or []) <= 20


def test_reasoning_pack_r1_contains_atb_trend_profile_when_target_atb_available():
    case = _case_fixture()
    case["evidence_readiness"]["atb"]["cache_status"] = "success"
    case["evidence_readiness"]["atb"]["features_summary"] = {
        "delta_dihedral": 0.7,
        "delta_gap": -0.4,
        "delta_volume": 1.2,
        "excitation_energy": 2.0,
    }
    pack = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}},
    )
    trend = ((pack.get("risk_scores") or {}).get("atb_trend_profile")) or {}
    assert trend.get("version") == "atb_trend_v1"
    assert trend.get("overall_motion_proxy") in {"low", "medium", "high"}

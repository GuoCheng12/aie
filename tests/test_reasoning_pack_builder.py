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
                    "s0_perm_dipole_tot_debye": 2.5,
                    "s1_perm_dipole_tot_debye": 2.8,
                    "delta_perm_dipole_tot_debye": 0.3,
                    "s1_transition_electric_dip_au": 1.4,
                    "s1_transition_magnetic_dip_norm_au": 0.6,
                    "s1_rotatory_strength_cgs": -12.5,
                    "s1_oscillator_strength_f": 0.17,
                    "s1_excitation_wavelength_nm": 550.0,
                    "aop_compact_reliability_score": 2.0,
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
    assert (risk.get("structure_prior_profile") or {}).get("version") == "structure_prior_v1"
    assert (risk.get("charge_redistribution_profile") or {}).get("version") == "charge_redistribution_v2"
    assert (risk.get("atb_ct_proxy_profile") or {}).get("version") == "atb_ct_proxy_v1"
    assert (risk.get("atb_structural_relaxation_profile") or {}).get("version") == "atb_structural_relaxation_v1"
    assert (risk.get("atb_shape_rigidity_profile") or {}).get("version") == "atb_shape_rigidity_v1"
    ids = {str(x.get("evidence_id")) for x in registry if isinstance(x, dict)}
    assert "E35" in ids
    assert "E36" in ids
    assert "E37" in ids
    assert "E38" in ids
    assert "E39" in ids
    assert "E40" in ids
    assert "E41" in ids
    assert "E42" in ids
    assert ("E43" in ids) or ("E44" in ids)
    assert "E60" in ids
    assert "E61" in ids
    assert "E62" in ids
    assert "E63" in ids
    e35 = next(x for x in registry if isinstance(x, dict) and x.get("evidence_id") == "E35")
    e36 = next(x for x in registry if isinstance(x, dict) and x.get("evidence_id") == "E36")
    assert e35.get("source_type") == "derived_pack"
    assert e36.get("source_type") == "derived_pack"
    assert "charge_variation" not in str(e35.get("value_preview"))
    assert "element" not in str(e35.get("value_preview"))
    assert "charge_variation" not in str(e36.get("value_preview"))
    assert "element" not in str(e36.get("value_preview"))


def test_build_reasoning_pack_is_deterministic_for_same_case():
    case = _case_fixture()
    pack1 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    pack2 = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    assert pack1 == pack2


def test_reasoning_pack_registry_size_cap():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    assert len(pack.get("evidence_registry") or []) <= 24


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


def test_reasoning_pack_r0_excludes_and_r1_includes_emission_observation_profile():
    case = _case_fixture()
    case["target_fields"] = {
        "emission_aggr_nm": 520.0,
        "emission_solid_or_film_nm": 565.0,
    }
    case["target_fields_provenance"] = {
        "emission_aggr_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=DEMO",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "aggregation",
            "condition_bucket": "aggregation",
        },
        "emission_solid_or_film_nm": {
            "source_type": "dataset_row",
            "source_ref": "/tmp/level1.csv",
            "source_locator": "row_index=0; code=DEMO",
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        },
    }

    pack_r0 = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R0"}},
    )
    assert "emission_observation_profile" not in (pack_r0.get("risk_scores") or {})
    ids_r0 = {str(x.get("evidence_id")) for x in (pack_r0.get("evidence_registry") or []) if isinstance(x, dict)}
    assert ids_r0.isdisjoint({"E70", "E71", "E72", "E73"})

    pack_r1 = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}},
    )
    emission_profile = ((pack_r1.get("risk_scores") or {}).get("emission_observation_profile")) or {}
    assert emission_profile.get("version") == "emission_observation_v1"
    assert emission_profile.get("coverage") == "both"
    ids_r1 = {str(x.get("evidence_id")) for x in (pack_r1.get("evidence_registry") or []) if isinstance(x, dict)}
    assert {"E70", "E71", "E72", "E73"}.issubset(ids_r1)


def test_reasoning_pack_sanitizes_raw_delta_dipole_arrays():
    case = _case_fixture()
    case["evidence_readiness"]["atb"]["features_summary"]["delta_dipole"] = {
        "element": ["C", "N"],
        "charge_variation": [0.01, -0.02],
    }
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_total_abs"] = 0.03
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_max_abs_atom"] = 0.02
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_top3_abs_share"] = 1.0
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_heteroatom_abs_share"] = 0.6667
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_n_atoms_ge_0p01"] = 2.0
    case["evidence_readiness"]["atb"]["features_summary"]["charge_redis_n_atoms_ge_0p02"] = 1.0

    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    fs = (((pack.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    assert "charge_variation" not in str(fs)
    assert "element" not in str(fs)
    assert not isinstance(fs.get("delta_dipole"), dict)


def test_aop_compact_evidence_ids_follow_target_atb_profile_gate():
    case = _case_fixture()

    pack_r0 = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R0"}},
    )
    ids_r0 = {str(x.get("evidence_id")) for x in (pack_r0.get("evidence_registry") or []) if isinstance(x, dict)}
    assert ids_r0.isdisjoint({"E60", "E61", "E62", "E63"})

    pack_r1 = build_reasoning_pack(
        case,
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}},
    )
    ids_r1 = {str(x.get("evidence_id")) for x in (pack_r1.get("evidence_registry") or []) if isinstance(x, dict)}
    assert bool({"E60", "E61", "E62", "E63"} & ids_r1)

from src.reasoning.master_reasoner import build_reasoning_pack


def _case_fixture() -> dict:
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-R1", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "web_search", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": [],
        "risk_scores": {
            "top1_sim": 0.7,
            "mean_topk_sim": 0.65,
            "novelty_struct": 0.2,
            "mechanism_entropy": 0.1,
            "atb_neighbor_consistency": {"flag": "insufficient_neighbors", "reliability": "low"},
            "atb_neighbor_features_all": [],
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": 11.2,
                    "delta_gap": -0.34,
                    "delta_volume": 0.42,
                    "excitation_energy": 1.83,
                    "delta_dipole": 0.51,
                    "delta_bonds": 0.04,
                    "delta_angles": 0.35,
                    "exciting_path_mean_volume": 2.7,
                    "s0_rays_asymmetry_parameter": 0.10,
                    "s1_rays_asymmetry_parameter": 0.12,
                    "s0_rotational_constant_a": 1.0,
                    "s1_rotational_constant_a": 0.98,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_reasoning_pack_r1_contains_atb_trends_self_and_registry_ids():
    pack = build_reasoning_pack(
        _case_fixture(),
        {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R1"}},
    )

    trends = ((pack.get("risk_scores") or {}).get("atb_trends_self")) or {}
    assert trends.get("enabled") is True
    assert trends.get("delta_dihedral_bucket") in {"none", "weak", "strong"}
    assert trends.get("delta_gap_direction") in {"decrease", "flat", "increase", "unknown"}
    assert trends.get("delta_volume_direction") in {"decrease", "flat", "increase", "unknown"}
    assert 0.0 <= float(trends.get("delta_dihedral_percentile_global")) <= 1.0
    assert 0.0 <= float(trends.get("delta_gap_percentile_global")) <= 1.0
    assert 0.0 <= float(trends.get("delta_volume_percentile_global")) <= 1.0

    ids = {str(x.get("evidence_id")) for x in (pack.get("evidence_registry") or []) if isinstance(x, dict)}
    assert "E_ATB_TREND_1" in ids
    assert "E_ATB_TREND_2" in ids
    assert "E_ATB_TREND_3" in ids
    assert "E_ATB_TREND_4" in ids
    assert "E31" in ids
    assert "E32" in ids
    assert "E33" in ids
    assert "E34" in ids
    assert "E35" in ids
    assert "E36" in ids
    assert "E37" in ids
    assert "E38" in ids
    assert "E39" in ids

    trend_profile = ((pack.get("risk_scores") or {}).get("atb_trend_profile")) or {}
    assert trend_profile.get("version") == "atb_trend_v1"
    assert ((pack.get("risk_scores") or {}).get("atb_ct_proxy_profile") or {}).get("version") == "atb_ct_proxy_v1"
    assert ((pack.get("risk_scores") or {}).get("atb_structural_relaxation_profile") or {}).get("version") == "atb_structural_relaxation_v1"
    assert ((pack.get("risk_scores") or {}).get("atb_shape_rigidity_profile") or {}).get("version") == "atb_shape_rigidity_v1"

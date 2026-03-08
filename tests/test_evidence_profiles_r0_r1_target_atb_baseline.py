from src.reasoning.evidence_profiles import default_evidence_profiles, resolve_evidence_profiles
from src.reasoning.master_reasoner import build_reasoning_pack


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-PROFILE",
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-PROFILE", "created_at": "2026-02-28T00:00:00Z"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {"state": "ready_conservative", "ready_for_reasoning": True, "reasoning_mode": "conservative", "reason": "ok"},
        "neighbors": [
            {"rank": 1, "sim": 0.82, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"},
            {"rank": 2, "sim": 0.77, "neighbor_inchikey": "N2", "neighbor_mechanism_label": "ESIPT"},
        ],
        "risk_scores": {},
        "evidence_readiness": {
            "atb": {"cache_status": "success", "features_summary": {"delta_dihedral": 9.0}},
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_profiles_r0_prior_then_r1_target_constraint():
    defaults = default_evidence_profiles()
    assert defaults["profiles"]["R0"]["include_target_atb_summary"] is False
    assert defaults["profiles"]["R0"]["include_neighbor_summary"] is True
    assert defaults["profiles"]["R1"]["include_target_atb_summary"] is True
    assert defaults["profiles"]["R1"]["include_neighbor_summary"] is True

    active_r0, cfg_r0, _ = resolve_evidence_profiles({"evidence_profiles": {"active_profile": "R0"}})
    active_r1, cfg_r1, _ = resolve_evidence_profiles({"evidence_profiles": {"active_profile": "R1"}})
    assert active_r0 == "R0"
    assert active_r1 == "R1"
    assert cfg_r0["include_target_atb_summary"] is False
    assert cfg_r1["include_target_atb_summary"] is True


def test_reasoning_pack_r0_r1_order_matches_information_contract():
    case = _case_fixture()
    cfg_base = default_evidence_profiles()

    pack_r0 = build_reasoning_pack(
        case,
        {
            "evidence_profiles": {
                "active_profile": "R0",
                "profiles": cfg_base["profiles"],
            }
        },
    )
    pack_r1 = build_reasoning_pack(
        case,
        {
            "evidence_profiles": {
                "active_profile": "R1",
                "profiles": cfg_base["profiles"],
            }
        },
    )

    atb_r0 = ((pack_r0.get("evidence_readiness") or {}).get("atb") or {})
    atb_r1 = ((pack_r1.get("evidence_readiness") or {}).get("atb") or {})
    assert atb_r0.get("features_summary") is None
    assert isinstance(atb_r1.get("features_summary"), dict)
    assert len(pack_r0.get("neighbors_topk") or []) > 0
    assert len(pack_r1.get("neighbors_topk") or []) > 0

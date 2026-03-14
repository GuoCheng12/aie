from src.reasoning import master_reasoner
from src.reasoning.master_reasoner import build_reasoning_pack


def _case_fixture() -> dict:
    neighbors = []
    atb_rows = []
    for i in range(6):
        inchikey = f"N{i}"
        label = "TICT" if i % 2 == 0 else "ICT"
        neighbors.append(
            {
                "rank": i + 1,
                "sim": 0.6 - i * 0.03,
                "neighbor_inchikey": inchikey,
                "neighbor_mechanism_label": label,
            }
        )
        atb_rows.append(
            {
                "neighbor_inchikey": inchikey,
                "rank": i + 1,
                "sim": 0.6 - i * 0.03,
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": float((-1) ** i * (i + 2)),
                    "delta_gap": -0.4 + 0.05 * i,
                    "delta_volume": 0.2 + 0.03 * i,
                },
            }
        )

    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-R2", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "web_search", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": neighbors,
        "risk_scores": {
            "top1_sim": 0.6,
            "mean_topk_sim": 0.45,
            "novelty_struct": 0.4,
            "mechanism_entropy": 0.5,
            "atb_neighbor_consistency": {"flag": "insufficient_neighbors", "reliability": "low"},
            "atb_neighbor_features_all": atb_rows,
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": -9.7,
                    "delta_gap": -0.3,
                    "delta_volume": 0.15,
                    "excitation_energy": 1.96,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_master_pack_profile_r2_includes_neighbor_stats_and_evidence_ids():
    case = _case_fixture()
    pack = build_reasoning_pack(
        case,
        {
            "run_lane": "atb_cache_only",
            "evidence_profiles": {"active_profile": "R2"},
        },
    )
    stats = ((pack.get("risk_scores") or {}).get("neighbor_atb_stats")) or {}
    assert stats.get("sample_size") >= 5
    assert "fields" in stats and "abs_delta_dihedral" in stats["fields"]

    registry_ids = {str(x.get("evidence_id")) for x in (pack.get("evidence_registry") or []) if isinstance(x, dict)}
    assert "E21" in registry_ids
    assert "E22" in registry_ids
    assert "E24" in registry_ids
    # by_label is data-dependent but this fixture should satisfy the emission condition.
    assert "E23" in registry_ids


def test_master_pack_profile_r2_keeps_comparative_ids_after_pack_shrink(monkeypatch):
    case = _case_fixture()
    monkeypatch.setattr(master_reasoner, "MAX_PACK_BYTES", 1)
    pack = build_reasoning_pack(
        case,
        {
            "run_lane": "atb_cache_only",
            "evidence_profiles": {"active_profile": "R2"},
        },
    )
    registry_ids = [str(x.get("evidence_id")) for x in (pack.get("evidence_registry") or []) if isinstance(x, dict)]
    assert len(registry_ids) <= 20
    for eid in ("E21", "E22", "E23", "E24"):
        assert eid in registry_ids

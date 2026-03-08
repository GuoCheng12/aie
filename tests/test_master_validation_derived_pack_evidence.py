from copy import deepcopy

from src.reasoning.master_reasoner import build_reasoning_pack, validate_master_output


def _case_fixture() -> dict:
    neighbors = []
    atb_rows = []
    for i in range(6):
        inchikey = f"N{i}"
        neighbors.append(
            {
                "rank": i + 1,
                "sim": 0.62 - i * 0.02,
                "neighbor_inchikey": inchikey,
                "neighbor_mechanism_label": "TICT" if i % 2 == 0 else "ICT",
            }
        )
        atb_rows.append(
            {
                "neighbor_inchikey": inchikey,
                "rank": i + 1,
                "sim": 0.62 - i * 0.02,
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": float((-1) ** i * (i + 1)),
                    "delta_gap": -0.4 + 0.06 * i,
                    "delta_volume": 0.1 + 0.04 * i,
                },
            }
        )
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-DERIVED", "aliases": []},
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
            "top1_sim": 0.62,
            "mean_topk_sim": 0.5,
            "novelty_struct": 0.38,
            "mechanism_entropy": 0.55,
            "atb_neighbor_consistency": {"flag": "insufficient_neighbors", "reliability": "low"},
            "atb_neighbor_features_all": atb_rows,
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_dihedral": -9.0,
                    "delta_gap": -0.28,
                    "delta_volume": 0.16,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _eid(pack: dict, *, case_path: str | None = None, evidence_id: str | None = None) -> str:
    for row in pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        if case_path is not None and row.get("case_path") == case_path:
            return str(row.get("evidence_id"))
        if evidence_id is not None and row.get("evidence_id") == evidence_id:
            return str(row.get("evidence_id"))
    raise AssertionError(f"missing evidence id: case_path={case_path} evidence_id={evidence_id}")


def _ev(evidence_id: str, note: str, role: str) -> dict:
    return {"evidence_id": evidence_id, "note": note, "role": role}


def _output(pack: dict) -> dict:
    e11 = _eid(pack, case_path="/evidence_readiness/atb/features_summary/delta_dihedral")
    e12 = _eid(pack, case_path="/evidence_readiness/atb/features_summary/delta_gap")
    e13 = _eid(pack, case_path="/evidence_readiness/atb/features_summary/delta_volume")
    e21 = _eid(pack, evidence_id="E21")
    e22 = _eid(pack, evidence_id="E22")
    return {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "unknown",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Derived evidence is used as comparative context.",
                "atb_support_level": "weak",
            },
            "confidence": 0.42,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "aTB structural access in excited state.", "evidence_used": [_ev(e11, "target torsion", "support")]},
            {"step_id": "B", "step_name": "ct_family", "claim": "nonradiative CT-family channel plausible.", "evidence_used": [_ev(e12, "delta gap context", "context")]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "aggregation rigidification suppresses motion.", "evidence_used": [_ev(e13, "delta volume context", "context")]},
            {"step_id": "D", "step_name": "discriminators", "claim": "compare and measure discriminators with neighbor statistics.", "evidence_used": [_ev(e21, "R2 comparative torsional evidence", "context")]},
        ],
        "competing_hypotheses": [
            {"name": "alt", "confidence": 0.2, "atb_support_level": "weak", "evidence_used": [_ev(e22, "neighbor delta_gap comparison", "context")]}
        ],
        "predictions": [
            {"prediction": "test 1", "expected_signal": "signal 1", "evidence_used": [_ev(e21, "compare", "context")]},
            {"prediction": "test 2", "expected_signal": "signal 2", "evidence_used": [_ev(e12, "ct", "context")]},
            {"prediction": "test 3", "expected_signal": "signal 3", "evidence_used": [_ev(e13, "packing", "context")]},
        ],
        "limits": ["Conservative mode: mechanism assignment is tentative and should be interpreted with uncertainty."],
        "evidence_used": [_ev(e21, "derived comparative context", "context"), _ev(e22, "derived gap context", "context")],
        "recommended_next_actions": ["provide_offline_pdf"],
    }


def _has_code(errors: list, code: str) -> bool:
    for row in errors:
        if isinstance(row, dict) and row.get("code") == code:
            return True
    return False


def test_derived_pack_evidence_passes_validation():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R2"}})
    out = _output(pack)
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3", "conservative_confidence_cap": 0.65},
    )
    assert ok is True
    assert errors == []


def test_derived_pack_missing_pack_path_rejected():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R2"}})
    for row in pack.get("evidence_registry") or []:
        if isinstance(row, dict) and row.get("evidence_id") == "E21":
            row.pop("pack_path", None)
    out = _output(pack)
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "derived_pack_path_missing")


def test_derived_pack_empty_value_rejected():
    case = _case_fixture()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R2"}})
    bad = deepcopy(pack)
    bad_stats = (((bad.get("risk_scores") or {}).get("neighbor_atb_stats") or {}).get("fields") or {})
    if isinstance(bad_stats, dict):
        bad_stats["abs_delta_dihedral"] = {}
    out = _output(bad)
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        bad,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "derived_pack_value_empty")

from src.reasoning.master_reasoner import (
    build_reasoning_pack,
    master_output_schema,
    validate_master_output,
)
from copy import deepcopy


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
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_gap": 0.1,
                    "delta_dihedral": 12.0,
                    "delta_volume": 0.5,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": None},
        },
        "target_fields": {
            "emission_aggr_nm": 520.0 if with_emission else None,
            "emission_solid_or_film_nm": None,
        },
        "target_fields_provenance": {},
    }


def _eid(pack: dict, case_path: str) -> str:
    for row in (pack.get("evidence_registry") or []):
        if isinstance(row, dict) and row.get("case_path") == case_path:
            evidence_id = row.get("evidence_id")
            if isinstance(evidence_id, str):
                return evidence_id
    raise AssertionError(f"missing evidence id for {case_path}")


def _ev(pack: dict, case_path: str, note: str, role: str) -> dict:
    return {"evidence_id": _eid(pack, case_path), "note": note, "role": role}


def _valid_output(pack: dict):
    ev_sim = [_ev(pack, "/current_gate/reasoning_mode", "reasoning mode context", "context")]
    ev_atb_a = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_dihedral", "torsional change", "support")]
    ev_atb_b = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_gap", "electronic redistribution cue", "support")]
    ev_atb_c = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_volume", "packing/rigidification proxy", "context")]
    ev_atb_d = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_dihedral", "test discriminator", "context")]
    return {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "ICT dominated",
                "atb_support_level": "weak",
            },
            "confidence": 0.6,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {
                "step_id": "A",
                "step_name": "torsion_access",
                "claim": "Excited-state geometry indicates torsional structural access.",
                "evidence_used": ev_atb_a,
            },
            {
                "step_id": "B",
                "step_name": "ct_family",
                "claim": "A torsion-driven nonradiative channel is plausible in CT-family states.",
                "evidence_used": ev_atb_b,
            },
            {
                "step_id": "C",
                "step_name": "aIE_bridge",
                "claim": "Aggregation-rigidification can suppress this nonradiative channel (RIM-like).",
                "evidence_used": ev_atb_c,
            },
            {
                "step_id": "D",
                "step_name": "discriminators",
                "claim": "Compare and measure discriminative tests across ICT/TICT/ESIPT channels.",
                "evidence_used": ev_atb_d,
            },
        ],
        "competing_hypotheses": [{"name": "TICT", "confidence": 0.3, "atb_support_level": "weak", "evidence_used": ev_sim}],
        "predictions": [
            {"prediction": "time-resolved PL test", "expected_signal": "lifetime change", "evidence_used": ev_atb_d},
            {"prediction": "solvent polarity compare", "expected_signal": "spectral shift pattern", "evidence_used": ev_atb_b},
            {"prediction": "temperature compare", "expected_signal": "nonradiative rate trend", "evidence_used": ev_atb_c},
        ],
        "limits": ["conservative estimate; no emission evidence yet"],
        "evidence_used": ev_sim + ev_atb_a,
        "recommended_next_actions": ["request_manual_pdf"],
    }


def _assert_strict_required_coverage(schema: dict, *, path: str = "$") -> None:
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object" and schema.get("additionalProperties") is False:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        missing = sorted([k for k in props.keys() if k not in required])
        assert not missing, f"{path}: missing required keys for strict schema: {missing}"
        for k, v in props.items():
            _assert_strict_required_coverage(v, path=f"{path}.properties.{k}")
    if schema.get("type") == "array":
        _assert_strict_required_coverage(schema.get("items"), path=f"{path}.items")


def _has_code(errors: list, code: str) -> bool:
    for row in errors:
        if isinstance(row, dict) and row.get("code") == code:
            return True
    return False


def test_validate_master_output_passes_for_valid_payload():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    ok, errors, normalized, used, used_ids, used_evidence = validate_master_output(
        _valid_output(pack),
        pack,
        case,
        {"conservative_confidence_cap": 0.65, "master_output_schema_version": "v3"},
    )
    assert ok is True
    assert errors == []
    assert normalized["status"] == "ok"
    assert "/current_gate/reasoning_mode" in used
    assert "/evidence_readiness/atb/features_summary/delta_dihedral" in used
    assert any(x.startswith("E") for x in used_ids)
    assert any(x.get("evidence_id", "").startswith("E") for x in used_evidence)


def test_master_output_schema_v3_strict_required_coverage():
    schema = master_output_schema(schema_version="v3")
    _assert_strict_required_coverage(schema)


def test_validate_master_output_fails_on_invalid_evidence_path():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["evidence_used"] = [{"evidence_id": "E999", "note": "bad", "role": "support"}]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"conservative_confidence_cap": 0.65, "master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "evidence_id_not_found")


def test_validate_master_output_enforces_conservative_cap_and_limits():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["mechanism_claim"]["confidence"] = 0.9
    out["limits"] = []
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"conservative_confidence_cap": 0.65, "master_output_schema_version": "v3"},
    )
    assert ok is True
    assert not _has_code(errors, "confidence_cap_exceeded")
    assert "missing_conservative_limit_statement" not in errors
    assert "missing_no_emission_evidence_limit" not in errors
    limits_lower = [str(x).lower() for x in normalized.get("limits") or []]
    assert any("conservative mode" in x for x in limits_lower)
    assert any("no emission evidence" in x for x in limits_lower)


def test_validate_master_output_accepts_semantic_limit_phrases():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["limits"] = [
        "Inference is uncertain because similarity support is weak.",
        "Direct emission-field confirmation is unavailable in this run.",
    ]
    ok, errors, normalized, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"conservative_confidence_cap": 0.65, "master_output_schema_version": "v3"},
    )
    assert ok is True
    assert errors == []
    # semantic statements are already present; auto-appended defaults should not duplicate.
    limits_lower = [str(x).lower() for x in normalized.get("limits") or []]
    assert sum("conservative mode" in x for x in limits_lower) <= 1
    assert sum("no emission evidence" in x for x in limits_lower) <= 1


def test_validate_master_output_rejects_neighbor_support_when_similarity_low():
    case = _case(conservative=False, with_emission=True)
    case["risk_scores"]["top1_sim"] = 0.4
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    pack["evidence_registry"].append(
        {
            "evidence_id": "E998",
            "case_path": "/neighbors/0/sim",
            "label": "neighbor similarity",
            "value_preview": 0.9,
            "role_hint": "context",
            "note_hint": "neighbor support",
        }
    )
    out["evidence_used"] = [{"evidence_id": "E998", "note": "neighbor support", "role": "support"}]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "neighbor_support_disallowed_low_similarity")


def test_validate_master_output_requires_step_order_and_atb_citations():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["supporting_chain"][0]["step_id"] = "B"
    out["supporting_chain"][0]["evidence_used"] = [_ev(pack, "/risk_scores/top1_sim", "sim", "context")]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "supporting_chain_step_order_invalid")
    assert _has_code(errors, "supporting_chain_step_a_missing_atb_citation")


def test_validate_master_output_avoids_hard_constant_similarity_entropy_cap():
    case = _case(conservative=False, with_emission=True)
    case["risk_scores"]["top1_sim"] = 0.49
    case["risk_scores"]["mechanism_entropy"] = 0.91
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["mechanism_claim"]["confidence"] = 0.6
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert not _has_code(errors, "confidence_cap_exceeded")


def test_validate_master_output_enforces_atb_support_level_consistency():
    case = _case(conservative=False, with_emission=True)
    case["evidence_readiness"]["atb"]["features_summary"]["delta_dihedral"] = 5.0
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["mechanism_claim"]["primary_hypothesis"]["atb_support_level"] = "strong"
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "atb_support_level_inconsistent")


def test_validate_master_output_rejects_null_evidence_value_paths():
    case = _case(conservative=False, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    # inject a null-resolving evidence id into registry for this negative test
    pack["evidence_registry"].append(
        {
            "evidence_id": "E999",
            "case_path": "/target_fields/emission_aggr_nm",
            "label": "null field",
            "value_preview": None,
            "role_hint": "context",
            "note_hint": "null field",
        }
    )
    out["evidence_used"] = [{"evidence_id": "E999", "note": "null field", "role": "support"}]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "evidence_path_empty_value")


def test_validation_rejects_pack_only_paths():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    pack["evidence_registry"].append(
        {
            "evidence_id": "E998",
            "case_path": "/neighbors_topk/0/sim",
            "label": "pack-only path",
            "value_preview": 0.9,
            "role_hint": "context",
            "note_hint": "invalid",
        }
    )
    out["evidence_used"] = [{"evidence_id": "E998", "note": "invalid pack path", "role": "context"}]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "evidence_path_not_found")


def test_five_signals_not_validated():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["five_signals"] = {"unexpected": True}
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True


def test_validate_master_output_auto_adds_aop_compact_citation_in_r2_when_missing():
    case = _case(conservative=False, with_emission=True)
    case["evidence_readiness"]["atb"]["features_summary"].update(
        {
            "s1_transition_electric_dip_au": 5.2,
            "s1_oscillator_strength_f": 0.21,
            "s1_excitation_wavelength_nm": 540.0,
            "delta_perm_dipole_tot_debye": 1.1,
            "s1_rotatory_strength_cgs": 0.03,
            "aop_compact_reliability_score": 2.0,
        }
    )
    pack = build_reasoning_pack(
        case,
        {
            "run_lane": "atb_cache_only",
            "evidence_profiles": {"active_profile": "R2"},
        },
    )
    assert any(row.get("evidence_id") in {"E60", "E61", "E62", "E63"} for row in (pack.get("evidence_registry") or []))

    out = _valid_output(pack)
    # Keep only non-aop citations to trigger auto-insertion.
    out["evidence_used"] = [
        _ev(pack, "/current_gate/reasoning_mode", "reasoning mode context", "context"),
        _ev(pack, "/evidence_readiness/atb/features_summary/delta_dihedral", "torsional change", "support"),
    ]
    ok, errors, normalized, _, used_ids, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "r2_missing_aop_compact_citation_auto_added")
    assert any(eid in {"E60", "E61", "E62", "E63"} for eid in used_ids)
    assert any(
        isinstance(row, dict) and str(row.get("evidence_id") or "") in {"E60", "E61", "E62", "E63"}
        for row in (normalized.get("evidence_used") or [])
    )
    assert not _has_code(errors, "additional_property_not_allowed")


def test_master_output_requires_step_name():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    del out["supporting_chain"][0]["step_name"]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is False
    assert _has_code(errors, "missing_required")


def test_no_invented_thresholds_rejected():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["supporting_chain"][1]["claim"] = "Use a 11-13 degree band to assert TICT."
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {
            "master_output_schema_version": "v3",
            "thresholds": {
                "atb_dihedral_thresh_none": 8.0,
                "atb_dihedral_thresh_strong": 15.0,
            },
        },
    )
    assert ok is True
    assert _has_code(errors, "invented_threshold_not_allowed")


def test_evidence_id_only_without_case_path_passes():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert not _has_code(errors, "evidence_case_path_forbidden")


def test_case_path_null_rejected_with_clear_code():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["evidence_used"][0]["case_path"] = None
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "evidence_case_path_forbidden")


def test_forbidden_hint_reference_rejected():
    case = _case(conservative=False, with_emission=True)
    case["risk_scores"]["mechanism_hint"] = "TICT"
    case["risk_scores"]["hint_confidence"] = 0.8
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    pack["evidence_registry"].append(
        {
            "evidence_id": "E777",
            "case_path": "/risk_scores/mechanism_hint",
            "label": "forbidden hint",
            "value_preview": "TICT",
            "role_hint": "context",
            "note_hint": "forbidden",
        }
    )
    out["evidence_used"] = [{"evidence_id": "E777", "note": "bad ref", "role": "context"}]
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert _has_code(errors, "forbidden_hint_reference")


def test_spectral_band_words_without_numeric_context_pass():
    case = _case(conservative=True, with_emission=False)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = _valid_output(pack)
    out["predictions"][0]["prediction"] = "Large Stokes-shifted emission band is observed."
    out["predictions"][1]["expected_signal"] = "Band assignment is consistent with CT relaxation."
    out["limits"].append("Emission band broadening may reflect mixed states.")
    ok, errors, _, _, _, _ = validate_master_output(
        out,
        pack,
        case,
        {"master_output_schema_version": "v3"},
    )
    assert ok is True
    assert not _has_code(errors, "invented_threshold_not_allowed")


def test_weak_band_range_terms_with_numeric_context_fail_without_threshold_citation():
    case = _case(conservative=False, with_emission=True)
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    phrases = [
        "band in 8–15° suggests torsional access",
        "range 0.5–0.7 indicates moderate confidence",
        "band > 500 nm suggests red-shifted emission",
    ]
    for phrase in phrases:
        out = deepcopy(_valid_output(pack))
        out["predictions"][2]["prediction"] = phrase
        ok, errors, _, _, _, _ = validate_master_output(
            out,
            pack,
            case,
            {"master_output_schema_version": "v3"},
        )
        assert ok is True
        assert _has_code(errors, "invented_threshold_not_allowed")

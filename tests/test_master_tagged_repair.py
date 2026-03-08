from src.reasoning.master_reasoner import (
    _repair_json_only,
    build_reasoning_pack,
    validate_master_output,
)
from src.tools.llm_client import ResponsesLLMClient


def _case() -> dict:
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-TAG"},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "demo",
        },
        "neighbors": [{"rank": 1, "sim": 0.9, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "other"}],
        "risk_scores": {
            "top1_sim": 0.9,
            "mean_topk_sim": 0.85,
            "novelty_struct": 0.1,
            "mechanism_entropy": 0.3,
            "atb_neighbor_consistency": {"flag": "inlier", "reliability": "medium"},
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {"delta_dihedral": 12.0, "delta_gap": 0.1, "delta_volume": 0.3},
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _llm_stub() -> ResponsesLLMClient:
    return ResponsesLLMClient(base_url="http://example/v1", model="dummy", api_key_env="OPENAI_API_KEY")


def test_tagged_output_repair_to_structured_json_passes():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: mixture\n"
        "STATUS: insufficient_evidence\n"
        "PRIMARY_LABEL: ICT\n"
        "PRIMARY_CONFIDENCE: 0.58\n"
        "PRIMARY: Provisional mechanism from aTB context (E10, E11).\n"
        "COMPETING: alt_a (E4)\n- alt_b (E6)\n"
        "EVIDENCE: E11 support torsion cue\nE12 context gap cue\nE4 neighbor prior\n"
        "PREDICTIONS: compare time-resolved PL response\n"
        "LIMITS: conservative mode in effect\n"
        "NEXT_ACTIONS: provide_offline_pdf\nswitch_run_lane_offline_pdf\n"
    )
    repaired = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "mixture"},
    )
    parsed = repaired["parsed"]
    assert isinstance(parsed, dict)
    required = {
        "status",
        "template_used",
        "mechanism_claim",
        "supporting_chain",
        "competing_hypotheses",
        "predictions",
        "limits",
        "evidence_used",
        "recommended_next_actions",
    }
    assert required.issubset(set(parsed.keys()))
    ok, errors, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert errors == [] or all(isinstance(x, dict) and x.get("type") == "warning" for x in errors)


def test_missing_competing_and_evidence_sections_fill_defaults():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: stable\n"
        "STATUS: ok\n"
        "PRIMARY_LABEL: other\n"
        "PRIMARY_CONFIDENCE: 0.42\n"
        "PRIMARY: Compact primary statement.\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "stable"},
    )["parsed"]
    assert isinstance(parsed.get("competing_hypotheses"), list)
    assert isinstance(parsed.get("evidence_used"), list)
    ok, errors, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert all((x.get("type") == "warning") for x in errors) if errors else True


def test_extra_text_after_next_section_ignored():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: mixture\n"
        "STATUS: insufficient_evidence\n"
        "PRIMARY_LABEL: ICT\n"
        "PRIMARY_CONFIDENCE: 0.51\n"
        "PRIMARY: Primary claim with E11.\n"
        "COMPETING: alt\n"
        "EVIDENCE: E11 support\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
        "This trailing paragraph should be ignored by tagged parser.\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "mixture"},
    )["parsed"]
    ok, errors, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert all((x.get("type") == "warning") for x in errors) if errors else True


def test_unknown_evidence_id_removed_with_warning():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: mixture\n"
        "STATUS: insufficient_evidence\n"
        "PRIMARY_LABEL: ICT\n"
        "PRIMARY_CONFIDENCE: 0.48\n"
        "PRIMARY: Primary claim.\n"
        "COMPETING: alt\n"
        "EVIDENCE: E999 unknown ref\nE11 valid ref\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "mixture"},
    )["parsed"]
    ok, errors, normalized, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert any(isinstance(x, dict) and x.get("code") == "evidence_id_not_found" for x in errors)
    assert all((row.get("evidence_id") != "E999") for row in (normalized.get("evidence_used") or []))


def test_missing_primary_label_or_confidence_fails_schema_validation():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: mixture\n"
        "STATUS: insufficient_evidence\n"
        "PRIMARY: Missing label/confidence sections.\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "mixture"},
    )["parsed"]
    ok, errors, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is False
    assert any(isinstance(x, dict) and x.get("code") in {"enum_violation", "missing_required"} for x in errors)


def test_primary_label_outside_allowed_pool_normalizes_unknown():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: stable\n"
        "STATUS: ok\n"
        "PRIMARY_LABEL: NOT_A_LABEL\n"
        "PRIMARY_CONFIDENCE: 0.52\n"
        "PRIMARY: Label should normalize.\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "stable"},
    )["parsed"]
    ok, errors, normalized, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert ((normalized.get("mechanism_claim") or {}).get("primary_hypothesis") or {}).get("mechanism_label") == "unknown"
    assert any(isinstance(x, dict) and x.get("code") == "evidence_path_empty_value" for x in errors) is False


def test_primary_label_annotated_text_normalizes_to_allowed_token():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    tagged = (
        "TEMPLATE_USED: mixture\n"
        "STATUS: insufficient_evidence\n"
        "PRIMARY_LABEL: TICT (torsion-enabled CT candidate)\n"
        "PRIMARY_CONFIDENCE: 0.47\n"
        "PRIMARY: Label should map to canonical token.\n"
        "NEXT_ACTIONS: provide_offline_pdf\n"
    )
    parsed = _repair_json_only(
        llm_client=_llm_stub(),
        raw_text=tagged,
        schema_name="master_output_schema_v3",
        schema={},
        reasoning_config={"__repair_reasoning_pack": pack, "__repair_template": "mixture"},
    )["parsed"]
    ok, errors, normalized, *_ = validate_master_output(parsed, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert ((normalized.get("mechanism_claim") or {}).get("primary_hypothesis") or {}).get("mechanism_label") == "TICT"
    assert all((x.get("code") != "enum_violation") for x in errors if isinstance(x, dict))

from src.reasoning.master_reasoner import _tagged_text_to_master_output, build_reasoning_pack


def _case(top1: float, entropy: float, separation: float, reliability: str = "medium") -> dict:
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-CONF"},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "test",
        },
        "neighbors": [{"rank": 1, "sim": top1, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": top1,
            "mean_topk_sim": top1,
            "novelty_struct": 0.2,
            "mechanism_entropy": entropy,
            "neighbor_atb_stats_by_label": {"reliability": reliability, "separation_score": separation},
            "atb_neighbor_consistency": {"flag": "inlier", "reliability": "medium"},
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {"delta_dihedral": 12.0, "delta_gap": -0.1, "delta_volume": 0.2},
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _parse_conf(case_json: dict) -> float:
    pack = build_reasoning_pack(case_json, {"run_lane": "atb_cache_only"})
    parsed = _tagged_text_to_master_output(
        raw_text=(
            "TEMPLATE_USED: stable\n"
            "STATUS: ok\n"
            "PRIMARY_LABEL: ICT\n"
            "PRIMARY_CONFIDENCE: 0.74\n"
            "PRIMARY: Test mechanism claim.\n"
            "EVIDENCE: E11 torsion cue\n"
            "NEXT_ACTIONS: provide_offline_pdf\n"
        ),
        reasoning_pack=pack,
        reasoning_config={"master_output_schema_version": "v3", "conservative_confidence_cap": 0.65},
        template_fallback="stable",
    )
    return float((parsed.get("mechanism_claim") or {}).get("confidence") or 0.0)


def test_soft_penalty_produces_non_constant_confidence():
    conf_a = _parse_conf(_case(top1=0.72, entropy=0.22, separation=0.8))
    conf_b = _parse_conf(_case(top1=0.49, entropy=0.9, separation=0.1))
    conf_c = _parse_conf(_case(top1=0.55, entropy=0.5, separation=0.7))

    values = {round(conf_a, 6), round(conf_b, 6), round(conf_c, 6)}
    assert len(values) >= 2
    assert conf_a <= 0.65
    assert conf_b <= 0.65
    assert conf_c <= 0.65

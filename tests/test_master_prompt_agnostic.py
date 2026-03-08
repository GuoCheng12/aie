from src.reasoning.master_reasoner import build_master_prompt_bundle, build_reasoning_pack
import re


def _base_case():
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-1", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_for_reasoning",
            "ready_for_reasoning": True,
            "reasoning_mode": "normal",
            "reason": "ok",
        },
        "neighbors": [{"rank": 1, "sim": 0.8, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "X"}],
        "risk_scores": {
            "top1_sim": 0.8,
            "mean_topk_sim": 0.7,
            "novelty_struct": 0.2,
            "mechanism_entropy": 0.4,
            "mechanism_hint": "unknown",
            "hint_confidence": 0.4,
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {"delta_dihedral": 10.0, "delta_gap": 0.1, "delta_volume": 0.3},
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": None},
        },
        "target_fields": {},
        "target_fields_provenance": {},
        "mechanism_signatures": {},
        "candidate_mechanisms": [],
    }


def _cfg():
    return {
        "run_lane": "atb_cache_only",
        "master_output_schema_version": "v3",
        "conservative_confidence_cap": 0.65,
    }


def test_prompt_generic_when_candidates_missing():
    case = _base_case()
    pack = build_reasoning_pack(case, _cfg())
    pack["mechanism_context"]["candidate_mechanisms_top3"] = []
    bundle = build_master_prompt_bundle(pack, _cfg())
    instructions = str(bundle.get("instructions") or "")
    assert "Top competing mechanisms are uncertain; propose plausible hypotheses from evidence." in instructions
    assert re.search(r"\\bICT\\b", instructions) is None
    assert re.search(r"\\bTICT\\b", instructions) is None
    assert re.search(r"\\bESIPT\\b", instructions) is None


def test_prompt_injects_candidate_labels_dynamically():
    case = _base_case()
    pack = build_reasoning_pack(case, _cfg())
    pack["mechanism_context"]["candidate_mechanisms_top3"] = [
        {"mechanism_id": "TICT", "probability": 0.6},
        {"mechanism_id": "ICT", "probability": 0.4},
    ]
    bundle = build_master_prompt_bundle(pack, _cfg())
    instructions = str(bundle.get("instructions") or "")
    assert "Top competing mechanisms (from retrieval priors): TICT, ICT." in instructions

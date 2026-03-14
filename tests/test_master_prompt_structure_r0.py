from src.reasoning.master_reasoner import build_master_prompt_bundle, build_reasoning_pack
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.motif_detector import detect_structure_motifs


def test_r0_prompt_treats_structure_evidence_as_candidate_generation():
    smiles = "Oc1ccccc1C=Nc2ccccc2"
    structure_prior = compute_structure_prior_profile(
        smiles,
        {
            "n_rotatable_bonds": 2,
            "n_hbd": 1,
            "n_hba": 2,
            "n_rings": 2,
            "n_aromatic_rings": 2,
            "tpsa": 32.0,
            "logp": 3.0,
            "n_heavy_atoms": 15,
        },
    )
    case = {
        "query": {"input_smiles": smiles, "canonical_smiles": smiles, "inchikey": "IK-R0", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "structure_prior_without_target_atb",
        },
        "neighbors": [{"rank": 1, "sim": 0.71, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
        "risk_scores": {
            "top1_sim": 0.71,
            "mean_topk_sim": 0.68,
            "novelty_struct": 0.19,
            "mechanism_entropy": 0.43,
            "structure_prior_profile": structure_prior,
            "structure_motif_profile": detect_structure_motifs(smiles, structure_prior.get("descriptor_snapshot")),
            "structure_retrieval_profile": {
                "version": "structure_retrieval_v1",
                "feature_morgan_topk": [{"case_index": 1, "sim": 0.81, "mechanism_label": "ESIPT"}],
                "murcko_topk": [{"case_index": 2, "sim": 1.0, "mechanism_label": "neutral aromatic"}],
                "feature_neighbor_label_distribution": {"ESIPT": 1.0},
                "scaffold_neighbor_label_distribution": {"neutral aromatic": 1.0},
                "retrieval_consensus_strength": "mid",
            },
            "structure_candidate_distribution": {
                "version": "structure_candidate_dist_v1",
                "label_probs": {"ESIPT": 0.44, "ICT": 0.30, "neutral aromatic": 0.18, "TICT": 0.04, "other": 0.04, "unknown": 0.0},
                "top3": [{"label": "ESIPT", "prob": 0.44}, {"label": "ICT", "prob": 0.30}, {"label": "neutral aromatic", "prob": 0.18}],
                "calibration": {"method": "retrieval_fallback", "reliability": "low"},
            },
        },
        "evidence_readiness": {
            "atb": {"cache_status": "failed", "features_summary": {}},
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R0"}})
    bundle = build_master_prompt_bundle(pack, {"run_lane": "atb_cache_only", "master_output_mode": "tagged_repair"})
    instructions = str(bundle.get("instructions") or "")
    assert "Read the R0 prior stack in this order" in instructions
    assert "candidate slate is a suggestion layer" in instructions.lower()
    payload = bundle.get("user_payload") or {}
    risk = (payload.get("risk_scores") or {})
    assert "structure_fact_sheet" in risk
    assert "prior_reliability_profile" in risk
    assert "candidate_slate_v2" in risk
    assert "structure_retrieval_profile" not in risk
    assert "structure_candidate_distribution" not in risk

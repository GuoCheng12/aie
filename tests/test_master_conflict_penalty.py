from src.reasoning.master_reasoner import build_reasoning_pack, validate_master_output


def _case() -> dict:
    return {
        "query": {"input_smiles": "C1=CC=C(C=C1)N=NC2=CC=CC=C2O", "canonical_smiles": "C1=CC=C(C=C1)N=NC2=CC=CC=C2O", "inchikey": "IK-CONFLICT", "aliases": []},
        "runtime": {"run_lane": "atb_cache_only"},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "demo",
        },
        "neighbors": [{"rank": 1, "sim": 0.78, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "other"}],
        "risk_scores": {
            "top1_sim": 0.78,
            "mean_topk_sim": 0.73,
            "novelty_struct": 0.21,
            "mechanism_entropy": 0.44,
            "structure_prior_profile": {
                "version": "structure_prior_v1",
                "donor_acceptor_topology": "mixed",
                "intramolecular_hbond_candidates": "possible",
                "aromatic_core_density": "high",
                "flexibility_proxy": "mid",
                "conjugation_proxy": "high",
                "overall_structure_prior": "Conjugation is high; aromatic-core density is high; flexibility is mid; donor/acceptor topology is mixed; intramolecular H-bond candidates are possible; reliability is high.",
                "reliability": "high",
                "notes": ["Aromatic-core density is high, flexibility is mid, and conjugation is high."],
            },
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_gap": -0.25,
                    "delta_dihedral": 9.5,
                    "delta_volume": 0.6,
                    "delta_dipole": 0.18,
                    "delta_bonds": 0.02,
                    "delta_angles": 0.3,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def test_conflict_penalty_reduces_confidence_and_records_meta():
    case = _case()
    pack = build_reasoning_pack(case, {"run_lane": "atb_cache_only"})
    out = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "Competing mechanisms remain close under current evidence.",
                "atb_support_level": "weak",
            },
            "confidence": 0.60,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {"step_id": "A", "step_name": "torsion_access", "claim": "Structural access is present.", "evidence_used": [{"evidence_id": "E31", "note": "torsion", "role": "support"}]},
            {"step_id": "B", "step_name": "ct_family", "claim": "Electronic redistribution remains plausible.", "evidence_used": [{"evidence_id": "E36", "note": "redistribution", "role": "support"}]},
            {"step_id": "C", "step_name": "aIE_bridge", "claim": "Aggregation bridge is still provisional.", "evidence_used": [{"evidence_id": "E37", "note": "relaxation", "role": "support"}]},
            {"step_id": "D", "step_name": "discriminators", "claim": "Compare and measure orthogonal discriminators.", "evidence_used": [{"evidence_id": "E40", "note": "prior context", "role": "context"}]},
        ],
        "competing_hypotheses": [
            {"name": "neutral aromatic", "confidence": 0.50, "atb_support_level": "weak", "evidence_used": [{"evidence_id": "E40", "note": "prior", "role": "context"}]},
            {"name": "other", "confidence": 0.35, "atb_support_level": "none", "evidence_used": [{"evidence_id": "E37", "note": "aux", "role": "context"}]},
        ],
        "predictions": [{"prediction": "measure orthogonal discriminators", "expected_signal": "hypothesis separation", "evidence_used": [{"evidence_id": "E40", "note": "prior", "role": "context"}]}],
        "limits": [],
        "evidence_used": [{"evidence_id": "E36", "note": "redistribution", "role": "support"}, {"evidence_id": "E37", "note": "relaxation", "role": "support"}],
        "recommended_next_actions": ["provide_offline_pdf"],
    }
    ok, errors, normalized, _, _, _ = validate_master_output(out, pack, case, {"master_output_schema_version": "v3"})
    assert ok is True
    assert float(normalized["mechanism_claim"]["confidence"]) < 0.60
    meta = normalized.get("__meta") or {}
    assert int(meta.get("active_conflict_count") or 0) >= 1
    assert bool(meta.get("conflict_penalty_applied")) is True

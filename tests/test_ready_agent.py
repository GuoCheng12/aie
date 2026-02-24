import json

from src.agents.ready_agent import (
    apply_ready_agent_patch,
    review_case_and_patch,
)


def _base_case():
    return {
        "case_id": "CASE-READY-1",
        "query": {
            "inchikey": "AAAA-BBBB",
            "canonical_smiles": "C",
        },
        "inputs": {"offline_pdfs": []},
        "target_fields": {},
        "target_fields_provenance": {},
        "evidence_candidates_staging": [],
        "evidence_readiness": {
            "atb": {"cache_status": "success"},
            "literature": {"status": "not_started", "sources": [], "last_update": "2026-02-24T00:00:00Z", "notes": None},
        },
        "current_gate": {
            "state": "ready_for_reasoning",
            "ready_for_reasoning": True,
            "reason": "contradiction_seed",
            "reasoning_mode": "normal",
        },
        "action_rationale": "contradiction_seed",
        "action_plan": [
            {
                "action": "run_master_reasoner",
                "priority": 1,
                "status": "pending",
                "inputs": {},
                "expected_outputs": [],
                "blocking": False,
                "notes": "",
            }
        ],
        "risk_scores": {},
    }


def _patch_paths(patch_ops):
    return [str(op.get("path")) for op in patch_ops]


def test_ready_agent_promotes_atb_success_without_emission_to_ready_conservative():
    case = _base_case()
    patch = review_case_and_patch(case)
    after = apply_ready_agent_patch(case, patch)

    assert after["current_gate"]["state"] == "ready_conservative"
    assert after["current_gate"]["ready_for_reasoning"] is True
    assert "gate=ready_conservative" in after["action_rationale"]
    assert after["action_plan"][0]["action"] == "run_master_reasoner"
    actions = [x.get("action") for x in after["action_plan"]]
    assert "request_manual_pdf" in actions


def test_ready_agent_rejects_aggr_leakage_when_no_aggregation_signal():
    case = _base_case()
    case["inputs"]["offline_pdfs"] = [{"path_or_id": "data/pdfs/DMA-AM.pdf"}]
    case["target_fields"]["emission_aggr_nm"] = 520.0
    case["target_fields_provenance"]["emission_aggr_nm"] = {
        "source_ref": "data/pdfs/DMA-AM.pdf",
        "source_locator": "thin film emission figure",
        "confidence": 0.95,
        "identity_match": "exact",
        "identity_match_confidence": 0.93,
    }

    patch = review_case_and_patch(case)
    after = apply_ready_agent_patch(case, patch)

    assert after["current_gate"]["state"] == "needs_manual"
    actions = [x.get("action") for x in after["action_plan"]]
    assert "rerun_offline_pdf_extractor" in actions
    assert "anti_leakage_failed" in after["current_gate"]["reason"]


def test_ready_agent_requires_identity_fields_and_downgrades_when_missing():
    case = _base_case()
    case["inputs"]["offline_pdfs"] = [{"path_or_id": "data/pdfs/DMA-AM.pdf"}]
    case["target_fields"]["emission_solid_or_film_nm"] = 610.0
    case["target_fields_provenance"]["emission_solid_or_film_nm"] = {
        "source_ref": "data/pdfs/DMA-AM.pdf",
        "source_locator": "Fig. 2",
        "confidence": 0.95,
        # identity fields intentionally missing
    }

    patch = review_case_and_patch(case)
    after = apply_ready_agent_patch(case, patch)

    assert after["current_gate"]["state"] == "needs_manual"
    actions = [x.get("action") for x in after["action_plan"]]
    assert "manual_identity_verify_from_pdf" in actions


def test_ready_agent_patch_does_not_touch_forbidden_fields():
    case = _base_case()
    patch = review_case_and_patch(case)
    paths = _patch_paths(patch)
    for p in paths:
        assert (
            p.startswith("/current_gate/")
            or p == "/action_rationale"
            or p == "/action_plan"
            or p.startswith("/risk_scores/readiness_")
        )


def test_ready_agent_sets_conservative_on_atb_neighbor_outlier():
    case = _base_case()
    case["inputs"]["offline_pdfs"] = [{"path_or_id": "data/pdfs/DMA-AM.pdf"}]
    case["target_fields"]["emission_aggr_nm"] = 530.0
    case["target_fields_provenance"]["emission_aggr_nm"] = {
        "source_ref": "data/pdfs/DMA-AM.pdf",
        "source_locator": "Table 1 (page 3)",
        "confidence": 0.9,
        "identity_match": "exact",
        "identity_match_confidence": 0.95,
        "condition": "aggregation in water fraction",
    }
    case["risk_scores"]["atb_neighbor_consistency"] = {
        "flag": "outlier",
        "reliability": "medium",
    }

    patch = review_case_and_patch(case)
    after = apply_ready_agent_patch(case, patch)

    assert after["current_gate"]["state"] == "ready_conservative"
    assert after["current_gate"]["reasoning_mode"] == "conservative"
    assert "atb_neighbor_outlier" in after["current_gate"]["reason"]
    assert after["risk_scores"]["readiness_atb_neighbor_flag"] == "outlier"
    actions = [x.get("action") for x in after["action_plan"]]
    assert "literature_search_web" in actions
    assert "request_min_experiment_emission" in actions

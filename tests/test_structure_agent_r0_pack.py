from pathlib import Path

from src.agents.structure_agent import StructureAgent
from src.core.patching import apply_patch
from src.core.types import AgentContext
from src.data.rdkit_descriptors import compute_basic_descriptors
from src.reasoning.master_reasoner import build_reasoning_pack
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import extract_murcko_scaffold


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-structure-r0",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        run_lane="atb_cache_only",
    )


def _case() -> dict:
    return {
        "case_id": "CASE-STRUCT-R0",
        "query": {
            "input_smiles": "Oc1ccccc1C=Nc2ccccc2",
            "canonical_smiles": "Oc1ccccc1C=Nc2ccccc2",
            "inchikey": "TARGET",
        },
        "risk_scores": {},
    }


def _row(smiles: str, label: str, inchikey: str) -> dict:
    descriptors = compute_basic_descriptors(smiles)
    scaffold_info = extract_murcko_scaffold(smiles)
    return {
        "inchikey": inchikey,
        "canonical_smiles": smiles,
        "mechanism_label": label,
        "descriptor_snapshot": descriptors,
        "morgan_count": compute_morgan_count(smiles),
        "feature_morgan_count": compute_feature_morgan_count(smiles),
        "scaffold_info": scaffold_info,
        "murcko_scaffold_smiles": scaffold_info.get("murcko_scaffold_smiles"),
        "generic_scaffold_smiles": scaffold_info.get("generic_scaffold_smiles"),
        "scaffold_feature_morgan_count": compute_feature_morgan_count(
            scaffold_info.get("generic_scaffold_smiles") or scaffold_info.get("murcko_scaffold_smiles") or smiles
        ),
        "structure_motif_profile": detect_structure_motifs(smiles, descriptors),
        "structure_prior_profile": compute_structure_prior_profile(smiles, descriptors),
    }


def test_r0_pack_uses_fact_sheet_reliability_and_candidate_slate(tmp_path):
    agent = StructureAgent(retrieval_topk=3, candidate_topk=5, classifier_model_root=tmp_path / "missing_model")
    agent._reference_rows = [
        _row("Oc1ccccc1C=Nc2ccccc2", "ESIPT", "IK1"),
        _row("c1ccc(cc1)c2ccccc2", "neutral aromatic", "IK2"),
        _row("CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1", "ICT", "IK3"),
    ]
    case = _case()
    ctx = _ctx(tmp_path)
    result = agent.run(case, ctx, agent.build_inputs(case, ctx))
    updated = apply_patch(case, result.patch)
    updated.update(
        {
            "runtime": {"run_lane": "atb_cache_only"},
            "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed"}},
            "current_gate": {
                "state": "ready_conservative",
                "ready_for_reasoning": True,
                "reasoning_mode": "conservative",
                "reason": "structure_prior_without_target_atb",
            },
            "neighbors": [{"rank": 1, "sim": 0.74, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"}],
            "evidence_readiness": {
                "atb": {"cache_status": "failed", "features_summary": {}},
                "literature": {"status": "not_started", "notes": "lane_disabled"},
                "experiment": {"status": "not_requested", "notes": "lane_disabled"},
            },
            "target_fields": {},
            "target_fields_provenance": {},
        }
    )
    updated["risk_scores"].update(
        {
            "top1_sim": 0.74,
            "mean_topk_sim": 0.70,
            "novelty_struct": 0.18,
            "mechanism_entropy": 0.42,
        }
    )
    pack = build_reasoning_pack(updated, {"run_lane": "atb_cache_only", "evidence_profiles": {"active_profile": "R0"}})
    risk = pack.get("risk_scores") or {}
    dist = (risk.get("candidate_slate_v2") or {})
    assert dist.get("top3")
    assert dist.get("top_candidates")
    assert len(dist.get("top_candidates") or []) <= 4
    assert (risk.get("structure_fact_sheet") or {}).get("version") == "structure_fact_sheet_v1"
    assert (risk.get("prior_reliability_profile") or {}).get("version") == "prior_reliability_v1"
    assert "structure_prior_profile" not in risk
    assert "structure_motif_profile" not in risk
    assert "structure_retrieval_profile" not in risk
    assert "structure_candidate_distribution" not in risk
    ids = {str(row.get("evidence_id")) for row in (pack.get("evidence_registry") or []) if isinstance(row, dict)}
    assert {"E50", "E51", "E52", "E53", "E54", "E55", "E56"}.issubset(ids)
    e56 = next(row for row in (pack.get("evidence_registry") or []) if isinstance(row, dict) and row.get("evidence_id") == "E56")
    assert e56.get("pack_path") == "/risk_scores/candidate_slate_v2"
    mechanism_ctx = pack.get("mechanism_context") or {}
    assert mechanism_ctx.get("candidate_mechanisms_topk")
    assert len(mechanism_ctx.get("candidate_mechanisms_topk") or []) <= 4

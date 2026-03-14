from pathlib import Path

from src.agents.structure_agent import StructureAgent
from src.core.patching import validate_patch
from src.core.types import AgentContext
from src.data.rdkit_descriptors import compute_basic_descriptors
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import extract_murcko_scaffold


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-structure",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        run_lane="atb_cache_only",
    )


def _case() -> dict:
    return {
        "case_id": "CASE-STRUCT",
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


def test_structure_agent_patch_scope_and_outputs(tmp_path: Path):
    agent = StructureAgent(retrieval_topk=3, candidate_topk=5, classifier_model_root=tmp_path / "missing_model")
    agent._reference_rows = [
        _row("Oc1ccccc1C=Nc2ccccc2", "ESIPT", "IK1"),
        _row("c1ccc(cc1)c2ccccc2", "neutral aromatic", "IK2"),
        _row("CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1", "ICT", "IK3"),
    ]
    case = _case()
    ctx = _ctx(tmp_path)
    inputs = agent.build_inputs(case, ctx)
    result = agent.run(case, ctx, inputs)

    assert result.status == "success"
    validate_patch(
        result.patch,
        allowed_prefixes=agent.allowed_patch_prefixes,
        append_only_prefixes=agent.append_only_prefixes,
    )
    paths = {op["path"] for op in result.patch}
    assert "/risk_scores/structure_prior_profile" in paths
    assert "/risk_scores/structure_motif_profile" in paths
    assert "/risk_scores/structure_retrieval_profile" in paths
    assert "/risk_scores/structure_candidate_distribution" in paths
    raw = result.raw_outputs["structure_agent_raw"]
    assert raw["structure_candidate_distribution"]["top3"]
    assert raw["structure_candidate_distribution"]["top_candidates"]
    assert raw["classifier_loaded"] is False
    assert raw["candidate_distribution_mode"] == "retrieval_fallback_only"
    assert raw["structure_candidate_distribution"]["calibration"]["method"] == "retrieval_fallback"

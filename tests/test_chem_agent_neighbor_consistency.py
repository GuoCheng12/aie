from pathlib import Path

from src.agents.chem_agent import ChemAgent
from src.core.types import AgentContext


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run-test",
        run_dir=tmp_path / "artifacts",
        case_path=tmp_path / "case.json",
        base_url="http://example/v1",
        model="gpt-test",
        run_lane="atb_cache_only",
    )


def test_chem_agent_writes_atb_neighbor_consistency(monkeypatch, tmp_path: Path):
    def _fake_get_atb_cache_record(inchikey: str):
        if inchikey == "TARGET-IK":
            return {
                "cache_status": "success",
                "missing_fields": [],
                "status": {"fail_stage": None, "error_msg": None},
                "features_summary": {"delta_gap": 0.1, "delta_dihedral": -1.0, "delta_volume": 0.5},
            }
        mapping = {
            "N1": {"cache_status": "success", "features_summary": {"delta_gap": 0.10, "delta_dihedral": -1.1, "delta_volume": 0.5}},
            "N2": {"cache_status": "success", "features_summary": {"delta_gap": 0.11, "delta_dihedral": -1.0, "delta_volume": 0.6}},
            "N3": {"cache_status": "success", "features_summary": {"delta_gap": 0.09, "delta_dihedral": -0.9, "delta_volume": 0.4}},
            "N4": {"cache_status": "success", "features_summary": {"delta_gap": 0.10, "delta_dihedral": -1.2, "delta_volume": 0.55}},
            "N5": {"cache_status": "success", "features_summary": {"delta_gap": 0.12, "delta_dihedral": -1.0, "delta_volume": 0.45}},
            "Nbad": {"cache_status": "failed", "features_summary": {"delta_gap": 9.9, "delta_dihedral": 9.9, "delta_volume": 9.9}},
        }
        rec = mapping.get(inchikey, {"cache_status": "absent", "features_summary": None})
        rec.setdefault("missing_fields", [])
        rec.setdefault("status", {"fail_stage": None, "error_msg": None})
        return rec

    monkeypatch.setattr("src.agents.chem_agent.get_atb_cache_record", _fake_get_atb_cache_record)

    case = {
        "case_id": "TARGET-IK",
        "query": {
            "inchikey": "TARGET-IK",
            "input_smiles": "C",
            "canonical_smiles": "C",
            "code": "TARGET",
            "aliases": [],
        },
        "neighbors": [
            {"neighbor_inchikey": "N1"},
            {"neighbor_inchikey": "N2"},
            {"neighbor_inchikey": "N3"},
            {"neighbor_inchikey": "N4"},
            {"neighbor_inchikey": "N5"},
            {"neighbor_inchikey": "Nbad"},
        ],
        "inputs": {"offline_pdfs": []},
        "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "relaxed", "extractor_mode": "mineru_llm"}},
    }

    agent = ChemAgent()
    ctx = _ctx(tmp_path)
    inputs = agent.build_inputs(case, ctx)
    result = agent.run(case, ctx, inputs)

    assert result.status == "success"
    risk_ops = [op for op in result.patch if op.get("path") == "/risk_scores/atb_neighbor_consistency"]
    assert len(risk_ops) == 1
    risk = risk_ops[0]["value"]
    assert risk["enabled"] is True
    assert risk["sample_size"] == 5
    assert risk["flag"] in {"inlier", "outlier"}

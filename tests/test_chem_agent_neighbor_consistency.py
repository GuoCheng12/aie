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
                "features": {"delta_gap": 0.1, "delta_dihedral": -1.0, "delta_volume": 0.5, "excitation_energy": 2.2},
            }
        mapping = {
            "N1": {"cache_status": "success", "features_summary": {"delta_gap": 0.10, "delta_dihedral": -1.1, "delta_volume": 0.5}, "features": {"delta_gap": 0.10, "delta_dihedral": -1.1, "delta_volume": 0.5, "excitation_energy": 2.1}},
            "N2": {"cache_status": "success", "features_summary": {"delta_gap": 0.11, "delta_dihedral": -1.0, "delta_volume": 0.6}, "features": {"delta_gap": 0.11, "delta_dihedral": -1.0, "delta_volume": 0.6, "excitation_energy": 2.3}},
            "N3": {"cache_status": "success", "features_summary": {"delta_gap": 0.09, "delta_dihedral": -0.9, "delta_volume": 0.4}, "features": {"delta_gap": 0.09, "delta_dihedral": -0.9, "delta_volume": 0.4, "excitation_energy": 2.0}},
            "N4": {"cache_status": "success", "features_summary": {"delta_gap": 0.10, "delta_dihedral": -1.2, "delta_volume": 0.55}, "features": {"delta_gap": 0.10, "delta_dihedral": -1.2, "delta_volume": 0.55, "excitation_energy": 2.2}},
            "N5": {"cache_status": "success", "features_summary": {"delta_gap": 0.12, "delta_dihedral": -1.0, "delta_volume": 0.45}, "features": {"delta_gap": 0.12, "delta_dihedral": -1.0, "delta_volume": 0.45, "excitation_energy": 2.4}},
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
    features_ops = [op for op in result.patch if op.get("path") == "/risk_scores/atb_neighbor_features_all"]
    assert len(features_ops) == 1
    features_rows = features_ops[0]["value"]
    assert len(features_rows) == 5
    assert features_rows[0]["neighbor_inchikey"] == "N1"
    assert "delta_gap" in features_rows[0]
    assert "delta_dihedral" in features_rows[0]
    assert "delta_volume" in features_rows[0]
    assert isinstance(features_rows[0]["features"], dict)
    atb_target_ops = [op for op in result.patch if op.get("path") == "/evidence_readiness/atb/features"]
    assert len(atb_target_ops) == 1
    assert isinstance(atb_target_ops[0]["value"], dict)

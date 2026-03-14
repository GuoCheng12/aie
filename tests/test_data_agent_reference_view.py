from pathlib import Path

import numpy as np
import pandas as pd

from src.agents import data_agent as data_agent_mod
from src.core.types import AgentContext


def _dummy_ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(
        run_id="run",
        run_dir=tmp_path / "run",
        case_path=tmp_path / "case.json",
        base_url="http://example.com",
        model="gpt-5.2",
    )


def test_data_agent_reads_selected_reference_view(tmp_path: Path, monkeypatch) -> None:
    view_dir = tmp_path / "views" / "leave_level_2"
    view_dir.mkdir(parents=True, exist_ok=True)
    rdkit_path = view_dir / "rdkit_features.parquet"
    label_path = view_dir / "mechanism_label_map_main_prior.parquet"
    pd.DataFrame({"inchikey": ["IK1"], "canonical_smiles": ["CCO"], "ecfp_2048": [[0, 1, 0]]}).to_parquet(
        rdkit_path, index=False
    )
    pd.DataFrame({"inchikey": ["IK1"], "mechanism_label": ["ICT"]}).to_parquet(label_path, index=False)
    pd.DataFrame({"inchikey": ["IK1"], "mechanism_label": ["clusterluminescence"]}).to_parquet(
        view_dir / "mechanism_label_map.parquet", index=False
    )

    agent = data_agent_mod.DataCaseAgent(data_dir=str(view_dir), top_k=1)
    inputs = agent.build_inputs({"case_id": "c1", "query": {"input_smiles": "CCO"}}, _dummy_ctx(tmp_path))

    read_paths = []
    real_read_parquet = pd.read_parquet

    def fake_read_parquet(path, *args, **kwargs):
        read_paths.append(str(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(data_agent_mod.pd, "read_parquet", fake_read_parquet)
    monkeypatch.setattr(data_agent_mod, "canonicalize_smiles", lambda _: ("CCO", "IKQ"))
    monkeypatch.setattr(data_agent_mod, "compute_ecfp", lambda _: np.array([1, 0, 1], dtype=np.uint8))
    monkeypatch.setattr(data_agent_mod, "search_neighbors", lambda **_: [])

    result = agent.run({"risk_scores": {}, "query": {"input_smiles": "CCO"}}, _dummy_ctx(tmp_path), inputs)
    assert result.status == "success"
    assert str(rdkit_path) in read_paths
    assert str(label_path) in read_paths
    assert all(str(view_dir) in p for p in read_paths)

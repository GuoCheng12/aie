import json
from pathlib import Path

import pandas as pd

from src.agents.structure_agent import StructureAgent
from src.structure.structure_classifier import build_reference_rows


def test_structure_agent_prefers_structure_reference_pool_main_prior(tmp_path: Path) -> None:
    pool = pd.DataFrame(
        [
            {
                "inchikey": "IK1",
                "canonical_smiles": "CCO",
                "mechanism_label": "ICT",
                "morgan_count": json.dumps({1: 2}),
                "feature_morgan_count": json.dumps({3: 1}),
                "murcko_scaffold_smiles": "CC",
                "generic_scaffold_smiles": "CC",
                "descriptor_snapshot": json.dumps({"mw": 46.0}),
                "structure_prior_profile": json.dumps({"reliability": "medium"}),
                "structure_motif_profile": json.dumps({"motif_density": "low"}),
            }
        ]
    )
    pool.to_parquet(tmp_path / "structure_reference_pool_main_prior.parquet", index=False)
    pd.DataFrame(
        [
            {
                "inchikey": "OLD",
                "canonical_smiles": "CCN",
                "mechanism_label": "clusterluminescence",
                "morgan_count": json.dumps({1: 1}),
                "feature_morgan_count": json.dumps({2: 1}),
                "murcko_scaffold_smiles": "CC",
                "generic_scaffold_smiles": "CC",
                "descriptor_snapshot": json.dumps({"mw": 45.0}),
                "structure_prior_profile": json.dumps({"reliability": "low"}),
                "structure_motif_profile": json.dumps({"motif_density": "low"}),
            }
        ]
    ).to_parquet(tmp_path / "structure_reference_pool.parquet", index=False)

    rows = build_reference_rows(data_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["inchikey"] == "IK1"

    agent = StructureAgent(data_dir=str(tmp_path))
    loaded = agent._load_reference_rows()
    assert len(loaded) == 1
    assert loaded[0]["canonical_smiles"] == "CCO"

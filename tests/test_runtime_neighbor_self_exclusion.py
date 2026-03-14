import numpy as np
import pandas as pd

from src.cases.create_case_from_smiles import search_neighbors


def test_search_neighbors_excludes_same_inchikey_and_same_canonical_smiles() -> None:
    rdkit_df = pd.DataFrame(
        [
            {"inchikey": "IK_SELF", "canonical_smiles": "CCO", "ecfp_2048": np.array([1, 0, 1], dtype=np.uint8)},
            {"inchikey": "IK_DUP", "canonical_smiles": "CCO", "ecfp_2048": np.array([1, 0, 1], dtype=np.uint8)},
            {"inchikey": "IK_OTHER", "canonical_smiles": "CCN", "ecfp_2048": np.array([0, 1, 1], dtype=np.uint8)},
        ]
    )
    label_map = pd.DataFrame(
        [
            {"inchikey": "IK_SELF", "mechanism_label": "ICT"},
            {"inchikey": "IK_DUP", "mechanism_label": "TICT"},
            {"inchikey": "IK_OTHER", "mechanism_label": "ESIPT"},
        ]
    )

    neighbors = search_neighbors(
        query_fp=np.array([1, 0, 1], dtype=np.uint8),
        query_inchikey="IK_SELF",
        query_canonical_smiles="CCO",
        rdkit_df=rdkit_df,
        label_map=label_map,
        k=5,
    )
    ids = [row["neighbor_inchikey"] for row in neighbors]
    assert "IK_SELF" not in ids
    assert "IK_DUP" not in ids
    assert ids == ["IK_OTHER"]

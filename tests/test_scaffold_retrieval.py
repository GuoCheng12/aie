from src.structure.feature_morgan import compute_feature_morgan_count
from src.structure.scaffold_retrieval import compute_scaffold_neighbors, extract_murcko_scaffold


def test_extract_murcko_scaffold_returns_scaffold_strings():
    out = extract_murcko_scaffold("c1ccc(cc1)c2ccccc2")
    assert out["murcko_scaffold_smiles"]
    assert out["generic_scaffold_smiles"]


def test_scaffold_neighbors_return_ordered_topk_and_distribution():
    reference_rows = [
        {
            "inchikey": "REF1",
            "canonical_smiles": "c1ccc(cc1)c2ccccc2",
            "mechanism_label": "neutral aromatic",
            "murcko_scaffold_smiles": extract_murcko_scaffold("c1ccc(cc1)c2ccccc2")["murcko_scaffold_smiles"],
            "generic_scaffold_smiles": extract_murcko_scaffold("c1ccc(cc1)c2ccccc2")["generic_scaffold_smiles"],
            "scaffold_feature_morgan_count": compute_feature_morgan_count(
                extract_murcko_scaffold("c1ccc(cc1)c2ccccc2")["generic_scaffold_smiles"]
            ),
        },
        {
            "inchikey": "REF2",
            "canonical_smiles": "Oc1ccccc1N",
            "mechanism_label": "ICT",
            "murcko_scaffold_smiles": extract_murcko_scaffold("Oc1ccccc1N")["murcko_scaffold_smiles"],
            "generic_scaffold_smiles": extract_murcko_scaffold("Oc1ccccc1N")["generic_scaffold_smiles"],
            "scaffold_feature_morgan_count": compute_feature_morgan_count(
                extract_murcko_scaffold("Oc1ccccc1N")["generic_scaffold_smiles"]
            ),
        },
    ]
    out = compute_scaffold_neighbors("c1ccc(cc1)c2ccccc2", reference_rows, topk=2, target_inchikey="TARGET")
    assert out["murcko_topk"]
    assert out["murcko_topk"][0]["mechanism_label"] == "neutral aromatic"
    assert out["murcko_topk"][0]["sim"] >= 0.9
    assert "neutral aromatic" in (out["scaffold_neighbor_label_distribution"] or {})

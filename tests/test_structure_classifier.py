from pathlib import Path

from src.data.rdkit_descriptors import compute_basic_descriptors
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import extract_murcko_scaffold
from src.structure.structure_classifier import (
    load_structure_classifier,
    predict_structure_candidate_distribution,
    train_structure_classifier,
)


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


def test_structure_classifier_train_load_and_predict(monkeypatch, tmp_path: Path):
    rows = [
        _row("Oc1ccccc1C=Nc2ccccc2", "ESIPT", "IK1"),
        _row("Oc1ccccc1C=NC2=CC=CC=C2", "ESIPT", "IK2"),
        _row("c1ccc(cc1)c2ccccc2", "neutral aromatic", "IK3"),
        _row("c1ccc(cc1)C(c2ccccc2)c3ccccc3", "neutral aromatic", "IK4"),
    ]
    monkeypatch.setattr(
        "src.structure.structure_classifier.build_reference_rows",
        lambda data_dir="data": rows,
    )
    outdir = tmp_path / "structure_model"
    result = train_structure_classifier(outdir=outdir)
    assert result["train_rows"] == 4
    assert result["training_history"]
    assert result["training_history"][0]["train_loss"] is not None
    assert result["best_epoch"] is not None
    assert (outdir / "train_history.json").exists()
    bundle = load_structure_classifier(outdir)
    assert bundle is not None
    assert bundle.training_history
    pred = predict_structure_candidate_distribution(bundle, _row("Oc1ccccc1C=Nc2ccccc2", "ESIPT", "IKT"))
    assert pred["version"] == "structure_candidate_dist_v1"
    assert pred["top3"]
    assert pred["top_candidates"]
    assert len(pred["top_candidates"]) >= len(pred["top3"])
    assert pred["calibration"]["method"] in {"sigmoid", "none"}

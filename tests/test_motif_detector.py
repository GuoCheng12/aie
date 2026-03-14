import re

from src.structure.motif_detector import detect_structure_motifs


def test_motif_detector_returns_generic_structure_facts_only():
    profile = detect_structure_motifs("Oc1ccccc1C=Nc2ccccc2")
    assert profile["version"] == "structure_motif_v1"
    assert profile["intramolecular_hbond_motif"] in {"none", "possible", "likely"}
    assert profile["tautomerizable_motif"] in {"none", "possible", "likely"}
    assert profile["donor_acceptor_path_strength"] in {"weak", "mid", "strong"}
    assert profile["aromatic_scaffold_type"] in {"simple", "extended", "fused", "mixed"}
    assert profile["flexibility_regime"] in {"low", "mid", "high"}
    joined = " ".join(str(x) for x in (profile.get("notes") or [])).lower()
    assert re.search(r"\bict\b", joined) is None
    assert re.search(r"\btict\b", joined) is None
    assert re.search(r"\besipt\b", joined) is None
    assert "neutral aromatic" not in joined

import json

from src.reasoning.structure_prior_profile import compute_structure_prior_profile


def test_structure_prior_profile_is_compact_and_generic():
    profile = compute_structure_prior_profile(
        "Oc1ccccc1N",
        {
            "n_rotatable_bonds": 1,
            "n_hbd": 2,
            "n_hba": 1,
            "n_rings": 1,
            "n_aromatic_rings": 1,
            "tpsa": 43.0,
            "logp": 1.2,
            "n_heavy_atoms": 8,
        },
    )
    assert profile["version"] == "structure_prior_v1"
    assert profile["donor_acceptor_topology"] in {"weak", "mixed", "strong", "unknown"}
    assert profile["intramolecular_hbond_candidates"] in {"none", "possible", "likely", "unknown"}
    assert profile["aromatic_core_density"] in {"low", "mid", "high", "unknown"}
    assert profile["flexibility_proxy"] in {"low", "mid", "high", "unknown"}
    assert profile["conjugation_proxy"] in {"low", "mid", "high", "unknown"}
    assert profile["reliability"] in {"low", "high"}
    notes = " ".join(profile.get("notes") or []).lower()
    assert "esipt" not in notes
    assert "neutral aromatic" not in notes
    assert len(json.dumps(profile, ensure_ascii=False).encode("utf-8")) < 2048


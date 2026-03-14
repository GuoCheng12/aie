import re

from src.reasoning.structure_prior_profile import compute_structure_prior_profile


def test_structure_prior_text_is_mechanism_neutral():
    profile = compute_structure_prior_profile(
        "Oc1c(/N=N/c2ccccc2)ccc(O)c1",
        {
            "n_rotatable_bonds": 2,
            "n_hbd": 2,
            "n_hba": 3,
            "n_rings": 2,
            "n_aromatic_rings": 2,
            "tpsa": 54.0,
            "logp": 2.1,
            "n_heavy_atoms": 15,
        },
    )
    joined = " ".join(
        [str(profile.get("overall_structure_prior") or "")] + [str(x) for x in (profile.get("notes") or [])]
    ).lower()
    assert "redistribution-prone" not in joined
    assert "ct-prone" not in joined
    assert "neutral aromatic" not in joined
    assert re.search(r"\bict\b", joined) is None
    assert re.search(r"\btict\b", joined) is None
    assert re.search(r"\besipt\b", joined) is None

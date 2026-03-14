from src.eval.zero_shot_baseline import build_zero_shot_prompt


def test_zero_shot_prompt_contains_only_smiles_labels_and_contract() -> None:
    prompt = build_zero_shot_prompt("CCO")
    system = prompt["system"]
    user = prompt["user"]

    assert "SMILES: CCO" in user
    assert "Allowed labels:" in user
    assert "TICT" in user
    assert "neutral aromatic" in user

    lowered = (system + "\n" + user).lower()
    forbidden = [
        "candidate mechanisms",
        "definition",
        "few-shot",
        "example",
    ]
    for token in forbidden:
        assert token not in lowered

    assert "do not assume access to any external evidence" in lowered
    assert "LABEL: <label>" in system

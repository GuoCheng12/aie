from src.eval.zero_shot_baseline import parse_zero_shot_label


def test_parse_zero_shot_label_accepts_explicit_label_line() -> None:
    assert parse_zero_shot_label("LABEL: ICT") == "ICT"
    assert parse_zero_shot_label("label: neutral aromatic") == "neutral aromatic"


def test_parse_zero_shot_label_accepts_single_canonical_fallback() -> None:
    assert parse_zero_shot_label("I choose ESIPT") == "ESIPT"


def test_parse_zero_shot_label_rejects_ambiguous_or_unknown_output() -> None:
    assert parse_zero_shot_label("It might be ICT or TICT") is None
    assert parse_zero_shot_label("LABEL: clusterluminescence") == "unknown"
    assert parse_zero_shot_label("") is None

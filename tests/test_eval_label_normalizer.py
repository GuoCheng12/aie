from src.eval.label_normalizer import normalize_label


def test_label_normalizer_aliases_and_case() -> None:
    assert normalize_label("tict") == "TICT"
    assert normalize_label("tict-like") == "TICT"
    assert normalize_label("ICT-like") == "ICT"
    assert normalize_label("neutral aromatic") == "neutral aromatic"


def test_label_normalizer_locked_unknown_mappings() -> None:
    assert normalize_label("clusterluminescence") == "unknown"
    assert normalize_label("ESIPT+ICT/TICT") == "unknown"
    assert normalize_label("") == "unknown"
    assert normalize_label(None) == "unknown"


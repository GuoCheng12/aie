from src.eval.evaluate_testset import extract_pred_label


def test_extract_pred_label_primary_path() -> None:
    case_json = {
        "master_reasoning": {
            "mechanism_claim": {
                "primary_hypothesis": {
                    "mechanism_label": "ICT",
                }
            }
        }
    }
    assert extract_pred_label(case_json) == "ICT"


def test_extract_pred_label_fallback_path() -> None:
    case_json = {
        "reasoning": {
            "master_reasoning": {
                "mechanism_claim": {
                    "primary_hypothesis": {
                        "mechanism_label": "TICT",
                    }
                }
            }
        }
    }
    assert extract_pred_label(case_json) == "TICT"


def test_extract_pred_label_missing() -> None:
    assert extract_pred_label({}) is None
    assert extract_pred_label({"master_reasoning": {}}) is None


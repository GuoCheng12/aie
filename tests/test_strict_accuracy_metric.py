from src.eval.evaluate_mechanism_benchmark import _strict_summary


def test_strict_accuracy_counts_missing_and_failed_as_wrong() -> None:
    rows = [
        {"status": "ok", "y_true": "ICT", "y_pred": "ICT"},
        {"status": "ok", "y_true": "TICT", "y_pred": "ICT"},
        {"status": "failed_run", "y_true": "ESIPT", "y_pred": None},
        {"status": "missing_pred", "y_true": "other", "y_pred": None},
        {"status": "missing_gt", "y_true": "unknown", "y_pred": "ICT"},
    ]
    summary = _strict_summary(rows)
    assert summary["counts"]["valid_gt_rows"] == 4
    assert summary["metrics"]["strict_top1_accuracy"] == 0.25
    assert summary["metrics"]["strict_top1_accuracy_including_other"] == 0.25
    assert summary["metrics"]["strict_top1_accuracy_excluding_other_gt"] == 0.333333
    assert summary["counts"]["status_counts"]["failed_run"] == 1
    assert summary["counts"]["status_counts"]["missing_pred"] == 1

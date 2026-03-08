from pathlib import Path

from src.core.io import save_json
from src.orchestration.run_one import _build_quick_view


def test_quick_view_contract_fields(tmp_path: Path):
    eval_report_path = tmp_path / "round_00.eval_report.json"
    save_json(
        eval_report_path,
        {
            "stop_recommendation": {
                "should_stop": True,
                "reason_code": "stagnation_no_new_evidence",
            }
        },
    )

    case_json = {
        "current_gate": {"state": "ready_conservative", "reasoning_mode": "conservative"},
        "master_reasoning": {
            "mechanism_claim": {
                "primary_hypothesis": {"mechanism_label": "unknown"},
                "confidence": 0.53,
            }
        },
        "master_reasoning_used_evidence_ids": ["E31", "E32", "E24"],
    }
    run_summary = {
        "iterative": {
            "executed_rounds": 2,
            "rounds": [
                {"round_index": 0},
                {"round_index": 1, "eval_report_path": str(eval_report_path)},
            ],
        }
    }

    out = _build_quick_view(
        case_json=case_json,
        case_id="CASE-QV",
        run_id="run-qv",
        run_time="2026-03-04T12:00:00Z",
        run_summary=run_summary,
        case_path=tmp_path / "case.json",
        run_summary_path=tmp_path / "run_summary.json",
        rounds_dir=tmp_path / "rounds",
        llm_dir=tmp_path / "llm",
    )

    assert out["case_id"] == "CASE-QV"
    assert out["run_id"] == "run-qv"
    assert out["final_label"] == "unknown"
    assert out["final_confidence"] == 0.53
    assert out["final_gate"]["state"] == "ready_conservative"
    assert out["rounds_executed"] == 2
    assert out["stop_recommendation"]["reason_code"] == "stagnation_no_new_evidence"
    assert out["used_evidence_ids_top"] == ["E31", "E32", "E24"]

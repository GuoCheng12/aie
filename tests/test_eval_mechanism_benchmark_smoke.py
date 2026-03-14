import json
from pathlib import Path

from src.eval import evaluate_mechanism_benchmark


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def responses_text(self, *, instructions, input_text, max_output_tokens=None, temperature=None):
        label = "ICT" if "CC" in input_text else "unknown"
        return {
            "request": {"instructions": instructions, "input": input_text},
            "response": {"id": "fake"},
            "text": f"LABEL: {label}",
        }


def _write_test_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "id,code,SMILES,reference,molecular_weight,emission_solid,emission_aggr,features_id,mechanism_id,inchikey",
                "1,A,CC,ref,1,,,,ICT,IK1",
                "2,B,CCC,ref,1,,,,TICT,IK2",
                "3,C,CCN,ref,1,,,,,IK3",
                "4,D,CO,ref,1,,,,other,IK4",
                "5,E,N,ref,1,,,,ESIPT,IK5",
            ]
        ),
        encoding="utf-8",
    )


def test_eval_mechanism_benchmark_smoke(tmp_path, monkeypatch) -> None:
    test_csv = tmp_path / "test.csv"
    _write_test_csv(test_csv)

    def fake_run_one(ns):
        idx = int(ns.row_index)
        if idx == 4:
            raise RuntimeError("synthetic-run-failure")
        case_id = f"CASE{idx}"
        case_path = Path(ns.outdir) / f"{case_id}.json"
        case_path.parent.mkdir(parents=True, exist_ok=True)
        if idx == 3:
            case_json = {"case_id": case_id, "master_reasoning_status": "failed_schema_validation"}
        else:
            pred = {0: "ICT", 1: "TICT", 2: "unknown"}.get(idx, "unknown")
            case_json = {
                "case_id": case_id,
                "master_reasoning": {
                    "mechanism_claim": {
                        "primary_hypothesis": {"mechanism_label": pred}
                    }
                },
            }
        case_path.write_text(json.dumps(case_json), encoding="utf-8")
        run_summary_path = Path(ns.artifacts_dir) / f"summary_{idx}.json"
        run_summary_path.parent.mkdir(parents=True, exist_ok=True)
        run_summary_path.write_text("{}", encoding="utf-8")
        return {
            "run_id": f"run-{idx}",
            "case_id": case_id,
            "case_path": str(case_path),
            "run_summary_path": str(run_summary_path),
            "primary_output_dir": str(Path(ns.artifacts_dir) / f"run_{idx}"),
        }

    monkeypatch.setattr(evaluate_mechanism_benchmark.evaluate_testset, "runtime_run_one", fake_run_one)
    monkeypatch.setattr(evaluate_mechanism_benchmark, "run_zero_shot_label", lambda **kwargs: {
        "prompt": {"system": "s", "user": "u"},
        "llm_result": {"text": "LABEL: ICT"},
        "label": "ICT",
        "error": None,
    })

    args = evaluate_mechanism_benchmark.build_parser().parse_args(
        [
            "--test-csv", str(test_csv),
            "--outdir", str(tmp_path / "eval_compare"),
            "--eval-id", "smoke",
            "--protocol", "compare",
            "--max-rows", "5",
            "--temperature", "0.0",
            "--no-show-progress",
        ]
    )
    report = evaluate_mechanism_benchmark.run_benchmark(args)

    eval_dir = Path(report["artifacts"]["eval_dir"])
    assert (eval_dir / "multi_agent" / "predictions.csv").exists()
    assert (eval_dir / "zero_shot" / "predictions.csv").exists()
    assert (eval_dir / "comparison" / "predictions_merged.csv").exists()
    assert (eval_dir / "comparison" / "evaluation_report.json").exists()
    assert (eval_dir / "comparison" / "evaluation_report.md").exists()
    assert report["comparison"]["disagreement_count"] >= 0

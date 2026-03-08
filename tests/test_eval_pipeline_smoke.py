import json
from pathlib import Path

from src.eval import evaluate_testset


def _write_test_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "id,code,SMILES,reference,molecular_weight,emission_solid,emission_aggr,features_id,mechanism_id,inchikey",
                "1,A,CC,ref,1,,,,ICT,IK1",
                "2,B,CCC,ref,1,,,,TICT,IK2",
                "3,C,CCCC,ref,1,,,,ESIPT,IK3",
                "4,D,CCN,ref,1,,,,other,IK4",
                "5,E,CCO,ref,1,,,,,IK5",
            ]
        ),
        encoding="utf-8",
    )


def test_eval_pipeline_smoke(tmp_path, monkeypatch) -> None:
    test_csv = tmp_path / "test.csv"
    _write_test_csv(test_csv)

    def fake_run_one(ns):
        idx = int(ns.row_index)
        if idx == 2:
            raise RuntimeError("synthetic-run-failure")
        case_id = f"CASE{idx}"
        case_path = Path(ns.outdir) / f"{case_id}.json"
        case_path.parent.mkdir(parents=True, exist_ok=True)
        if idx == 3:
            case_json = {"case_id": case_id}
        else:
            pred = {0: "ICT", 1: "TICT", 4: "ICT"}.get(idx, "unknown")
            case_json = {
                "case_id": case_id,
                "master_reasoning": {
                    "mechanism_claim": {
                        "primary_hypothesis": {
                            "mechanism_label": pred,
                        }
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

    monkeypatch.setattr(evaluate_testset, "runtime_run_one", fake_run_one)

    args = evaluate_testset.build_parser().parse_args(
        [
            "--test-csv",
            str(test_csv),
            "--outdir",
            str(tmp_path / "eval"),
            "--max-rows",
            "5",
            "--temperature",
            "0.0",
        ]
    )
    report = evaluate_testset.run_benchmark(args)

    artifacts = report["artifacts"]
    predictions_path = Path(artifacts["predictions_csv"])
    report_json_path = Path(artifacts["evaluation_report_json"])
    report_md_path = Path(artifacts["evaluation_report_md"])
    assert predictions_path.exists()
    assert report_json_path.exists()
    assert report_md_path.exists()

    status_counts = (report.get("results") or {}).get("counts", {}).get("status", {})
    assert status_counts.get("ok", 0) >= 1
    assert status_counts.get("failed_run", 0) == 1
    assert status_counts.get("missing_pred", 0) == 1
    assert status_counts.get("missing_gt", 0) == 1


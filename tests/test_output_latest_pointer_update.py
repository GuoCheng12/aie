import json
from pathlib import Path

from src.core.io import save_json
from src.core.output_layout import (
    OUTPUT_LAYOUT_CASE_CENTRIC,
    plan_output_layout,
    refresh_latest_case_view,
    write_latest_pointer,
)


def _write_run_payloads(run_dir: Path, tag: str) -> tuple[Path, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "llm").mkdir(parents=True, exist_ok=True)
    (run_dir / "rounds").mkdir(parents=True, exist_ok=True)
    save_json(run_dir / "llm" / "trace.json", {"tag": tag})
    save_json(run_dir / "rounds" / "round_00.eval_report.json", {"tag": tag})

    case_path = run_dir / "case_source.json"
    run_summary_path = run_dir / "run_summary.json"
    quick_view_path = run_dir / "quick_view.json"
    save_json(case_path, {"case_id": "CASE-1", "tag": tag})
    save_json(run_summary_path, {"run_id": f"run-{tag}", "tag": tag})
    save_json(quick_view_path, {"run_id": f"run-{tag}", "tag": tag})
    return case_path, run_summary_path, quick_view_path


def test_latest_pointer_updates_to_newest_run(tmp_path: Path):
    artifacts_root = tmp_path / "artifacts"
    llm_root = artifacts_root / "llm_responses"

    first = plan_output_layout(
        artifacts_root=artifacts_root,
        llm_response_root=llm_root,
        case_id="CASE-1",
        run_id="11111111111111111111111111111111",
        layout=OUTPUT_LAYOUT_CASE_CENTRIC,
        timestamp_format="utc_compact",
        write_legacy_run_view=False,
    )
    c1, s1, q1 = _write_run_payloads(first.run_dir, "first")
    refresh_latest_case_view(paths=first, case_path=c1, run_summary_path=s1, quick_view_path=q1)
    write_latest_pointer(
        paths=first,
        latest_payload={"case_id": "CASE-1", "run_id": "1111", "run_name": first.run_name},
    )

    second = plan_output_layout(
        artifacts_root=artifacts_root,
        llm_response_root=llm_root,
        case_id="CASE-1",
        run_id="22222222222222222222222222222222",
        layout=OUTPUT_LAYOUT_CASE_CENTRIC,
        timestamp_format="utc_compact",
        write_legacy_run_view=False,
    )
    c2, s2, q2 = _write_run_payloads(second.run_dir, "second")
    refresh_latest_case_view(paths=second, case_path=c2, run_summary_path=s2, quick_view_path=q2)
    write_latest_pointer(
        paths=second,
        latest_payload={"case_id": "CASE-1", "run_id": "2222", "run_name": second.run_name},
    )

    latest_json = json.loads((artifacts_root / "cases" / "CASE-1" / "latest.json").read_text(encoding="utf-8"))
    latest_case = json.loads((artifacts_root / "cases" / "CASE-1" / "latest" / "case.json").read_text(encoding="utf-8"))
    assert latest_json["run_id"] == "2222"
    assert latest_case["tag"] == "second"

import json
from pathlib import Path

from src.core.output_layout import OUTPUT_LAYOUT_CASE_CENTRIC, plan_output_layout, update_history_index


def test_history_retention_prunes_old_runs(tmp_path: Path):
    artifacts_root = tmp_path / "artifacts"
    llm_root = artifacts_root / "llm_responses"
    case_id = "CASE-KEEP"
    retain = 2

    run_ids = [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "cccccccccccccccccccccccccccccccc",
    ]

    last_paths = None
    for idx, run_id in enumerate(run_ids):
        paths = plan_output_layout(
            artifacts_root=artifacts_root,
            llm_response_root=llm_root,
            case_id=case_id,
            run_id=run_id,
            layout=OUTPUT_LAYOUT_CASE_CENTRIC,
            timestamp_format="utc_compact",
            write_legacy_run_view=False,
        )
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        (paths.run_dir / "marker.txt").write_text(str(idx), encoding="utf-8")
        update_history_index(
            paths=paths,
            run_record={
                "run_id": run_id,
                "run_name": paths.run_name,
                "run_time": paths.run_time_iso,
                "status": "ok",
                "run_dir": str(paths.run_dir),
            },
            retain_runs=retain,
        )
        last_paths = paths

    assert last_paths is not None
    history_path = artifacts_root / "cases" / case_id / "history_index.json"
    payload = json.loads(history_path.read_text(encoding="utf-8"))
    runs = payload.get("runs") or []
    assert len(runs) == retain
    kept_ids = [str(x.get("run_id")) for x in runs]
    assert kept_ids == [run_ids[2], run_ids[1]]

    dropped_dir = artifacts_root / "cases" / case_id / "runs"
    dropped_matches = [p for p in dropped_dir.iterdir() if p.is_dir() and run_ids[0][:8] in p.name]
    assert not dropped_matches

import argparse
import csv
import json
from pathlib import Path

from src.orchestration import run_one as run_one_mod
from src.orchestration.run_status import atomic_write_json


def test_run_one_preserves_status_errors_at_run_end(monkeypatch, tmp_path: Path):
    test_csv = tmp_path / "test.csv"
    with test_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "code", "SMILES", "reference", "inchikey"])
        w.writeheader()
        w.writerow({"id": "1", "code": "DEMO", "SMILES": "C", "reference": "r", "inchikey": "IK-DEMO"})

    monkeypatch.setattr(run_one_mod, "build_setup_agents", lambda: [])

    def _fake_run_iterative_rounds(*, case_json, ctx, mode, max_rounds, start_profile, status_path, evaluator_use_llm, master_model, master_reasoning_effort, evaluator_model, evaluator_reasoning_effort):
        _ = mode, max_rounds, start_profile, evaluator_use_llm, master_model, master_reasoning_effort, evaluator_model, evaluator_reasoning_effort
        atomic_write_json(
            Path(status_path),
            {
                "run_id": ctx.run_id,
                "case_id": case_json.get("case_id"),
                "round_index": 0,
                "max_rounds": 4,
                "active_profile": "R0",
                "round_runner_mode": "dryrun_then_commit",
                "stage": "validate",
                "last_event": "validation_failed",
                "last_updated_at": "2026-03-01T00:00:00Z",
                "errors": [{"code": "llm_error", "path": "$", "detail": "broken_json"}],
                "round_dir": str((Path(ctx.llm_response_dir) / ctx.run_id / "rounds").resolve()),
                "latest_eval_report": None,
            },
        )
        return case_json, {
            "mode": "dryrun_then_commit",
            "max_rounds": 4,
            "executed_rounds": 1,
            "stopped": True,
            "stop_reason": "stagnation_no_information_gain",
            "rounds": [{"round_index": 0, "active_profile": "R0", "eval_report_path": "x"}],
        }

    monkeypatch.setattr(run_one_mod, "run_iterative_rounds", _fake_run_iterative_rounds)

    args = argparse.Namespace(
        test_csv=str(test_csv),
        row_index=0,
        code=None,
        smiles=None,
        offline_pdf=None,
        run_lane="atb_cache_only",
        emit_stage_snapshots=False,
        stage_snapshots_dir=str(tmp_path / "snapshots"),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_response_dir=str(tmp_path / "llm_responses"),
        outdir=str(tmp_path / "cases"),
        base_url="http://example/v1",
        model="gpt-test",
        llm_api_key_env="OPENAI_API_KEY",
        llm_max_output_tokens=512,
        llm_reasoning_effort="xhigh",
        mineru_bin="mineru",
        mineru_output_root=str(tmp_path / "mineru_out"),
        mineru_backend="hybrid-auto-engine",
        mineru_method=None,
        mineru_lang=None,
        mineru_start_page=None,
        mineru_end_page=None,
        mineru_timeout_sec=120,
        force=False,
        iterative=True,
        round_runner_mode="dryrun_then_commit",
        max_rounds=4,
        round_start_profile="R0",
        evaluator_use_llm=True,
        evaluator_model=None,
        evaluator_reasoning_effort=None,
    )

    out = run_one_mod.run_one(args)
    _ = out
    status = json.loads((Path(tmp_path / "artifacts" / "run_status.json")).read_text(encoding="utf-8"))
    assert status.get("stage") == "run_end"
    assert isinstance(status.get("errors"), list)
    assert status.get("errors"), "errors should be preserved after run_end"

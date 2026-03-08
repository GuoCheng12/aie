import argparse
import json

import src.cli as cli


def test_case_run_command_invokes_release_runtime(monkeypatch, capsys):
    captured = {}

    def _fake_run_case_run(ns):
        captured["ns"] = ns
        return {"ok": True, "run_lane": ns.run_lane, "case_path": "cases/x.json"}

    monkeypatch.setattr(cli, "_run_case_run", _fake_run_case_run)

    args = argparse.Namespace(
        test_csv="data/test.csv",
        row_index=0,
        code=None,
        smiles=None,
        offline_pdf=None,
        run_lane="atb_cache_only",
        emit_stage_snapshots=False,
        stage_snapshots_dir="cases/stage_snapshots",
        artifacts_dir="artifacts/multi_agent",
        outdir="cases/multi_agent",
        base_url="http://35.220.164.252:3888/v1",
        model="gpt-5.1",
        llm_api_key_env="OPENAI_API_KEY",
        llm_max_output_tokens=1500,
        llm_reasoning_effort=None,
        mineru_bin="third_party/MinerU/.venv/bin/mineru",
        mineru_output_root="third_party/MinerU/output",
        mineru_backend="hybrid-auto-engine",
        mineru_method=None,
        mineru_lang=None,
        mineru_start_page=None,
        mineru_end_page=None,
        mineru_timeout_sec=1200,
        force=False,
    )

    cli.case_run_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert captured["ns"].run_lane == "atb_cache_only"

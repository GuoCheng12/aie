import argparse
import json

import src.cli as cli


def test_case_e2e_atb_alias_forwards_to_case_run(monkeypatch, capsys):
    captured = {}

    def _fake_run_case_run(ns):
        captured["ns"] = ns
        return {
            "ok": True,
            "run_lane": ns.run_lane,
            "snapshots": {
                "data_agent_case": "a.json",
                "chem_agent_case": "b.json",
                "ready_agent_case": "c.json",
            },
        }

    monkeypatch.setattr(cli, "_run_case_run", _fake_run_case_run)

    args = argparse.Namespace(
        code="DBA-AM",
        smiles=None,
        test_csv="data/test.csv",
        smiles_col="SMILES",
        k=10,
        outdir="cases/test_inputs",
        snapshots_dir="cases/stage_snapshots",
        require_atb_success=True,
        cache_dir="cache/atb",
    )

    cli.case_e2e_atb_command(args)
    out = json.loads(capsys.readouterr().out)

    assert out["ok"] is True
    assert captured["ns"].run_lane == "atb_cache_only"
    assert captured["ns"].emit_stage_snapshots is True

import argparse
import json

import src.cli as cli


def test_case_e2e_alias_forwards_to_case_run(monkeypatch, capsys):
    captured = {}

    def _fake_run_case_run(ns):
        captured["ns"] = ns
        return {"ok": True, "run_lane": ns.run_lane, "case_path": "cases/x.json"}

    monkeypatch.setattr(cli, "_run_case_run", _fake_run_case_run)

    args = argparse.Namespace(
        code="DBA-AM",
        smiles=None,
        test_csv="data/test.csv",
        smiles_col="SMILES",
        pdf="/tmp/DBA-AM.pdf",
        k=10,
        outdir="cases/test_inputs",
        artifacts_dir="artifacts/e2e",
        artifact_mode="final_case_only",
        mode="offline_pdf",
        force=False,
        extractor_mode="mineru_llm",
        extractor_name="mineru_offline_adapter",
        extractor_version="0.1.0",
        extractor_config_json="",
        normalizer_config_json="",
        mapping_version="e0_v2",
        pdf_page_selection_json="",
        mineru_bin="third_party/MinerU/.venv/bin/mineru",
        mineru_output_root="third_party/MinerU/output",
        mineru_backend="hybrid-auto-engine",
        mineru_method=None,
        mineru_lang=None,
        mineru_start_page=None,
        mineru_end_page=None,
        mineru_timeout_sec=1200,
        llm_base_url="http://35.220.164.252:3888/v1",
        llm_model="deepseek-v3.2",
        llm_api_key_env="OPENAI_API_KEY",
        llm_max_output_tokens=1500,
        llm_reasoning_effort=None,
        llm_prompt_version="mineru_llm_prompt_v1",
        llm_schema_version="mineru_llm_candidates_v1",
        writeback_evidence_table=False,
        evidence_table_path="data/evidence_table.parquet",
    )

    cli.case_e2e_command(args)
    out = json.loads(capsys.readouterr().out)

    assert out["ok"] is True
    assert captured["ns"].run_lane == "offline_pdf"
    assert captured["ns"].code == "DBA-AM"
    assert captured["ns"].offline_pdf == "/tmp/DBA-AM.pdf"

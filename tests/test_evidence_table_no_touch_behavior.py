import json
from pathlib import Path

from src.agents.base import CaseAgent
from src.core.io import save_json
from src.core.types import AgentContext, AgentResult
from src.orchestration.run_one import run_one


class _FakeDataAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(patch=[{"op": "replace", "path": "/query/canonical_smiles", "value": "C"}], status="success")


def test_case_run_does_not_call_example_evidence_writer(monkeypatch, tmp_path: Path):
    # Behavior-level guard: this writer is forbidden in E0 and should never be touched by case-run runtime.
    import src.cases.example_a_first_runner as e0_runner
    import src.orchestration.run_one as run_one_mod

    def _boom(*, evidence_table_path, rows):
        raise AssertionError("forbidden_example_evidence_writer_called")

    monkeypatch.setattr(e0_runner, "_write_evidence_table_rows", _boom)
    monkeypatch.setattr(run_one_mod, "build_default_agents", lambda: [_FakeDataAgent()])

    test_csv = tmp_path / "test.csv"
    test_csv.write_text(
        "id,code,SMILES,reference,inchikey\n"
        "1,DBA-AM,C,J. Mater. Chem. C,AAAA-BBBB\n",
        encoding="utf-8",
    )

    args = type(
        "Args",
        (),
        {
            "test_csv": str(test_csv),
            "row_index": 0,
            "code": None,
            "smiles": None,
            "offline_pdf": None,
            "run_lane": "atb_cache_only",
            "emit_stage_snapshots": False,
            "stage_snapshots_dir": str(tmp_path / "snaps"),
            "artifacts_dir": str(tmp_path / "artifacts"),
            "outdir": str(tmp_path / "cases"),
            "base_url": "http://example/v1",
            "model": "gpt-test",
            "llm_api_key_env": "OPENAI_API_KEY",
            "llm_max_output_tokens": 512,
            "llm_reasoning_effort": None,
            "mineru_bin": "mineru",
            "mineru_output_root": str(tmp_path / "mineru_out"),
            "mineru_backend": "hybrid-auto-engine",
            "mineru_method": None,
            "mineru_lang": None,
            "mineru_start_page": None,
            "mineru_end_page": None,
            "mineru_timeout_sec": 120,
            "force": False,
        },
    )()

    out = run_one(args)
    assert out["ok"] is True
    case = json.loads(Path(out["case_path"]).read_text(encoding="utf-8"))
    assert case["query"]["canonical_smiles"] == "C"


def test_safe_fs_blocks_evidence_table_write(tmp_path: Path):
    blocked = Path("data/evidence_table.parquet")
    try:
        save_json(blocked, {"forbidden": True})
    except PermissionError as exc:
        assert "safe_fs_write_denied" in str(exc)
    else:
        raise AssertionError("expected PermissionError for evidence_table write")

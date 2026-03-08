import argparse
import csv
import json
from pathlib import Path

from src.agents.base import CaseAgent
from src.agents.ready_agent import ReadyAgent
from src.core.types import AgentResult
from src.orchestration import run_one as run_one_mod


class _FakeDataAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/neighbors", "/risk_scores/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "replace", "path": "/query/canonical_smiles", "value": "C"},
                {"op": "replace", "path": "/query/inchikey", "value": "TEST-IK"},
                {"op": "replace", "path": "/neighbors", "value": []},
                {"op": "add", "path": "/risk_scores/top1_sim", "value": 0.88},
                {"op": "add", "path": "/risk_scores/mean_topk_sim", "value": 0.81},
                {"op": "add", "path": "/risk_scores/neighbor_gap", "value": 0.07},
                {"op": "add", "path": "/risk_scores/novelty_struct", "value": 0.12},
                {"op": "add", "path": "/risk_scores/mechanism_entropy", "value": 0.3},
                {"op": "add", "path": "/risk_scores/mechanism_hint", "value": "ict"},
                {"op": "add", "path": "/risk_scores/hint_confidence", "value": 0.9},
            ],
            status="success",
        )


class _FakeChemAtbSuccess(CaseAgent):
    name = "chem_agent"
    version = "test"
    allowed_patch_prefixes = (
        "/evidence_readiness/atb/",
        "/evidence_readiness/literature/",
        "/evidence_readiness/experiment/",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id"), "run_lane": ctx.run_lane}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "add", "path": "/evidence_readiness/atb/cache_status", "value": "success"},
                {"op": "add", "path": "/evidence_readiness/atb/request_status", "value": "done"},
                {"op": "add", "path": "/evidence_readiness/literature/status", "value": "not_started"},
                {"op": "add", "path": "/evidence_readiness/literature/notes", "value": "lane_disabled"},
                {"op": "add", "path": "/evidence_readiness/experiment/status", "value": "not_requested"},
                {"op": "add", "path": "/evidence_readiness/experiment/notes", "value": "lane_disabled"},
            ],
            status="success",
        )


class _FakeReasoningAgent(CaseAgent):
    name = "reasoning_agent"
    version = "test"
    allowed_patch_prefixes = ("/reasoning/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "add", "path": "/reasoning/status", "value": "completed"},
            ],
            status="success",
        )


class _FakeJudgeAgent(CaseAgent):
    name = "judge_agent"
    version = "test"
    allowed_patch_prefixes = ("/post_uq", "/post_uq/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {
                    "op": "add",
                    "path": "/post_uq",
                    "value": {
                        "status": "ok",
                        "confidence": 0.8,
                        "contradictions": [],
                        "missing_evidence": [],
                        "recommended_actions": [],
                    },
                }
            ],
            status="success",
        )


def test_case_run_atb_success_lane(monkeypatch, tmp_path: Path):
    test_csv = tmp_path / "test.csv"
    with test_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "code", "SMILES", "reference", "inchikey"])
        w.writeheader()
        w.writerow(
            {
                "id": "1",
                "code": "DBA-AM",
                "SMILES": "C",
                "reference": "J. Mater. Chem. C",
                "inchikey": "CASE-ATB-IK",
            }
        )

    monkeypatch.setattr(
        run_one_mod,
        "build_default_agents",
        lambda: [
            _FakeDataAgent(),
            _FakeChemAtbSuccess(),
            ReadyAgent(),
            _FakeReasoningAgent(),
            _FakeJudgeAgent(),
            ReadyAgent(),
        ],
    )

    args = argparse.Namespace(
        test_csv=str(test_csv),
        row_index=None,
        code="DBA-AM",
        smiles=None,
        offline_pdf=None,
        run_lane="atb_cache_only",
        emit_stage_snapshots=True,
        stage_snapshots_dir=str(tmp_path / "snapshots"),
        artifacts_dir=str(tmp_path / "artifacts"),
        outdir=str(tmp_path / "cases"),
        base_url="http://example/v1",
        model="gpt-test",
        llm_api_key_env="OPENAI_API_KEY",
        llm_max_output_tokens=512,
        llm_reasoning_effort=None,
        mineru_bin="mineru",
        mineru_output_root=str(tmp_path / "mineru_out"),
        mineru_backend="hybrid-auto-engine",
        mineru_method=None,
        mineru_lang=None,
        mineru_start_page=None,
        mineru_end_page=None,
        mineru_timeout_sec=120,
        force=False,
    )

    out = run_one_mod.run_one(args)
    assert out["ok"] is True
    assert out["run_lane"] == "atb_cache_only"
    assert Path(out["case_path"]).exists()
    assert Path(out["artifacts_dir"]).exists()
    assert "data_agent_case" in out["snapshots"]
    assert "chem_agent_case" in out["snapshots"]
    assert "ready_agent_case" in out["snapshots"]

    case = json.loads(Path(out["case_path"]).read_text(encoding="utf-8"))
    assert len(case.get("agent_runs", [])) == 6
    assert case.get("current_gate", {}).get("state") in {"blocked_input_missing", "needs_manual", "ready_for_reasoning", "ready_conservative"}

    reasoning_step = [s for s in out["steps"] if s["agent"] == "reasoning_agent"][0]
    if reasoning_step["status"] == "skipped":
        assert reasoning_step["status_reason_code"] == "gate_blocked_reasoning"

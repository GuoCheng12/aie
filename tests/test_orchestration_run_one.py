import argparse
import csv
import json
from pathlib import Path

from src.agents.base import CaseAgent
from src.agents.ready_agent import ReadyAgent
from src.core.types import AgentContext, AgentResult
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
                {"op": "replace", "path": "/neighbors", "value": [{"neighbor_inchikey": "N1", "sim": 0.9, "rank": 1, "neighbor_mechanism_label": "ict"}]},
                {"op": "add", "path": "/risk_scores/top1_sim", "value": 0.9},
                {"op": "add", "path": "/risk_scores/mean_topk_sim", "value": 0.9},
                {"op": "add", "path": "/risk_scores/neighbor_gap", "value": 0.1},
                {"op": "add", "path": "/risk_scores/novelty_struct", "value": 0.1},
                {"op": "add", "path": "/risk_scores/mechanism_entropy", "value": 0.2},
                {"op": "add", "path": "/risk_scores/mechanism_hint", "value": "ict"},
                {"op": "add", "path": "/risk_scores/hint_confidence", "value": 0.8},
            ],
            status="success",
        )


class _FakeChemAgent(CaseAgent):
    name = "chem_agent"
    version = "test"
    allowed_patch_prefixes = (
        "/evidence_readiness/atb/",
        "/evidence_readiness/literature/",
        "/target_fields/",
        "/target_fields_provenance/",
        "/evidence_candidates_staging/-",
        "/risk_scores/atb_neighbor_consistency",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/evidence_candidates_staging", "/agent_runs")

    def build_inputs(self, case, ctx):
        return {"case_id": case.get("case_id")}

    def run(self, case, ctx, inputs):
        cand = {
            "candidate_id": "0:0",
            "field": "emission_aggr_nm",
            "normalized_value_nm": 530.0,
            "raw_value": "530",
            "unit": "nm",
            "condition": "aggregation in water fraction",
            "condition_bucket": "aggr",
            "value_source_kind": "table",
            "source_type": "offline_pdf",
            "source_ref": "paper.pdf",
            "source_locator": "Table 1 (page 3)",
            "page": 3,
            "bbox": None,
            "identity_match": "exact",
            "identity_match_confidence": 0.95,
            "confidence": 0.9,
            "verification_status": "verified",
            "rejection_reason": None,
            "run_id": "test",
            "artifact_ref": None,
        }
        return AgentResult(
            patch=[
                {"op": "add", "path": "/evidence_readiness/atb/cache_status", "value": "success"},
                {"op": "add", "path": "/evidence_readiness/atb/request_status", "value": "done"},
                {"op": "add", "path": "/evidence_readiness/literature/status", "value": "found"},
                {"op": "add", "path": "/evidence_readiness/literature/sources", "value": [{"source_ref": "paper.pdf"}]},
                {"op": "add", "path": "/evidence_candidates_staging/-", "value": cand},
                {"op": "add", "path": "/target_fields/emission_aggr_nm", "value": 530.0},
                {
                    "op": "add",
                    "path": "/target_fields_provenance/emission_aggr_nm",
                    "value": {
                        "source_ref": "paper.pdf",
                        "source_locator": "Table 1 (page 3)",
                        "confidence": 0.9,
                        "identity_match": "exact",
                        "identity_match_confidence": 0.95,
                        "condition": "aggregation in water fraction",
                    },
                },
            ],
            status="success",
        )


class _FakeChemAgentOutlier(_FakeChemAgent):
    def run(self, case, ctx, inputs):
        result = super().run(case, ctx, inputs)
        result.patch.append(
            {
                "op": "add",
                "path": "/risk_scores/atb_neighbor_consistency",
                "value": {
                    "enabled": True,
                    "sample_size": 10,
                    "fields_used": ["delta_gap", "delta_dihedral", "delta_volume"],
                    "median": {"delta_gap": 0.1, "delta_dihedral": -1.0, "delta_volume": 0.5},
                    "mad": {"delta_gap": 0.05, "delta_dihedral": 0.4, "delta_volume": 0.2},
                    "z_scores": {"delta_gap": 4.2, "delta_dihedral": 0.3, "delta_volume": 0.2},
                    "outlier_score_max": 4.2,
                    "outlier_score_rss": 2.44,
                    "outlier_dims": ["delta_gap"],
                    "flag": "outlier",
                    "reliability": "high",
                    "thresholds": {"z_max": 3.5, "min_sample_size": 5},
                    "warnings": [],
                    "updated_at": "2026-02-24T00:00:00Z",
                },
            }
        )
        return result


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
                {"op": "add", "path": "/reasoning/master_output", "value": {"summary": "ok", "hypotheses": [], "limitations": []}},
            ],
            status="success",
        )


class _FakeJudgeAgent(CaseAgent):
    name = "judge_agent"
    version = "test"
    allowed_patch_prefixes = ("/post_uq", "/post_uq/", "/action_plan/-", "/agent_runs/-")
    append_only_prefixes = ("/action_plan", "/agent_runs")

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


def test_run_one_integration_with_sample(monkeypatch, tmp_path):
    test_csv = tmp_path / "test.csv"
    with test_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "code", "SMILES", "reference", "inchikey"])
        w.writeheader()
        w.writerow(
            {
                "id": "1",
                "code": "DBA-AM",
                "SMILES": "CCCCN(CCCC)c1ccc(/C=N/C(C#N)=C(N)/C#N)cc1",
                "reference": "J. Mater. Chem. C",
                "inchikey": "FROQVQNFQFCXNO-KSZJGORMSA-N",
            }
        )
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        run_one_mod,
        "build_default_agents",
        lambda: [
            _FakeDataAgent(),
            _FakeChemAgent(),
            ReadyAgent(),
            _FakeReasoningAgent(),
            _FakeJudgeAgent(),
            ReadyAgent(),
        ],
    )

    args = argparse.Namespace(
        test_csv=str(test_csv),
        row_index=0,
        offline_pdf=str(pdf),
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
    case_path = Path(out["case_path"])
    assert case_path.exists()
    case = json.loads(case_path.read_text(encoding="utf-8"))
    assert len(case.get("agent_runs", [])) == 6
    assert case.get("current_gate", {}).get("state") in {"ready_for_reasoning", "ready_conservative"}
    assert case.get("target_fields", {}).get("emission_aggr_nm") == 530.0
    assert (Path(out["artifacts_dir"]) / "run_summary.json").exists()


def test_run_one_ready_agent_uses_outlier_signal(monkeypatch, tmp_path):
    test_csv = tmp_path / "test.csv"
    with test_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "code", "SMILES", "reference", "inchikey"])
        w.writeheader()
        w.writerow(
            {
                "id": "1",
                "code": "OUTLIER-CASE",
                "SMILES": "C",
                "reference": "J. Mater. Chem. C",
                "inchikey": "OUTLIER-IK",
            }
        )
    pdf = tmp_path / "paper.pdf"
    pdf.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        run_one_mod,
        "build_default_agents",
        lambda: [
            _FakeDataAgent(),
            _FakeChemAgentOutlier(),
            ReadyAgent(),
            _FakeReasoningAgent(),
            _FakeJudgeAgent(),
            ReadyAgent(),
        ],
    )

    args = argparse.Namespace(
        test_csv=str(test_csv),
        row_index=0,
        offline_pdf=str(pdf),
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
    case = json.loads(Path(out["case_path"]).read_text(encoding="utf-8"))

    assert case["risk_scores"]["atb_neighbor_consistency"]["flag"] == "outlier"
    assert case["current_gate"]["reasoning_mode"] == "conservative"
    assert "atb_neighbor_outlier" in case["current_gate"]["reason"]


def test_build_initial_case_seeds_dataset_row_emission_fields():
    row = {
        "SMILES": "C",
        "code": "LEVEL1-ROW",
        "reference": "demo",
        "emission_aggr": "520",
        "emission_solid": "565",
    }
    case = run_one_mod._build_initial_case(
        row,
        offline_pdf=None,
        run_lane="atb_cache_only",
        source_ref="/tmp/level1.csv",
        source_locator="row_index=3; code=LEVEL1-ROW",
        reference_index_root="/tmp/views",
        reference_view="leave_level_1",
        difficulty_level=1,
        allow_other_label=False,
    )
    assert case["target_fields"]["emission_aggr_nm"] == 520.0
    assert case["target_fields"]["emission_solid_or_film_nm"] == 565.0
    aggr_prov = case["target_fields_provenance"]["emission_aggr_nm"]
    solid_prov = case["target_fields_provenance"]["emission_solid_or_film_nm"]
    assert aggr_prov["source_type"] == "dataset_row"
    assert aggr_prov["condition"] == "aggregation"
    assert aggr_prov["condition_bucket"] == "aggregation"
    assert solid_prov["condition"] == "solid_or_film"
    assert solid_prov["condition_bucket"] == "solid_or_film"
    assert case["runtime"]["reference_view"] == "leave_level_1"
    assert case["runtime"]["difficulty_level"] == 1
    assert case["runtime"]["allow_other_label"] is False
    assert case["runtime"]["label_pool_name"] == "main_no_other"

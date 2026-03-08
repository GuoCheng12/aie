import argparse
import csv
import json
from pathlib import Path

from src.agents.base import CaseAgent
from src.agents.judge_agent import JudgeAgent
from src.agents.reasoning_agent import ReasoningAgent
from src.agents.ready_agent import ReadyAgent
from src.core.types import AgentResult
from src.orchestration import run_one as run_one_mod


class _FakeDataAgent(CaseAgent):
    name = "data_agent"
    version = "test"
    allowed_patch_prefixes = ("/query/", "/neighbors", "/risk_scores/", "/agent_runs/-")
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "replace", "path": "/query/canonical_smiles", "value": "C"},
                {"op": "replace", "path": "/query/inchikey", "value": "IK-TEST"},
                {
                    "op": "replace",
                    "path": "/neighbors",
                    "value": [
                        {"rank": 1, "sim": 0.91, "neighbor_inchikey": "N1", "neighbor_mechanism_label": "ICT"},
                    ],
                },
                {"op": "add", "path": "/risk_scores/top1_sim", "value": 0.91},
                {"op": "add", "path": "/risk_scores/mean_topk_sim", "value": 0.87},
                {"op": "add", "path": "/risk_scores/novelty_struct", "value": 0.12},
                {"op": "add", "path": "/risk_scores/mechanism_entropy", "value": 0.4},
                {"op": "add", "path": "/risk_scores/mechanism_hint", "value": "ICT"},
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
        "/evidence_readiness/experiment/",
        "/risk_scores/atb_neighbor_consistency",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        return {}

    def run(self, case, ctx, inputs):
        return AgentResult(
            patch=[
                {"op": "add", "path": "/evidence_readiness/atb/cache_status", "value": "success"},
                {"op": "add", "path": "/evidence_readiness/atb/request_status", "value": "done"},
                {
                    "op": "add",
                    "path": "/evidence_readiness/atb/features_summary",
                    "value": {"delta_dihedral": 12.0, "delta_gap": 0.1, "delta_volume": 0.5},
                },
                {"op": "add", "path": "/evidence_readiness/literature/status", "value": "not_started"},
                {"op": "add", "path": "/evidence_readiness/experiment/status", "value": "not_requested"},
                {
                    "op": "add",
                    "path": "/risk_scores/atb_neighbor_consistency",
                    "value": {
                        "flag": "inlier",
                        "reliability": "medium",
                        "sample_size": 10,
                        "outlier_score_max": 1.2,
                    },
                },
            ],
            status="success",
        )


def test_run_one_writes_master_reasoning_block(monkeypatch, tmp_path: Path):
    def _fake_responses_json(self, *, instructions, input_text, schema_name, schema, **kwargs):
        _ = kwargs
        payload = json.loads(input_text)
        registry = payload.get("evidence_registry") or {}

        def _eid(case_path: str) -> str:
            rows = registry if isinstance(registry, list) else list((registry or {}).values())
            for row in rows:
                if isinstance(row, dict) and row.get("case_path") == case_path and isinstance(row.get("evidence_id"), str):
                    return str(row.get("evidence_id"))
            raise AssertionError(f"missing evidence id for {case_path}")

        return {
            "request": {"schema_name": schema_name},
            "response": {"id": "resp-master"},
            "parsed": {
                "status": "ok",
                "template_used": "stable",
                "mechanism_claim": {
                    "primary_hypothesis": {
                        "mechanism_label": "ICT",
                        "aie_rationale_type": "stable",
                        "natural_language_mechanism": "ICT likely dominates",
                        "atb_support_level": "weak",
                    },
                    "confidence": 0.6,
                    "reasoning_mode_used": "conservative",
                },
                "supporting_chain": [
                    {
                        "step_id": "A",
                        "step_name": "torsion_access",
                        "claim": "Excited-state structure indicates torsional access.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "dihedral", "role": "support"}],
                    },
                    {
                        "step_id": "B",
                        "step_name": "ct_family",
                        "claim": "Nonradiative CT/torsion channel is plausible.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "ct context", "role": "context"}],
                    },
                    {
                        "step_id": "C",
                        "step_name": "aIE_bridge",
                        "claim": "Aggregation rigidification suppresses nonradiative path.",
                        "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "rigidification proxy", "role": "context"}],
                    },
                    {
                        "step_id": "D",
                        "step_name": "discriminators",
                        "claim": "Compare and measure discriminative tests across ICT/TICT/ESIPT.",
                        "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}],
                    },
                ],
                "competing_hypotheses": [{"name": "TICT", "confidence": 0.25, "atb_support_level": "weak", "evidence_used": [{"evidence_id": _eid("/risk_scores/top1_sim"), "note": "prior", "role": "context"}]}],
                "predictions": [
                    {"prediction": "measure TRPL", "expected_signal": "lifetime change", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "torsion", "role": "context"}]},
                    {"prediction": "compare solvent polarity", "expected_signal": "CT shift", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_gap"), "note": "ct", "role": "context"}]},
                    {"prediction": "compare aggregation state", "expected_signal": "suppression trend", "evidence_used": [{"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_volume"), "note": "aggregation", "role": "context"}]},
                ],
                "limits": ["conservative estimate; no emission evidence"],
                "evidence_used": [
                    {"evidence_id": _eid("/risk_scores/top1_sim"), "note": "0.91 prior", "role": "context"},
                    {"evidence_id": _eid("/evidence_readiness/atb/features_summary/delta_dihedral"), "note": "support", "role": "support"},
                ],
                "recommended_next_actions": ["request_manual_pdf"],
            },
        }

    monkeypatch.setattr("src.tools.llm_client.ResponsesLLMClient.responses_json", _fake_responses_json)

    test_csv = tmp_path / "test.csv"
    with test_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "code", "SMILES", "reference", "inchikey"])
        w.writeheader()
        w.writerow({"id": "1", "code": "DEMO", "SMILES": "C", "reference": "ref", "inchikey": "IK-TEST"})

    monkeypatch.setattr(
        run_one_mod,
        "build_default_agents",
        lambda: [_FakeDataAgent(), _FakeChemAgent(), ReadyAgent(), ReasoningAgent(use_llm=True), JudgeAgent(use_llm=False), ReadyAgent()],
    )

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

    assert case["master_reasoning_status"] == "completed"
    assert isinstance(case["master_reasoning"], dict)
    assert case["master_reasoning"]["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "ICT"
    assert "/risk_scores/top1_sim" in case["master_reasoning_used_evidence_paths"]
    assert "reasoning" in case
    assert "/risk_scores/top1_sim" in case["reasoning"]["used_evidence_paths"]
    assert any(str(x).startswith("E") for x in case["reasoning"]["used_evidence_ids"])
    run_id = out["run_id"]
    llm_run_dir = tmp_path / "llm_responses" / run_id
    assert (llm_run_dir / f"{run_id}.reasoning_agent.response.json").exists()
    assert (llm_run_dir / f"{run_id}.reasoning_agent.summary5.json").exists()

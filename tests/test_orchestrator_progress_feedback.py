import json
from pathlib import Path

from src.agents.base import CaseAgent
from src.core.types import AgentContext, AgentResult
from src.orchestration.orchestrator import Orchestrator


class _FakeAgent(CaseAgent):
    name = "fake_agent"
    version = "0.0.1"
    allowed_patch_prefixes = ("/agent_runs/-",)
    append_only_prefixes = ("/agent_runs",)

    def build_inputs(self, case, ctx):
        _ = case, ctx
        return {"x": 1}

    def run(self, case, ctx, inputs):
        _ = case, ctx, inputs
        return AgentResult(patch=[], status="success", warnings=[], raw_outputs={"ok": True})


def test_orchestrator_emits_agent_progress_and_updates_status(tmp_path: Path, capsys):
    status_path = tmp_path / "artifacts" / "run_status.json"
    ctx = AgentContext(
        run_id="run-progress",
        run_dir=tmp_path / "artifacts" / "run-progress",
        case_path=tmp_path / "CASE-PROGRESS.json",
        base_url="http://example/v1",
        model="gpt-test",
        llm_response_dir=tmp_path / "llm_responses",
        run_lane="atb_cache_only",
        status_path=status_path,
        progress_round_index=0,
        progress_max_rounds=4,
        progress_active_profile="setup",
    )
    case = {
        "case_id": "CASE-PROGRESS",
        "query": {"input_smiles": "C"},
        "agent_runs": [],
        "action_plan": [],
        "risk_scores": {},
        "evidence_readiness": {},
        "target_fields": {},
        "target_fields_provenance": {},
        "evidence_candidates_staging": [],
        "current_gate": {},
        "post_uq": {},
    }

    Orchestrator(agents=[_FakeAgent()], ctx=ctx).run(case)

    out = capsys.readouterr().out.strip().splitlines()
    assert any('"stage": "agent:fake_agent"' in line and '"status": "running"' in line for line in out)
    assert any('"stage": "agent:fake_agent"' in line and '"status": "success"' in line for line in out)

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "agent:fake_agent"
    assert payload["last_event"] == "fake_agent_success"
    assert payload["active_profile"] == "setup"

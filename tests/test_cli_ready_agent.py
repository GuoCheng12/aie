import argparse
import json
from pathlib import Path

import src.cli as cli


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_ready_agent_cli_updates_case_file(tmp_path, capsys):
    case_path = tmp_path / "case.json"
    _write_json(
        case_path,
        {
            "case_id": "CASE-RA-1",
            "query": {"inchikey": "AAAA", "canonical_smiles": "C"},
            "inputs": {"offline_pdfs": []},
            "target_fields": {},
            "target_fields_provenance": {},
            "evidence_readiness": {
                "atb": {"cache_status": "success"},
                "literature": {"status": "not_started", "sources": [], "last_update": "2026-02-24T00:00:00Z", "notes": None},
            },
            "current_gate": {"state": "ready_for_reasoning", "ready_for_reasoning": True, "reason": "seed"},
            "action_rationale": "seed",
            "action_plan": [],
            "risk_scores": {},
        },
    )

    args = argparse.Namespace(case=str(case_path), dry_run=False)
    cli.ready_agent_command(args)
    out = json.loads(capsys.readouterr().out)

    assert out["ok"] is True
    after = json.loads(case_path.read_text(encoding="utf-8"))
    assert after["current_gate"]["state"] == "ready_conservative"
    assert after["action_plan"][0]["action"] == "run_master_reasoner"
    assert "request_manual_pdf" in [x.get("action") for x in after["action_plan"]]

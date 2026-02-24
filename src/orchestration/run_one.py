"""
Single-entrypoint multi-agent run for one sample.

Example:
python -m src.orchestration.run_one \
  --test-csv data/test.csv --row-index 0 \
  --offline-pdf /abs/path/paper.pdf \
  --artifacts-dir artifacts \
  --base-url http://...:3888/v1 --model gpt-5.1
"""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.hashing import sha256_file
from src.core.io import save_case
from src.core.types import AgentContext
from src.orchestration.orchestrator import Orchestrator
from src.orchestration.registry import build_default_agents


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_from_test_csv(path: Path, row_index: int) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if row_index < 0 or row_index >= len(rows):
        raise IndexError(f"row_index_out_of_range:{row_index} (rows={len(rows)})")
    return rows[row_index]


def _build_initial_case(row: Dict[str, Any], offline_pdf: Optional[str]) -> Dict[str, Any]:
    smiles = str(row.get("SMILES") or "").strip()
    if not smiles:
        raise ValueError("selected_row_missing_smiles")
    case_id = str(row.get("inchikey") or "").strip() or uuid.uuid4().hex
    pdf_items = []
    if offline_pdf:
        p = Path(offline_pdf)
        pdf_items.append(
            {
                "path_or_id": str(p),
                "sha256": sha256_file(p) if p.exists() and p.is_file() else None,
                "provided_by": "operator",
            }
        )
    mode = "offline_pdf" if pdf_items else "web_search"
    return {
        "case_id": case_id,
        "case_version": "1.0.0-multi-agent",
        "query": {
            "input_smiles": smiles,
            "canonical_smiles": None,
            "inchikey": str(row.get("inchikey") or "").strip() or None,
            "code": str(row.get("code") or "").strip() or None,
            "reference": str(row.get("reference") or "").strip() or None,
            "created_at": _now(),
            "aliases": [str(row.get("code") or "").strip()] if str(row.get("code") or "").strip() else [],
        },
        "inputs": {"offline_pdfs": pdf_items},
        "evidence_acquire": {
            "emission": {
                "mode": mode,
                "strictness": "relaxed",
                "extractor_mode": "mineru_llm",
            }
        },
        "neighbors": [],
        "risk_scores": {},
        "evidence_readiness": {
            "atb": {"cache_status": "absent", "request_status": "not_requested", "missing_fields": [], "last_update": _now()},
            "literature": {"status": "not_started", "sources": [], "last_update": _now(), "notes": None},
            "experiment": {"status": "not_requested", "requested_fields": [], "received_fields": [], "last_update": _now(), "notes": None},
        },
        "target_fields": {},
        "target_fields_provenance": {},
        "evidence_candidates_staging": [],
        "current_gate": {
            "state": "needs_manual",
            "ready_for_reasoning": False,
            "reason": "not_evaluated",
            "reasoning_mode": "blocked",
        },
        "action_rationale": "",
        "action_plan": [],
        "post_uq": {
            "status": "not_started",
            "confidence": None,
            "contradictions": [],
            "missing_evidence": [],
            "recommended_actions": [],
        },
        "agent_runs": [],
        "history": [],
    }


def run_one(args: argparse.Namespace) -> Dict[str, Any]:
    row = _row_from_test_csv(Path(args.test_csv), int(args.row_index))
    initial_case = _build_initial_case(row, args.offline_pdf)

    case_id = str(initial_case["case_id"])
    run_id = uuid.uuid4().hex
    run_dir = Path(args.artifacts_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    case_outdir = Path(args.outdir)
    case_outdir.mkdir(parents=True, exist_ok=True)
    case_path = case_outdir / f"{case_id}.json"
    save_case(case_path, initial_case)

    ctx = AgentContext(
        run_id=run_id,
        run_dir=run_dir,
        case_path=case_path,
        base_url=str(args.base_url),
        model=str(args.model),
        llm_api_key_env=str(args.llm_api_key_env),
        llm_max_output_tokens=int(args.llm_max_output_tokens),
        llm_reasoning_effort=args.llm_reasoning_effort,
        mineru_bin=str(args.mineru_bin),
        mineru_output_root=Path(args.mineru_output_root),
        mineru_backend=str(args.mineru_backend),
        mineru_method=args.mineru_method,
        mineru_lang=args.mineru_lang,
        mineru_start_page=args.mineru_start_page,
        mineru_end_page=args.mineru_end_page,
        mineru_timeout_sec=int(args.mineru_timeout_sec),
        force=bool(args.force),
    )

    orchestrator = Orchestrator(agents=build_default_agents(), ctx=ctx)
    final_case, run_summary = orchestrator.run(initial_case)
    save_case(case_path, final_case)

    summary = {
        "ok": True,
        "run_id": run_id,
        "case_id": case_id,
        "case_path": str(case_path),
        "artifacts_dir": str(run_dir),
        "input": {
            "test_csv": str(args.test_csv),
            "row_index": int(args.row_index),
            "resolved_row": row,
            "offline_pdf": args.offline_pdf,
        },
        "final_gate": final_case.get("current_gate"),
        "target_fields": final_case.get("target_fields"),
        "agent_runs_total": len(final_case.get("agent_runs", [])),
        "steps": run_summary.get("steps", []),
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one multi-agent case loop (data -> chem -> ready -> reasoning -> judge -> ready).")
    parser.add_argument("--test-csv", type=str, required=True)
    parser.add_argument("--row-index", type=int, required=True)
    parser.add_argument("--offline-pdf", type=str, default=None)
    parser.add_argument("--artifacts-dir", type=str, default="artifacts/multi_agent")
    parser.add_argument("--outdir", type=str, default="cases/multi_agent")
    parser.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    parser.add_argument("--model", type=str, default="gpt-5.1")
    parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    parser.add_argument("--llm-reasoning-effort", type=str, default=None)
    parser.add_argument("--mineru-bin", type=str, default="third_party/MinerU/.venv/bin/mineru")
    parser.add_argument("--mineru-output-root", type=str, default="third_party/MinerU/output")
    parser.add_argument("--mineru-backend", type=str, default="hybrid-auto-engine")
    parser.add_argument("--mineru-method", type=str, default=None)
    parser.add_argument("--mineru-lang", type=str, default=None)
    parser.add_argument("--mineru-start-page", type=int, default=None)
    parser.add_argument("--mineru-end-page", type=int, default=None)
    parser.add_argument("--mineru-timeout-sec", type=int, default=1200)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    summary = run_one(args)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


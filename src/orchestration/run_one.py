"""
Single-entrypoint multi-agent run for one sample.

Release command:
python -m src.orchestration.run_one \
  --test-csv data/test.csv --code DBA-AM \
  --run-lane atb_cache_only \
  --artifacts-dir artifacts/multi_agent --outdir cases/multi_agent
"""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import shutil
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Optional

from src.core.hashing import sha256_file
from src.core.io import save_case, save_json
from src.core.output_layout import (
    OUTPUT_LAYOUT_CASE_CENTRIC,
    OUTPUT_LAYOUTS,
    TIMESTAMP_FORMAT_UTC_COMPACT,
    TIMESTAMP_FORMATS,
    plan_output_layout,
    refresh_latest_case_view,
    update_history_index,
    write_latest_pointer,
    write_legacy_pointers,
)
from src.core.types import AgentContext
from src.orchestration.orchestrator import Orchestrator
from src.orchestration.registry import build_default_agents, build_setup_agents
from src.orchestration.round_runner import (
    ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
    ROUND_RUNNER_MODES,
    run_iterative_rounds,
)
from src.orchestration.run_status import atomic_write_json, emit_progress_event, now_iso8601
from src.reasoning.reasoning_config import resolve_allow_other_label
from src.tools.llm_trace_store import resolve_rounds_trace_dir, resolve_run_trace_dir

SUPPORTED_RUN_LANES = {"atb_cache_only", "offline_pdf", "full"}
REFERENCE_VIEW_AUTO = "auto"
REFERENCE_VIEW_ALL = "all_levels_full"
REFERENCE_VIEWS = {
    REFERENCE_VIEW_AUTO,
    REFERENCE_VIEW_ALL,
    "leave_level_1",
    "leave_level_2",
    "leave_level_3",
}
DEFAULT_REFERENCE_INDEX_ROOT = "data/reference_indices/split_levels_v2/views"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_test_rows(path: Path) -> list[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _row_by_index(rows: list[Dict[str, Any]], row_index: int) -> Dict[str, Any]:
    if row_index < 0 or row_index >= len(rows):
        raise IndexError(f"row_index_out_of_range:{row_index} (rows={len(rows)})")
    return rows[row_index]


def _row_by_code(rows: list[Dict[str, Any]], code: str) -> Dict[str, Any]:
    target = str(code).strip()
    for row in rows:
        if str(row.get("code") or "").strip() == target:
            return row
    raise ValueError(f"code_not_found_in_test_csv:{code}")


def _resolve_input_row(args: argparse.Namespace) -> Dict[str, Any]:
    smiles = getattr(args, "smiles", None)
    code = getattr(args, "code", None)
    if smiles:
        return {
            "id": None,
            "code": str(code or "").strip() or None,
            "SMILES": str(smiles).strip(),
            "reference": None,
            "inchikey": None,
        }

    rows = _read_test_rows(Path(getattr(args, "test_csv", "data/test.csv")))
    if code:
        return _row_by_code(rows, str(code))
    row_index = getattr(args, "row_index", None)
    if row_index is None:
        raise ValueError("require_one_of: --smiles | --code | --row-index")
    return _row_by_index(rows, int(row_index))


def _parse_difficulty_level(row: Dict[str, Any]) -> Optional[int]:
    for key in ("difficulty_level", "level", "difficulty"):
        value = row.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            parsed = int(float(str(value)))
        except Exception:
            continue
        if parsed in {1, 2, 3}:
            return parsed
    source_split_file = str(row.get("source_split_file") or "").strip()
    if source_split_file:
        for level in (1, 2, 3):
            if source_split_file.startswith(f"{level}_level"):
                return level
    return None


def _resolve_reference_view(args: argparse.Namespace, row: Dict[str, Any]) -> tuple[str, Optional[int]]:
    configured = str(getattr(args, "reference_view", REFERENCE_VIEW_ALL) or REFERENCE_VIEW_ALL)
    if configured not in REFERENCE_VIEWS:
        raise ValueError(f"unsupported_reference_view:{configured}")
    difficulty_level = _parse_difficulty_level(row)
    if configured != REFERENCE_VIEW_AUTO:
        return configured, difficulty_level
    if getattr(args, "smiles", None):
        return REFERENCE_VIEW_ALL, difficulty_level
    if difficulty_level in {1, 2, 3}:
        return f"leave_level_{difficulty_level}", difficulty_level
    return REFERENCE_VIEW_ALL, difficulty_level


def _resolve_reference_data_dir(args: argparse.Namespace, row: Dict[str, Any]) -> tuple[Path, str, Optional[int]]:
    reference_view, difficulty_level = _resolve_reference_view(args, row)
    reference_root = Path(
        str(getattr(args, "reference_index_root", DEFAULT_REFERENCE_INDEX_ROOT) or DEFAULT_REFERENCE_INDEX_ROOT)
    )
    view_dir = reference_root / reference_view
    if view_dir.exists():
        return view_dir, reference_view, difficulty_level
    return Path("data"), "legacy_data", difficulty_level


def _emission_mode_for_lane(run_lane: str, has_offline_pdf: bool) -> str:
    if run_lane == "offline_pdf":
        return "offline_pdf"
    if run_lane == "full":
        return "offline_pdf" if has_offline_pdf else "web_search"
    return "offline_pdf" if has_offline_pdf else "web_search"


def _build_initial_case(
    row: Dict[str, Any],
    offline_pdf: Optional[str],
    run_lane: str,
    *,
    source_ref: Optional[str],
    source_locator: Optional[str],
    reference_index_root: str,
    reference_view: str,
    difficulty_level: Optional[int],
    allow_other_label: bool,
) -> Dict[str, Any]:
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
    mode = _emission_mode_for_lane(run_lane, has_offline_pdf=bool(pdf_items))
    emission_aggr = _to_float(row.get("emission_aggr"))
    emission_solid = _to_float(row.get("emission_solid"))
    target_fields: Dict[str, Any] = {}
    target_fields_provenance: Dict[str, Any] = {}
    if emission_aggr is not None:
        target_fields["emission_aggr_nm"] = emission_aggr
        target_fields_provenance["emission_aggr_nm"] = {
            "source_type": "dataset_row",
            "source_ref": source_ref,
            "source_locator": source_locator,
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "aggregation",
            "condition_bucket": "aggregation",
        }
    if emission_solid is not None:
        target_fields["emission_solid_or_film_nm"] = emission_solid
        target_fields_provenance["emission_solid_or_film_nm"] = {
            "source_type": "dataset_row",
            "source_ref": source_ref,
            "source_locator": source_locator,
            "confidence": 1.0,
            "identity_match": "exact",
            "identity_match_confidence": 1.0,
            "condition": "solid_or_film",
            "condition_bucket": "solid_or_film",
        }

    return {
        "case_id": case_id,
        "case_version": "1.1.0-multi-agent",
        "runtime": {
            "run_lane": run_lane,
            "reference_index_root": reference_index_root,
            "reference_view": reference_view,
            "difficulty_level": difficulty_level,
            "allow_other_label": bool(allow_other_label),
            "label_pool_name": "default_with_other" if allow_other_label else "main_no_other",
        },
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
        "target_fields": target_fields,
        "target_fields_provenance": target_fields_provenance,
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


def _write_stage_snapshots(run_summary: Dict[str, Any], *, case_id: str, snapshots_dir: Path) -> Dict[str, str]:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    wanted = {"data_agent": "data_agent_case", "chem_agent": "chem_agent_case", "ready_agent": "ready_agent_case"}
    seen: set[str] = set()
    out: Dict[str, str] = {}
    for step in run_summary.get("steps", []) or []:
        agent = str(step.get("agent") or "")
        if agent not in wanted or agent in seen:
            continue
        art = step.get("artifact_paths") or {}
        src = art.get("case_after")
        if not src:
            continue
        dst = snapshots_dir / f"{case_id}.{agent}.json"
        shutil.copyfile(src, dst)
        out[wanted[agent]] = str(dst)
        seen.add(agent)
        if seen == set(wanted.keys()):
            break
    return out


def _extract_final_reasoning(case_json: Dict[str, Any]) -> Dict[str, Any]:
    root = case_json.get("master_reasoning")
    if isinstance(root, dict):
        return root
    reasoning = case_json.get("reasoning")
    if isinstance(reasoning, dict):
        candidate = reasoning.get("master_reasoning")
        if isinstance(candidate, dict):
            return candidate
    return {}


def _extract_final_label_confidence(case_json: Dict[str, Any]) -> tuple[Optional[str], Optional[float]]:
    reasoning = _extract_final_reasoning(case_json)
    mechanism_claim = reasoning.get("mechanism_claim") if isinstance(reasoning.get("mechanism_claim"), dict) else {}
    primary = mechanism_claim.get("primary_hypothesis") if isinstance(mechanism_claim.get("primary_hypothesis"), dict) else {}
    label = str(primary.get("mechanism_label") or "").strip() or None
    confidence = mechanism_claim.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else None
    except Exception:
        conf = None
    return label, conf


def _extract_used_evidence_ids(case_json: Dict[str, Any], *, limit: int = 8) -> list[str]:
    ids: list[str] = []
    reasoning = case_json.get("reasoning")
    if isinstance(reasoning, dict):
        rows = reasoning.get("used_evidence_ids")
        if isinstance(rows, list):
            ids = [str(x) for x in rows if str(x)]
    if not ids:
        rows = case_json.get("master_reasoning_used_evidence_ids")
        if isinstance(rows, list):
            ids = [str(x) for x in rows if str(x)]
    return ids[: max(0, int(limit))]


def _build_quick_view(
    *,
    case_json: Dict[str, Any],
    case_id: str,
    run_id: str,
    run_time: str,
    run_summary: Dict[str, Any],
    case_path: Path,
    run_summary_path: Path,
    rounds_dir: Path,
    llm_dir: Path,
) -> Dict[str, Any]:
    final_label, final_confidence = _extract_final_label_confidence(case_json)
    iterative = run_summary.get("iterative") if isinstance(run_summary.get("iterative"), dict) else {}
    rounds = iterative.get("rounds") if isinstance(iterative.get("rounds"), list) else []
    stop_info = None
    if rounds:
        last_eval_path = (rounds[-1] or {}).get("eval_report_path")
        if last_eval_path:
            try:
                eval_payload = json.loads(Path(str(last_eval_path)).read_text(encoding="utf-8"))
                stop_info = eval_payload.get("stop_recommendation")
            except Exception:
                stop_info = None

    gate = case_json.get("current_gate") if isinstance(case_json.get("current_gate"), dict) else {}
    return {
        "case_id": case_id,
        "run_id": run_id,
        "run_time": run_time,
        "final_label": final_label,
        "final_confidence": final_confidence,
        "final_gate": {
            "state": gate.get("state"),
            "reasoning_mode": gate.get("reasoning_mode"),
        },
        "rounds_executed": int(iterative.get("executed_rounds") or (1 if run_summary.get("steps") else 0)),
        "stop_recommendation": stop_info,
        "used_evidence_ids_top": _extract_used_evidence_ids(case_json),
        "paths": {
            "case_json": str(case_path),
            "run_summary_json": str(run_summary_path),
            "rounds_dir": str(rounds_dir),
            "llm_dir": str(llm_dir),
        },
    }


def _collect_round_confidence_summary(iterative_summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    rounds = iterative_summary.get("rounds") if isinstance(iterative_summary, dict) else []
    if not isinstance(rounds, list):
        return out
    for row in rounds:
        if not isinstance(row, dict):
            continue
        round_index = int(row.get("round_index") or 0)
        active_profile = str(row.get("active_profile") or "")
        master_conf = None
        r0_penalty_applied = active_profile == "R0"
        eval_delta = None
        eval_new = None
        master_path = row.get("master_round_report_path")
        if master_path:
            try:
                master_payload = json.loads(Path(str(master_path)).read_text(encoding="utf-8"))
                master_conf = (master_payload or {}).get("confidence")
            except Exception:
                pass
        eval_path = row.get("eval_report_path")
        if eval_path:
            try:
                eval_payload = json.loads(Path(str(eval_path)).read_text(encoding="utf-8"))
                cu = (eval_payload or {}).get("confidence_update") or {}
                eval_delta = cu.get("delta")
                eval_new = cu.get("new")
            except Exception:
                pass
        out.append(
            {
                "round_index": round_index,
                "active_profile": active_profile,
                "master_confidence": master_conf,
                "evaluator_confidence_delta": eval_delta,
                "evaluator_confidence_new": eval_new,
                "r0_penalty_applied": r0_penalty_applied,
            }
        )
    return out


def run_one(args: argparse.Namespace) -> Dict[str, Any]:
    run_lane = str(getattr(args, "run_lane", "atb_cache_only") or "atb_cache_only")
    if run_lane not in SUPPORTED_RUN_LANES:
        raise ValueError(f"unsupported_run_lane:{run_lane}")

    row = _resolve_input_row(args)
    reference_data_dir, reference_view, difficulty_level = _resolve_reference_data_dir(args, row)
    reference_index_root = str(
        Path(str(getattr(args, "reference_index_root", DEFAULT_REFERENCE_INDEX_ROOT) or DEFAULT_REFERENCE_INDEX_ROOT))
    )
    source_ref = str(Path(args.test_csv).resolve()) if getattr(args, "test_csv", None) else None
    locator_bits = []
    if getattr(args, "row_index", None) is not None:
        locator_bits.append(f"row_index={int(args.row_index)}")
    row_code = str(row.get("code") or "").strip()
    if row_code:
        locator_bits.append(f"code={row_code}")
    source_locator = "; ".join(locator_bits) if locator_bits else None
    allow_other_label = resolve_allow_other_label(
        runtime={
            "reference_index_root": reference_index_root,
            "reference_view": reference_view,
            "difficulty_level": difficulty_level,
        },
        reference_index_root=reference_index_root,
        source_ref=source_ref,
    )
    initial_case = _build_initial_case(
        row,
        args.offline_pdf,
        run_lane,
        source_ref=source_ref,
        source_locator=source_locator,
        reference_index_root=reference_index_root,
        reference_view=reference_view,
        difficulty_level=difficulty_level,
        allow_other_label=allow_other_label,
    )

    case_id = str(initial_case["case_id"])
    run_id = uuid.uuid4().hex
    output_layout = str(getattr(args, "output_layout", OUTPUT_LAYOUT_CASE_CENTRIC) or OUTPUT_LAYOUT_CASE_CENTRIC)
    if output_layout not in OUTPUT_LAYOUTS:
        raise ValueError(f"unsupported_output_layout:{output_layout}")
    timestamp_format = str(getattr(args, "output_timestamp_format", TIMESTAMP_FORMAT_UTC_COMPACT) or TIMESTAMP_FORMAT_UTC_COMPACT)
    if timestamp_format not in TIMESTAMP_FORMATS:
        raise ValueError(f"unsupported_output_timestamp_format:{timestamp_format}")
    retain_runs = max(1, int(getattr(args, "retain_runs", 10) or 10))
    write_legacy_run_view = bool(getattr(args, "write_legacy_run_view", True))

    layout_paths = plan_output_layout(
        artifacts_root=Path(args.artifacts_dir),
        llm_response_root=Path(getattr(args, "llm_response_dir", "artifacts/llm_responses")),
        case_id=case_id,
        run_id=run_id,
        layout=output_layout,
        timestamp_format=timestamp_format,
        write_legacy_run_view=write_legacy_run_view,
    )

    run_dir = layout_paths.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = Path(args.artifacts_dir) / "run_status.json"

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
        llm_temperature=float(getattr(args, "llm_temperature", 0.2)),
        llm_use_json_schema=bool(getattr(args, "llm_use_json_schema", False)),
        llm_response_dir=layout_paths.llm_run_dir if output_layout == OUTPUT_LAYOUT_CASE_CENTRIC else Path(getattr(args, "llm_response_dir", "artifacts/llm_responses")),
        llm_response_run_scoped=bool(output_layout == OUTPUT_LAYOUT_CASE_CENTRIC),
        llm_rounds_dir=layout_paths.rounds_dir if output_layout == OUTPUT_LAYOUT_CASE_CENTRIC else None,
        run_lane=run_lane,
        mineru_bin=str(args.mineru_bin),
        mineru_output_root=Path(args.mineru_output_root),
        mineru_backend=str(args.mineru_backend),
        mineru_method=args.mineru_method,
        mineru_lang=args.mineru_lang,
        mineru_start_page=args.mineru_start_page,
        mineru_end_page=args.mineru_end_page,
        mineru_timeout_sec=int(args.mineru_timeout_sec),
        force=bool(args.force),
        status_path=status_path,
        progress_round_index=0,
        progress_max_rounds=int(getattr(args, "max_rounds", 1) if bool(getattr(args, "iterative", False)) else 1),
        progress_active_profile="setup" if bool(getattr(args, "iterative", False)) else "single",
    )

    def _write_status(
        *,
        round_index: int,
        active_profile: str,
        stage: str,
        last_event: str,
        errors: Optional[list[dict]] = None,
        latest_eval_report: Optional[str] = None,
    ) -> None:
        prev_errors: list[dict] = []
        prev_eval_report: Optional[str] = None
        if status_path.exists():
            try:
                prev = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(prev, dict):
                    if isinstance(prev.get("errors"), list):
                        prev_errors = list(prev.get("errors") or [])
                    prev_eval_report = prev.get("latest_eval_report")
            except Exception:
                pass
        payload = {
            "run_id": run_id,
            "case_id": case_id,
            "round_index": int(round_index),
            "max_rounds": int(getattr(args, "max_rounds", 1) if bool(getattr(args, "iterative", False)) else 1),
            "active_profile": str(active_profile),
            "round_runner_mode": str(getattr(args, "round_runner_mode", ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT)),
            "stage": stage,
            "last_event": last_event,
            "last_updated_at": now_iso8601(),
            "errors": list(prev_errors if errors is None else (errors or [])),
            "round_dir": str(resolve_rounds_trace_dir(ctx, create=False).resolve()),
            "latest_eval_report": latest_eval_report if latest_eval_report is not None else prev_eval_report,
        }
        atomic_write_json(status_path, payload)

    emit_progress_event(
        round_index=0,
        max_rounds=int(getattr(args, "max_rounds", 1) if bool(getattr(args, "iterative", False)) else 1),
        active_profile=str(getattr(args, "round_start_profile", "R0")),
        stage="run_start",
        status="running",
        elapsed_ms=0,
        extra={"run_id": run_id, "case_id": case_id},
    )
    _write_status(
        round_index=0,
        active_profile=str(getattr(args, "round_start_profile", "R0")),
        stage="run_start",
        last_event="run_start",
        errors=[],
    )

    iterative_mode = bool(getattr(args, "iterative", False))
    if iterative_mode:
        t_setup = perf_counter()
        setup_kwargs: Dict[str, Any] = {}
        setup_sig = inspect.signature(build_setup_agents).parameters
        if "neighbor_topk" in setup_sig:
            setup_kwargs["neighbor_topk"] = int(getattr(args, "neighbor_topk", 10))
        if "data_dir" in setup_sig:
            setup_kwargs["data_dir"] = str(reference_data_dir)
        setup_orchestrator = Orchestrator(
            agents=build_setup_agents(**setup_kwargs),
            ctx=ctx,
        )
        setup_case, setup_summary = setup_orchestrator.run(initial_case)
        save_case(case_path, setup_case)
        emit_progress_event(
            round_index=0,
            max_rounds=int(getattr(args, "max_rounds", 4)),
            active_profile=str(getattr(args, "round_start_profile", "R0")),
            stage="setup",
            status="completed",
            elapsed_ms=int((perf_counter() - t_setup) * 1000),
            extra={"steps": len(setup_summary.get("steps", []) or [])},
        )
        _write_status(
            round_index=0,
            active_profile=str(getattr(args, "round_start_profile", "R0")),
            stage="setup",
            last_event="setup_completed",
            errors=[],
        )

        iterative_kwargs = {
            "case_json": setup_case,
            "ctx": ctx,
            "mode": str(getattr(args, "round_runner_mode", ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT)),
            "max_rounds": int(getattr(args, "max_rounds", 4)),
            "start_profile": str(getattr(args, "round_start_profile", "R0")),
            "status_path": status_path,
            "evaluator_use_llm": bool(getattr(args, "evaluator_use_llm", False)),
            "master_model": str(getattr(args, "model", ctx.model)),
            "master_reasoning_effort": getattr(args, "llm_reasoning_effort", ctx.llm_reasoning_effort),
            "evaluator_model": getattr(args, "evaluator_model", None),
            "evaluator_reasoning_effort": getattr(args, "evaluator_reasoning_effort", None),
        }
        if "evaluator_confidence_adjustment_enabled" in inspect.signature(run_iterative_rounds).parameters:
            iterative_kwargs["evaluator_confidence_adjustment_enabled"] = bool(
                getattr(args, "evaluator_confidence_adjustment_enabled", False)
            )
        if "evaluator_confidence_adjustment_max_abs_delta" in inspect.signature(run_iterative_rounds).parameters:
            iterative_kwargs["evaluator_confidence_adjustment_max_abs_delta"] = float(
                getattr(args, "evaluator_confidence_adjustment_max_abs_delta", 0.05)
            )
        if "neighbor_topk" in inspect.signature(run_iterative_rounds).parameters:
            iterative_kwargs["neighbor_topk"] = int(getattr(args, "neighbor_topk", 10))
        if "pre_r2_failure_recovery_mode" in inspect.signature(run_iterative_rounds).parameters:
            iterative_kwargs["pre_r2_failure_recovery_mode"] = str(
                getattr(args, "pre_r2_failure_recovery_mode", "force_r2")
            )
        final_case, iterative_summary = run_iterative_rounds(
            **iterative_kwargs,
        )
        save_case(case_path, final_case)
        run_summary = {
            "run_id": ctx.run_id,
            "run_dir": str(ctx.run_dir),
            "steps": setup_summary.get("steps", []),
            "setup_agent_runs_total": setup_summary.get("agent_runs_total"),
            "final_gate": final_case.get("current_gate"),
            "agent_runs_total": len(final_case.get("agent_runs", [])),
            "iterative": iterative_summary,
        }
    else:
        t_orch = perf_counter()
        default_kwargs: Dict[str, Any] = {}
        default_sig = inspect.signature(build_default_agents).parameters
        if "neighbor_topk" in default_sig:
            default_kwargs["neighbor_topk"] = int(getattr(args, "neighbor_topk", 10))
        if "data_dir" in default_sig:
            default_kwargs["data_dir"] = str(reference_data_dir)
        orchestrator = Orchestrator(agents=build_default_agents(**default_kwargs), ctx=ctx)
        final_case, run_summary = orchestrator.run(initial_case)
        save_case(case_path, final_case)
        emit_progress_event(
            round_index=0,
            max_rounds=1,
            active_profile="single",
            stage="orchestrator",
            status="completed",
            elapsed_ms=int((perf_counter() - t_orch) * 1000),
            extra={"steps": len(run_summary.get("steps", []) or [])},
        )
        _write_status(
            round_index=0,
            active_profile="single",
            stage="orchestrator",
            last_event="orchestrator_completed",
            errors=[],
        )

    snapshots = {}
    if bool(getattr(args, "emit_stage_snapshots", False)):
        snapshots_dir = Path(getattr(args, "stage_snapshots_dir", "cases/stage_snapshots"))
        snapshots = _write_stage_snapshots(run_summary, case_id=case_id, snapshots_dir=snapshots_dir)

    primary_output_dir = str(run_dir)
    latest_dir = str(layout_paths.latest_dir) if layout_paths.latest_dir is not None else None
    history_index_path = str(layout_paths.history_index_path) if layout_paths.history_index_path is not None else None
    trace_dir = resolve_run_trace_dir(ctx, create=False)
    rounds_dir = resolve_rounds_trace_dir(ctx, create=False)

    summary = {
        "ok": True,
        "run_id": run_id,
        "run_name": layout_paths.run_name,
        "run_time": layout_paths.run_time_iso,
        "case_id": case_id,
        "case_path": str(case_path),
        "artifacts_dir": str(run_dir),
        "llm_response_dir": str(trace_dir),
        "run_lane": run_lane,
        "output_layout": output_layout,
        "primary_output_dir": primary_output_dir,
        "latest_dir": latest_dir,
        "history_index_path": history_index_path,
        "legacy_paths": {},
        "quick_view_path": str(run_dir / "quick_view.json"),
        "input": {
            "test_csv": str(args.test_csv) if getattr(args, "test_csv", None) else None,
            "row_index": int(args.row_index) if getattr(args, "row_index", None) is not None else None,
            "code": getattr(args, "code", None),
            "smiles": getattr(args, "smiles", None),
            "resolved_row": row,
            "offline_pdf": args.offline_pdf,
            "reference_index_root": reference_index_root,
            "reference_view": reference_view,
            "reference_data_dir": str(reference_data_dir),
            "difficulty_level": difficulty_level,
        },
        "final_gate": final_case.get("current_gate"),
        "target_fields": final_case.get("target_fields"),
        "agent_runs_total": len(final_case.get("agent_runs", [])),
        "steps": run_summary.get("steps", []),
        "snapshots": snapshots,
        "iterative": run_summary.get("iterative"),
        "round_confidence_summary": _collect_round_confidence_summary(run_summary.get("iterative") or {}),
    }
    save_json(layout_paths.run_summary_path, summary)

    quick_view = _build_quick_view(
        case_json=final_case,
        case_id=case_id,
        run_id=run_id,
        run_time=layout_paths.run_time_iso,
        run_summary=run_summary,
        case_path=case_path,
        run_summary_path=layout_paths.run_summary_path,
        rounds_dir=rounds_dir,
        llm_dir=trace_dir,
    )
    quick_view_path = run_dir / "quick_view.json"
    save_json(quick_view_path, quick_view)

    if output_layout == OUTPUT_LAYOUT_CASE_CENTRIC:
        refresh_latest_case_view(
            paths=layout_paths,
            case_path=case_path,
            run_summary_path=layout_paths.run_summary_path,
            quick_view_path=quick_view_path,
        )
        latest_payload = {
            "case_id": case_id,
            "run_id": run_id,
            "run_name": layout_paths.run_name,
            "run_time": layout_paths.run_time_iso,
            "primary_output_dir": primary_output_dir,
            "latest_dir": latest_dir,
            "run_summary_path": str(layout_paths.run_summary_path),
            "quick_view_path": str(quick_view_path),
            "layout": output_layout,
        }
        write_latest_pointer(paths=layout_paths, latest_payload=latest_payload)

        run_record = {
            "run_id": run_id,
            "run_name": layout_paths.run_name,
            "run_time": layout_paths.run_time_iso,
            "status": "ok",
            "final_label": quick_view.get("final_label"),
            "final_confidence": quick_view.get("final_confidence"),
            "run_dir": str(run_dir),
            "run_summary_path": str(layout_paths.run_summary_path),
            "quick_view_path": str(quick_view_path),
        }
        update_history_index(paths=layout_paths, run_record=run_record, retain_runs=retain_runs)

    if write_legacy_run_view:
        pointer_payload = {
            "ok": True,
            "run_id": run_id,
            "case_id": case_id,
            "layout": output_layout,
            "primary_output_dir": primary_output_dir,
            "run_summary_path": str(layout_paths.run_summary_path),
            "quick_view_path": str(quick_view_path),
            "latest_dir": latest_dir,
            "history_index_path": history_index_path,
        }
        summary["legacy_paths"] = write_legacy_pointers(paths=layout_paths, pointer_payload=pointer_payload)
    else:
        summary["legacy_paths"] = {"legacy_run_summary": None, "legacy_llm_pointer": None}

    save_json(layout_paths.run_summary_path, summary)
    emit_progress_event(
        round_index=int((((run_summary.get("iterative") or {}).get("executed_rounds") or 1) - 1) if iterative_mode else 0),
        max_rounds=int(getattr(args, "max_rounds", 1) if iterative_mode else 1),
        active_profile=str(
            (((run_summary.get("iterative") or {}).get("rounds") or [{}])[-1] or {}).get("active_profile")
            if iterative_mode
            else "single"
        ),
        stage="run_end",
        status="completed",
        elapsed_ms=0,
        extra={"run_id": run_id},
    )
    _write_status(
        round_index=int((((run_summary.get("iterative") or {}).get("executed_rounds") or 1) - 1) if iterative_mode else 0),
        active_profile=str(
            (((run_summary.get("iterative") or {}).get("rounds") or [{}])[-1] or {}).get("active_profile")
            if iterative_mode
            else "single"
        ),
        stage="run_end",
        last_event="run_completed",
        errors=None,
        latest_eval_report=str(
            (((run_summary.get("iterative") or {}).get("rounds") or [{}])[-1] or {}).get("eval_report_path")
            if iterative_mode
            else None
        )
        if iterative_mode
        else None,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one multi-agent case loop (release runtime).")
    parser.add_argument("--test-csv", type=str, default="data/test.csv")
    parser.add_argument("--row-index", type=int, default=None)
    parser.add_argument("--code", type=str, default=None)
    parser.add_argument("--smiles", type=str, default=None)
    parser.add_argument("--offline-pdf", type=str, default=None)
    parser.add_argument("--run-lane", type=str, default="atb_cache_only", choices=sorted(SUPPORTED_RUN_LANES))
    parser.add_argument("--output-layout", type=str, default=OUTPUT_LAYOUT_CASE_CENTRIC, choices=sorted(OUTPUT_LAYOUTS))
    parser.add_argument("--retain-runs", type=int, default=10)
    parser.add_argument("--output-timestamp-format", type=str, default=TIMESTAMP_FORMAT_UTC_COMPACT, choices=sorted(TIMESTAMP_FORMATS))
    parser.add_argument(
        "--write-legacy-run-view",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write compatibility pointers in legacy run-id directories.",
    )
    parser.add_argument("--emit-stage-snapshots", action="store_true")
    parser.add_argument("--stage-snapshots-dir", type=str, default="cases/stage_snapshots")
    parser.add_argument("--artifacts-dir", type=str, default="artifacts")
    parser.add_argument("--llm-response-dir", type=str, default="artifacts/llm_responses")
    parser.add_argument("--outdir", type=str, default="cases/multi_agent")
    parser.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    parser.add_argument("--model", type=str, default="gpt-5.2")
    parser.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    parser.add_argument("--llm-max-output-tokens", type=int, default=1500)
    parser.add_argument("--llm-reasoning-effort", type=str, default="medium")
    parser.add_argument("--llm-temperature", type=float, default=0.2)
    parser.add_argument("--llm-use-json-schema", action="store_true")
    parser.add_argument("--mineru-bin", type=str, default="third_party/MinerU/.venv/bin/mineru")
    parser.add_argument("--mineru-output-root", type=str, default="third_party/MinerU/output")
    parser.add_argument("--mineru-backend", type=str, default="hybrid-auto-engine")
    parser.add_argument("--mineru-method", type=str, default=None)
    parser.add_argument("--mineru-lang", type=str, default=None)
    parser.add_argument("--mineru-start-page", type=int, default=None)
    parser.add_argument("--mineru-end-page", type=int, default=None)
    parser.add_argument("--mineru-timeout-sec", type=int, default=1200)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--neighbor-topk", type=int, default=10)
    parser.add_argument("--reference-index-root", type=str, default=DEFAULT_REFERENCE_INDEX_ROOT)
    parser.add_argument("--reference-view", type=str, default=REFERENCE_VIEW_ALL, choices=sorted(REFERENCE_VIEWS))
    parser.add_argument("--iterative", action="store_true", help="Run iterative closure rounds after setup (Data/Chem/Ready).")
    parser.add_argument(
        "--round-runner-mode",
        type=str,
        default=ROUND_RUNNER_MODE_DRYRUN_THEN_COMMIT,
        choices=sorted(ROUND_RUNNER_MODES),
    )
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--round-start-profile", type=str, default="R0")
    parser.add_argument("--evaluator-use-llm", action="store_true", help="Enable optional LLM critique layer in evaluator.")
    parser.add_argument("--evaluator-model", type=str, default=None, help="Optional evaluator LLM model override.")
    parser.add_argument("--evaluator-reasoning-effort", type=str, default=None, help="Optional evaluator reasoning effort override.")
    parser.add_argument(
        "--evaluator-confidence-adjustment-enabled",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow bounded evaluator confidence adjustment (does not modify mechanism label).",
    )
    parser.add_argument("--evaluator-confidence-adjustment-max-abs-delta", type=float, default=0.05)
    parser.add_argument(
        "--pre-r2-failure-recovery-mode",
        type=str,
        default="force_r2",
        choices=["force_r2", "degraded_retry"],
        help="Recovery strategy when master fails before R2.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    summary = run_one(args)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

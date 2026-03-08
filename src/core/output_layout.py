"""
Output layout helpers for case-centric/run-centric runtime artifacts.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.io import save_json

OUTPUT_LAYOUT_CASE_CENTRIC = "case_centric"
OUTPUT_LAYOUT_RUN_CENTRIC = "run_centric"
OUTPUT_LAYOUTS = {OUTPUT_LAYOUT_CASE_CENTRIC, OUTPUT_LAYOUT_RUN_CENTRIC}

TIMESTAMP_FORMAT_UTC_COMPACT = "utc_compact"
TIMESTAMP_FORMATS = {TIMESTAMP_FORMAT_UTC_COMPACT}


@dataclass(frozen=True)
class OutputLayoutPaths:
    layout: str
    case_id: str
    run_id: str
    run_name: str
    run_time_iso: str
    artifacts_root: Path
    run_dir: Path
    llm_run_dir: Path
    rounds_dir: Path
    run_summary_path: Path
    case_root: Optional[Path] = None
    latest_dir: Optional[Path] = None
    latest_case_path: Optional[Path] = None
    latest_run_summary_path: Optional[Path] = None
    latest_quick_view_path: Optional[Path] = None
    latest_rounds_dir: Optional[Path] = None
    latest_llm_dir: Optional[Path] = None
    latest_meta_path: Optional[Path] = None
    history_index_path: Optional[Path] = None
    legacy_run_dir: Optional[Path] = None
    legacy_llm_dir: Optional[Path] = None


def utc_compact_timestamp(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def iso_utc_timestamp(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_if_exists(path: Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _legacy_multi_agent_root(artifacts_root: Path) -> Path:
    root = Path(artifacts_root)
    return root if root.name == "multi_agent" else root / "multi_agent"


def plan_output_layout(
    *,
    artifacts_root: Path,
    llm_response_root: Path,
    case_id: str,
    run_id: str,
    layout: str = OUTPUT_LAYOUT_CASE_CENTRIC,
    timestamp_format: str = TIMESTAMP_FORMAT_UTC_COMPACT,
    write_legacy_run_view: bool = True,
) -> OutputLayoutPaths:
    if layout not in OUTPUT_LAYOUTS:
        raise ValueError(f"unsupported_output_layout:{layout}")
    if timestamp_format not in TIMESTAMP_FORMATS:
        raise ValueError(f"unsupported_output_timestamp_format:{timestamp_format}")

    run_key = utc_compact_timestamp() if timestamp_format == TIMESTAMP_FORMAT_UTC_COMPACT else utc_compact_timestamp()
    run_name = f"{run_key}__{run_id[:8]}"
    run_time_iso = iso_utc_timestamp()
    artifacts_root = Path(artifacts_root)
    llm_response_root = Path(llm_response_root)

    if layout == OUTPUT_LAYOUT_CASE_CENTRIC:
        case_root = artifacts_root / "cases" / str(case_id)
        run_dir = case_root / "runs" / run_name
        llm_run_dir = run_dir / "llm"
        rounds_dir = run_dir / "rounds"
        latest_dir = case_root / "latest"
        latest_case_path = latest_dir / "case.json"
        latest_run_summary_path = latest_dir / "run_summary.json"
        latest_quick_view_path = latest_dir / "quick_view.json"
        latest_rounds_dir = latest_dir / "rounds"
        latest_llm_dir = latest_dir / "llm"
        latest_meta_path = case_root / "latest.json"
        history_index_path = case_root / "history_index.json"
        legacy_run_dir = _legacy_multi_agent_root(artifacts_root) / run_id if write_legacy_run_view else None
        legacy_llm_dir = llm_response_root / run_id if write_legacy_run_view else None
    else:
        case_root = None
        run_dir = artifacts_root / run_id
        llm_run_dir = llm_response_root / run_id
        rounds_dir = llm_run_dir / "rounds"
        latest_dir = None
        latest_case_path = None
        latest_run_summary_path = None
        latest_quick_view_path = None
        latest_rounds_dir = None
        latest_llm_dir = None
        latest_meta_path = None
        history_index_path = None
        legacy_run_dir = None
        legacy_llm_dir = None

    run_summary_path = run_dir / "run_summary.json"
    return OutputLayoutPaths(
        layout=layout,
        case_id=str(case_id),
        run_id=str(run_id),
        run_name=run_name,
        run_time_iso=run_time_iso,
        artifacts_root=artifacts_root,
        run_dir=run_dir,
        llm_run_dir=llm_run_dir,
        rounds_dir=rounds_dir,
        run_summary_path=run_summary_path,
        case_root=case_root,
        latest_dir=latest_dir,
        latest_case_path=latest_case_path,
        latest_run_summary_path=latest_run_summary_path,
        latest_quick_view_path=latest_quick_view_path,
        latest_rounds_dir=latest_rounds_dir,
        latest_llm_dir=latest_llm_dir,
        latest_meta_path=latest_meta_path,
        history_index_path=history_index_path,
        legacy_run_dir=legacy_run_dir,
        legacy_llm_dir=legacy_llm_dir,
    )


def _copy_tree(src: Path, dst: Path) -> None:
    s = Path(src)
    d = Path(dst)
    if not s.exists() or not s.is_dir():
        return
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(s, d)


def refresh_latest_case_view(
    *,
    paths: OutputLayoutPaths,
    case_path: Path,
    run_summary_path: Path,
    quick_view_path: Path,
) -> None:
    if paths.layout != OUTPUT_LAYOUT_CASE_CENTRIC:
        return
    assert paths.latest_dir is not None
    assert paths.latest_case_path is not None
    assert paths.latest_run_summary_path is not None
    assert paths.latest_quick_view_path is not None
    assert paths.latest_rounds_dir is not None
    assert paths.latest_llm_dir is not None

    paths.latest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(case_path, paths.latest_case_path)
    shutil.copyfile(run_summary_path, paths.latest_run_summary_path)
    shutil.copyfile(quick_view_path, paths.latest_quick_view_path)
    _copy_tree(paths.rounds_dir, paths.latest_rounds_dir)
    _copy_tree(paths.llm_run_dir, paths.latest_llm_dir)


def _is_safe_prune_target(path: Path, *, case_root: Path) -> bool:
    p = Path(path).resolve()
    runs_root = (Path(case_root) / "runs").resolve()
    return p.is_relative_to(runs_root)


def update_history_index(
    *,
    paths: OutputLayoutPaths,
    run_record: Dict[str, Any],
    retain_runs: int,
) -> Dict[str, Any]:
    if paths.layout != OUTPUT_LAYOUT_CASE_CENTRIC or paths.history_index_path is None:
        return {}

    retain = max(1, int(retain_runs))
    payload = _load_json_if_exists(paths.history_index_path)
    runs = payload.get("runs")
    if not isinstance(runs, list):
        runs = []

    normalized: List[Dict[str, Any]] = []
    for row in runs:
        if not isinstance(row, dict):
            continue
        if str(row.get("run_id") or "") == paths.run_id:
            continue
        normalized.append(row)

    normalized.insert(0, run_record)
    dropped = normalized[retain:]
    kept = normalized[:retain]
    payload = {
        "case_id": paths.case_id,
        "retain_runs": retain,
        "updated_at": iso_utc_timestamp(),
        "runs": kept,
    }
    save_json(paths.history_index_path, payload)

    if paths.case_root is not None:
        for row in dropped:
            run_dir = row.get("run_dir")
            if not run_dir:
                continue
            try:
                run_path = Path(str(run_dir))
                if run_path.exists() and _is_safe_prune_target(run_path, case_root=paths.case_root):
                    shutil.rmtree(run_path)
            except Exception:
                continue
    return payload


def write_latest_pointer(
    *,
    paths: OutputLayoutPaths,
    latest_payload: Dict[str, Any],
) -> None:
    if paths.layout != OUTPUT_LAYOUT_CASE_CENTRIC or paths.latest_meta_path is None:
        return
    save_json(paths.latest_meta_path, latest_payload)


def write_legacy_pointers(
    *,
    paths: OutputLayoutPaths,
    pointer_payload: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {"legacy_run_summary": None, "legacy_llm_pointer": None}

    if paths.legacy_run_dir is not None:
        run_summary_ptr = paths.legacy_run_dir / "run_summary.json"
        save_json(run_summary_ptr, pointer_payload)
        out["legacy_run_summary"] = str(run_summary_ptr)

    if paths.legacy_llm_dir is not None:
        llm_ptr = paths.legacy_llm_dir / "pointer.json"
        save_json(llm_ptr, pointer_payload)
        out["legacy_llm_pointer"] = str(llm_ptr)

    return out

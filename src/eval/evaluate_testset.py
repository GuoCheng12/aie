"""
Benchmark runner for test.csv mechanism-label evaluation.

Evaluation-only path:
- Calls release runtime (`src.orchestration.run_one.run_one`)
- Does not change inference strategy
- Does not write evidence table
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.eval.label_normalizer import CANONICAL_LABELS, normalize_label
from src.orchestration.run_one import build_parser as build_run_one_parser
from src.orchestration.run_one import run_one as runtime_run_one

STATUS_OK = "ok"
STATUS_FAILED_RUN = "failed_run"
STATUS_MISSING_PRED = "missing_pred"
STATUS_MISSING_GT = "missing_gt"

PREDICTIONS_COLUMNS = [
    "row_index",
    "case_id",
    "inchikey",
    "smiles",
    "y_true_raw",
    "y_true",
    "y_pred_raw",
    "y_pred",
    "status",
    "error",
    "run_id",
    "model",
    "case_path",
    "run_summary_path",
    "primary_output_dir",
]


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _truncate(text: Optional[str], *, max_len: int = 64) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max(0, max_len - 3)] + "..."


def _read_status_round(status_path: Path) -> int:
    if not status_path.exists():
        return 0
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("round_index", 0)))
    except Exception:
        return 0


def _progress_line(
    *,
    done: int,
    total: int,
    sample_index: int,
    smiles: Optional[str],
    round_index: int,
    accuracy: Optional[float],
) -> str:
    width = 28
    ratio = 0.0 if total <= 0 else float(done) / float(total)
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(width * ratio))
    bar = "#" * filled + "-" * (width - filled)
    acc_txt = "n/a" if accuracy is None else f"{accuracy:.4f}"
    return (
        f"[{bar}] {done}/{total} "
        f"| sample={sample_index + 1}/{total} "
        f"| smiles={_truncate(smiles, max_len=54)} "
        f"| round={round_index + 1} "
        f"| acc={acc_txt}"
    )


def _print_progress(line: str, *, final: bool = False) -> None:
    if not sys.stdout.isatty():
        if final:
            print(line, flush=True)
        return
    end = "\n" if final else ""
    print(f"\r{line}", end=end, flush=True)


def _running_accuracy(records_by_idx: Dict[int, Dict[str, Any]]) -> Optional[float]:
    ok_rows = [r for r in records_by_idx.values() if str(r.get("status")) == STATUS_OK]
    if not ok_rows:
        return None
    total = len(ok_rows)
    correct = sum(1 for r in ok_rows if str(r.get("y_true") or "") == str(r.get("y_pred") or ""))
    return float(correct) / float(total)


@contextmanager
def _mute_runtime_progress_events():
    import src.orchestration.round_runner as rr_mod
    import src.orchestration.run_one as run_one_mod

    old_run_one_emit = getattr(run_one_mod, "emit_progress_event", None)
    old_round_emit = getattr(rr_mod, "emit_progress_event", None)
    old_round_err = getattr(rr_mod, "emit_error_summary", None)
    run_one_mod.emit_progress_event = lambda **_: None
    rr_mod.emit_progress_event = lambda **_: None
    rr_mod.emit_error_summary = lambda **_: None
    try:
        yield
    finally:
        run_one_mod.emit_progress_event = old_run_one_emit
        rr_mod.emit_progress_event = old_round_emit
        rr_mod.emit_error_summary = old_round_err


def _silence_external_runtime_logs() -> None:
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    try:
        from rdkit import RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
    except Exception:
        pass


def _run_one_with_progress(
    *,
    run_args: argparse.Namespace,
    status_path: Path,
    progress_cb,
):
    result: Dict[str, Any] = {}
    error: Dict[str, Exception] = {}

    def _worker() -> None:
        try:
            with _mute_runtime_progress_events():
                result["summary"] = runtime_run_one(run_args)
        except Exception as exc:  # pragma: no cover - exercised via caller paths
            error["exc"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    while t.is_alive():
        progress_cb(_read_status_round(status_path))
        time.sleep(0.25)
    t.join()
    progress_cb(_read_status_round(status_path))
    if "exc" in error:
        raise error["exc"]
    return result.get("summary")


def _read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_predictions_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTIONS_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in PREDICTIONS_COLUMNS})


def _load_existing_predictions(path: Path) -> Dict[int, Dict[str, Any]]:
    if not path.exists():
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row.get("row_index", ""))
            except Exception:
                continue
            out[idx] = dict(row)
    return out


def extract_pred_label(case_json: Dict[str, Any]) -> Optional[str]:
    root = case_json.get("master_reasoning")
    if not isinstance(root, dict):
        reasoning = case_json.get("reasoning")
        if isinstance(reasoning, dict):
            candidate = reasoning.get("master_reasoning")
            if isinstance(candidate, dict):
                root = candidate
    if not isinstance(root, dict):
        return None
    mech = root.get("mechanism_claim")
    if not isinstance(mech, dict):
        return None
    primary = mech.get("primary_hypothesis")
    if not isinstance(primary, dict):
        return None
    label = str(primary.get("mechanism_label") or "").strip()
    return label or None


def _build_run_args(
    *,
    eval_args: argparse.Namespace,
    row_index: int,
    runtime_artifacts_dir: Path,
    runtime_case_dir: Path,
) -> argparse.Namespace:
    parser = build_run_one_parser()
    base = parser.parse_args([])
    ns = argparse.Namespace(**vars(base))
    ns.test_csv = str(eval_args.test_csv)
    ns.row_index = int(row_index)
    ns.code = None
    ns.smiles = None
    ns.offline_pdf = None
    ns.run_lane = str(eval_args.run_lane)
    ns.output_layout = str(eval_args.output_layout)
    ns.retain_runs = int(eval_args.retain_runs)
    ns.output_timestamp_format = "utc_compact"
    ns.write_legacy_run_view = bool(eval_args.write_legacy_run_view)
    ns.emit_stage_snapshots = False
    ns.stage_snapshots_dir = str(runtime_artifacts_dir / "stage_snapshots")
    ns.artifacts_dir = str(runtime_artifacts_dir)
    ns.llm_response_dir = str(runtime_artifacts_dir / "llm_responses")
    ns.outdir = str(runtime_case_dir)
    ns.base_url = str(eval_args.base_url)
    ns.model = str(eval_args.model)
    ns.llm_api_key_env = str(eval_args.llm_api_key_env)
    ns.llm_max_output_tokens = int(eval_args.llm_max_output_tokens)
    ns.llm_reasoning_effort = str(eval_args.reasoning_effort)
    ns.llm_temperature = float(eval_args.temperature)
    ns.llm_use_json_schema = bool(eval_args.llm_use_json_schema)
    ns.force = bool(eval_args.force)
    ns.neighbor_topk = int(eval_args.neighbor_topk)
    ns.iterative = bool(eval_args.iterative)
    ns.round_runner_mode = str(eval_args.round_runner_mode)
    ns.max_rounds = int(eval_args.max_rounds)
    ns.round_start_profile = str(eval_args.round_start_profile)
    ns.pre_r2_failure_recovery_mode = "force_r2"
    ns.evaluator_use_llm = bool(eval_args.evaluator_use_llm)
    ns.evaluator_model = str(eval_args.evaluator_model) if eval_args.evaluator_model else None
    ns.evaluator_reasoning_effort = (
        str(eval_args.evaluator_reasoning_effort) if eval_args.evaluator_reasoning_effort else None
    )
    ns.evaluator_confidence_adjustment_enabled = False
    ns.evaluator_confidence_adjustment_max_abs_delta = 0.05
    return ns


def _safe_ratio(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(float(num) / float(den), 6)


def _compute_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_rows = len(rows)
    status_counter = Counter(str(r.get("status") or "") for r in rows)

    covered_rows = [
        r for r in rows if str(r.get("status")) in {STATUS_OK, STATUS_MISSING_GT} and str(r.get("y_pred") or "").strip()
    ]
    ok_rows = [r for r in rows if str(r.get("status")) == STATUS_OK]

    unknown_in_covered = sum(1 for r in covered_rows if str(r.get("y_pred") or "") == "unknown")
    coverage = _safe_ratio(len(covered_rows), total_rows)
    unknown_rate = _safe_ratio(unknown_in_covered, len(covered_rows))

    y_true = [str(r.get("y_true")) for r in ok_rows]
    y_pred = [str(r.get("y_pred")) for r in ok_rows]
    labels_set = set(y_true) | set(y_pred)
    labels = [x for x in CANONICAL_LABELS if x in labels_set]
    for x in sorted(labels_set):
        if x not in labels:
            labels.append(x)

    confusion: Dict[str, Dict[str, int]] = {yt: {yp: 0 for yp in labels} for yt in labels}
    for yt, yp in zip(y_true, y_pred):
        if yt not in confusion:
            confusion[yt] = {k: 0 for k in labels}
        if yp not in confusion[yt]:
            confusion[yt][yp] = 0
        confusion[yt][yp] += 1

    per_class: Dict[str, Dict[str, Any]] = {}
    f1_values: List[float] = []
    for label in labels:
        tp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == label and yp == label)
        fp = sum(1 for yt, yp in zip(y_true, y_pred) if yt != label and yp == label)
        fn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == label and yp != label)
        support = sum(1 for yt in y_true if yt == label)
        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        per_class[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        }
        f1_values.append(f1)

    correct = sum(1 for yt, yp in zip(y_true, y_pred) if yt == yp)
    accuracy = _safe_ratio(correct, len(ok_rows))
    macro_f1 = round(sum(f1_values) / len(f1_values), 6) if f1_values else None

    return {
        "counts": {
            "total_rows": total_rows,
            "status": dict(status_counter),
            "ok_rows": len(ok_rows),
            "covered_rows": len(covered_rows),
            "unknown_predictions_in_covered": unknown_in_covered,
        },
        "metrics": {
            "top1_accuracy": accuracy,
            "macro_f1": macro_f1,
            "coverage": coverage,
            "unknown_rate": unknown_rate,
            "per_class_precision_recall_f1": per_class,
            "confusion_matrix": confusion,
            "labels": labels,
        },
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    cfg = report.get("config") or {}
    counts = (report.get("results") or {}).get("counts") or {}
    metrics = (report.get("results") or {}).get("metrics") or {}
    per_class = metrics.get("per_class_precision_recall_f1") or {}
    confusion = metrics.get("confusion_matrix") or {}
    labels = metrics.get("labels") or []

    lines = [
        "# Testset Mechanism Benchmark (v0)",
        "",
        "## Run Config",
        f"- test_csv: `{cfg.get('test_csv')}`",
        f"- run_lane: `{cfg.get('run_lane')}`",
        f"- model: `{cfg.get('model')}`",
        f"- base_url: `{cfg.get('base_url')}`",
        f"- reasoning_effort: `{cfg.get('reasoning_effort')}`",
        f"- temperature: `{cfg.get('temperature')}`",
        f"- llm_use_json_schema: `{cfg.get('llm_use_json_schema')}`",
        f"- seed_supported: `{cfg.get('seed_supported')}`",
        "",
        "## Key Metrics",
        f"- top1_accuracy (ok subset): `{metrics.get('top1_accuracy')}`",
        f"- macro_f1 (ok subset): `{metrics.get('macro_f1')}`",
        f"- coverage (full set): `{metrics.get('coverage')}`",
        f"- unknown_rate (covered subset): `{metrics.get('unknown_rate')}`",
        "",
        "## Counts",
        f"- total_rows: `{counts.get('total_rows')}`",
        f"- ok_rows: `{counts.get('ok_rows')}`",
        f"- covered_rows: `{counts.get('covered_rows')}`",
        f"- status: `{counts.get('status')}`",
        "",
        "## Per-class Precision/Recall/F1",
        "",
        "| label | precision | recall | f1 | support |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in labels:
        row = per_class.get(label) or {}
        lines.append(
            f"| {label} | {row.get('precision')} | {row.get('recall')} | {row.get('f1')} | {row.get('support')} |"
        )

    if labels:
        lines += [
            "",
            "## Confusion Matrix (y_true rows, y_pred columns)",
            "",
            "| y_true \\\\ y_pred | " + " | ".join(labels) + " |",
            "|" + "---|" * (len(labels) + 1),
        ]
        for yt in labels:
            row_vals = []
            row = confusion.get(yt) or {}
            for yp in labels:
                row_vals.append(str(row.get(yp, 0)))
            lines.append(f"| {yt} | " + " | ".join(row_vals) + " |")
    return "\n".join(lines) + "\n"


def _status_and_error(
    *,
    y_true_raw: str,
    y_pred_raw: Optional[str],
    run_error: Optional[str],
) -> tuple[str, Optional[str]]:
    if run_error:
        return STATUS_FAILED_RUN, run_error
    if not str(y_pred_raw or "").strip():
        return STATUS_MISSING_PRED, None
    if not str(y_true_raw or "").strip():
        return STATUS_MISSING_GT, None
    return STATUS_OK, None


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    _silence_external_runtime_logs()
    test_csv = Path(args.test_csv)
    rows = _read_rows(test_csv)
    start_row = max(0, int(args.start_row))
    end_row = len(rows) if args.max_rows is None else min(len(rows), start_row + max(0, int(args.max_rows)))

    eval_id = str(args.eval_id or _utc_compact())
    eval_dir = Path(args.outdir) / eval_id
    runtime_artifacts_dir = eval_dir / "runtime_artifacts"
    runtime_case_dir = eval_dir / "runtime_cases"
    predictions_path = eval_dir / "predictions.csv"
    report_json_path = eval_dir / "evaluation_report.json"
    report_md_path = eval_dir / "evaluation_report.md"
    failed_index_path = eval_dir / "failed_cases_index.json"

    eval_dir.mkdir(parents=True, exist_ok=True)
    runtime_artifacts_dir.mkdir(parents=True, exist_ok=True)
    runtime_case_dir.mkdir(parents=True, exist_ok=True)

    records_by_idx = {} if bool(args.force) else _load_existing_predictions(predictions_path)
    total_to_run = max(0, end_row - start_row)

    for row_index in range(start_row, end_row):
        if row_index in records_by_idx:
            continue
        row = rows[row_index]
        y_true_raw = str(row.get("mechanism_id") or "").strip()
        y_true = normalize_label(y_true_raw)
        case_id = str(row.get("inchikey") or "").strip() or None
        smiles = str(row.get("SMILES") or "").strip() or None
        pred_raw = None
        pred_norm = None
        run_id = None
        error = None
        case_path = None
        run_summary_path = None
        primary_output_dir = None
        try:
            run_args = _build_run_args(
                eval_args=args,
                row_index=row_index,
                runtime_artifacts_dir=runtime_artifacts_dir,
                runtime_case_dir=runtime_case_dir,
            )
            status_path = Path(run_args.artifacts_dir) / "run_status.json"

            def _cb(round_idx: int) -> None:
                done = sum(1 for i in range(start_row, end_row) if i in records_by_idx)
                acc = _running_accuracy(records_by_idx)
                if bool(args.show_progress):
                    _print_progress(
                        _progress_line(
                            done=done,
                            total=total_to_run,
                            sample_index=row_index - start_row,
                            smiles=smiles,
                            round_index=round_idx,
                            accuracy=acc,
                        ),
                        final=False,
                    )

            summary = _run_one_with_progress(
                run_args=run_args,
                status_path=status_path,
                progress_cb=_cb,
            )
            run_id = summary.get("run_id")
            case_id = summary.get("case_id") or case_id
            case_path = summary.get("case_path")
            run_summary_path = summary.get("run_summary_path")
            primary_output_dir = summary.get("primary_output_dir")
            if case_path:
                cp = Path(str(case_path))
                if cp.exists():
                    case_json = json.loads(cp.read_text(encoding="utf-8"))
                    pred_raw = extract_pred_label(case_json)
                    pred_norm = normalize_label(pred_raw) if pred_raw else None
        except Exception as exc:  # keep full testset progressing
            error = str(exc)

        status, status_error = _status_and_error(
            y_true_raw=y_true_raw,
            y_pred_raw=pred_raw,
            run_error=error,
        )
        records_by_idx[row_index] = {
            "row_index": row_index,
            "case_id": case_id,
            "inchikey": str(row.get("inchikey") or "").strip() or None,
            "smiles": smiles,
            "y_true_raw": y_true_raw,
            "y_true": y_true,
            "y_pred_raw": pred_raw,
            "y_pred": pred_norm,
            "status": status,
            "error": status_error,
            "run_id": run_id,
            "model": args.model,
            "case_path": case_path,
            "run_summary_path": run_summary_path,
            "primary_output_dir": primary_output_dir,
        }
        _write_predictions_csv(predictions_path, [records_by_idx[k] for k in sorted(records_by_idx.keys())])
        if bool(args.show_progress):
            done = sum(1 for i in range(start_row, end_row) if i in records_by_idx)
            acc = _running_accuracy(records_by_idx)
            _print_progress(
                _progress_line(
                    done=done,
                    total=total_to_run,
                    sample_index=row_index - start_row,
                    smiles=smiles,
                    round_index=_read_status_round(Path(run_args.artifacts_dir) / "run_status.json"),
                    accuracy=acc,
                ),
                final=(done >= total_to_run),
            )

    ordered = [records_by_idx[k] for k in sorted(records_by_idx.keys())]
    results = _compute_metrics(ordered)

    failed_index = [
        {
            "row_index": r.get("row_index"),
            "status": r.get("status"),
            "error": r.get("error"),
            "run_id": r.get("run_id"),
            "case_id": r.get("case_id"),
            "case_path": r.get("case_path"),
            "run_summary_path": r.get("run_summary_path"),
            "primary_output_dir": r.get("primary_output_dir"),
        }
        for r in ordered
        if str(r.get("status")) != STATUS_OK
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eval_id": eval_id,
        "config": {
            "test_csv": str(test_csv),
            "run_lane": str(args.run_lane),
            "runtime_mode": "single_pass_case_run",
            "iterative": bool(args.iterative),
            "round_runner_mode": str(args.round_runner_mode),
            "max_rounds": int(args.max_rounds),
            "round_start_profile": str(args.round_start_profile),
            "model": str(args.model),
            "base_url": str(args.base_url),
            "reasoning_effort": str(args.reasoning_effort),
            "evaluator_use_llm": bool(args.evaluator_use_llm),
            "evaluator_model": str(args.evaluator_model) if args.evaluator_model else None,
            "evaluator_reasoning_effort": str(args.evaluator_reasoning_effort) if args.evaluator_reasoning_effort else None,
            "temperature": float(args.temperature),
            "llm_use_json_schema": bool(args.llm_use_json_schema),
            "seed_supported": False,
            "seed": None,
            "max_rows": args.max_rows,
            "start_row": start_row,
            "neighbor_topk": int(args.neighbor_topk),
        },
        "results": results,
        "artifacts": {
            "predictions_csv": str(predictions_path),
            "evaluation_report_json": str(report_json_path),
            "evaluation_report_md": str(report_md_path),
            "failed_cases_index_json": str(failed_index_path),
            "runtime_artifacts_dir": str(runtime_artifacts_dir),
            "runtime_case_dir": str(runtime_case_dir),
        },
    }

    _write_json(report_json_path, report)
    report_md_path.write_text(_render_markdown(report), encoding="utf-8")
    _write_json(failed_index_path, {"failed_cases": failed_index})

    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate mechanism-label accuracy on data/test.csv.")
    p.add_argument("--test-csv", type=str, default="data/test.csv")
    p.add_argument("--run-lane", type=str, default="atb_cache_only", choices=["atb_cache_only", "offline_pdf", "full"])
    p.add_argument("--model", type=str, default="gpt-5.2")
    p.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    p.add_argument("--reasoning-effort", type=str, default="medium")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--llm-use-json-schema", action="store_true")
    p.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    p.add_argument("--llm-max-output-tokens", type=int, default=1500)
    p.add_argument("--neighbor-topk", type=int, default=10)
    p.add_argument("--outdir", type=str, default="artifacts/eval")
    p.add_argument("--eval-id", type=str, default=None)
    p.add_argument("--start-row", type=int, default=0)
    p.add_argument("--max-rows", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--output-layout", type=str, default="case_centric", choices=["case_centric", "run_centric"])
    p.add_argument("--retain-runs", type=int, default=10)
    p.add_argument("--write-legacy-run-view", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--iterative", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--round-runner-mode", type=str, default="dryrun_then_commit", choices=["dryrun_then_commit", "commit_all_rounds"])
    p.add_argument("--max-rounds", type=int, default=4)
    p.add_argument("--round-start-profile", type=str, default="R0")
    p.add_argument("--evaluator-use-llm", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--evaluator-model", type=str, default=None)
    p.add_argument("--evaluator-reasoning-effort", type=str, default=None)
    p.add_argument("--show-progress", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--print-report", action=argparse.BooleanOptionalAction, default=False)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    report = run_benchmark(args)
    if bool(args.print_report):
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

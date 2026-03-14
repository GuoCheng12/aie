"""Compare mechanism-label performance between multi-agent runtime and zero-shot baseline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.eval import evaluate_testset
from src.eval.label_normalizer import (
    CANONICAL_LABELS,
    normalize_ground_truth_label,
    normalize_prediction_label,
)
from src.eval.zero_shot_baseline import run_zero_shot_label

PROTOCOL_MULTI_AGENT = "multi_agent"
PROTOCOL_ZERO_SHOT = "zero_shot"
PROTOCOL_COMPARE = "compare"
PROTOCOL_CHOICES = [PROTOCOL_MULTI_AGENT, PROTOCOL_ZERO_SHOT, PROTOCOL_COMPARE]

ZERO_SHOT_COLUMNS = [
    "row_index",
    "inchikey",
    "smiles",
    "y_true_raw",
    "y_true",
    "y_pred_raw",
    "y_pred",
    "status",
    "error",
    "model",
    "trace_path",
]

MERGED_COLUMNS = [
    "row_index",
    "case_id",
    "inchikey",
    "smiles",
    "y_true_raw",
    "y_true",
    "multi_agent_pred_raw",
    "multi_agent_pred",
    "multi_agent_status",
    "multi_agent_error",
    "multi_agent_run_id",
    "zero_shot_pred_raw",
    "zero_shot_pred",
    "zero_shot_status",
    "zero_shot_error",
    "model",
    "agree",
    "winner",
]

STATUS_OK = evaluate_testset.STATUS_OK
STATUS_FAILED_RUN = evaluate_testset.STATUS_FAILED_RUN
STATUS_MISSING_PRED = evaluate_testset.STATUS_MISSING_PRED
STATUS_MISSING_GT = evaluate_testset.STATUS_MISSING_GT
LABEL_OTHER = "other"


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_rows(path: Path) -> List[Dict[str, str]]:
    return evaluate_testset._read_rows(path)  # type: ignore[attr-defined]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def _load_csv_by_index(path: Path) -> Dict[int, Dict[str, Any]]:
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


def _truncate(text: Optional[str], max_len: int = 54) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max(0, max_len - 3)] + "..."


def _safe_ratio(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(float(num) / float(den), 6)


def _progress_line(*, protocol: str, done: int, total: int, sample_index: int, smiles: Optional[str], accuracy: Optional[float]) -> str:
    width = 28
    ratio = 0.0 if total <= 0 else min(1.0, max(0.0, float(done) / float(total)))
    filled = int(round(width * ratio))
    bar = "#" * filled + "-" * (width - filled)
    acc_txt = "n/a" if accuracy is None else f"{accuracy:.4f}"
    return (
        f"[{bar}] {done}/{total} "
        f"| arm={protocol} "
        f"| sample={sample_index + 1}/{total} "
        f"| smiles={_truncate(smiles)} "
        f"| acc={acc_txt}"
    )


def _print_progress(line: str, *, final: bool = False) -> None:
    if not sys.stdout.isatty():
        if final:
            print(line, flush=True)
        return
    end = "\n" if final else ""
    print(f"\r{line}", end=end, flush=True)


def _strict_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts = Counter(str(r.get("status") or "") for r in rows)
    valid_gt_rows = [r for r in rows if str(r.get("status")) != STATUS_MISSING_GT]
    valid_gt_rows_excluding_other = [r for r in valid_gt_rows if str(r.get("y_true") or "") != LABEL_OTHER]
    correct_rows = [
        r for r in valid_gt_rows
        if str(r.get("status")) == STATUS_OK and str(r.get("y_true") or "") == str(r.get("y_pred") or "")
    ]
    correct_rows_excluding_other = [
        r
        for r in valid_gt_rows_excluding_other
        if str(r.get("status")) == STATUS_OK and str(r.get("y_true") or "") == str(r.get("y_pred") or "")
    ]
    covered_rows = [
        r for r in rows if str(r.get("status")) in {STATUS_OK, STATUS_MISSING_GT} and str(r.get("y_pred") or "").strip()
    ]
    unknown_predictions = sum(1 for r in covered_rows if str(r.get("y_pred") or "") == "unknown")

    per_label: Dict[str, Dict[str, int]] = {}
    for label in CANONICAL_LABELS:
        support = sum(1 for r in valid_gt_rows if str(r.get("y_true") or "") == label)
        correct = sum(
            1
            for r in valid_gt_rows
            if str(r.get("y_true") or "") == label
            and str(r.get("status")) == STATUS_OK
            and str(r.get("y_pred") or "") == label
        )
        wrong = support - correct
        if support or correct or wrong:
            per_label[label] = {"support": support, "correct": correct, "wrong": wrong}

    return {
        "counts": {
            "total_rows": len(rows),
            "valid_gt_rows": len(valid_gt_rows),
            "covered_rows": len(covered_rows),
            "status_counts": dict(status_counts),
            "unknown_predictions_in_covered": unknown_predictions,
            "missing_gt_count": status_counts.get(STATUS_MISSING_GT, 0),
        },
        "metrics": {
            "strict_top1_accuracy": _safe_ratio(len(correct_rows), len(valid_gt_rows)),
            "strict_top1_accuracy_including_other": _safe_ratio(len(correct_rows), len(valid_gt_rows)),
            "strict_top1_accuracy_excluding_other_gt": _safe_ratio(
                len(correct_rows_excluding_other), len(valid_gt_rows_excluding_other)
            ),
            "coverage": _safe_ratio(len(covered_rows), len(rows)),
            "unknown_rate": _safe_ratio(unknown_predictions, len(covered_rows)),
            "per_label_support_correct_wrong": per_label,
        },
    }


def _running_strict_accuracy(records_by_idx: Dict[int, Dict[str, Any]]) -> Optional[float]:
    ordered = [records_by_idx[k] for k in sorted(records_by_idx.keys())]
    return (_strict_summary(ordered).get("metrics") or {}).get("strict_top1_accuracy")


def _build_multi_agent_args(args: argparse.Namespace, eval_dir: Path) -> argparse.Namespace:
    parser = evaluate_testset.build_parser()
    ns = parser.parse_args([])
    ns.test_csv = str(args.test_csv)
    ns.run_lane = str(args.run_lane)
    ns.model = str(args.model)
    ns.base_url = str(args.base_url)
    ns.reasoning_effort = str(args.reasoning_effort)
    ns.temperature = float(args.temperature)
    ns.llm_use_json_schema = bool(args.llm_use_json_schema)
    ns.llm_api_key_env = str(args.llm_api_key_env)
    ns.llm_max_output_tokens = int(args.llm_max_output_tokens)
    ns.neighbor_topk = int(args.neighbor_topk)
    ns.outdir = str(eval_dir)
    ns.eval_id = PROTOCOL_MULTI_AGENT
    ns.start_row = int(args.start_row)
    ns.max_rows = args.max_rows
    ns.force = bool(args.force)
    ns.output_layout = str(args.output_layout)
    ns.retain_runs = int(args.retain_runs)
    ns.write_legacy_run_view = bool(args.write_legacy_run_view)
    ns.iterative = bool(args.iterative)
    ns.round_runner_mode = str(args.round_runner_mode)
    ns.max_rounds = int(args.max_rounds)
    ns.round_start_profile = str(args.round_start_profile)
    ns.evaluator_use_llm = bool(args.evaluator_use_llm)
    ns.evaluator_model = str(args.evaluator_model) if args.evaluator_model else None
    ns.evaluator_reasoning_effort = str(args.evaluator_reasoning_effort) if args.evaluator_reasoning_effort else None
    ns.show_progress = bool(args.show_progress)
    ns.print_report = False
    return ns


def _run_multi_agent_benchmark(args: argparse.Namespace, eval_dir: Path) -> Dict[str, Any]:
    ns = _build_multi_agent_args(args, eval_dir)
    report = evaluate_testset.run_benchmark(ns)
    report_path = Path(report["artifacts"]["predictions_csv"])
    rows = list(_load_csv_by_index(report_path).values())
    return {
        "report": report,
        "rows": rows,
        "predictions_path": report_path,
    }


def _run_zero_shot_benchmark(args: argparse.Namespace, eval_dir: Path) -> Dict[str, Any]:
    test_csv = Path(args.test_csv)
    rows = _read_rows(test_csv)
    start_row = max(0, int(args.start_row))
    end_row = len(rows) if args.max_rows is None else min(len(rows), start_row + max(0, int(args.max_rows)))
    predictions_path = eval_dir / PROTOCOL_ZERO_SHOT / "predictions.csv"
    traces_dir = eval_dir / PROTOCOL_ZERO_SHOT / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    records_by_idx = {} if bool(args.force) else _load_csv_by_index(predictions_path)
    total = max(0, end_row - start_row)

    for row_index in range(start_row, end_row):
        if row_index in records_by_idx:
            continue
        row = rows[row_index]
        smiles = str(row.get("SMILES") or "").strip()
        y_true_raw = str(row.get("mechanism_id") or "").strip()
        y_true = normalize_ground_truth_label(y_true_raw)
        pred_raw = None
        pred_norm = None
        error = None
        trace_path = traces_dir / f"row_{row_index:04d}.json"
        try:
            result = run_zero_shot_label(
                smiles=smiles,
                model=str(args.model),
                base_url=str(args.base_url),
                reasoning_effort=str(args.reasoning_effort),
                temperature=float(args.temperature),
                api_key_env=str(args.llm_api_key_env),
                max_output_tokens=int(args.llm_max_output_tokens),
                labels=CANONICAL_LABELS,
                trace_path=trace_path,
            )
            pred_raw = result.get("label")
            pred_norm = normalize_prediction_label(pred_raw) if pred_raw else None
            error = result.get("error")
        except Exception as exc:  # pragma: no cover - defensive fallback
            error = str(exc)

        status, status_error = evaluate_testset._status_and_error(  # type: ignore[attr-defined]
            y_true_raw=y_true_raw,
            y_pred_raw=pred_raw,
            run_error=error if error and not str(error).startswith("missing_pred:") else None,
            case_error=error if error and str(error).startswith("missing_pred:") else None,
        )
        records_by_idx[row_index] = {
            "row_index": row_index,
            "inchikey": str(row.get("inchikey") or "").strip() or None,
            "smiles": smiles,
            "y_true_raw": y_true_raw,
            "y_true": y_true,
            "y_pred_raw": pred_raw,
            "y_pred": pred_norm,
            "status": status,
            "error": status_error,
            "model": args.model,
            "trace_path": str(trace_path),
        }
        _write_csv(predictions_path, ZERO_SHOT_COLUMNS, [records_by_idx[k] for k in sorted(records_by_idx.keys())])
        if bool(args.show_progress):
            done = sum(1 for i in range(start_row, end_row) if i in records_by_idx)
            acc = _running_strict_accuracy(records_by_idx)
            _print_progress(
                _progress_line(
                    protocol=PROTOCOL_ZERO_SHOT,
                    done=done,
                    total=total,
                    sample_index=row_index - start_row,
                    smiles=smiles,
                    accuracy=acc,
                ),
                final=(done >= total),
            )

    ordered = [records_by_idx[k] for k in sorted(records_by_idx.keys())]
    return {
        "rows": ordered,
        "predictions_path": predictions_path,
        "traces_dir": traces_dir,
        "summary": _strict_summary(ordered),
    }


def _merge_rows(
    *,
    multi_agent_rows: List[Dict[str, Any]],
    zero_shot_rows: List[Dict[str, Any]],
    model: str,
) -> List[Dict[str, Any]]:
    ma_map = {int(r["row_index"]): r for r in multi_agent_rows}
    zs_map = {int(r["row_index"]): r for r in zero_shot_rows}
    merged: List[Dict[str, Any]] = []
    all_indices = sorted(set(ma_map) | set(zs_map))
    for idx in all_indices:
        ma = ma_map.get(idx, {})
        zs = zs_map.get(idx, {})
        y_true = str(ma.get("y_true") or zs.get("y_true") or "")
        ma_correct = str(ma.get("status")) == STATUS_OK and str(ma.get("y_pred") or "") == y_true and y_true != ""
        zs_correct = str(zs.get("status")) == STATUS_OK and str(zs.get("y_pred") or "") == y_true and y_true != ""
        winner = (
            "both_correct" if ma_correct and zs_correct else
            "multi_agent_only" if ma_correct else
            "zero_shot_only" if zs_correct else
            "both_wrong"
        )
        merged.append(
            {
                "row_index": idx,
                "case_id": ma.get("case_id") or ma.get("inchikey") or zs.get("inchikey"),
                "inchikey": ma.get("inchikey") or zs.get("inchikey"),
                "smiles": ma.get("smiles") or zs.get("smiles"),
                "y_true_raw": ma.get("y_true_raw") or zs.get("y_true_raw"),
                "y_true": y_true,
                "multi_agent_pred_raw": ma.get("y_pred_raw"),
                "multi_agent_pred": ma.get("y_pred"),
                "multi_agent_status": ma.get("status"),
                "multi_agent_error": ma.get("error"),
                "multi_agent_run_id": ma.get("run_id"),
                "zero_shot_pred_raw": zs.get("y_pred_raw"),
                "zero_shot_pred": zs.get("y_pred"),
                "zero_shot_status": zs.get("status"),
                "zero_shot_error": zs.get("error"),
                "model": model,
                "agree": str(ma.get("y_pred") or "") == str(zs.get("y_pred") or ""),
                "winner": winner,
            }
        )
    return merged


def _comparison_summary(merged_rows: List[Dict[str, Any]], multi_summary: Optional[Dict[str, Any]], zero_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    winner_counts = Counter(str(r.get("winner") or "") for r in merged_rows)
    disagreement_count = sum(1 for r in merged_rows if not bool(r.get("agree")))
    out: Dict[str, Any] = {
        "both_correct": winner_counts.get("both_correct", 0),
        "multi_agent_only_correct": winner_counts.get("multi_agent_only", 0),
        "zero_shot_only_correct": winner_counts.get("zero_shot_only", 0),
        "both_wrong": winner_counts.get("both_wrong", 0),
        "disagreement_count": disagreement_count,
    }
    if multi_summary and zero_summary:
        ma_acc = ((multi_summary.get("metrics") or {}).get("strict_top1_accuracy"))
        zs_acc = ((zero_summary.get("metrics") or {}).get("strict_top1_accuracy"))
        if isinstance(ma_acc, (int, float)) and isinstance(zs_acc, (int, float)):
            out["accuracy_delta"] = round(float(ma_acc) - float(zs_acc), 6)
        else:
            out["accuracy_delta"] = None
    return out


def _render_report_md(report: Dict[str, Any]) -> str:
    lines = [
        "# Mechanism Benchmark: Multi-agent vs Zero-shot",
        "",
        "## Config",
        f"- protocol: `{report.get('config', {}).get('protocol')}`",
        f"- model: `{report.get('config', {}).get('model')}`",
        f"- base_url: `{report.get('config', {}).get('base_url')}`",
        f"- reasoning_effort: `{report.get('config', {}).get('reasoning_effort')}`",
        f"- temperature: `{report.get('config', {}).get('temperature')}`",
        f"- test_csv: `{report.get('dataset', {}).get('test_csv')}`",
        "",
    ]
    for arm in (PROTOCOL_MULTI_AGENT, PROTOCOL_ZERO_SHOT):
        section = report.get(arm)
        if not isinstance(section, dict):
            continue
        counts = section.get("counts") or {}
        metrics = section.get("metrics") or {}
        lines.extend(
            [
                f"## {arm}",
                f"- strict_top1_accuracy_including_other: `{metrics.get('strict_top1_accuracy_including_other')}`",
                f"- strict_top1_accuracy_excluding_other_gt: `{metrics.get('strict_top1_accuracy_excluding_other_gt')}`",
                f"- coverage: `{metrics.get('coverage')}`",
                f"- unknown_rate: `{metrics.get('unknown_rate')}`",
                f"- status_counts: `{counts.get('status_counts')}`",
                "",
            ]
        )
    comparison = report.get("comparison") or {}
    if comparison:
        lines.extend(
            [
                "## Comparison",
                f"- accuracy_delta (multi_agent - zero_shot): `{comparison.get('accuracy_delta')}`",
                f"- both_correct: `{comparison.get('both_correct')}`",
                f"- multi_agent_only_correct: `{comparison.get('multi_agent_only_correct')}`",
                f"- zero_shot_only_correct: `{comparison.get('zero_shot_only_correct')}`",
                f"- both_wrong: `{comparison.get('both_wrong')}`",
                f"- disagreement_count: `{comparison.get('disagreement_count')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    protocol = str(args.protocol)
    eval_id = str(args.eval_id or _utc_compact())
    eval_dir = Path(args.outdir) / eval_id
    comparison_dir = eval_dir / "comparison"
    dataset_rows = _read_rows(Path(args.test_csv))
    start_row = max(0, int(args.start_row))
    total_rows = len(dataset_rows) if args.max_rows is None else min(len(dataset_rows), start_row + max(0, int(args.max_rows)))
    total_rows = max(0, total_rows - start_row)
    eval_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)

    multi_agent_payload: Optional[Dict[str, Any]] = None
    zero_shot_payload: Optional[Dict[str, Any]] = None
    multi_summary: Optional[Dict[str, Any]] = None
    zero_summary: Optional[Dict[str, Any]] = None

    if protocol in {PROTOCOL_MULTI_AGENT, PROTOCOL_COMPARE}:
        multi_agent_payload = _run_multi_agent_benchmark(args, eval_dir)
        multi_summary = _strict_summary(multi_agent_payload["rows"])

    if protocol in {PROTOCOL_ZERO_SHOT, PROTOCOL_COMPARE}:
        zero_shot_payload = _run_zero_shot_benchmark(args, eval_dir)
        zero_summary = zero_shot_payload["summary"]

    merged_rows: List[Dict[str, Any]] = []
    if multi_agent_payload and zero_shot_payload:
        merged_rows = _merge_rows(
            multi_agent_rows=multi_agent_payload["rows"],
            zero_shot_rows=zero_shot_payload["rows"],
            model=str(args.model),
        )
        _write_csv(comparison_dir / "predictions_merged.csv", MERGED_COLUMNS, merged_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eval_id": eval_id,
        "config": {
            "protocol": protocol,
            "model": str(args.model),
            "base_url": str(args.base_url),
            "reasoning_effort": str(args.reasoning_effort),
            "temperature": float(args.temperature),
            "llm_api_key_env": str(args.llm_api_key_env),
            "llm_max_output_tokens": int(args.llm_max_output_tokens),
            "run_lane": str(args.run_lane),
            "iterative": bool(args.iterative),
            "max_rounds": int(args.max_rounds),
            "round_start_profile": str(args.round_start_profile),
            "neighbor_topk": int(args.neighbor_topk),
            "evaluator_use_llm": bool(args.evaluator_use_llm),
            "evaluator_model": str(args.evaluator_model) if args.evaluator_model else None,
            "evaluator_reasoning_effort": str(args.evaluator_reasoning_effort) if args.evaluator_reasoning_effort else None,
            "seed_supported": False,
            "seed": None,
            "strict_accuracy_policy": "full_denominator_excluding_missing_gt_only",
            "zero_shot_prompt_policy": "labels_only_no_definitions_no_examples",
        },
        "dataset": {
            "test_csv": str(args.test_csv),
            "gt_column": "mechanism_id",
            "total_rows": total_rows,
            "canonical_labels": list(CANONICAL_LABELS),
        },
        PROTOCOL_MULTI_AGENT: multi_summary,
        PROTOCOL_ZERO_SHOT: zero_summary,
        "comparison": _comparison_summary(merged_rows, multi_summary, zero_summary),
        "artifacts": {
            "eval_dir": str(eval_dir),
            "multi_agent_predictions_csv": str(multi_agent_payload["predictions_path"]) if multi_agent_payload else None,
            "zero_shot_predictions_csv": str(zero_shot_payload["predictions_path"]) if zero_shot_payload else None,
            "zero_shot_traces_dir": str(zero_shot_payload["traces_dir"]) if zero_shot_payload else None,
            "comparison_predictions_csv": str(comparison_dir / "predictions_merged.csv") if merged_rows else None,
            "evaluation_report_json": str(comparison_dir / "evaluation_report.json"),
            "evaluation_report_md": str(comparison_dir / "evaluation_report.md"),
        },
    }
    _write_json(comparison_dir / "evaluation_report.json", report)
    (comparison_dir / "evaluation_report.md").write_text(_render_report_md(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare multi-agent vs zero-shot mechanism-label performance on data/test.csv.")
    p.add_argument("--test-csv", type=str, default="data/test.csv")
    p.add_argument("--protocol", type=str, default=PROTOCOL_COMPARE, choices=PROTOCOL_CHOICES)
    p.add_argument("--model", type=str, default="gpt-5.2")
    p.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    p.add_argument("--reasoning-effort", type=str, default="medium")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--llm-api-key-env", type=str, default="OPENAI_API_KEY")
    p.add_argument("--llm-max-output-tokens", type=int, default=1500)
    p.add_argument("--outdir", type=str, default="artifacts/eval_compare")
    p.add_argument("--eval-id", type=str, default=None)
    p.add_argument("--start-row", type=int, default=0)
    p.add_argument("--max-rows", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--show-progress", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--run-lane", type=str, default="atb_cache_only", choices=["atb_cache_only", "offline_pdf", "full"])
    p.add_argument("--iterative", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--max-rounds", type=int, default=4)
    p.add_argument("--round-start-profile", type=str, default="R0")
    p.add_argument("--neighbor-topk", type=int, default=10)
    p.add_argument("--evaluator-use-llm", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--evaluator-model", type=str, default=None)
    p.add_argument("--evaluator-reasoning-effort", type=str, default=None)
    p.add_argument("--llm-use-json-schema", action="store_true")
    p.add_argument("--output-layout", type=str, default="case_centric", choices=["case_centric", "run_centric"])
    p.add_argument("--retain-runs", type=int, default=10)
    p.add_argument("--write-legacy-run-view", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--round-runner-mode", type=str, default="dryrun_then_commit", choices=["dryrun_then_commit", "commit_all_rounds"])
    p.add_argument("--print-report", action=argparse.BooleanOptionalAction, default=False)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    report = run_benchmark(args)
    if bool(args.print_report):
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

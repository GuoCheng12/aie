"""
Runtime status + progress helpers for iterative orchestration.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.core.safe_fs import guard_write_path, safe_write_text


def now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    guard_write_path(p)
    guard_write_path(tmp)
    safe_write_text(tmp, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def summarize_errors(errors: Any, *, limit: int = 5) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(errors, list):
        return out
    for row in errors:
        if len(out) >= limit:
            break
        if isinstance(row, dict):
            out.append(
                {
                    "code": str(row.get("code") or "unknown_error"),
                    "path": str(row.get("path") or "$"),
                    "detail": str(row.get("detail") or ""),
                }
            )
        else:
            out.append({"code": "unknown_error", "path": "$", "detail": str(row)})
    return out


def emit_progress_event(
    *,
    round_index: int,
    max_rounds: int,
    active_profile: str,
    stage: str,
    status: str,
    elapsed_ms: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "event": "round_progress",
        "round_index": int(round_index),
        "max_rounds": int(max_rounds),
        "active_profile": str(active_profile),
        "stage": str(stage),
        "status": str(status),
        "elapsed_ms": int(elapsed_ms),
        "ts": now_iso8601(),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_error_summary(
    *,
    round_index: int,
    max_rounds: int,
    active_profile: str,
    stage: str,
    errors: Iterable[Dict[str, str]],
) -> None:
    rows = list(errors)
    payload = {
        "event": "round_error_summary",
        "round_index": int(round_index),
        "max_rounds": int(max_rounds),
        "active_profile": str(active_profile),
        "stage": str(stage),
        "error_codes": [str(x.get("code") or "") for x in rows],
        "error_paths": [str(x.get("path") or "") for x in rows],
        "ts": now_iso8601(),
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)

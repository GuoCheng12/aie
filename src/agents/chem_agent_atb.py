"""
src/agents/chem_agent_atb.py

Chem Agent (aTB lane):
- Read aTB cache artifacts by InChIKey
- Write cache-derived signals back into an existing case file
- Keep writes scoped to aTB readiness and execution trace fields
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Tuple

from src.cases.case_schema import (
    Actor,
    AtbCacheStatus,
    AtbRequestStatus,
    EventType,
    create_history_event,
    now_iso,
    validate_case_file,
)
from src.cases.case_sections import sync_case_sections
from src.chem.atb_cache import DEFAULT_CACHE_DIR, get_atb_cache_record, get_cache_paths


def _hash_obj(obj: Dict[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _request_status_from_cache_status(cache_status: str) -> str:
    if cache_status == AtbCacheStatus.PENDING.value:
        return AtbRequestStatus.REQUESTED.value
    if cache_status in {
        AtbCacheStatus.SUCCESS.value,
        AtbCacheStatus.PARTIAL.value,
        AtbCacheStatus.FAILED.value,
    }:
        return AtbRequestStatus.DONE.value
    return AtbRequestStatus.NOT_REQUESTED.value


def enrich_case_with_atb_cache(case: Dict[str, Any], cache_dir: str = DEFAULT_CACHE_DIR) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Enrich an existing case with cache-backed aTB status/features.

    Returns:
        (updated_case, summary)
    """
    started_at = now_iso()
    case_out: Dict[str, Any] = dict(case)

    query = case_out.get("query") or {}
    inchikey = str(query.get("inchikey") or case_out.get("case_id") or "").strip()
    if not inchikey:
        raise ValueError("case missing inchikey (query.inchikey)")

    record = get_atb_cache_record(inchikey, cache_dir=cache_dir)
    cache_status = str(record.get("cache_status") or AtbCacheStatus.ABSENT.value)
    missing_fields = list(record.get("missing_fields") or [])
    features_summary = record.get("features_summary")
    status_raw = record.get("status") or {}

    evidence_readiness = case_out.setdefault("evidence_readiness", {})
    atb = evidence_readiness.setdefault("atb", {})

    atb["cache_status"] = cache_status
    atb["request_status"] = _request_status_from_cache_status(cache_status)
    atb["missing_fields"] = missing_fields
    atb["last_update"] = now_iso()
    atb["error_stage"] = status_raw.get("fail_stage")
    atb["error_msg"] = status_raw.get("error_msg")
    if isinstance(features_summary, dict):
        atb["features_summary"] = features_summary

    history = case_out.setdefault("history", [])
    history.append(
        create_history_event(
            actor=Actor.CHEM_AGENT.value,
            event_type=EventType.ATB_UPDATED.value,
            details={
                "stage": "chem_agent_atb",
                "inchikey": inchikey,
                "cache_status": cache_status,
                "missing_fields": missing_fields,
                "keyfield_complete": bool(record.get("keyfield_complete")),
                "has_features_json": bool(record.get("has_features_json")),
            },
        )
    )

    cache_paths = get_cache_paths(inchikey, cache_dir=cache_dir)
    artifacts = []
    for key in ("status_path", "features_path", "smiles_path"):
        p = cache_paths[key]
        if p.exists():
            artifacts.append({"kind": key, "path": str(p)})

    inputs_fingerprint = {
        "inchikey": inchikey,
        "cache_dir": str(cache_dir),
        "cache_status": cache_status,
        "missing_fields": missing_fields,
    }
    inputs_hash = _hash_obj(inputs_fingerprint)

    agent_runs = case_out.setdefault("agent_runs", [])
    agent_runs.append(
        {
            "agent_name": "chem_agent_atb",
            "version": "1.0.0",
            "started_at": started_at,
            "ended_at": now_iso(),
            "inputs_hash": inputs_hash,
            "artifacts": artifacts,
            "warnings": [],
            "status": "completed",
        }
    )

    sync_case_sections(case_out)

    is_valid, errors = validate_case_file(case_out)
    if not is_valid:
        raise ValueError(f"case became invalid after chem_agent_atb update: {errors}")

    summary = {
        "inchikey": inchikey,
        "cache_status": cache_status,
        "request_status": atb["request_status"],
        "missing_fields": missing_fields,
        "features_summary_keys": sorted(list(features_summary.keys())) if isinstance(features_summary, dict) else [],
        "inputs_hash": inputs_hash,
    }
    return case_out, summary


def apply_atb_cache_to_case_file(case_path: Path, cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, Any]:
    """
    Load case JSON from disk, enrich from aTB cache, and write it back.
    """
    path = Path(case_path)
    case = json.loads(path.read_text(encoding="utf-8"))
    updated, summary = enrich_case_with_atb_cache(case, cache_dir=cache_dir)
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    summary["case_path"] = str(path)
    return summary


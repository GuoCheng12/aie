"""
Composite Chem Agent for aTB + offline PDF emission extraction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.base import CaseAgent
from src.cases.mineru_llm_extractor import (
    build_mineru_prompt_payload,
    parse_llm_candidates,
)
from src.chem.atb_neighbor_consistency import compute_atb_neighbor_consistency
from src.chem.atb_cache import get_atb_cache_record
from src.core.types import AgentContext, AgentResult
from src.tools.llm_client import LLMClientError, ResponsesLLMClient
from src.tools.mineru_runner import MineruRunner


AGGR_KEYWORDS = ("aggregate", "aggregation", "aggr", "aie", "water fraction", "poor solvent", "cluster")
FILM_KEYWORDS = ("film", "solid", "powder", "crystal")
SOURCE_KIND_RANK = {"table": 0, "figure": 1, "text": 2}
FILM_PRIORITY_RANK = {"film": 0, "solid": 1, "powder": 1, "crystal": 2}
ATB_CONSISTENCY_FIELDS = ("delta_gap", "delta_dihedral", "delta_volume")


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if v != v:  # NaN
        return None
    return v


def _normalize_unit(u: Any) -> str:
    s = str(u or "").strip().lower()
    if s in {"nm", "nanometer", "nanometers"}:
        return "nm"
    return s or "nm"


def _condition_bucket(cond: str) -> str:
    c = cond.lower()
    for k in ("film", "solid", "powder", "crystal"):
        if k in c:
            return k
    for k in AGGR_KEYWORDS:
        if k in c:
            return "aggr"
    return "unknown"


def _target_field_from_condition(cond: str) -> Optional[str]:
    bucket = _condition_bucket(cond)
    if bucket == "aggr":
        return "emission_aggr_nm"
    if bucket in {"film", "solid", "powder", "crystal"}:
        return "emission_solid_or_film_nm"
    return None


def _status_for_atb(cache_status: str) -> str:
    if cache_status == "pending":
        return "requested"
    if cache_status in {"success", "partial", "failed"}:
        return "done"
    return "not_requested"


def _candidate_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "value": {"type": ["number", "string", "null"]},
                        "unit": {"type": ["string", "null"]},
                        "condition": {"type": ["string", "null"]},
                        "source_locator": {"type": ["string", "null"]},
                        "page": {"type": ["integer", "number", "null"]},
                        "value_source_kind": {"type": ["string", "null"]},
                        "identity_match": {"type": ["string", "null"]},
                        "identity_match_confidence": {"type": ["number", "null"]},
                        "confidence": {"type": ["number", "null"]},
                        "bbox": {"type": ["object", "null"]},
                    },
                    "required": [
                        "value",
                        "unit",
                        "condition",
                        "source_locator",
                        "page",
                        "value_source_kind",
                        "identity_match",
                        "identity_match_confidence",
                        "confidence",
                        "bbox",
                    ],
                },
            }
        },
        "required": ["candidates"],
    }


def _build_staging_candidate(
    *,
    source_ref: str,
    run_id: str,
    candidate_id: str,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    cond = str(row.get("condition") or "")
    field = _target_field_from_condition(cond)
    value = _to_float(row.get("value"))
    unit = _normalize_unit(row.get("unit"))
    source_locator = str(row.get("source_locator") or "").strip()
    value_source_kind = str(row.get("value_source_kind") or "text").lower()
    if value_source_kind not in SOURCE_KIND_RANK:
        value_source_kind = "text"
    identity_match = str(row.get("identity_match") or "ambiguous").lower()
    if identity_match not in {"matched", "ambiguous", "unmatched", "exact", "series_inferred", "uncertain", "not_found"}:
        identity_match = "ambiguous"
    identity_conf = _to_float(row.get("identity_match_confidence"))
    if identity_conf is None:
        identity_conf = 0.5
    confidence = _to_float(row.get("confidence"))
    if confidence is None:
        confidence = 0.5

    rejection_reason = None
    status = "verified"
    if value is None:
        status = "rejected"
        rejection_reason = "value_not_numeric"
    elif unit != "nm":
        status = "rejected"
        rejection_reason = f"unit_not_nm:{unit}"
    elif field is None:
        status = "rejected"
        rejection_reason = "condition_unmapped"
    elif identity_match in {"unmatched", "not_found"}:
        status = "rejected"
        rejection_reason = "identity_unmatched"
    elif source_locator == "":
        status = "unverified"
    return {
        "candidate_id": candidate_id,
        "field": field,
        "normalized_value_nm": value,
        "raw_value": row.get("value"),
        "unit": "nm",
        "condition": cond,
        "condition_bucket": _condition_bucket(cond),
        "value_source_kind": value_source_kind,
        "source_type": "offline_pdf",
        "source_ref": source_ref,
        "source_locator": source_locator,
        "page": int(row["page"]) if row.get("page") is not None else None,
        "bbox": row.get("bbox") if isinstance(row.get("bbox"), dict) else None,
        "identity_match": identity_match,
        "identity_match_confidence": identity_conf,
        "confidence": confidence,
        "verification_status": status,
        "rejection_reason": rejection_reason,
        "run_id": run_id,
        "artifact_ref": None,
    }


def _candidate_sort_key(item: Dict[str, Any], *, field: str) -> Tuple[Any, ...]:
    verified_rank = 0 if item.get("verification_status") == "verified" else 1
    source_rank = SOURCE_KIND_RANK.get(str(item.get("value_source_kind") or "text"), 9)
    if field == "emission_solid_or_film_nm":
        bucket = str(item.get("condition_bucket") or "unknown")
        cond_rank = FILM_PRIORITY_RANK.get(bucket, 9)
    else:
        cond_rank = 0
    conf = float(item.get("confidence") or 0.0)
    page = item.get("page")
    page_rank = int(page) if isinstance(page, int) else 10**9
    cid = str(item.get("candidate_id") or "")
    return (verified_rank, source_rank, cond_rank, -conf, page_rank, cid)


def _select_best(candidates: Sequence[Dict[str, Any]], field: str) -> Optional[Dict[str, Any]]:
    pool = [x for x in candidates if x.get("field") == field and x.get("verification_status") != "rejected"]
    if not pool:
        return None
    pool = sorted(pool, key=lambda x: _candidate_sort_key(x, field=field))
    return pool[0]


def _neighbor_feature_row_from_pack(pack: Dict[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    features_summary = pack.get("features_summary")
    if not isinstance(features_summary, dict):
        features_summary = {}
    row: Dict[str, Any] = {"cache_status": str(pack.get("cache_status") or "").lower()}
    for field in fields:
        row[field] = features_summary.get(field)
    return row


def _collect_neighbor_feature_rows(
    neighbors: Sequence[Dict[str, Any]],
    *,
    fields: Sequence[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"total_neighbors": 0, "used_neighbor_pack": 0, "used_cache_lookup": 0}
    for neighbor in neighbors:
        if not isinstance(neighbor, dict):
            continue
        stats["total_neighbors"] += 1
        pack = neighbor.get("neighbor_atb")
        if isinstance(pack, dict):
            rows.append(_neighbor_feature_row_from_pack(pack, fields))
            stats["used_neighbor_pack"] += 1
            continue
        neighbor_inchikey = str(neighbor.get("neighbor_inchikey") or neighbor.get("inchikey") or "").strip()
        if neighbor_inchikey == "":
            continue
        rec = get_atb_cache_record(neighbor_inchikey)
        rec_pack = {
            "cache_status": rec.get("cache_status"),
            "features_summary": rec.get("features_summary"),
        }
        rows.append(_neighbor_feature_row_from_pack(rec_pack, fields))
        stats["used_cache_lookup"] += 1
    return rows, stats


class ChemAgent(CaseAgent):
    name = "chem_agent"
    version = "1.0.0"
    allowed_patch_prefixes = (
        "/evidence_readiness/atb/",
        "/evidence_readiness/literature/",
        "/evidence_readiness/experiment/",
        "/target_fields/",
        "/target_fields_provenance/",
        "/evidence_candidates_staging/-",
        "/risk_scores/atb_neighbor_consistency",
        "/agent_runs/-",
    )
    append_only_prefixes = ("/evidence_candidates_staging", "/agent_runs")

    def build_inputs(self, case: Dict[str, Any], ctx: AgentContext) -> Dict[str, Any]:
        query = case.get("query") or {}
        emission_cfg = ((case.get("evidence_acquire") or {}).get("emission") or {})
        offline_pdfs = ((case.get("inputs") or {}).get("offline_pdfs") or [])
        normalized_pdfs = []
        for item in offline_pdfs:
            if isinstance(item, str):
                normalized_pdfs.append({"path_or_id": item})
            elif isinstance(item, dict) and item.get("path_or_id"):
                normalized_pdfs.append({"path_or_id": str(item["path_or_id"])})
        return {
            "case_id": case.get("case_id"),
            "inchikey": query.get("inchikey"),
            "smiles": query.get("canonical_smiles") or query.get("input_smiles"),
            "code": query.get("code"),
            "aliases": query.get("aliases") or [],
            "run_lane": ctx.run_lane,
            "mode": emission_cfg.get("mode") or "offline_pdf",
            "strictness": emission_cfg.get("strictness") or "relaxed",
            "extractor_mode": emission_cfg.get("extractor_mode") or "mineru_llm",
            "offline_pdfs": normalized_pdfs,
        }

    def run(self, case: Dict[str, Any], ctx: AgentContext, inputs: Dict[str, Any]) -> AgentResult:
        patch: List[Dict[str, Any]] = []
        warnings: List[str] = []
        raw_outputs: Dict[str, Any] = {}

        inchikey = str(inputs.get("inchikey") or "").strip()
        atb_rec = get_atb_cache_record(inchikey)
        cache_status = str(atb_rec.get("cache_status") or "absent")
        patch.extend(
            [
                {"op": "add", "path": "/evidence_readiness/atb/cache_status", "value": cache_status},
                {"op": "add", "path": "/evidence_readiness/atb/request_status", "value": _status_for_atb(cache_status)},
                {"op": "add", "path": "/evidence_readiness/atb/missing_fields", "value": list(atb_rec.get("missing_fields") or [])},
                {"op": "add", "path": "/evidence_readiness/atb/error_stage", "value": (atb_rec.get("status") or {}).get("fail_stage")},
                {"op": "add", "path": "/evidence_readiness/atb/error_msg", "value": (atb_rec.get("status") or {}).get("error_msg")},
                {"op": "add", "path": "/evidence_readiness/atb/features_summary", "value": atb_rec.get("features_summary")},
            ]
        )
        raw_outputs["atb_cache_record"] = atb_rec

        target_features = atb_rec.get("features_summary") if cache_status == "success" else None
        neighbor_rows, neighbor_row_stats = _collect_neighbor_feature_rows(
            case.get("neighbors") or [],
            fields=ATB_CONSISTENCY_FIELDS,
        )
        atb_neighbor_consistency = compute_atb_neighbor_consistency(
            target_features=target_features,
            neighbor_features=neighbor_rows,
            fields=ATB_CONSISTENCY_FIELDS,
            min_sample_size=5,
            z_max_threshold=3.5,
        )
        patch.append(
            {
                "op": "add",
                "path": "/risk_scores/atb_neighbor_consistency",
                "value": atb_neighbor_consistency,
            }
        )
        raw_outputs["atb_neighbor_consistency"] = {
            "result": atb_neighbor_consistency,
            "neighbor_rows_stats": neighbor_row_stats,
        }

        run_lane = str(inputs.get("run_lane") or "atb_cache_only")
        if run_lane == "atb_cache_only":
            patch.extend(
                [
                    {"op": "add", "path": "/evidence_readiness/literature/status", "value": "not_started"},
                    {"op": "add", "path": "/evidence_readiness/literature/sources", "value": []},
                    {"op": "add", "path": "/evidence_readiness/literature/notes", "value": "lane_disabled"},
                    {"op": "add", "path": "/evidence_readiness/experiment/status", "value": "not_requested"},
                    {"op": "add", "path": "/evidence_readiness/experiment/notes", "value": "lane_disabled"},
                ]
            )
            raw_outputs["lane"] = {"run_lane": run_lane, "literature": "disabled", "experiment": "disabled"}
            return AgentResult(
                patch=patch,
                status="success",
                warnings=["lane_disabled:literature", "lane_disabled:experiment"],
                raw_outputs=raw_outputs,
                metrics={"run_lane": run_lane},
            )

        mode = str(inputs.get("mode") or "offline_pdf")
        offline_pdfs = list(inputs.get("offline_pdfs") or [])
        if mode != "offline_pdf":
            patch.extend(
                [
                    {"op": "add", "path": "/evidence_readiness/literature/status", "value": "not_started"},
                    {"op": "add", "path": "/evidence_readiness/literature/sources", "value": []},
                    {"op": "add", "path": "/evidence_readiness/literature/notes", "value": "mode_not_supported"},
                ]
            )
            warnings.append("literature_mode_not_supported")
            return AgentResult(patch=patch, status="partial", warnings=warnings, raw_outputs=raw_outputs)

        if not offline_pdfs:
            patch.extend(
                [
                    {"op": "add", "path": "/evidence_readiness/literature/status", "value": "not_started"},
                    {"op": "add", "path": "/evidence_readiness/literature/sources", "value": []},
                    {"op": "add", "path": "/evidence_readiness/literature/notes", "value": "offline_pdf_missing"},
                ]
            )
            warnings.append("offline_pdf_missing")
            return AgentResult(patch=patch, status="partial", warnings=warnings, raw_outputs=raw_outputs)

        mineru = MineruRunner(
            mineru_bin=ctx.mineru_bin,
            output_root=ctx.mineru_output_root,
            backend=ctx.mineru_backend,
            method=ctx.mineru_method,
            lang=ctx.mineru_lang,
            start_page=ctx.mineru_start_page,
            end_page=ctx.mineru_end_page,
            timeout_sec=ctx.mineru_timeout_sec,
        )
        llm_client = ResponsesLLMClient(
            base_url=ctx.base_url,
            model=ctx.model,
            api_key_env=ctx.llm_api_key_env,
            max_output_tokens=ctx.llm_max_output_tokens,
            reasoning_effort=ctx.llm_reasoning_effort,
        )

        staged: List[Dict[str, Any]] = []
        source_rows: List[Dict[str, Any]] = []
        llm_trace: List[Dict[str, Any]] = []
        for pdf_idx, item in enumerate(offline_pdfs):
            source_ref = str(item.get("path_or_id") or "").strip()
            if source_ref == "":
                warnings.append("offline_pdf_empty_path")
                continue
            try:
                bundle = mineru.resolve_bundle(Path(source_ref))
                md_path = Path(bundle["md_path"])
                content_path = Path(bundle["content_list_v2_path"])
                md_text = md_path.read_text(encoding="utf-8", errors="ignore")
                md_excerpt = md_text[:20000]
                content_excerpt = mineru.build_content_excerpt(content_path)
                case_ctx = {
                    "case_id": inputs.get("case_id"),
                    "target_smiles": inputs.get("smiles"),
                    "target_code": inputs.get("code"),
                    "target_inchikey": inputs.get("inchikey"),
                    "target_aliases": list(inputs.get("aliases") or []),
                }
                prompt = build_mineru_prompt_payload(
                    case_context=case_ctx,
                    source_ref=source_ref,
                    md_text=md_excerpt,
                    content_list_excerpt=content_excerpt,
                )
                llm_out = llm_client.responses_json(
                    instructions=(
                        "Extract emission evidence from the provided MinerU content. "
                        "Return strict JSON only and do not fabricate values."
                    ),
                    input_text=prompt,
                    schema_name="chem_agent_mineru_candidates_v1",
                    schema=_candidate_schema(),
                )
                parsed = parse_llm_candidates(llm_out.get("parsed") or {})
                llm_trace.append(
                    {
                        "source_ref": source_ref,
                        "bundle": bundle,
                        "request": llm_out.get("request"),
                        "response": llm_out.get("response"),
                    }
                )
                source_rows.append({"source_ref": source_ref, "bundle": bundle})
                for i, row in enumerate(parsed.get("candidates") or []):
                    candidate_id = f"{pdf_idx}:{i}"
                    staged.append(
                        _build_staging_candidate(
                            source_ref=source_ref,
                            run_id=ctx.run_id,
                            candidate_id=candidate_id,
                            row=row,
                        )
                    )
                warnings.extend(parsed.get("warnings") or [])
            except (LLMClientError, Exception) as exc:  # broad on purpose: keep run alive per source
                warnings.append(f"offline_pdf_extract_failed:{source_ref}:{exc}")

        raw_outputs["literature_sources"] = source_rows
        raw_outputs["llm_trace"] = llm_trace

        for cand in staged:
            patch.append({"op": "add", "path": "/evidence_candidates_staging/-", "value": cand})

        lit_status = "found" if staged else "not_found"
        patch.extend(
            [
                {"op": "add", "path": "/evidence_readiness/literature/status", "value": lit_status},
                {"op": "add", "path": "/evidence_readiness/literature/sources", "value": source_rows},
                {"op": "add", "path": "/evidence_readiness/literature/notes", "value": None if staged else "no_candidates"},
            ]
        )

        selected_aggr = _select_best(staged, "emission_aggr_nm")
        selected_solid = _select_best(staged, "emission_solid_or_film_nm")

        for field, item in (("emission_aggr_nm", selected_aggr), ("emission_solid_or_film_nm", selected_solid)):
            if item is None:
                continue
            patch.append({"op": "add", "path": f"/target_fields/{field}", "value": item.get("normalized_value_nm")})
            patch.append(
                {
                    "op": "add",
                    "path": f"/target_fields_provenance/{field}",
                    "value": {
                        "source_type": "offline_pdf",
                        "source_ref": item.get("source_ref"),
                        "source_locator": item.get("source_locator"),
                        "confidence": item.get("confidence"),
                        "identity_match": item.get("identity_match"),
                        "identity_match_confidence": item.get("identity_match_confidence"),
                        "matched_entity_in_paper": None,
                        "condition": item.get("condition"),
                        "page": item.get("page"),
                        "value_source_kind": item.get("value_source_kind"),
                        "candidate_id": item.get("candidate_id"),
                    },
                }
            )

        if staged:
            status = "success"
        elif warnings:
            status = "failed"
        else:
            status = "partial"
        return AgentResult(
            patch=patch,
            status=status,
            warnings=warnings,
            raw_outputs=raw_outputs,
            metrics={
                "staged_candidates": len(staged),
                "writeback_fields": [k for k, v in [("emission_aggr_nm", selected_aggr), ("emission_solid_or_film_nm", selected_solid)] if v is not None],
            },
        )

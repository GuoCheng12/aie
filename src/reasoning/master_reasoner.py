"""
Pure-function master reasoner core.

Inputs:
- case_json
- reasoning_config

Outputs:
- reasoning_pack
- master_prompt_bundle
- master_output (strict JSON)
- master_patch (RFC6902 patch preview)
- replay-friendly metadata
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core.hashing import canonical_json_bytes, sha256_json
from src.tools.llm_client import ResponsesLLMClient


MASTER_PACK_VERSION = "master_pack_v1"
MASTER_PROMPT_BUNDLE_VERSION = "master_prompt_bundle_v1"
MASTER_OUTPUT_SCHEMA_VERSION = "master_output_schema_v1"
MAX_PACK_BYTES = 15 * 1024


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_size_bytes(obj: Any) -> int:
    return len(canonical_json_bytes(obj))


def _json_pointer_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _resolve_pointer(doc: Any, path: str) -> Tuple[bool, Any]:
    if path == "":
        return False, None
    if path == "/":
        return True, doc
    if not isinstance(path, str) or not path.startswith("/"):
        return False, None
    cur = doc
    for tok in path.split("/")[1:]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if tok not in cur:
                return False, None
            cur = cur[tok]
            continue
        if isinstance(cur, list):
            try:
                idx = int(tok)
            except Exception:
                return False, None
            if idx < 0 or idx >= len(cur):
                return False, None
            cur = cur[idx]
            continue
        return False, None
    return True, cur


def _collect_paths(prefix: str, value: Any) -> Set[str]:
    out: Set[str] = set()
    if prefix == "":
        return out
    out.add(prefix)
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{prefix}/{_json_pointer_escape(str(k))}"
            out.update(_collect_paths(child, v))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            child = f"{prefix}/{i}"
            out.update(_collect_paths(child, v))
    return out


def _truncate_text(value: Any, max_chars: int = 300) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _risk_scores_subset(case_json: Dict[str, Any]) -> Dict[str, Any]:
    src = case_json.get("risk_scores") or {}
    keep = [
        "top1_sim",
        "mean_topk_sim",
        "novelty_struct",
        "mechanism_entropy",
        "mechanism_hint",
        "hint_confidence",
        "atb_neighbor_consistency",
    ]
    return {k: src.get(k) for k in keep if k in src}


def _evidence_readiness_subset(case_json: Dict[str, Any]) -> Dict[str, Any]:
    er = case_json.get("evidence_readiness") or {}
    atb = er.get("atb") or {}
    return {
        "atb": {
            "cache_status": atb.get("cache_status"),
            "features_summary": atb.get("features_summary"),
            "missing_fields": atb.get("missing_fields"),
        },
        "literature": {
            "status": (er.get("literature") or {}).get("status"),
            "notes": (er.get("literature") or {}).get("notes"),
        },
        "experiment": {
            "status": (er.get("experiment") or {}).get("status"),
            "notes": (er.get("experiment") or {}).get("notes"),
        },
    }


def _neighbors_topk(case_json: Dict[str, Any], k: int = 10) -> List[Dict[str, Any]]:
    rows = []
    for idx, n in enumerate((case_json.get("neighbors") or [])[:k]):
        if not isinstance(n, dict):
            continue
        rows.append(
            {
                "case_index": idx,
                "rank": n.get("rank"),
                "sim": n.get("sim"),
                "neighbor_inchikey": n.get("neighbor_inchikey"),
                "neighbor_mechanism_label": n.get("neighbor_mechanism_label"),
            }
        )
    return rows


def _mechanism_context(case_json: Dict[str, Any]) -> Dict[str, Any]:
    risk = case_json.get("risk_scores") or {}
    out: Dict[str, Any] = {
        "mechanism_hint": risk.get("mechanism_hint"),
        "candidate_mechanisms_top3": [],
        "mechanism_signatures_top3": [],
    }
    candidates = case_json.get("candidate_mechanisms")
    if isinstance(candidates, list):
        for row in candidates[:3]:
            if not isinstance(row, dict):
                continue
            out["candidate_mechanisms_top3"].append(
                {
                    "mechanism_id": row.get("mechanism_id") or row.get("label") or row.get("name"),
                    "probability": row.get("probability") or row.get("confidence"),
                }
            )

    signatures = case_json.get("mechanism_signatures")
    if isinstance(signatures, dict):
        for i, (name, val) in enumerate(signatures.items()):
            if i >= 3:
                break
            out["mechanism_signatures_top3"].append(
                {
                    "name": str(name),
                    "signature": _truncate_text(val, max_chars=300),
                }
            )
    return out


def _build_path_map(pack: Dict[str, Any], case_json: Dict[str, Any]) -> Dict[str, str]:
    path_map: Dict[str, str] = {}
    # Direct mappings where pack path mirrors case path.
    direct_roots = [
        "query",
        "risk_scores",
        "evidence_readiness",
        "target_fields",
        "target_fields_provenance",
        "gate",
    ]
    for root in direct_roots:
        p = f"/{root}"
        if root == "gate":
            found, val = _resolve_pointer(case_json, "/current_gate")
            if found:
                for path in sorted(_collect_paths(p, val)):
                    path_map[path] = path.replace("/gate", "/current_gate", 1)
            continue
        found, val = _resolve_pointer(case_json, p)
        if found:
            for path in sorted(_collect_paths(p, val)):
                path_map[path] = path

    # neighbors_topk -> neighbors/<case_index>
    for i, row in enumerate(pack.get("neighbors_topk") or []):
        if not isinstance(row, dict):
            continue
        case_idx = row.get("case_index")
        if not isinstance(case_idx, int):
            continue
        pack_prefix = f"/neighbors_topk/{i}"
        case_prefix = f"/neighbors/{case_idx}"
        for k in row.keys():
            path_map[f"{pack_prefix}/{k}"] = f"{case_prefix}/{k}"
        path_map[pack_prefix] = case_prefix

    # mechanism_context paths mapped back to source case paths.
    path_map["/mechanism_context/mechanism_hint"] = "/risk_scores/mechanism_hint"
    for i, _ in enumerate(pack.get("mechanism_context", {}).get("candidate_mechanisms_top3") or []):
        path_map[f"/mechanism_context/candidate_mechanisms_top3/{i}"] = f"/candidate_mechanisms/{i}"
    for i, row in enumerate(pack.get("mechanism_context", {}).get("mechanism_signatures_top3") or []):
        name = None
        if isinstance(row, dict):
            name = row.get("name")
        if name:
            path_map[f"/mechanism_context/mechanism_signatures_top3/{i}"] = f"/mechanism_signatures/{_json_pointer_escape(str(name))}"
    return path_map


def _allowed_evidence_paths(case_json: Dict[str, Any], neighbors_topk: Sequence[Dict[str, Any]]) -> List[str]:
    allowed: Set[str] = set()
    for prefix in [
        "/risk_scores",
        "/evidence_readiness/atb",
        "/evidence_readiness/literature",
        "/evidence_readiness/experiment",
        "/target_fields",
        "/target_fields_provenance",
        "/current_gate",
    ]:
        found, value = _resolve_pointer(case_json, prefix)
        if found:
            allowed.update(_collect_paths(prefix, value))

    for row in neighbors_topk:
        if not isinstance(row, dict):
            continue
        case_index = row.get("case_index")
        if not isinstance(case_index, int):
            continue
        prefix = f"/neighbors/{case_index}"
        found, value = _resolve_pointer(case_json, prefix)
        if found:
            allowed.update(_collect_paths(prefix, value))
    return sorted(allowed)


def build_reasoning_pack(case_json: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    query = case_json.get("query") or {}
    runtime = case_json.get("runtime") or {}
    emission_cfg = ((case_json.get("evidence_acquire") or {}).get("emission") or {})
    neighbors_topk = _neighbors_topk(case_json, k=10)

    pack = {
        "pack_version": MASTER_PACK_VERSION,
        "query": {
            "input_smiles": query.get("input_smiles"),
            "canonical_smiles": query.get("canonical_smiles"),
            "inchikey": query.get("inchikey"),
            "aliases": query.get("aliases") or [],
            "code": query.get("code"),
            "reference": query.get("reference"),
        },
        "runtime": {
            "run_lane": runtime.get("run_lane") or reasoning_config.get("run_lane"),
            "emission_mode": emission_cfg.get("mode"),
            "emission_strictness": emission_cfg.get("strictness"),
        },
        "gate": deepcopy(case_json.get("current_gate") or {}),
        "neighbors_topk": neighbors_topk,
        "risk_scores": _risk_scores_subset(case_json),
        "evidence_readiness": _evidence_readiness_subset(case_json),
        "target_fields": deepcopy(case_json.get("target_fields") or {}),
        "target_fields_provenance": deepcopy(case_json.get("target_fields_provenance") or {}),
        "mechanism_context": _mechanism_context(case_json),
    }
    pack["allowed_evidence_paths"] = _allowed_evidence_paths(case_json, neighbors_topk)
    pack["path_map"] = _build_path_map(pack, case_json)

    if _safe_json_size_bytes(pack) > MAX_PACK_BYTES:
        # deterministic shrink strategy
        pack["neighbors_topk"] = pack["neighbors_topk"][:5]
        pack["mechanism_context"]["mechanism_signatures_top3"] = (
            pack["mechanism_context"].get("mechanism_signatures_top3") or []
        )[:2]
        pack["allowed_evidence_paths"] = pack["allowed_evidence_paths"][:200]
        pack["path_map"] = _build_path_map(pack, case_json)
    return pack


def _choose_template(reasoning_pack: Dict[str, Any]) -> str:
    gate = reasoning_pack.get("gate") or {}
    mode = str(gate.get("reasoning_mode") or "").lower()
    risk = reasoning_pack.get("risk_scores") or {}
    atb_nc = (risk.get("atb_neighbor_consistency") or {}) if isinstance(risk.get("atb_neighbor_consistency"), dict) else {}
    novelty = risk.get("novelty_struct")
    entropy = risk.get("mechanism_entropy")

    if mode == "conservative":
        if isinstance(entropy, (int, float)) and entropy >= 0.55:
            return "mixture"
        return "stable"

    if atb_nc.get("flag") == "outlier" and atb_nc.get("reliability") in {"medium", "high"}:
        return "novelty"
    if isinstance(novelty, (int, float)) and novelty >= 0.60:
        return "novelty"
    if isinstance(entropy, (int, float)) and entropy >= 0.55:
        return "mixture"
    if isinstance(novelty, (int, float)) and novelty >= 0.35:
        return "mixture"
    return "stable"


def build_master_prompt_bundle(reasoning_pack: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    template = _choose_template(reasoning_pack)
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "normal").lower()

    system = (
        "You are the master reasoner for AIE mechanism discovery.\n"
        "Use ONLY the provided reasoning_pack JSON.\n"
        "Do not fabricate evidence or facts.\n"
        "Every evidence reference must use a case_path from allowed_evidence_paths.\n"
        "Return strict JSON that matches the provided schema."
    )
    instructions = [
        "Template rubric:",
        f"- template_used should be {template}.",
        "- stable: pick one dominant mechanism with conservative uncertainty.",
        "- mixture: discuss multiple plausible mechanisms and tradeoffs.",
        "- novelty: emphasize uncertainty and verification path.",
    ]
    if gate_mode == "conservative":
        instructions.append(
            "- Conservative mode: keep confidence capped and explicitly list evidence limitations."
        )

    schema = master_output_schema()
    return {
        "prompt_bundle_version": MASTER_PROMPT_BUNDLE_VERSION,
        "template_version": f"{template}_v1",
        "template_used": template,
        "system": system,
        "instructions": "\n".join(instructions),
        "user_payload": reasoning_pack,
        "output_schema_name": MASTER_OUTPUT_SCHEMA_VERSION,
        "output_schema": schema,
    }


def _evidence_item_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "case_path": {"type": "string"},
            "note": {"type": "string"},
            "role": {"type": "string", "enum": ["support", "counter", "context"]},
        },
        "required": ["case_path", "note", "role"],
    }


def master_output_schema() -> Dict[str, Any]:
    evidence_item = _evidence_item_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["ok", "insufficient_evidence"]},
            "template_used": {"type": "string", "enum": ["stable", "mixture", "novelty"]},
            "mechanism_claim": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "primary_hypothesis": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "mechanism_label": {"type": "string", "enum": ["TICT", "ESIPT", "ICT", "other", "unknown"]},
                            "aie_rationale_type": {"type": "string", "enum": ["stable", "mixture", "novelty"]},
                            "natural_language_mechanism": {"type": "string"},
                        },
                        "required": ["mechanism_label", "aie_rationale_type", "natural_language_mechanism"],
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reasoning_mode_used": {"type": "string", "enum": ["normal", "conservative"]},
                },
                "required": ["primary_hypothesis", "confidence", "reasoning_mode_used"],
            },
            "supporting_chain": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim": {"type": "string"},
                        "evidence_used": {"type": "array", "items": evidence_item},
                    },
                    "required": ["claim", "evidence_used"],
                },
            },
            "competing_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "evidence_used": {"type": "array", "items": evidence_item},
                    },
                    "required": ["name", "confidence", "evidence_used"],
                },
            },
            "predictions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "prediction": {"type": "string"},
                        "expected_signal": {"type": "string"},
                        "evidence_used": {"type": "array", "items": evidence_item},
                    },
                    "required": ["prediction", "expected_signal", "evidence_used"],
                },
            },
            "limits": {"type": "array", "items": {"type": "string"}},
            "evidence_used": {"type": "array", "items": evidence_item},
            "recommended_next_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "status",
            "template_used",
            "mechanism_claim",
            "supporting_chain",
            "competing_hypotheses",
            "predictions",
            "limits",
            "evidence_used",
        ],
    }


def _collect_all_evidence_entries(master_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    top = master_output.get("evidence_used")
    if isinstance(top, list):
        rows.extend([x for x in top if isinstance(x, dict)])

    for chain in master_output.get("supporting_chain") or []:
        if not isinstance(chain, dict):
            continue
        ev = chain.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    for comp in master_output.get("competing_hypotheses") or []:
        if not isinstance(comp, dict):
            continue
        ev = comp.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    for pred in master_output.get("predictions") or []:
        if not isinstance(pred, dict):
            continue
        ev = pred.get("evidence_used")
        if isinstance(ev, list):
            rows.extend([x for x in ev if isinstance(x, dict)])
    return rows


def validate_master_output(
    master_output: Dict[str, Any],
    reasoning_pack: Dict[str, Any],
    case_json: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Tuple[bool, List[str], Dict[str, Any], List[str]]:
    """
    Semantic validations after schema parse.
    Returns: (ok, errors, normalized_output, used_case_paths)
    """
    out = deepcopy(master_output)
    errors: List[str] = []
    used_paths: List[str] = []
    allowed_paths = set(reasoning_pack.get("allowed_evidence_paths") or [])

    for row in _collect_all_evidence_entries(out):
        case_path = str(row.get("case_path") or "").strip()
        if not case_path:
            errors.append("evidence_used_missing_case_path")
            continue
        if case_path not in allowed_paths:
            errors.append(f"evidence_path_not_allowed:{case_path}")
            continue
        found, _ = _resolve_pointer(case_json, case_path)
        if not found:
            errors.append(f"evidence_path_not_found:{case_path}")
            continue
        used_paths.append(case_path)

    # Conservative constraints
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    conservative_cap = float(reasoning_config.get("conservative_confidence_cap", 0.65))
    if gate_mode == "conservative":
        tpl = str(out.get("template_used") or "").lower()
        if tpl == "novelty":
            errors.append("conservative_mode_template_novelty_forbidden")
        confidence = (
            (out.get("mechanism_claim") or {}).get("confidence")
            if isinstance(out.get("mechanism_claim"), dict)
            else None
        )
        try:
            conf_val = float(confidence)
            if conf_val > conservative_cap:
                errors.append(f"conservative_confidence_cap_exceeded:{conf_val}>{conservative_cap}")
        except Exception:
            errors.append("mechanism_claim_confidence_invalid")

        limits = [str(x).lower() for x in (out.get("limits") or []) if isinstance(x, str)]
        if not any("conservative" in x for x in limits):
            errors.append("missing_conservative_limit_statement")

        tf = reasoning_pack.get("target_fields") or {}
        no_emission = tf.get("emission_aggr_nm") is None and tf.get("emission_solid_or_film_nm") is None
        if no_emission and not any("no emission evidence" in x for x in limits):
            errors.append("missing_no_emission_evidence_limit")

    used_paths = sorted(set(used_paths))
    return len(errors) == 0, errors, out, used_paths


def _set_or_replace_op(case_json: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    found, _ = _resolve_pointer(case_json, path)
    return {"op": "replace" if found else "add", "path": path, "value": value}


def build_master_patch(
    case_json: Dict[str, Any],
    normalized_output: Optional[Dict[str, Any]],
    *,
    status: str,
    used_paths: Sequence[str],
    meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    patch: List[Dict[str, Any]] = []
    if normalized_output is not None:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", normalized_output))
    else:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", None))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_status", status))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_used_evidence_paths", list(used_paths)))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_meta", meta))
    return patch


def run_master_reasoner_once(
    case_json: Dict[str, Any],
    reasoning_config: Dict[str, Any],
    llm_client: ResponsesLLMClient,
    reasoning_pack: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pack = deepcopy(reasoning_pack) if isinstance(reasoning_pack, dict) else build_reasoning_pack(case_json, reasoning_config)
    pack_hash = sha256_json(pack)
    prompt_bundle = build_master_prompt_bundle(pack, reasoning_config)
    input_text = json.dumps(prompt_bundle.get("user_payload"), ensure_ascii=False, indent=2)

    llm_out = llm_client.responses_json(
        instructions=f"{prompt_bundle.get('system')}\n\n{prompt_bundle.get('instructions')}",
        input_text=input_text,
        schema_name=str(prompt_bundle.get("output_schema_name") or MASTER_OUTPUT_SCHEMA_VERSION),
        schema=prompt_bundle.get("output_schema") or master_output_schema(),
    )
    parsed = llm_out.get("parsed") or {}
    ok, errors, normalized_output, used_paths = validate_master_output(
        parsed,
        pack,
        case_json,
        reasoning_config,
    )
    return {
        "reasoning_pack": pack,
        "pack_hash": pack_hash,
        "prompt_bundle": prompt_bundle,
        "template_used": prompt_bundle.get("template_used"),
        "llm_request": llm_out.get("request"),
        "llm_response_raw": llm_out.get("response"),
        "master_output_parsed": parsed,
        "normalized_output": normalized_output,
        "validation_errors": errors,
        "used_case_paths": used_paths,
        "status": "success" if ok else "failed_schema_validation",
    }

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
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.core.hashing import canonical_json_bytes, sha256_json
from src.reasoning.evidence_profiles import resolve_evidence_profiles
from src.reasoning.atb_ct_proxy_profile import compute_atb_ct_proxy_profile
from src.reasoning.atb_shape_rigidity_profile import compute_atb_shape_rigidity_profile
from src.reasoning.atb_structural_relaxation_profile import compute_atb_structural_relaxation_profile
from src.reasoning.atb_trend_profile import compute_atb_trend_profile
from src.reasoning.atb_trends_self import compute_atb_trends_self
from src.reasoning.neighbor_atb_stats import (
    ATB_DELTA_FIELDS,
    compact_neighbor_atb_rows,
    compute_neighbor_atb_stats_by_label,
)
from src.reasoning.reasoning_config import build_allowed_mechanism_labels, build_reasoning_policy
from src.tools.llm_client import ResponsesLLMClient


MASTER_PACK_VERSION = "master_pack_v1"
MASTER_PROMPT_BUNDLE_VERSION = "master_prompt_bundle_v1"
MASTER_OUTPUT_SCHEMA_VERSION_V1 = "master_output_schema_v1"
MASTER_OUTPUT_SCHEMA_VERSION_V2 = "master_output_schema_v2"
MASTER_OUTPUT_SCHEMA_VERSION_V3 = "master_output_schema_v3"
MASTER_OUTPUT_SCHEMA_VERSION = MASTER_OUTPUT_SCHEMA_VERSION_V3
MAX_PACK_BYTES = 15 * 1024
EVIDENCE_ID_PATTERN = re.compile(r"^(?:E[0-9]+|E_ATB_TREND_[1-4])$")
EVIDENCE_TOKEN_PATTERN = re.compile(r"\b(?:E_ATB_TREND_[1-4]|E[0-9]+)\b", flags=re.IGNORECASE)
STRONG_THRESHOLD_TRIGGER_PATTERN = re.compile(r"(?i)\b(?:threshold|cutoff)\b")
WEAK_THRESHOLD_TRIGGER_PATTERN = re.compile(r"(?i)\b(?:range|band)\b")
COMPARISON_PATTERN = re.compile(r"(?:<=|>=|<|>)")
INTERVAL_PATTERN = re.compile(r"-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?")
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
NUMERIC_CONTEXT_TOKEN_PATTERN = re.compile(
    r"(?i)(?:<=|>=|<|>|~|±|\bbetween\b|\bfrom\b|\bto\b|\bapprox(?:\.|imately)?\b)"
)
STANDARD_LIMIT_CONSERVATIVE = (
    "Conservative mode: mechanism assignment is tentative and should be interpreted with uncertainty."
)
STANDARD_LIMIT_NO_EMISSION = (
    "No emission evidence: emission_aggr_nm and emission_solid_or_film_nm are missing, so no direct emission-field confirmation is available."
)
STANDARD_LIMIT_LANE_DISABLED = (
    "Literature/experiment lane is disabled in this run; mechanism confidence is limited by missing external verification."
)
FORBIDDEN_MASTER_RISK_PATHS = {
    "/risk_scores/mechanism_hint",
    "/risk_scores/hint_confidence",
}
MASTER_NOTE_MAX_CHARS = 180
MASTER_MAX_SUPPORTING_CHAIN_ITEMS = 4
MASTER_MAX_PREDICTIONS_ITEMS = 3
MASTER_MAX_COMPETING_ITEMS = 3
MASTER_MAX_EVIDENCE_USED_ITEMS = 10
MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS = 3200
MASTER_DEFAULT_TEMPERATURE = 0.2
MASTER_OUTPUT_MODE_TAGGED_REPAIR = "tagged_repair"
MASTER_OUTPUT_MODE_STRICT_SCHEMA = "strict_schema"
TAGGED_SECTION_ORDER = [
    "TEMPLATE_USED",
    "STATUS",
    "PRIMARY_LABEL",
    "PRIMARY_CONFIDENCE",
    "PRIMARY",
    "COMPETING",
    "EVIDENCE",
    "PREDICTIONS",
    "LIMITS",
    "NEXT_ACTIONS",
]
TAGGED_SECTION_ALIASES = {
    "TEMPLATE": "TEMPLATE_USED",
    "NEXT": "NEXT_ACTIONS",
}
ATB_TREND_EVIDENCE_IDS = (
    "E_ATB_TREND_1",
    "E_ATB_TREND_2",
    "E_ATB_TREND_3",
    "E_ATB_TREND_4",
)
ATB_TREND_PROFILE_EVIDENCE_IDS = ("E31", "E32", "E33", "E34")
ATB_ENRICHMENT_EVIDENCE_IDS = ("E35", "E36", "E37", "E38", "E39")


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


def _json_only_contract_text(*, required_keys: Sequence[str], array_caps: Dict[str, int]) -> str:
    caps = ", ".join([f"{k}<={v}" for k, v in array_caps.items()])
    keys = ", ".join(required_keys)
    return (
        "JSON-only contract:\n"
        "- Output must start with '{' and end with '}'.\n"
        "- Do not output explanations, markdown, code fences, or any prefix/suffix text.\n"
        "- Output valid JSON only (no non-JSON text).\n"
        f"- Required top-level keys: {keys}.\n"
        f"- Array size caps: {caps}.\n"
        f"- Each evidence note must be <= {MASTER_NOTE_MAX_CHARS} chars."
    )


def _has_any_token(lines_lower: Sequence[str], tokens: Sequence[str]) -> bool:
    for line in lines_lower:
        for tok in tokens:
            if tok in line:
                return True
    return False


def _normalize_limits(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for row in value:
            txt = str(row or "").strip()
            if txt:
                out.append(txt)
        return out
    if isinstance(value, str):
        txt = value.strip()
        return [txt] if txt else []
    return []


def _to_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _policy(reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    return build_reasoning_policy(reasoning_config.get("policy") if isinstance(reasoning_config, dict) else None)


def _thresholds(reasoning_config: Dict[str, Any]) -> Dict[str, float]:
    cfg = reasoning_config if isinstance(reasoning_config, dict) else {}
    user = cfg.get("thresholds")
    if isinstance(user, dict):
        out: Dict[str, float] = {}
        for k, v in user.items():
            fv = _to_float(v)
            if fv is not None:
                out[str(k)] = fv
        if out:
            return out
    policy = _policy(cfg)
    return {
        "neighbor_support_min_sim": float(policy["neighbor_support_min_sim"]),
        "atb_dihedral_thresh_none": float(policy["atb_dihedral_thresh_none"]),
        "atb_dihedral_thresh_strong": float(policy["atb_dihedral_thresh_strong"]),
        "atb_dihedral_flat_eps": float(policy.get("atb_dihedral_flat_eps", 1.0e-6)),
        "atb_gap_flat_eps": float(policy.get("atb_gap_flat_eps", 0.05)),
        "atb_gap_weak": float(policy.get("atb_gap_weak", 0.2)),
        "atb_gap_strong": float(policy.get("atb_gap_strong", 0.6)),
        "atb_dipole_flat_eps": float(policy.get("atb_dipole_flat_eps", 0.05)),
        "atb_dipole_weak": float(policy.get("atb_dipole_weak", 0.2)),
        "atb_dipole_strong": float(policy.get("atb_dipole_strong", 0.6)),
        "atb_vol_flat_eps": float(policy.get("atb_vol_flat_eps", 0.1)),
        "atb_vol_weak": float(policy.get("atb_vol_weak", 0.5)),
        "atb_vol_strong": float(policy.get("atb_vol_strong", 2.0)),
        "atb_bonds_weak": float(policy.get("atb_bonds_weak", 0.02)),
        "atb_bonds_strong": float(policy.get("atb_bonds_strong", 0.08)),
        "atb_angles_weak": float(policy.get("atb_angles_weak", 0.2)),
        "atb_angles_strong": float(policy.get("atb_angles_strong", 0.8)),
        "atb_asymmetry_weak": float(policy.get("atb_asymmetry_weak", 0.05)),
        "atb_asymmetry_strong": float(policy.get("atb_asymmetry_strong", 0.2)),
        "atb_rotconst_rel_weak": float(policy.get("atb_rotconst_rel_weak", 0.05)),
        "atb_rotconst_rel_strong": float(policy.get("atb_rotconst_rel_strong", 0.15)),
        "top1_sim_low": float(policy["top1_sim_low"]),
        "entropy_high": float(policy["entropy_high"]),
        "global_confidence_cap": float(policy.get("global_confidence_cap", 0.95)),
        "r0_penalty_factor": float(policy.get("r0_penalty_factor", 0.90)),
        "conservative_confidence_cap": float(cfg.get("conservative_confidence_cap", 0.65)),
    }


def _threshold_values(reasoning_config: Dict[str, Any]) -> Set[float]:
    values: Set[float] = set()
    for v in _thresholds(reasoning_config).values():
        fv = _to_float(v)
        if fv is not None:
            values.add(round(float(fv), 6))
    return values


def _atb_support_level_from_features(
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> str:
    fs = (((reasoning_pack.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    if not isinstance(fs, dict):
        return "none"
    dihedral = _to_float(fs.get("delta_dihedral"))
    if dihedral is None:
        return "none"
    policy = _policy(reasoning_config)
    abs_dihedral = abs(dihedral)
    if abs_dihedral < float(policy["atb_dihedral_thresh_none"]):
        return "none"
    if abs_dihedral < float(policy["atb_dihedral_thresh_strong"]):
        return "weak"
    return "strong"


def _separation_score(reasoning_pack: Dict[str, Any]) -> Optional[float]:
    stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats_by_label")
    if not isinstance(stats, dict):
        stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats")
    if not isinstance(stats, dict):
        return None
    score = _to_float(stats.get("separation_score"))
    if score is None:
        return None
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _separation_reliability(reasoning_pack: Dict[str, Any]) -> str:
    stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats_by_label")
    if not isinstance(stats, dict):
        stats = (reasoning_pack.get("risk_scores") or {}).get("neighbor_atb_stats")
    if not isinstance(stats, dict):
        return "low"
    return str(stats.get("reliability") or "low").lower()


def _soft_confidence(
    *,
    raw_confidence: Optional[float],
    template_used: str,
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    policy = _policy(reasoning_config)
    tpl = str(template_used or "mixture").lower()
    base_defaults = {
        "stable": float(policy.get("confidence_base_stable", 0.62)),
        "mixture": float(policy.get("confidence_base_mixture", 0.52)),
        "novelty": float(policy.get("confidence_base_novelty", 0.45)),
    }
    base = base_defaults.get(tpl, base_defaults["mixture"])
    raw = raw_confidence if raw_confidence is not None else base
    raw = max(0.0, min(1.0, float(raw)))

    risk = reasoning_pack.get("risk_scores") or {}
    top1 = _to_float(risk.get("top1_sim"))
    entropy = _to_float(risk.get("mechanism_entropy"))
    top1_low = float(policy.get("top1_sim_low", 0.5))
    entropy_high = float(policy.get("entropy_high", 0.75))
    sim_strength = float(policy.get("penalty_sim_strength", 0.25))
    ent_strength = float(policy.get("penalty_entropy_strength", 0.25))

    sim_factor = 1.0
    if top1 is not None and top1 < top1_low:
        ratio = (top1_low - top1) / max(top1_low, 1e-9)
        sim_factor = max(0.55, 1.0 - sim_strength * max(0.0, ratio))

    entropy_factor = 1.0
    if entropy is not None and entropy > entropy_high:
        ratio = (entropy - entropy_high) / max(1.0 - entropy_high, 1e-9)
        entropy_factor = max(0.55, 1.0 - ent_strength * max(0.0, ratio))

    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    mode_factor = float(policy.get("penalty_mode_conservative", 0.86)) if gate_mode == "conservative" else 1.0

    separation = _separation_score(reasoning_pack)
    separation_rel = _separation_reliability(reasoning_pack)
    separation_boost = 1.0
    if separation is not None and separation_rel in {"medium", "high"}:
        center = float(policy.get("separation_center", 0.45))
        strength = float(policy.get("separation_boost_strength", 0.22))
        delta = separation - center
        separation_boost = max(0.8, min(1.25, 1.0 + strength * delta))

    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    round_index_raw = reasoning_config.get("round_index") if isinstance(reasoning_config, dict) else None
    try:
        round_index = int(round_index_raw) if round_index_raw is not None else None
    except Exception:
        round_index = None
    r0_penalty_factor = float(policy.get("r0_penalty_factor", 0.90))
    apply_r0_penalty = bool(active_profile == "R0" or round_index == 0)

    final_pre_cap = raw * sim_factor * entropy_factor * mode_factor * separation_boost
    if apply_r0_penalty:
        final_pre_cap *= r0_penalty_factor

    global_cap = float(policy.get("global_confidence_cap", 0.95))
    cap_value = max(0.05, min(0.95, global_cap))
    cap_reason = "global_cap"
    if gate_mode == "conservative":
        cap_value = min(cap_value, float(reasoning_config.get("conservative_confidence_cap", 0.65)))
        cap_reason = "conservative_cap"

    final = min(final_pre_cap, cap_value)
    final = max(0.05, min(0.95, final))

    components = {
        "raw_confidence_from_model": raw,
        "base_conf": base,
        "top1_sim": top1,
        "mechanism_entropy": entropy,
        "sim_factor": round(sim_factor, 6),
        "ent_factor": round(entropy_factor, 6),
        "mode_factor": round(mode_factor, 6),
        "separation_score": separation,
        "separation_reliability": separation_rel,
        "neighbor_factor": round(separation_boost, 6),
        "final_conf_pre_cap": round(float(final_pre_cap), 6),
        "final_conf_post_cap": round(float(final), 6),
        "cap_value": round(float(cap_value), 6),
        "cap_reason": cap_reason,
        "r0_penalty_applied": apply_r0_penalty,
        "r0_penalty_factor": round(float(r0_penalty_factor), 6),
        "confidence_formula_version": "soft_v1",
    }
    return round(float(final), 6), components


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _err(err_type: str, code: str, path: str, detail: str) -> Dict[str, str]:
    return {
        "type": err_type,
        "code": code,
        "path": path,
        "detail": detail,
    }


def _warn(code: str, path: str, detail: str) -> Dict[str, str]:
    return _err("warning", code, path, detail)


def _llm_failure_reason_from_exc(exc: BaseException) -> str:
    code = str(getattr(exc, "code", "") or "").strip().lower()
    if code in {"no_message_output", "json_parse_error", "json_repair_used"}:
        return code
    text = str(exc).lower()
    if "responses_empty_output_text" in text:
        return "no_message_output"
    if "responses_invalid_json" in text or "unterminated string" in text or "expecting property name enclosed in double quotes" in text:
        return "json_parse_error"
    return "llm_error"


def _llm_error_payload(exc: BaseException) -> Dict[str, Any]:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        return details
    return {}


def _parse_json_candidate_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates: List[str] = [raw]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    for block in fenced:
        b = str(block or "").strip()
        if b:
            candidates.append(b)
    l_brace = raw.find("{")
    r_brace = raw.rfind("}")
    if l_brace != -1 and r_brace > l_brace:
        candidates.append(raw[l_brace : r_brace + 1].strip())
    seen: Set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_tagged_sections(text: str) -> Dict[str, str]:
    raw = str(text or "")
    sections: Dict[str, str] = {}
    all_tags = list(TAGGED_SECTION_ORDER) + list(TAGGED_SECTION_ALIASES.keys())
    tag_alt = "|".join(sorted({re.escape(x) for x in all_tags}, key=len, reverse=True))
    patt = re.compile(rf"(?mi)^({tag_alt}):\s*(.*)$")
    matches = list(patt.finditer(raw))
    if not matches:
        primary = raw.strip()
        if primary:
            sections["PRIMARY"] = primary
        return sections
    for i, m in enumerate(matches):
        raw_key = str(m.group(1) or "").upper()
        key = TAGGED_SECTION_ALIASES.get(raw_key, raw_key)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        head = str(m.group(2) or "").strip()
        body = raw[start:end].strip()
        content = (head + ("\n" + body if body else "")).strip()
        sections[key] = content
    return sections


def _extract_evidence_ids(text: str) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for m in EVIDENCE_TOKEN_PATTERN.findall(str(text or "")):
        eid = str(m).upper()
        if eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    return out


def _candidate_set_labels(reasoning_pack: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ctx = reasoning_pack.get("mechanism_context")
    if not isinstance(ctx, dict):
        return out
    rows = ctx.get("candidate_mechanisms_top3")
    if not isinstance(rows, list):
        return out
    for row in rows:
        label: Optional[str] = None
        if isinstance(row, dict):
            raw = row.get("mechanism_id") or row.get("label") or row.get("name")
            label = str(raw or "").strip() if raw is not None else None
        elif isinstance(row, str):
            label = row.strip()
        if label and label not in out:
            out.append(label)
    return out


def resolve_allowed_mechanism_labels(
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> List[str]:
    cfg_labels = None
    if isinstance(reasoning_config, dict):
        cfg_labels = reasoning_config.get("allowed_mechanism_labels")
    out = build_allowed_mechanism_labels(cfg_labels)
    for label in _candidate_set_labels(reasoning_pack):
        if label not in out:
            out.append(label)
    return out


def _parse_role(text: str, default: str = "context") -> str:
    t = str(text or "").lower()
    if "counter" in t or "against" in t:
        return "counter"
    if "support" in t or "evidence for" in t:
        return "support"
    if default in {"support", "counter", "context"}:
        return default
    return "context"


def _pick_registry_id_by_suffix(reasoning_pack: Dict[str, Any], suffix: str) -> Optional[str]:
    for row in reasoning_pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("case_path") or "")
        eid = str(row.get("evidence_id") or "")
        if path.endswith(suffix) and eid:
            return eid
    return None


def _fallback_evidence_ids(reasoning_pack: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    registry = _registry_map(reasoning_pack.get("evidence_registry") or [])
    for eid in ATB_ENRICHMENT_EVIDENCE_IDS:
        if eid in registry and eid not in seen:
            seen.add(eid)
            out.append(eid)
    for eid in ATB_TREND_EVIDENCE_IDS:
        if eid in registry and eid not in seen:
            seen.add(eid)
            out.append(eid)

    prefs = [
        "/evidence_readiness/atb/features_summary/delta_dihedral",
        "/evidence_readiness/atb/features_summary/delta_gap",
        "/evidence_readiness/atb/features_summary/delta_volume",
        "/risk_scores/top1_sim",
    ]
    for suffix in prefs:
        eid = _pick_registry_id_by_suffix(reasoning_pack, suffix)
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    for row in reasoning_pack.get("evidence_registry") or []:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("evidence_id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
        if len(out) >= MASTER_MAX_EVIDENCE_USED_ITEMS:
            break
    return out


def _parse_template_value(text: str, fallback: str) -> str:
    t = str(text or "").strip().lower()
    if t in {"stable", "mixture", "novelty"}:
        return t
    for v in ("stable", "mixture", "novelty"):
        if v in t:
            return v
    return fallback if fallback in {"stable", "mixture", "novelty"} else "mixture"


def _parse_status_value(text: str) -> str:
    t = str(text or "").strip().lower()
    if t == "ok":
        return "ok"
    if "insufficient" in t:
        return "insufficient_evidence"
    return "insufficient_evidence"


def _normalize_primary_label(
    raw_label: str,
    label_map: Dict[str, str],
) -> Optional[str]:
    raw = str(raw_label or "").strip()
    if not raw:
        return None
    direct = label_map.get(raw.lower())
    if direct:
        return direct
    # Allow lightweight normalization from annotated label text without generic keyword scanning.
    candidates: List[str] = [raw]
    for sep in ("(", ":", ";", ",", "/", "|"):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if head:
                candidates.append(head)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]*", raw)
    for tok in tokens:
        candidates.append(tok.strip())
    seen: Set[str] = set()
    for cand in candidates:
        key = cand.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized = label_map.get(key)
        if normalized:
            return normalized
    return None


def _parse_lines(text: Any) -> List[str]:
    out: List[str] = []
    for row in str(text or "").splitlines():
        line = row.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line:
            out.append(line)
    return out


def _parse_first_float(text: Any) -> Optional[float]:
    m = NUMBER_PATTERN.search(str(text or ""))
    if not m:
        return None
    return _to_float(m.group(0))


def _tagged_text_to_master_output(
    *,
    raw_text: str,
    reasoning_pack: Dict[str, Any],
    reasoning_config: Dict[str, Any],
    template_fallback: str,
) -> Dict[str, Any]:
    sections = _parse_tagged_sections(raw_text)
    template_used = _parse_template_value(
        sections.get("TEMPLATE_USED") or sections.get("TEMPLATE"),
        template_fallback,
    )
    status = _parse_status_value(sections.get("STATUS"))
    required_sections = ["STATUS", "PRIMARY_LABEL", "PRIMARY_CONFIDENCE", "PRIMARY"]
    missing_sections = [s for s in required_sections if not str(sections.get(s) or "").strip()]
    if missing_sections:
        status = "invalid"
    primary_text = str(sections.get("PRIMARY") or "").strip()
    competing_text = str(sections.get("COMPETING") or "").strip()
    evidence_text = str(sections.get("EVIDENCE") or "").strip()
    predictions_text = str(sections.get("PREDICTIONS") or "").strip()
    limits_text = str(sections.get("LIMITS") or "").strip()
    next_text = str(sections.get("NEXT_ACTIONS") or sections.get("NEXT") or "").strip()

    fallback_ids = _fallback_evidence_ids(reasoning_pack)
    evidence_lines = _parse_lines(evidence_text)
    evidence_ids = _extract_evidence_ids(evidence_text)
    if not evidence_ids:
        evidence_ids = _extract_evidence_ids(primary_text)
    if not evidence_ids:
        evidence_ids = list(fallback_ids[:3])
    evidence_ids = evidence_ids[:MASTER_MAX_EVIDENCE_USED_ITEMS]

    evidence_used: List[Dict[str, Any]] = []
    for i, eid in enumerate(evidence_ids):
        note = f"tagged evidence reference {eid}"
        if i < len(evidence_lines):
            note = evidence_lines[i][:MASTER_NOTE_MAX_CHARS]
        evidence_used.append({"evidence_id": eid, "note": note[:MASTER_NOTE_MAX_CHARS], "role": _parse_role(note)})
    if not evidence_used and fallback_ids:
        evidence_used.append(
            {
                "evidence_id": fallback_ids[0],
                "note": "fallback evidence from registry",
                "role": "context",
            }
        )

    allowed_labels = resolve_allowed_mechanism_labels(reasoning_pack, reasoning_config)
    label_map = {str(x).lower(): str(x) for x in allowed_labels}
    primary_label_raw = str(sections.get("PRIMARY_LABEL") or "").strip()
    primary_label = _normalize_primary_label(primary_label_raw, label_map) or "unknown"

    raw_confidence = _parse_first_float(sections.get("PRIMARY_CONFIDENCE"))
    final_confidence, conf_components = _soft_confidence(
        raw_confidence=raw_confidence,
        template_used=template_used,
        reasoning_pack=reasoning_pack,
        reasoning_config=reasoning_config,
    )

    atb_level = _atb_support_level_from_features(reasoning_pack, reasoning_config)
    mechanism_claim = {
        "primary_hypothesis": {
            "mechanism_label": primary_label,
            "aie_rationale_type": template_used,
            "natural_language_mechanism": primary_text or "Insufficient direct evidence; provisional mechanism summary.",
            "atb_support_level": atb_level,
        },
        "confidence": float(round(final_confidence, 4)),
        "reasoning_mode_used": str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "normal"),
    }

    def _ev_one(default_idx: int, default_note: str, default_role: str = "context") -> List[Dict[str, Any]]:
        if evidence_used:
            src = dict(evidence_used[min(default_idx, len(evidence_used) - 1)])
            src["role"] = default_role
            src["note"] = default_note[:MASTER_NOTE_MAX_CHARS]
            return [src]
        if fallback_ids:
            return [{"evidence_id": fallback_ids[0], "note": default_note[:MASTER_NOTE_MAX_CHARS], "role": default_role}]
        return []

    supporting_chain = [
        {
            "step_id": "A",
            "step_name": "torsion_access",
            "claim": "Excited-state structural access is inferred from available aTB cues.",
            "evidence_used": _ev_one(0, "aTB structural cue", "support"),
        },
        {
            "step_id": "B",
            "step_name": "ct_family",
            "claim": "A nonradiative channel is hypothesized from CT/torsional context.",
            "evidence_used": _ev_one(1, "channel context cue", "context"),
        },
        {
            "step_id": "C",
            "step_name": "aIE_bridge",
            "claim": "Aggregation/rigidification may suppress nonradiative pathways.",
            "evidence_used": _ev_one(2, "aggregation bridge cue", "context"),
        },
        {
            "step_id": "D",
            "step_name": "discriminators",
            "claim": "Discriminator tests are needed to separate top competing hypotheses.",
            "evidence_used": _ev_one(0, "discriminator context", "context"),
        },
    ]

    competing_hypotheses: List[Dict[str, Any]] = []
    for i, line in enumerate(_parse_lines(competing_text)[:MASTER_MAX_COMPETING_ITEMS]):
        name = line.split(":", 1)[0].strip() or f"alt_hyp_{i+1}"
        line_conf = _parse_first_float(line)
        cand_conf = float(line_conf) if line_conf is not None else float(max(0.1, round(0.35 - 0.1 * i, 3)))
        cand_conf = max(0.0, min(1.0, cand_conf))
        competing_hypotheses.append(
            {
                "name": name[:120],
                "confidence": cand_conf,
                "atb_support_level": atb_level,
                "evidence_used": _ev_one(i, f"competing hypothesis context: {name}", "context"),
            }
        )

    predictions: List[Dict[str, Any]] = []
    prediction_lines = _parse_lines(predictions_text)
    if not prediction_lines:
        prediction_lines = _parse_lines(next_text)
    for i, line in enumerate(prediction_lines[:MASTER_MAX_PREDICTIONS_ITEMS]):
        predictions.append(
            {
                "prediction": line[:180],
                "expected_signal": "discriminator readout",
                "evidence_used": _ev_one(i, "prediction context", "context"),
            }
        )
    while len(predictions) < MASTER_MAX_PREDICTIONS_ITEMS:
        idx = len(predictions) + 1
        predictions.append(
            {
                "prediction": f"discriminator_test_{idx}",
                "expected_signal": "mechanism-separating trend",
                "evidence_used": _ev_one(idx - 1, f"prediction {idx} context", "context"),
            }
        )

    next_lines = _parse_lines(next_text)
    rec_next: List[str] = [x[:120] for x in next_lines[:5]]
    if not rec_next:
        rec_next = ["provide_offline_pdf", "switch_run_lane_offline_pdf"]

    limits = _parse_lines(limits_text)
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    if gate_mode == "conservative":
        limits.append(STANDARD_LIMIT_CONSERVATIVE)
    limits.append("Tagged natural-language output converted to structured master_output.")
    if primary_label == "unknown" and primary_label_raw:
        limits.append(f"PRIMARY_LABEL '{primary_label_raw}' was normalized to unknown (not in allowed mechanism labels).")
    if missing_sections:
        limits.append(f"Tagged response missing required sections: {', '.join(missing_sections)}.")
    limits.append(
        "Confidence is computed from raw PRIMARY_CONFIDENCE via soft penalty (sim/entropy/mode/separation)."
    )
    limits = limits[:6]

    return {
        "status": status,
        "template_used": template_used,
        "mechanism_claim": mechanism_claim,
        "supporting_chain": supporting_chain[:MASTER_MAX_SUPPORTING_CHAIN_ITEMS],
        "competing_hypotheses": competing_hypotheses[:MASTER_MAX_COMPETING_ITEMS],
        "predictions": predictions[:MASTER_MAX_PREDICTIONS_ITEMS],
        "limits": limits,
        "evidence_used": evidence_used[:MASTER_MAX_EVIDENCE_USED_ITEMS],
        "recommended_next_actions": rec_next[:5],
        "__meta": {
            "raw_confidence_from_model": conf_components.get("raw_confidence_from_model"),
            "final_confidence": conf_components.get("final_conf_post_cap"),
            "confidence_components": {
                "base_conf": conf_components.get("base_conf"),
                "sim_factor": conf_components.get("sim_factor"),
                "ent_factor": conf_components.get("ent_factor"),
                "mode_factor": conf_components.get("mode_factor"),
                "neighbor_factor": conf_components.get("neighbor_factor"),
                "final_conf_pre_cap": conf_components.get("final_conf_pre_cap"),
                "final_conf_post_cap": conf_components.get("final_conf_post_cap"),
                "cap_value": conf_components.get("cap_value"),
                "cap_reason": conf_components.get("cap_reason"),
                "r0_penalty_applied": conf_components.get("r0_penalty_applied"),
                "r0_penalty_factor": conf_components.get("r0_penalty_factor"),
            },
            "penalty_components": {
                "base_conf": conf_components.get("base_conf"),
                "sim_factor": conf_components.get("sim_factor"),
                "ent_factor": conf_components.get("ent_factor"),
                "mode_factor": conf_components.get("mode_factor"),
                "neighbor_factor": conf_components.get("neighbor_factor"),
                "final_conf_pre_cap": conf_components.get("final_conf_pre_cap"),
                "final_conf_post_cap": conf_components.get("final_conf_post_cap"),
                "cap_value": conf_components.get("cap_value"),
                "cap_reason": conf_components.get("cap_reason"),
                "r0_penalty_applied": conf_components.get("r0_penalty_applied"),
                "r0_penalty_factor": conf_components.get("r0_penalty_factor"),
            },
            "allowed_mechanism_labels": allowed_labels,
            "missing_required_sections": missing_sections,
            "confidence_formula_version": conf_components.get("confidence_formula_version"),
        },
    }


def _max_budgeted_items(rows: Any, budget: int) -> bool:
    return isinstance(rows, list) and len(rows) <= int(budget)


def _weak_trigger_in_numeric_context(text: str, window_chars: int = 40) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    for m in WEAK_THRESHOLD_TRIGGER_PATTERN.finditer(raw):
        lo = max(0, m.start() - window_chars)
        hi = min(len(raw), m.end() + window_chars)
        snippet = raw[lo:hi]
        if NUMBER_PATTERN.search(snippet):
            return True
        if COMPARISON_PATTERN.search(snippet):
            return True
        if INTERVAL_PATTERN.search(snippet):
            return True
        if NUMERIC_CONTEXT_TOKEN_PATTERN.search(snippet):
            return True
    return False


def _risk_scores_subset(
    case_json: Dict[str, Any],
    *,
    include_neighbor_summary: bool,
    include_neighbor_feature_rows: bool,
) -> Dict[str, Any]:
    src = case_json.get("risk_scores") or {}
    keep = [
        "top1_sim",
        "mean_topk_sim",
        "novelty_struct",
        "mechanism_entropy",
        "atb_neighbor_consistency",
    ]
    if include_neighbor_summary and include_neighbor_feature_rows:
        keep.append("atb_neighbor_features_all")
    out = {k: src.get(k) for k in keep if k in src}
    # Keep mechanism hint in case for debug/routing only; exclude from master reasoning pack.
    out.pop("mechanism_hint", None)
    out.pop("hint_confidence", None)
    if include_neighbor_summary and include_neighbor_feature_rows:
        out["atb_neighbor_features_all"] = compact_neighbor_atb_rows(src.get("atb_neighbor_features_all"))
    elif "atb_neighbor_features_all" in out:
        out["atb_neighbor_features_all"] = []
    return out


def _evidence_readiness_subset(
    case_json: Dict[str, Any],
    *,
    include_target_atb_summary: bool,
    include_target_atb_full: bool,
    include_literature_status: bool,
    include_experiment_status: bool,
) -> Dict[str, Any]:
    er = case_json.get("evidence_readiness") or {}
    atb = er.get("atb") or {}
    lit = er.get("literature") or {}
    exp = er.get("experiment") or {}
    return {
        "atb": {
            "cache_status": atb.get("cache_status"),
            "features_summary": atb.get("features_summary") if include_target_atb_summary else None,
            "features": atb.get("features") if include_target_atb_full else None,
            "missing_fields": atb.get("missing_fields"),
        },
        "literature": (
            {
                "status": lit.get("status"),
                "notes": lit.get("notes"),
            }
            if include_literature_status
            else {"status": None, "notes": None}
        ),
        "experiment": (
            {
                "status": exp.get("status"),
                "notes": exp.get("notes"),
            }
            if include_experiment_status
            else {"status": None, "notes": None}
        ),
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


def _neighbor_label_lookup(case_json: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in case_json.get("neighbors") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("neighbor_mechanism_label") or "").strip()
        if not label:
            continue
        inchikey = str(row.get("neighbor_inchikey") or "").strip()
        if inchikey:
            out[inchikey] = label
        rank = row.get("rank")
        if isinstance(rank, int):
            out[f"rank:{rank}"] = label
    return out


def _mechanism_context(case_json: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
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


def _build_evidence_registry(
    case_json: Dict[str, Any],
    neighbors_topk: Sequence[Dict[str, Any]],
    *,
    include_target_atb_signals: bool,
    atb_trend_profile: Optional[Dict[str, Any]],
    atb_ct_proxy_profile: Optional[Dict[str, Any]],
    atb_structural_relaxation_profile: Optional[Dict[str, Any]],
    atb_shape_rigidity_profile: Optional[Dict[str, Any]],
    include_literature_status: bool,
    include_experiment_status: bool,
    include_atb_trends_self: bool,
    atb_trends_self: Optional[Dict[str, Any]],
    include_neighbor_atb_stats: bool,
    neighbor_atb_stats: Optional[Dict[str, Any]],
    max_items: int = 20,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    def _add(path: str, label: str, role_hint: str, note_hint: str) -> None:
        if any(row.get("case_path") == path for row in entries):
            return
        found, value = _resolve_pointer(case_json, path)
        if not found or _is_empty_value(value):
            return
        entries.append(
            {
                "source_type": "case",
                "case_path": path,
                "label": label,
                "value_preview": value,
                "role_hint": role_hint,
                "note_hint": note_hint,
            }
        )

    # Gate core
    _add("/current_gate/state", "gate state", "context", "gate state")
    _add("/current_gate/reasoning_mode", "gate reasoning mode", "context", "reasoning mode")
    _add("/current_gate/reason", "gate reason", "context", "gate rationale")

    # Risk priors
    _add("/risk_scores/top1_sim", "top1 similarity", "context", "closest-neighbor similarity prior")
    _add("/risk_scores/mean_topk_sim", "mean top-k similarity", "context", "local neighborhood density")
    _add("/risk_scores/mechanism_entropy", "neighbor mechanism entropy", "context", "neighbor label uncertainty")
    _add("/risk_scores/novelty_struct", "structural novelty", "context", "structural novelty score")

    # aTB evidence keys (R1+ by default; excluded from R0 prior-only stage).
    if include_target_atb_signals:
        _add("/evidence_readiness/atb/cache_status", "aTB cache status", "context", "target aTB cache readiness")
        _add(
            "/evidence_readiness/atb/features_summary/delta_dihedral",
            "aTB delta dihedral",
            "support",
            "excited-state torsional accessibility",
        )
        _add(
            "/evidence_readiness/atb/features_summary/delta_gap",
            "aTB delta gap",
            "context",
            "CT-family weak context",
        )
        _add(
            "/evidence_readiness/atb/features_summary/delta_volume",
            "aTB delta volume",
            "context",
            "packing/rigidification proxy",
        )
        _add(
            "/evidence_readiness/atb/features_summary/excitation_energy",
            "aTB excitation energy",
            "context",
            "excited-state energy context",
        )

    # Neighbors: top-2 sim + label as prior
    for i, row in enumerate(neighbors_topk[:2]):
        if not isinstance(row, dict):
            continue
        case_index = row.get("case_index")
        if not isinstance(case_index, int):
            continue
        _add(
            f"/neighbors/{case_index}/sim",
            f"neighbor {i+1} similarity",
            "context",
            f"neighbor {i+1} prior similarity",
        )
        _add(
            f"/neighbors/{case_index}/neighbor_mechanism_label",
            f"neighbor {i+1} mechanism label",
            "context",
            f"neighbor {i+1} mechanism prior label",
        )

    # Downstream status signals
    if include_literature_status:
        _add("/evidence_readiness/literature/status", "literature status", "context", "literature readiness status")
    if include_experiment_status:
        _add("/evidence_readiness/experiment/status", "experiment status", "context", "experiment readiness status")

    reg: List[Dict[str, Any]] = []
    for idx, row in enumerate(entries[:max_items], start=1):
        reg.append(
            {
                "evidence_id": f"E{idx}",
                "source_type": row.get("source_type") or "case",
                "case_path": row["case_path"],
                "label": row["label"],
                "value_preview": row["value_preview"],
                "role_hint": row["role_hint"],
                "note_hint": row["note_hint"],
            }
        )

    if isinstance(atb_trend_profile, dict):
        buckets = atb_trend_profile.get("buckets") if isinstance(atb_trend_profile.get("buckets"), dict) else {}
        direction = atb_trend_profile.get("direction") if isinstance(atb_trend_profile.get("direction"), dict) else {}
        reliability = str(atb_trend_profile.get("reliability") or "unknown")
        motion = str(atb_trend_profile.get("overall_motion_proxy") or "unknown")
        reg.extend(
            [
                {
                    "evidence_id": "E31",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_dihedral",
                    "label": "aTB torsion trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_dihedral"),
                        "direction": direction.get("delta_dihedral"),
                    },
                    "role_hint": "support",
                    "note_hint": f"torsion trend bucket={buckets.get('delta_dihedral')} direction={direction.get('delta_dihedral')}",
                },
                {
                    "evidence_id": "E32",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_gap",
                    "label": "aTB CT proxy trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_gap"),
                        "direction": direction.get("delta_gap"),
                    },
                    "role_hint": "context",
                    "note_hint": f"ct proxy bucket={buckets.get('delta_gap')} direction={direction.get('delta_gap')}",
                },
                {
                    "evidence_id": "E33",
                    "source_type": "case",
                    "case_path": "/evidence_readiness/atb/features_summary/delta_volume",
                    "label": "aTB volume trend",
                    "value_preview": {
                        "bucket": buckets.get("delta_volume"),
                        "direction": direction.get("delta_volume"),
                    },
                    "role_hint": "context",
                    "note_hint": f"volume trend bucket={buckets.get('delta_volume')} direction={direction.get('delta_volume')}",
                },
                {
                    "evidence_id": "E34",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/atb_trend_profile/overall_motion_proxy",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_dihedral",
                        "/evidence_readiness/atb/features_summary/delta_gap",
                        "/evidence_readiness/atb/features_summary/delta_volume",
                    ],
                    "label": "aTB overall motion proxy",
                    "value_preview": {"overall_motion_proxy": motion, "reliability": reliability},
                    "role_hint": "context",
                    "note_hint": "self-only motion proxy from bucketized aTB trend profile",
                },
            ]
        )

    def _append_registry_entry(row: Dict[str, Any]) -> None:
        preview = row.get("value_preview")
        if _is_empty_value(preview):
            return
        reg.append(row)

    if isinstance(atb_ct_proxy_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E35",
                "source_type": "case",
                "case_path": "/evidence_readiness/atb/features_summary/delta_dipole",
                "label": "aTB charge-separation proxy",
                "value_preview": {
                    "delta_dipole_bucket": atb_ct_proxy_profile.get("delta_dipole_bucket"),
                    "delta_dipole_direction": atb_ct_proxy_profile.get("delta_dipole_direction"),
                    "reliability": atb_ct_proxy_profile.get("reliability"),
                },
                "role_hint": "support",
                "note_hint": "charge-separation change from target-only aTB",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E36",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/atb_ct_proxy_profile/ct_proxy_score",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/delta_dipole",
                    "/evidence_readiness/atb/features_summary/delta_gap",
                ],
                "label": "aTB CT proxy summary",
                "value_preview": {
                    "ct_proxy_score": atb_ct_proxy_profile.get("ct_proxy_score"),
                    "delta_gap_bucket": atb_ct_proxy_profile.get("delta_gap_bucket"),
                    "delta_dipole_bucket": atb_ct_proxy_profile.get("delta_dipole_bucket"),
                },
                "role_hint": "support",
                "note_hint": "compact CT proxy from dipole and gap change",
            }
        )

    if isinstance(atb_structural_relaxation_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E37",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/atb_structural_relaxation_profile/relaxation_proxy_score",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/delta_dihedral",
                    "/evidence_readiness/atb/features_summary/delta_bonds",
                    "/evidence_readiness/atb/features_summary/delta_angles",
                    "/evidence_readiness/atb/features_summary/delta_volume",
                ],
                "label": "aTB structural relaxation summary",
                "value_preview": {
                    "relaxation_proxy_score": atb_structural_relaxation_profile.get("relaxation_proxy_score"),
                    "delta_dihedral_bucket": ((atb_structural_relaxation_profile.get("buckets") or {}).get("delta_dihedral")),
                    "delta_volume_bucket": ((atb_structural_relaxation_profile.get("buckets") or {}).get("delta_volume")),
                },
                "role_hint": "support",
                "note_hint": "combined structural relaxation from torsion, bonds, angles, and volume",
            }
        )
        _append_registry_entry(
            {
                "evidence_id": "E38",
                "source_type": "case",
                "case_path": "/evidence_readiness/atb/features_summary/exciting_path_mean_volume",
                "label": "aTB excited-path volume cue",
                "value_preview": {
                    "exciting_path_mean_volume": (
                        ((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary", {})
                    ).get("exciting_path_mean_volume"),
                },
                "role_hint": "context",
                "note_hint": "excited-path volume cue from cached aTB output",
            }
        )

    if isinstance(atb_shape_rigidity_profile, dict):
        _append_registry_entry(
            {
                "evidence_id": "E39",
                "source_type": "derived_pack",
                "pack_path": "/risk_scores/atb_shape_rigidity_profile/rigidity_proxy",
                "derived_from_case_paths": [
                    "/evidence_readiness/atb/features_summary/s0_rays_asymmetry_parameter",
                    "/evidence_readiness/atb/features_summary/s1_rays_asymmetry_parameter",
                    "/evidence_readiness/atb/features_summary/s0_rotational_constant_a",
                    "/evidence_readiness/atb/features_summary/s1_rotational_constant_a",
                ],
                "label": "aTB shape-rigidity summary",
                "value_preview": {
                    "rigidity_proxy": atb_shape_rigidity_profile.get("rigidity_proxy"),
                    "reliability": atb_shape_rigidity_profile.get("reliability"),
                },
                "role_hint": "context",
                "note_hint": "auxiliary rigidity/shape cue from asymmetry and rotational changes",
            }
        )

    if include_atb_trends_self and isinstance(atb_trends_self, dict):
        if bool(atb_trends_self.get("enabled")) or str(atb_trends_self.get("reliability") or "").lower() in {"low", "medium", "high"}:
            reg_seed = [
                (
                    "E_ATB_TREND_1",
                    "/risk_scores/atb_trends_self/delta_dihedral_bucket",
                    "aTB self trend: delta_dihedral",
                    {
                        "delta_dihedral_abs_deg": atb_trends_self.get("delta_dihedral_abs_deg"),
                        "delta_dihedral_bucket": atb_trends_self.get("delta_dihedral_bucket"),
                        "delta_dihedral_direction": atb_trends_self.get("delta_dihedral_direction"),
                        "delta_dihedral_percentile_global": atb_trends_self.get("delta_dihedral_percentile_global"),
                    },
                    "support",
                    "Target-only torsional self trend bucket.",
                ),
                (
                    "E_ATB_TREND_2",
                    "/risk_scores/atb_trends_self/delta_gap_bucket",
                    "aTB self trend: delta_gap",
                    {
                        "delta_gap_direction": atb_trends_self.get("delta_gap_direction"),
                        "delta_gap_bucket": atb_trends_self.get("delta_gap_bucket"),
                        "delta_gap_percentile_global": atb_trends_self.get("delta_gap_percentile_global"),
                    },
                    "context",
                    "Target-only gap trend direction and magnitude bucket.",
                ),
                (
                    "E_ATB_TREND_3",
                    "/risk_scores/atb_trends_self/delta_volume_bucket",
                    "aTB self trend: delta_volume",
                    {
                        "delta_volume_direction": atb_trends_self.get("delta_volume_direction"),
                        "delta_volume_bucket": atb_trends_self.get("delta_volume_bucket"),
                        "delta_volume_percentile_global": atb_trends_self.get("delta_volume_percentile_global"),
                    },
                    "context",
                    "Target-only volume trend direction and magnitude bucket.",
                ),
                (
                    "E_ATB_TREND_4",
                    "/risk_scores/atb_trends_self/overall_motion_proxy",
                    "aTB self trend: overall motion proxy",
                    {
                        "overall_motion_proxy": atb_trends_self.get("overall_motion_proxy"),
                        "reliability": atb_trends_self.get("reliability"),
                    },
                    "context",
                    "Self-trend summary reliability and motion proxy.",
                ),
            ]
            for evidence_id, pack_path, label, value_preview, role_hint, note_hint in reg_seed:
                reg.append(
                    {
                        "evidence_id": evidence_id,
                        "source_type": "derived_pack",
                        "pack_path": pack_path,
                        "derived_from_case_paths": [
                            "/evidence_readiness/atb/features_summary/delta_dihedral",
                            "/evidence_readiness/atb/features_summary/delta_gap",
                            "/evidence_readiness/atb/features_summary/delta_volume",
                            "/evidence_readiness/atb/features_summary/excitation_energy",
                        ],
                        "label": label,
                        "value_preview": value_preview,
                        "role_hint": role_hint,
                        "note_hint": note_hint,
                    }
                )

    if include_neighbor_atb_stats and isinstance(neighbor_atb_stats, dict):
        fields = neighbor_atb_stats.get("fields") if isinstance(neighbor_atb_stats.get("fields"), dict) else {}
        reliability = str(neighbor_atb_stats.get("reliability") or "").strip()
        by_label = neighbor_atb_stats.get("by_label") if isinstance(neighbor_atb_stats.get("by_label"), dict) else {}

        def _preview(field_name: str) -> Optional[Dict[str, Any]]:
            row = fields.get(field_name)
            if not isinstance(row, dict):
                return None
            preview = {
                "target": row.get("target"),
                "neighbors_median": row.get("neighbors_median"),
                "neighbors_iqr": row.get("neighbors_iqr"),
                "target_percentile": row.get("target_percentile"),
                "z_robust": row.get("z_robust"),
            }
            if all(v is None for v in preview.values()):
                return None
            return preview

        e21 = _preview("abs_delta_dihedral")
        if e21 is not None:
            reg.append(
                {
                    "evidence_id": "E21",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/fields/abs_delta_dihedral",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_dihedral",
                        "/risk_scores/atb_neighbor_features_all",
                    ],
                    "label": "target abs_delta_dihedral vs neighbor distribution",
                    "value_preview": e21,
                    "role_hint": "support",
                    "note_hint": "R2 comparative torsional evidence",
                }
            )

        e22 = _preview("delta_gap")
        if e22 is not None:
            reg.append(
                {
                    "evidence_id": "E22",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/fields/delta_gap",
                    "derived_from_case_paths": [
                        "/evidence_readiness/atb/features_summary/delta_gap",
                        "/risk_scores/atb_neighbor_features_all",
                    ],
                    "label": "target delta_gap vs neighbor distribution",
                    "value_preview": e22,
                    "role_hint": "context",
                    "note_hint": "R2 comparative CT-family context",
                }
            )

        if by_label:
            reg.append(
                {
                    "evidence_id": "E23",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/by_label",
                    "derived_from_case_paths": ["/risk_scores/atb_neighbor_features_all"],
                    "label": "label-stratified neighbor aTB comparison",
                    "value_preview": by_label,
                    "role_hint": "context",
                    "note_hint": "R2 label-conditioned neighbor comparison",
                }
            )

        if reliability:
            reg.append(
                {
                    "evidence_id": "E24",
                    "source_type": "derived_pack",
                    "pack_path": "/risk_scores/neighbor_atb_stats_by_label/reliability",
                    "derived_from_case_paths": ["/risk_scores/atb_neighbor_features_all"],
                    "label": "neighbor comparative reliability",
                    "value_preview": {
                        "reliability": reliability,
                        "sample_size": neighbor_atb_stats.get("sample_size"),
                        "separation_score": neighbor_atb_stats.get("separation_score"),
                    },
                    "role_hint": "context",
                    "note_hint": "R2 comparative reliability level",
                }
            )
    # Keep registry compact (target <=20) while preserving derived comparative entries.
    if len(reg) > 20:
        protected = {"E21", "E22", "E23", "E24", *ATB_TREND_PROFILE_EVIDENCE_IDS, *ATB_ENRICHMENT_EVIDENCE_IDS, *ATB_TREND_EVIDENCE_IDS}
        trimmed: List[Dict[str, Any]] = []
        for row in reg:
            if len(trimmed) >= 20:
                break
            eid = str(row.get("evidence_id") or "")
            if eid in protected:
                trimmed.append(row)
        for row in reg:
            if len(trimmed) >= 20:
                break
            eid = str(row.get("evidence_id") or "")
            if eid in protected:
                continue
            trimmed.append(row)
        reg = trimmed
    return reg


def _registry_map(evidence_registry: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(evidence_registry, dict):
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in evidence_registry.items():
            if isinstance(v, dict):
                evidence_id = str(v.get("evidence_id") or k)
                out[evidence_id] = dict(v)
        return out
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(evidence_registry, list):
        for row in evidence_registry:
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("evidence_id") or "").strip()
            if not evidence_id:
                continue
            out[evidence_id] = dict(row)
    return out


def build_reasoning_pack(case_json: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    query = case_json.get("query") or {}
    runtime = case_json.get("runtime") or {}
    emission_cfg = ((case_json.get("evidence_acquire") or {}).get("emission") or {})
    active_profile, active_profile_cfg, profiles_cfg = resolve_evidence_profiles(reasoning_config)
    gate_mode = str(((case_json.get("current_gate") or {}).get("reasoning_mode") or "")).lower()
    default_neighbor_k = 5 if gate_mode == "conservative" else 10
    neighbor_k = int(active_profile_cfg.get("neighbor_topk", default_neighbor_k) or 0)
    include_neighbor_summary = bool(active_profile_cfg.get("include_neighbor_summary", True))
    include_atb_trends_self = bool(active_profile_cfg.get("include_atb_trends_self", active_profile in {"R1", "R2", "R3"}))
    include_neighbor_atb_stats = bool(
        active_profile_cfg.get("include_neighbor_atb_stats_by_label")
        if "include_neighbor_atb_stats_by_label" in active_profile_cfg
        else active_profile_cfg.get("include_neighbor_atb_stats", True)
    )
    include_neighbor_feature_rows = bool(active_profile_cfg.get("include_neighbor_feature_rows", False))
    include_target_atb_summary = bool(active_profile_cfg.get("include_target_atb_summary", True))
    include_target_atb_full = bool(active_profile_cfg.get("include_target_atb_full", False))
    include_atb_trend_profile = bool(
        active_profile_cfg.get("include_atb_trend_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_ct_proxy_profile = bool(
        active_profile_cfg.get("include_atb_ct_proxy_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_structural_relaxation_profile = bool(
        active_profile_cfg.get("include_atb_structural_relaxation_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_atb_shape_rigidity_profile = bool(
        active_profile_cfg.get("include_atb_shape_rigidity_profile", active_profile in {"R1", "R2", "R3"})
    )
    include_literature_status = bool(active_profile_cfg.get("include_literature_status", True))
    include_experiment_status = bool(active_profile_cfg.get("include_experiment_status", True))
    registry_max_items = int(active_profile_cfg.get("registry_max_items", 20) or 20)

    neighbors_topk = _neighbors_topk(case_json, k=neighbor_k) if include_neighbor_summary else []
    neighbor_rows_compact = compact_neighbor_atb_rows(((case_json.get("risk_scores") or {}).get("atb_neighbor_features_all") or []))
    risk_subset = _risk_scores_subset(
        case_json,
        include_neighbor_summary=include_neighbor_summary,
        include_neighbor_feature_rows=include_neighbor_feature_rows,
    )
    atb_status = str((((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("cache_status") or "")).lower()
    target_summary = (((case_json.get("evidence_readiness") or {}).get("atb") or {}).get("features_summary") or {})
    if include_atb_trends_self and active_profile in {"R1", "R2", "R3"}:
        atb_trends_self = compute_atb_trends_self(
            target_atb_features_summary=target_summary if isinstance(target_summary, dict) else {},
            thresholds=_thresholds(reasoning_config),
        )
        if atb_status != "success":
            atb_trends_self["enabled"] = False
            atb_trends_self["reliability"] = "low"
            notes = list(atb_trends_self.get("notes") or [])
            notes.append(f"atb cache_status is {atb_status or 'unknown'}; self-trend is informational only.")
            atb_trends_self["notes"] = notes[:4]
    else:
        atb_trends_self = {
            "enabled": False,
            "fields_used": ["delta_dihedral", "delta_gap", "delta_volume", "excitation_energy"],
            "delta_dihedral_abs_deg": None,
            "delta_dihedral_bucket": "unknown",
            "delta_dihedral_direction": "unknown",
            "delta_gap_direction": "unknown",
            "delta_gap_bucket": "unknown",
            "delta_volume_direction": "unknown",
            "delta_volume_bucket": "unknown",
            "overall_motion_proxy": "unknown",
            "reliability": "low",
            "notes": ["atb_trends_self disabled by profile"],
        }
    risk_subset["atb_trends_self"] = atb_trends_self
    atb_trend_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_trend_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_trend_profile = compute_atb_trend_profile(target_summary)
        risk_subset["atb_trend_profile"] = atb_trend_profile
    atb_ct_proxy_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_ct_proxy_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_ct_proxy_profile = compute_atb_ct_proxy_profile(target_summary, thresholds=_thresholds(reasoning_config))
        risk_subset["atb_ct_proxy_profile"] = atb_ct_proxy_profile
    atb_structural_relaxation_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_structural_relaxation_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_structural_relaxation_profile = compute_atb_structural_relaxation_profile(
            target_summary,
            thresholds=_thresholds(reasoning_config),
        )
        risk_subset["atb_structural_relaxation_profile"] = atb_structural_relaxation_profile
    atb_shape_rigidity_profile: Optional[Dict[str, Any]] = None
    if (
        include_atb_shape_rigidity_profile
        and include_target_atb_summary
        and active_profile in {"R1", "R2", "R3"}
        and atb_status == "success"
        and isinstance(target_summary, dict)
    ):
        atb_shape_rigidity_profile = compute_atb_shape_rigidity_profile(
            target_summary,
            thresholds=_thresholds(reasoning_config),
        )
        risk_subset["atb_shape_rigidity_profile"] = atb_shape_rigidity_profile
    if include_neighbor_atb_stats and active_profile in {"R2", "R3"}:
        neighbor_atb_stats = compute_neighbor_atb_stats_by_label(
            target_features_summary=target_summary if isinstance(target_summary, dict) else {},
            neighbor_atb_features_all=neighbor_rows_compact,
            neighbor_label_lookup=_neighbor_label_lookup(case_json),
        )
    else:
        neighbor_atb_stats = {
            "sample_size": 0,
            "fields": {},
            "by_label": {},
            "summary": ["neighbor_atb_stats disabled by profile"],
            "reliability": "low",
        }
    risk_subset["neighbor_atb_stats_by_label"] = neighbor_atb_stats
    risk_subset["neighbor_atb_stats"] = neighbor_atb_stats

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
        "evidence_profile": {
            "active_profile": active_profile,
            "config": active_profile_cfg,
            "profiles": profiles_cfg.get("profiles"),
        },
        "gate": deepcopy(case_json.get("current_gate") or {}),
        "neighbors_topk": neighbors_topk,
        "risk_scores": risk_subset,
        "neighbor_atb_stats_by_label": neighbor_atb_stats,
        "neighbor_atb_stats": neighbor_atb_stats,
        "atb_trends_self": atb_trends_self,
        "evidence_readiness": _evidence_readiness_subset(
            case_json,
            include_target_atb_summary=include_target_atb_summary,
            include_target_atb_full=include_target_atb_full,
            include_literature_status=include_literature_status,
            include_experiment_status=include_experiment_status,
        ),
        "target_fields": deepcopy(case_json.get("target_fields") or {}),
        "target_fields_provenance": deepcopy(case_json.get("target_fields_provenance") or {}),
        "mechanism_context": _mechanism_context(case_json),
    }
    pack["evidence_registry"] = _build_evidence_registry(
        case_json,
        neighbors_topk,
        include_target_atb_signals=include_target_atb_summary and active_profile in {"R1", "R2", "R3"},
        atb_trend_profile=atb_trend_profile,
        atb_ct_proxy_profile=atb_ct_proxy_profile,
        atb_structural_relaxation_profile=atb_structural_relaxation_profile,
        atb_shape_rigidity_profile=atb_shape_rigidity_profile,
        include_literature_status=include_literature_status,
        include_experiment_status=include_experiment_status,
        include_atb_trends_self=include_atb_trends_self and active_profile in {"R1", "R2", "R3"},
        atb_trends_self=atb_trends_self,
        include_neighbor_atb_stats=include_neighbor_atb_stats and active_profile in {"R2", "R3"},
        neighbor_atb_stats=neighbor_atb_stats,
        max_items=registry_max_items,
    )

    if _safe_json_size_bytes(pack) > MAX_PACK_BYTES:
        # deterministic shrink strategy
        pack["neighbors_topk"] = pack["neighbors_topk"][:5]
        pack["mechanism_context"]["mechanism_signatures_top3"] = (
            pack["mechanism_context"].get("mechanism_signatures_top3") or []
        )[:2]
        pack["evidence_registry"] = _build_evidence_registry(
            case_json,
            pack["neighbors_topk"],
            include_target_atb_signals=include_target_atb_summary and active_profile in {"R1", "R2", "R3"},
            atb_trend_profile=atb_trend_profile,
            atb_ct_proxy_profile=atb_ct_proxy_profile,
            atb_structural_relaxation_profile=atb_structural_relaxation_profile,
            atb_shape_rigidity_profile=atb_shape_rigidity_profile,
            include_literature_status=include_literature_status,
            include_experiment_status=include_experiment_status,
            include_atb_trends_self=include_atb_trends_self and active_profile in {"R1", "R2", "R3"},
            atb_trends_self=atb_trends_self,
            include_neighbor_atb_stats=include_neighbor_atb_stats and active_profile in {"R2", "R3"},
            neighbor_atb_stats=neighbor_atb_stats,
            max_items=min(registry_max_items, 16),
        )
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


def _build_prompt_payload(reasoning_pack: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep model-facing payload compact:
    - prioritize chemical signal fields,
    - reduce traceability token overhead while preserving strict server-side validation.
    """
    payload = deepcopy(reasoning_pack)
    risk_scores = payload.get("risk_scores")
    if isinstance(risk_scores, dict):
        # Keep prompt compact: neighbor raw rows stay out of model-facing payload.
        risk_scores.pop("atb_neighbor_features_all", None)
    payload["validation_note"] = "Use only evidence_id keys from evidence_registry for citations."
    payload["candidate_set_text"] = _candidate_set_text(reasoning_pack)
    payload["reasoning_config"] = {"thresholds": _thresholds(reasoning_config)}
    return payload


def _candidate_set_text(reasoning_pack: Dict[str, Any]) -> str:
    ctx = reasoning_pack.get("mechanism_context") or {}
    rows = ctx.get("candidate_mechanisms_top3")
    labels: List[str] = []
    if isinstance(rows, list):
        for row in rows:
            label: Optional[str] = None
            if isinstance(row, dict):
                raw = row.get("mechanism_id") or row.get("label") or row.get("name")
                if isinstance(raw, str):
                    label = raw.strip()
            elif isinstance(row, str):
                label = row.strip()
            if label and label not in labels:
                labels.append(label)
    if labels:
        return f"Top competing mechanisms (from retrieval priors): {', '.join(labels)}."
    return "Top competing mechanisms are uncertain; propose plausible hypotheses from evidence."


def build_master_prompt_bundle(reasoning_pack: Dict[str, Any], reasoning_config: Dict[str, Any]) -> Dict[str, Any]:
    template = _choose_template(reasoning_pack)
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "normal").lower()
    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    policy = _policy(reasoning_config)
    schema_version = str(reasoning_config.get("master_output_schema_version") or "v3").lower()
    output_mode = str(reasoning_config.get("master_output_mode") or MASTER_OUTPUT_MODE_TAGGED_REPAIR).strip().lower()
    thresholds = _thresholds(reasoning_config)
    candidate_set_text = _candidate_set_text(reasoning_pack)
    allowed_labels = resolve_allowed_mechanism_labels(reasoning_pack, reasoning_config)

    output_line = (
        "Respond in natural language using tagged sections only.\n"
        if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
        else "Return strict JSON that matches the provided schema.\n"
    )
    system = (
        "You are the master reasoner for AIE mechanism discovery.\n"
        "Use ONLY the provided reasoning_pack JSON.\n"
        "Do not fabricate evidence or facts.\n"
        "Every evidence reference must use evidence_id from evidence_registry only.\n"
        f"{output_line}"
        "Neighbors are priors/context by default. Use aTB features as primary support evidence.\n"
        "DO NOT invent numeric thresholds or bands. If a threshold is needed, use only values from reasoning_config.thresholds or evidence_registry."
    )
    instructions = [
        "Template rubric:",
        f"- template_used should be {template}.",
        "- stable: pick one dominant mechanism with conservative uncertainty.",
        "- mixture: discuss multiple plausible mechanisms and tradeoffs.",
        "- novelty: emphasize uncertainty and verification path.",
        "Evidence Weighting Policy:",
        f"- Neighbors are context/prior unless top1_sim >= {policy['neighbor_support_min_sim']:.2f}.",
        "- aTB features are support evidence when available.",
        "- In R1+ profiles, use self-trend evidence IDs (E31..E34) as primary aTB support when present.",
        "- When available, also use E35..E39 to summarize CT proxy, structural relaxation, and shape-rigidity cues from target-only aTB.",
        "- Do not use raw absolute aTB values as standalone mechanism verdicts; cite self-trend buckets/directions first.",
        f"- {candidate_set_text}",
        "aTB discriminative rubric:",
        "- Assign atb_support_level by comparing abs(delta_dihedral) against reasoning_config.thresholds.atb_dihedral_thresh_none and reasoning_config.thresholds.atb_dihedral_thresh_strong.",
        "- If you mention threshold logic in text, you must cite exact key=value from reasoning_config.thresholds.",
        "- Otherwise use relative wording only (e.g., modest/large) and avoid threshold/range/band/cutoff terms.",
        "- delta_gap is weak CT-family context only (never strong evidence).",
        "- For aTB evidence, prefer direction/bucket wording from atb_trend_profile (or legacy atb_trends_self) over raw numeric thresholds.",
        "- Treat delta_dipole plus delta_gap as the CT proxy axis; do not use delta_gap alone as the only CT argument when E35/E36 are available.",
        "- Treat structural relaxation as a combined signal from torsion, bond, angle, and volume changes; do not rely on delta_dihedral alone when E37 is available.",
        "- Treat shape-rigidity (E39) as auxiliary context only; it can support neutral-aromatic-like stability but should not override stronger CT or relaxation evidence.",
        "supporting_chain must contain exactly 4 ordered steps A->B->C->D:",
        "- A excited-state structural access (aTB features)",
        "- B hypothesized nonradiative channel",
        "- C aggregation/rigidification suppressing nonradiative channel",
        "- D discriminative predictions to separate the top competing mechanisms listed above (or the top hypotheses you propose if none are provided).",
        "- step_name must be chosen from: ct_family, torsion_access, aIE_bridge, neighbor_priors, discriminators, limits",
        "If constraints cannot be satisfied, set status=insufficient_evidence and still return predictions.",
        "When citing evidence, use only evidence_id keys (E1, E2, ..., E31..E34, plus legacy E_ATB_TREND_1..4 if present).",
        "Additional cache-derived aTB evidence IDs may appear as E35..E39; prefer these summaries over raw field-by-field narration when they are present.",
        "Never output case_path anywhere in the JSON (including evidence_used, supporting_chain, competing_hypotheses, predictions).",
        f"PRIMARY_LABEL must be exactly one mechanism token from this set: {', '.join(allowed_labels)}. Do not add explanation text in PRIMARY_LABEL.",
        "Hard output budgets:",
        f"- supporting_chain max {MASTER_MAX_SUPPORTING_CHAIN_ITEMS} items (must still be A->B->C->D).",
        f"- predictions max {MASTER_MAX_PREDICTIONS_ITEMS} items.",
        f"- competing_hypotheses max {MASTER_MAX_COMPETING_ITEMS} items.",
        f"- evidence_used max {MASTER_MAX_EVIDENCE_USED_ITEMS} items.",
        f"- each evidence note max {MASTER_NOTE_MAX_CHARS} chars.",
        "Top-level evidence_used should stay compact and prioritize: uncertainty bounds (E2,E4,E6), aTB cues (E11,E12,[E14]), and missing discriminators (E19,E20,[E10]) when available.",
        "When profile is R2/R3 and E21/E22 exist, cite at least one of them in supporting_chain to ground comparative neighbor-vs-target interpretation.",
        "natural_language_mechanism should be a three-paragraph narrative in one string: best hypothesis, unresolved boundary among top competing mechanisms, and falsifiable next tests.",
        "Do not cite neighbors_topk fields directly.",
        "Hard rule: DO NOT invent numeric thresholds/bands. If threshold mention is necessary, reference reasoning_config.thresholds key/value exactly.",
        f"Configured thresholds (authoritative): {json.dumps(thresholds, ensure_ascii=False, sort_keys=True)}",
    ]
    if active_profile == "R0":
        instructions.extend(
            [
                "Round contract (R0 prior-only):",
                "- Treat this as a prior round from neighbor/similarity/novelty signals.",
                "- Do NOT output a high-confidence final verdict; keep status=insufficient_evidence if uncertainty remains.",
                "- Focus on candidate mechanism set and explicit discriminators needed for later rounds.",
            ]
        )
    elif active_profile == "R1":
        instructions.extend(
            [
                "Round contract (R1 target-constraint):",
                "- Use E31..E34 as primary aTB evidence when present.",
                "- Use E35/E36 to express CT-proxy gain or loss of support when available.",
                "- Use E37 to express structural-relaxation gain or loss of support when available.",
                "- Explain which candidate mechanisms gain/lose weight under target self-trend evidence.",
                "- Prefer bucket/direction/percentile_global wording; do not make absolute-value threshold verdicts.",
            ]
        )
    elif active_profile == "R2":
        instructions.extend(
            [
                "Round contract (R2 comparative-control):",
                "- Use comparative evidence (E21/E22/E23/E24) to assess neighbor transferability vs outlier behavior.",
                "- Keep target-only evidence (E35..E39) in view when comparative neighbor evidence is weak or mixed.",
                "- If comparative evidence is weak/unavailable, state limited information gain and avoid over-updating claims.",
            ]
        )
    elif active_profile == "R3":
        instructions.extend(
            [
                "Round contract (R3 external-evidence):",
                "- Incorporate literature/experiment readiness with explicit strictness limits.",
                "- Distinguish plausible narrative from externally verifiable support.",
            ]
        )
    if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA:
        instructions.extend(
            [
                "Response format for this turn: natural language only.",
                "Use EXACT tagged section prefixes in this order (one line each to start):",
                "TEMPLATE_USED:, STATUS:, PRIMARY_LABEL:, PRIMARY_CONFIDENCE:, PRIMARY:, COMPETING:, EVIDENCE:, PREDICTIONS:, LIMITS:, NEXT_ACTIONS:",
                "You may write multiple lines under each tagged section.",
                "In each section, cite evidence_id inline when relevant (for example: E11, E12).",
                f"Hard limits: COMPETING <= {MASTER_MAX_COMPETING_ITEMS}, EVIDENCE <= {MASTER_MAX_EVIDENCE_USED_ITEMS}, NEXT <= 5.",
                "Anything after NEXT section may be ignored by parser.",
                "Do NOT output raw JSON in this response.",
            ]
        )
    if gate_mode == "conservative":
        instructions.append(
            "- Conservative mode: keep confidence capped and explicitly list evidence limitations."
        )
    instructions.extend(
        [
            "Confidence policy:",
            "- Use continuous soft-penalty factors from reasoning_config thresholds/policy; avoid hard step caps.",
            "- Apply one final cap only at the end (global cap, and conservative cap when mode is conservative).",
            "- In R0, apply the configured r0_penalty_factor as a soft multiplier rather than hard clipping.",
        ]
    )

    if schema_version == "v1":
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V1
    elif schema_version == "v2":
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V2
    else:
        schema_name = MASTER_OUTPUT_SCHEMA_VERSION_V3
    schema = master_output_schema(schema_version=schema_version)
    if output_mode == MASTER_OUTPUT_MODE_STRICT_SCHEMA:
        contract = _json_only_contract_text(
            required_keys=list(schema.get("required") or []),
            array_caps={
                "supporting_chain": MASTER_MAX_SUPPORTING_CHAIN_ITEMS,
                "predictions": MASTER_MAX_PREDICTIONS_ITEMS,
                "competing_hypotheses": MASTER_MAX_COMPETING_ITEMS,
                "evidence_used": MASTER_MAX_EVIDENCE_USED_ITEMS,
            },
        )
        instructions.append(contract)
    return {
        "prompt_bundle_version": MASTER_PROMPT_BUNDLE_VERSION,
        "template_version": f"{template}_v1",
        "template_used": template,
        "output_mode": output_mode,
        "system": system,
        "instructions": "\n".join(instructions),
        "user_payload": _build_prompt_payload(reasoning_pack, reasoning_config),
        "reasoning_policy": policy,
        "output_schema_name": schema_name,
        "output_schema": schema,
    }


def _evidence_item_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidence_id": {"type": "string", "pattern": "^(?:E[0-9]+|E_ATB_TREND_[1-4])$"},
            "note": {"type": "string"},
            "role": {"type": "string", "enum": ["support", "counter", "context"]},
        },
        "required": ["evidence_id", "note", "role"],
    }


def _base_master_output_schema(*, schema_version: str) -> Dict[str, Any]:
    ver = str(schema_version).lower()
    is_v2_plus = ver in {"v2", "v3"}
    is_v3 = ver == "v3"
    evidence_item = _evidence_item_schema()
    primary_props: Dict[str, Any] = {
        "mechanism_label": {"type": "string"},
        "aie_rationale_type": {"type": "string", "enum": ["stable", "mixture", "novelty"]},
        "natural_language_mechanism": {"type": "string"},
    }
    primary_required = ["mechanism_label", "aie_rationale_type", "natural_language_mechanism"]
    if is_v2_plus:
        primary_props["atb_support_level"] = {"type": "string", "enum": ["none", "weak", "strong"]}
        primary_required.append("atb_support_level")

    competing_props: Dict[str, Any] = {
        "name": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_used": {"type": "array", "items": evidence_item},
    }
    competing_required = ["name", "confidence", "evidence_used"]
    if is_v2_plus:
        competing_props["atb_support_level"] = {"type": "string", "enum": ["none", "weak", "strong"]}
        competing_required.append("atb_support_level")

    chain_props: Dict[str, Any] = {
        "claim": {"type": "string"},
        "evidence_used": {"type": "array", "items": evidence_item},
    }
    chain_required = ["claim", "evidence_used"]
    if is_v2_plus:
        chain_props["step_id"] = {"type": "string", "enum": ["A", "B", "C", "D"]}
        if is_v3:
            chain_props["step_name"] = {
                "type": "string",
                "enum": [
                    "ct_family",
                    "torsion_access",
                    "aIE_bridge",
                    "neighbor_priors",
                    "discriminators",
                    "limits",
                ],
            }
        else:
            chain_props["step_name"] = {"type": "string"}
        chain_required = ["step_id", "step_name", "claim", "evidence_used"]

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
                        "properties": primary_props,
                        "required": primary_required,
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
                    "properties": chain_props,
                    "required": chain_required,
                },
            },
            "competing_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": competing_props,
                    "required": competing_required,
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
            "recommended_next_actions",
        ],
    }


def master_output_schema_v1() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v1")


def master_output_schema_v2() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v2")


def master_output_schema_v3() -> Dict[str, Any]:
    return _base_master_output_schema(schema_version="v3")


def master_output_schema(schema_version: str = "v3") -> Dict[str, Any]:
    ver = str(schema_version).lower()
    if ver == "v1":
        return master_output_schema_v1()
    if ver == "v2":
        return master_output_schema_v2()
    return master_output_schema_v3()


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


def _schema_validate_value(
    value: Any,
    schema: Dict[str, Any],
    *,
    path: str,
    errors: List[Dict[str, str]],
) -> None:
    if not isinstance(schema, dict):
        return
    typ = schema.get("type")
    if typ == "object":
        if not isinstance(value, dict):
            errors.append(_err("schema", "type_mismatch", path, "expected object"))
            return
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(_err("schema", "missing_required", f"{path}/{key}", f"missing required key: {key}"))
        # Keep local validation lightweight: allow extra keys to reduce brittleness.
        for key, subschema in props.items():
            if key in value:
                _schema_validate_value(value[key], subschema, path=f"{path}/{key}", errors=errors)
        return
    if typ == "array":
        if not isinstance(value, list):
            errors.append(_err("schema", "type_mismatch", path, "expected array"))
            return
        item_schema = schema.get("items")
        for idx, row in enumerate(value):
            _schema_validate_value(row, item_schema, path=f"{path}/{idx}", errors=errors)
        return
    if typ == "string":
        if not isinstance(value, str):
            errors.append(_err("schema", "type_mismatch", path, "expected string"))
            return
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(_err("schema", "enum_violation", path, f"value '{value}' not in enum"))
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            if re.match(pattern, value) is None:
                errors.append(_err("schema", "pattern_mismatch", path, f"value '{value}' does not match pattern {pattern}"))
        return
    if typ == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(_err("schema", "type_mismatch", path, "expected number"))
            return
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and float(value) < float(minimum):
            errors.append(_err("schema", "minimum_violation", path, f"{value} < {minimum}"))
        if isinstance(maximum, (int, float)) and float(value) > float(maximum):
            errors.append(_err("schema", "maximum_violation", path, f"{value} > {maximum}"))
        return
    # Unknown/no type in schema: skip strict local check, rely on provider-side strict schema.


def _validate_master_output_schema(
    master_output: Dict[str, Any],
    *,
    schema_version: str,
) -> List[Dict[str, str]]:
    if not isinstance(master_output, dict):
        return [_err("schema", "root_not_object", "$", "master_output must be a JSON object")]
    schema = master_output_schema(schema_version=schema_version)
    errors: List[Dict[str, str]] = []
    _schema_validate_value(master_output, schema, path="$", errors=errors)
    return errors


def _is_neighbor_path(case_path: str) -> bool:
    return case_path.startswith("/neighbors/") or case_path.startswith("/neighbors_topk/")


def resolve_evidence_id(
    evidence_id: str,
    reasoning_pack: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    registry = _registry_map(reasoning_pack.get("evidence_registry") or {})
    row = registry.get(str(evidence_id))
    if not isinstance(row, dict):
        return None, None
    source_type = str(row.get("source_type") or "case")
    if source_type == "derived_pack":
        case_path = row.get("pack_path")
    else:
        case_path = row.get("case_path")
    label = row.get("label")
    return (str(case_path) if isinstance(case_path, str) else None, str(label) if isinstance(label, str) else None)


def _resolve_evidence_entry(
    entry: Dict[str, Any],
    evidence_registry: Dict[str, Dict[str, Any]],
    case_json: Dict[str, Any],
    reasoning_pack: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    if "case_path" in entry:
        return False, None, "evidence_case_path_forbidden", None
    evidence_id = str(entry.get("evidence_id") or "").strip()
    if not evidence_id:
        return False, None, "evidence_used_missing_evidence_id", None
    if not EVIDENCE_ID_PATTERN.match(evidence_id):
        return False, None, f"evidence_id_format_invalid:{evidence_id}", None
    reg = evidence_registry.get(evidence_id)
    if not isinstance(reg, dict):
        return False, None, f"evidence_id_not_found:{evidence_id}", None
    source_type = str(reg.get("source_type") or "case").strip().lower()
    if source_type not in {"case", "derived_pack"}:
        return False, None, f"unsupported_source_type:{source_type}", None

    if source_type == "case":
        case_path = str(reg.get("case_path") or "").strip()
        if not case_path:
            return False, None, f"evidence_id_missing_case_path:{evidence_id}", None
        if case_path in FORBIDDEN_MASTER_RISK_PATHS:
            return False, None, f"forbidden_hint_reference:{case_path}", None
        found, value = _resolve_pointer(case_json, case_path)
        if not found:
            return False, None, f"evidence_path_not_found:{case_path}", None
        if _is_empty_value(value):
            return False, None, f"evidence_path_empty_value:{case_path}", None
        return True, case_path, None, {
            "value": value,
            "registry_entry": reg,
            "source_type": "case",
            "resolved_case_paths": [case_path],
        }

    pack_path = str(reg.get("pack_path") or "").strip()
    if not pack_path:
        return False, None, f"derived_pack_path_missing:{evidence_id}", None
    found, value = _resolve_pointer(reasoning_pack, pack_path)
    if not found:
        return False, None, f"derived_pack_path_not_found:{pack_path}", None
    if _is_empty_value(value):
        return False, None, f"derived_pack_value_empty:{pack_path}", None

    derived_paths: List[str] = []
    for p in reg.get("derived_from_case_paths") or []:
        if isinstance(p, str) and p.strip():
            derived_paths.append(p.strip())
    primary_path = derived_paths[0] if derived_paths else f"pack:{pack_path}"
    return True, primary_path, None, {
        "value": value,
        "registry_entry": reg,
        "source_type": "derived_pack",
        "pack_path": pack_path,
        "resolved_case_paths": derived_paths,
    }


def _validate_supporting_chain_structure(
    out: Dict[str, Any],
) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    chain = out.get("supporting_chain")
    if not isinstance(chain, list):
        return [_err("evidence", "supporting_chain_not_list", "/supporting_chain", "supporting_chain must be a list")]
    if len(chain) != 4:
        errors.append(
            _err("evidence", "supporting_chain_length_invalid", "/supporting_chain", f"expected 4 steps, got {len(chain)}")
        )
        return errors
    expected_steps = ["A", "B", "C", "D"]
    for idx, expected in enumerate(expected_steps):
        row = chain[idx]
        if not isinstance(row, dict):
            errors.append(_err("evidence", "supporting_chain_step_not_object", f"/supporting_chain/{idx}", "step must be object"))
            continue
        step_id = str(row.get("step_id") or "")
        if step_id != expected:
            errors.append(
                _err(
                    "evidence",
                    "supporting_chain_step_order_invalid",
                    f"/supporting_chain/{idx}/step_id",
                    f"expected {expected}, got {step_id}",
                )
            )
        ev = row.get("evidence_used")
        if not isinstance(ev, list) or len(ev) == 0:
            errors.append(
                _err(
                    "evidence",
                    "supporting_chain_step_missing_evidence",
                    f"/supporting_chain/{idx}/evidence_used",
                    f"missing evidence in step {expected}",
                )
            )
    # Step D discriminator requirement: keep minimal and deterministic.
    preds = out.get("predictions")
    if not isinstance(preds, list) or len(preds) < 3:
        errors.append(
            _err(
                "evidence",
                "supporting_chain_step_d_requires_predictions_gte3",
                "/predictions",
                "predictions must contain at least 3 items",
            )
        )
    return errors


def validate_master_output(
    master_output: Dict[str, Any],
    reasoning_pack: Dict[str, Any],
    case_json: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Tuple[bool, List[Dict[str, str]], Dict[str, Any], List[str], List[str], List[Dict[str, Any]]]:
    """
    Semantic validations after schema parse.
    Returns: (ok, errors, normalized_output, used_case_paths, used_evidence_ids, used_evidence_expanded)
    """
    if not isinstance(master_output, dict):
        return (
            False,
            [_err("schema", "root_not_object", "$", "master_output must be a JSON object")],
            {},
            [],
            [],
            [],
        )

    out = deepcopy(master_output)
    if isinstance(out.get("__meta"), dict):
        out.pop("__meta", None)
    structural_errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    used_paths: List[str] = []
    used_evidence_ids: List[str] = []
    used_evidence: List[Dict[str, Any]] = []
    evidence_registry = _registry_map(reasoning_pack.get("evidence_registry") or {})
    policy = _policy(reasoning_config)
    top1_sim = _to_float((reasoning_pack.get("risk_scores") or {}).get("top1_sim"))
    active_profile = str((((reasoning_pack.get("evidence_profile") or {}).get("active_profile")) or "R0")).upper()
    trend_ids_in_registry = {
        eid
        for eid in (*ATB_TREND_EVIDENCE_IDS, *ATB_TREND_PROFILE_EVIDENCE_IDS)
        if eid in evidence_registry
    }
    schema_version = str(reasoning_config.get("master_output_schema_version") or "v3").lower()

    # Phase A: structural validation (hard fail).
    structural_errors.extend(_validate_master_output_schema(out, schema_version=schema_version))
    if structural_errors:
        return False, structural_errors[:5], out, [], [], []

    threshold_values = _threshold_values(reasoning_config)
    threshold_keys = {str(k).lower() for k in _thresholds(reasoning_config).keys()}

    def _text_nodes(value: Any, path: str = "$") -> Iterable[Tuple[str, str]]:
        if isinstance(value, str):
            yield path, value
            return
        if isinstance(value, dict):
            for k, v in value.items():
                yield from _text_nodes(v, f"{path}/{k}")
            return
        if isinstance(value, list):
            for i, row in enumerate(value):
                yield from _text_nodes(row, f"{path}/{i}")

    def _contains_unapproved_threshold(text: str) -> bool:
        raw = str(text or "")
        lower = raw.lower()
        strong_trigger = bool(STRONG_THRESHOLD_TRIGGER_PATTERN.search(raw))
        weak_trigger_numeric = _weak_trigger_in_numeric_context(raw)
        threshold_like = bool(
            strong_trigger
            or weak_trigger_numeric
            or COMPARISON_PATTERN.search(raw)
            or INTERVAL_PATTERN.search(raw)
        )
        if not threshold_like:
            return False
        has_key = any(k in lower for k in threshold_keys)
        nums = [round(float(m), 6) for m in NUMBER_PATTERN.findall(raw)]
        has_allowed_num = any(
            any(abs(n - allow) <= 1e-6 for allow in threshold_values)
            for n in nums
        )
        return not (has_key and has_allowed_num)

    for text_path, text_value in _text_nodes(out):
        if _contains_unapproved_threshold(text_value):
            warnings.append(
                _warn(
                    "invented_threshold_not_allowed",
                    text_path,
                    "threshold/range text must use configured reasoning_config.thresholds",
                )
            )

    # Hard output budgets (prompt-constrained + validator-enforced).
    if isinstance(out.get("supporting_chain"), list) and len(out["supporting_chain"]) > MASTER_MAX_SUPPORTING_CHAIN_ITEMS:
        out["supporting_chain"] = out["supporting_chain"][:MASTER_MAX_SUPPORTING_CHAIN_ITEMS]
        warnings.append(_warn("supporting_chain_budget_trimmed", "/supporting_chain", "trimmed to budget"))
    if isinstance(out.get("predictions"), list) and len(out["predictions"]) > MASTER_MAX_PREDICTIONS_ITEMS:
        out["predictions"] = out["predictions"][:MASTER_MAX_PREDICTIONS_ITEMS]
        warnings.append(_warn("predictions_budget_trimmed", "/predictions", "trimmed to budget"))
    if isinstance(out.get("competing_hypotheses"), list) and len(out["competing_hypotheses"]) > MASTER_MAX_COMPETING_ITEMS:
        out["competing_hypotheses"] = out["competing_hypotheses"][:MASTER_MAX_COMPETING_ITEMS]
        warnings.append(_warn("competing_hypotheses_budget_trimmed", "/competing_hypotheses", "trimmed to budget"))
    if isinstance(out.get("evidence_used"), list) and len(out["evidence_used"]) > MASTER_MAX_EVIDENCE_USED_ITEMS:
        out["evidence_used"] = out["evidence_used"][:MASTER_MAX_EVIDENCE_USED_ITEMS]
        warnings.append(_warn("evidence_used_budget_trimmed", "/evidence_used", "trimmed to budget"))

    def _validate_and_collect(entry: Dict[str, Any], entry_path: str) -> Optional[str]:
        if "case_path" in entry:
            warnings.append(
                _warn(
                    "evidence_case_path_forbidden",
                    f"{entry_path}/case_path",
                    "case_path is forbidden in evidence_id mode; removed",
                )
            )
            entry.pop("case_path", None)
            return None
        ok, case_path, err, resolved = _resolve_evidence_entry(entry, evidence_registry, case_json, reasoning_pack)
        if not ok:
            code = str(err).split(":", 1)[0]
            warnings.append(_warn(code, entry_path, str(err)))
            entry["__drop__"] = True
            return None
        role = str(entry.get("role") or "").strip().lower()
        if (
            top1_sim is not None
            and top1_sim < float(policy["neighbor_support_min_sim"])
            and _is_neighbor_path(str(case_path))
            and role == "support"
        ):
            warnings.append(
                _warn(
                    "neighbor_support_disallowed_low_similarity",
                    str(case_path),
                    f"top1_sim={top1_sim} < neighbor_support_min_sim={policy['neighbor_support_min_sim']}; role downgraded to context",
                )
            )
            role = "context"
            entry["role"] = "context"
        evidence_id = str(entry.get("evidence_id") or "").strip()
        note = entry.get("note")
        if not isinstance(note, str):
            warnings.append(
                _warn(
                    "evidence_note_type_invalid",
                    f"{entry_path}/note",
                    "note coerced to string",
                )
            )
            note = str(note or "")
            entry["note"] = note
        if len(note) > MASTER_NOTE_MAX_CHARS:
            entry["note"] = note[:MASTER_NOTE_MAX_CHARS]
            warnings.append(_warn("evidence_note_trimmed", f"{entry_path}/note", "trimmed to note budget"))
        resolved_paths = []
        if isinstance(resolved, dict):
            for p in resolved.get("resolved_case_paths") or []:
                if isinstance(p, str) and p.strip():
                    resolved_paths.append(p.strip())
        if resolved_paths:
            used_paths.extend(resolved_paths)
        else:
            used_paths.append(str(case_path))
        used_evidence_ids.append(evidence_id)
        if isinstance(resolved, dict):
            reg = resolved.get("registry_entry") if isinstance(resolved.get("registry_entry"), dict) else {}
            used_evidence.append(
                {
                    "evidence_id": evidence_id,
                    "case_path": str(case_path),
                    "source_type": resolved.get("source_type"),
                    "pack_path": resolved.get("pack_path"),
                    "value_preview": resolved.get("value"),
                    "label": reg.get("label"),
                    "role": role,
                    "note": entry.get("note"),
                }
            )
        return str(case_path)

    # supporting_chain contract
    if schema_version != "v1":
        for row in _validate_supporting_chain_structure(out):
            if isinstance(row, dict):
                warnings.append(_warn(str(row.get("code") or "supporting_chain_warning"), str(row.get("path") or "/supporting_chain"), str(row.get("detail") or "")))
    chain = out.get("supporting_chain") if isinstance(out.get("supporting_chain"), list) else []
    step_a_atb = 0
    atb_citations = 0
    atb_support_citations = 0
    step_semantics = {
        "A": ("excited", "struct", "geometry", "dihedral", "atb"),
        "B": ("nonradiative", "channel", "torsion", "ict", "tict", "ct"),
        "C": ("aggregation", "rigid", "rim", "suppress", "packing"),
        "D": ("discrimin", "test", "measure", "compare", "separate", "prediction"),
    }
    if isinstance(chain, list):
        for idx, row in enumerate(chain):
            if not isinstance(row, dict):
                continue
            step_id = str(row.get("step_id") or "")
            evidence_used = row.get("evidence_used")
            if isinstance(evidence_used, list):
                for j, ev in enumerate(evidence_used):
                    if not isinstance(ev, dict):
                        continue
                    case_path = _validate_and_collect(ev, f"/supporting_chain/{idx}/evidence_used/{j}")
                    if not case_path:
                        continue
                    role = str(ev.get("role") or "").lower()
                    if case_path.startswith("/evidence_readiness/atb/features_summary/"):
                        atb_citations += 1
                        if role == "support":
                            atb_support_citations += 1
                        if idx == 0:
                            step_a_atb += 1
            claim_text = f"{row.get('step_name') or ''} {row.get('claim') or ''}".lower()
            if step_id in step_semantics and not _has_any_token([claim_text], step_semantics[step_id]):
                warnings.append(
                    _warn(
                        "supporting_chain_step_semantics_missing",
                        f"/supporting_chain/{idx}",
                        f"semantic tokens for step {step_id} are missing",
                    )
                )

    if schema_version != "v1":
        if atb_citations < 2:
            warnings.append(
                _warn(
                    "supporting_chain_atb_citations_insufficient",
                    "/supporting_chain",
                    f"found {atb_citations}, require >=2",
                )
            )
        if atb_support_citations < 1:
            warnings.append(
                _warn(
                    "supporting_chain_atb_support_citations_insufficient",
                    "/supporting_chain",
                    f"found {atb_support_citations}, require >=1 support citation",
                )
            )
        if step_a_atb < 1:
            warnings.append(
                _warn(
                    "supporting_chain_step_a_missing_atb_citation",
                    "/supporting_chain/0",
                    "step A requires at least one aTB citation",
                )
            )

    # Validate non-chain evidence lists.
    top_evidence = out.get("evidence_used")
    if isinstance(top_evidence, list):
        for i, ev in enumerate(top_evidence):
            if isinstance(ev, dict):
                _validate_and_collect(ev, f"/evidence_used/{i}")
    for i, row in enumerate(out.get("competing_hypotheses") or []):
        if not isinstance(row, dict):
            continue
        ev_list = row.get("evidence_used")
        if isinstance(ev_list, list):
            for j, ev in enumerate(ev_list):
                if isinstance(ev, dict):
                    _validate_and_collect(ev, f"/competing_hypotheses/{i}/evidence_used/{j}")
    for i, row in enumerate(out.get("predictions") or []):
        if not isinstance(row, dict):
            continue
        ev_list = row.get("evidence_used")
        if isinstance(ev_list, list):
            for j, ev in enumerate(ev_list):
                if isinstance(ev, dict):
                    _validate_and_collect(ev, f"/predictions/{i}/evidence_used/{j}")

    # Round-specific evidence discipline.
    used_ids_set = {str(x) for x in used_evidence_ids}
    if active_profile == "R1" and trend_ids_in_registry and used_ids_set.isdisjoint(trend_ids_in_registry):
        warnings.append(
            _warn(
                "r1_missing_atb_self_trend_citation",
                "/supporting_chain",
                "R1 requires at least one self-trend evidence citation (E31..E34 or legacy E_ATB_TREND_*) when available",
            )
        )
        out["status"] = "insufficient_evidence"
        limits = _normalize_limits(out.get("limits"))
        msg = "R1 output lacks self-trend citation; confidence kept conservative until trend evidence is used."
        if msg not in limits:
            limits.append(msg)
        out["limits"] = limits

    def _prune_evidence_list(rows: Any) -> List[Dict[str, Any]]:
        out_rows: List[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return out_rows
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.pop("__drop__", False):
                continue
            out_rows.append(row)
        return out_rows

    out["evidence_used"] = _prune_evidence_list(out.get("evidence_used"))
    for row in out.get("supporting_chain") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))
    for row in out.get("competing_hypotheses") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))
    for row in out.get("predictions") or []:
        if isinstance(row, dict):
            row["evidence_used"] = _prune_evidence_list(row.get("evidence_used"))

    # Confidence sanity (single cap is applied in _soft_confidence upstream).
    confidence = (
        (out.get("mechanism_claim") or {}).get("confidence")
        if isinstance(out.get("mechanism_claim"), dict)
        else None
    )
    try:
        conf_val = float(confidence)
        if conf_val < 0.05 or conf_val > 0.95:
            bounded = max(0.05, min(0.95, conf_val))
            warnings.append(
                _warn(
                    "confidence_out_of_bounds",
                    "/mechanism_claim/confidence",
                    f"{conf_val} outside [0.05,0.95]; normalized to {bounded}",
                )
            )
            if isinstance(out.get("mechanism_claim"), dict):
                out["mechanism_claim"]["confidence"] = float(bounded)
    except Exception:
        warnings.append(
            _warn(
                "mechanism_claim_confidence_invalid",
                "/mechanism_claim/confidence",
                "confidence coerced to 0.05",
            )
        )
        if isinstance(out.get("mechanism_claim"), dict):
            out["mechanism_claim"]["confidence"] = 0.05

    # R0 stays prior-only: status remains conservative, but no hard confidence cut.
    if active_profile == "R0" and isinstance(out.get("mechanism_claim"), dict):
        out["status"] = "insufficient_evidence"

    # Conservative constraints
    gate_mode = str((reasoning_pack.get("gate") or {}).get("reasoning_mode") or "").lower()
    if gate_mode == "conservative":
        tpl = str(out.get("template_used") or "").lower()
        if tpl == "novelty":
            warnings.append(
                _warn(
                    "conservative_mode_template_novelty_forbidden",
                    "/template_used",
                    "template changed from novelty to mixture in conservative mode",
                )
            )
            out["template_used"] = "mixture"

        limits = _normalize_limits(out.get("limits"))
        out["limits"] = limits
        limits_lower = [x.lower() for x in limits]
        conservative_tokens = [
            "conservative",
            "uncertain",
            "uncertainty",
            "tentative",
            "cautious",
            "not definitive",
            "ambig",
            "low similarity",
            "weak evidence",
            "insufficient",
        ]
        if not _has_any_token(limits_lower, conservative_tokens):
            out["limits"].append(STANDARD_LIMIT_CONSERVATIVE)
            limits_lower.append(STANDARD_LIMIT_CONSERVATIVE.lower())

        tf = reasoning_pack.get("target_fields") or {}
        no_emission = tf.get("emission_aggr_nm") is None and tf.get("emission_solid_or_film_nm") is None
        no_emission_tokens = [
            "no emission evidence",
            "without emission",
            "emission missing",
            "missing emission",
            "no direct emission",
            "emission fields missing",
            "emission not available",
            "no emission-field confirmation",
        ]
        if no_emission and not _has_any_token(limits_lower, no_emission_tokens):
            out["limits"].append(STANDARD_LIMIT_NO_EMISSION)

    runtime = reasoning_pack.get("runtime") or {}
    run_lane = str(runtime.get("run_lane") or "").lower()
    literature = (reasoning_pack.get("evidence_readiness") or {}).get("literature") or {}
    experiment = (reasoning_pack.get("evidence_readiness") or {}).get("experiment") or {}
    lit_disabled = "lane_disabled" in str(literature.get("notes") or "").lower() or run_lane == "atb_cache_only"
    exp_disabled = "lane_disabled" in str(experiment.get("notes") or "").lower() or run_lane == "atb_cache_only"
    if lit_disabled or exp_disabled:
        limits = _normalize_limits(out.get("limits"))
        limits_lower = [x.lower() for x in limits]
        lane_tokens = ["lane is disabled", "missing external verification", "literature", "experiment"]
        if not _has_any_token(limits_lower, lane_tokens):
            out["limits"] = limits + [STANDARD_LIMIT_LANE_DISABLED]

    # aTB support-level consistency
    if schema_version != "v1":
        expected_level = _atb_support_level_from_features(reasoning_pack, reasoning_config)
        mech = out.get("mechanism_claim") if isinstance(out.get("mechanism_claim"), dict) else {}
        primary_raw = mech.get("primary_hypothesis") if isinstance(mech, dict) else {}
        primary = primary_raw if isinstance(primary_raw, dict) else {}
        observed_level = str(primary.get("atb_support_level") or "none")
        if expected_level == "none" and observed_level in {"weak", "strong"}:
            warnings.append(
                _warn(
                    "atb_support_level_inconsistent",
                    "/mechanism_claim/primary_hypothesis/atb_support_level",
                    f"observed {observed_level}, expected <= none; corrected",
                )
            )
            primary["atb_support_level"] = "none"
        elif expected_level == "weak" and observed_level == "strong":
            warnings.append(
                _warn(
                    "atb_support_level_inconsistent",
                    "/mechanism_claim/primary_hypothesis/atb_support_level",
                    "observed strong, expected <= weak; corrected",
                )
            )
            primary["atb_support_level"] = "weak"
        elif expected_level == "strong" and observed_level == "none":
            limits = _normalize_limits(out.get("limits"))
            warn = "aTB delta_dihedral suggests strong torsional access; claim keeps conservative atb_support_level=none."
            if warn not in limits:
                limits.append(warn)
            out["limits"] = limits

    used_paths = sorted(set(used_paths))
    def _eid_sort_key(eid: str) -> Tuple[int, int, str]:
        token = str(eid or "")
        if token.startswith("E") and token[1:].isdigit():
            return (0, int(token[1:]), token)
        if token.startswith("E_ATB_TREND_") and token.split("_")[-1].isdigit():
            return (1, int(token.split("_")[-1]), token)
        return (2, 0, token)

    used_evidence_ids = sorted(set(used_evidence_ids), key=_eid_sort_key)
    dedup_used_evidence: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for row in used_evidence:
        key = (str(row.get("evidence_id") or ""), str(row.get("case_path") or ""))
        if key in seen:
            continue
        seen.add(key)
        dedup_used_evidence.append(row)
    issues = warnings
    return len(structural_errors) == 0, issues, out, used_paths, used_evidence_ids, dedup_used_evidence


def _set_or_replace_op(case_json: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    found, _ = _resolve_pointer(case_json, path)
    return {"op": "replace" if found else "add", "path": path, "value": value}


def build_master_patch(
    case_json: Dict[str, Any],
    normalized_output: Optional[Dict[str, Any]],
    *,
    status: str,
    used_paths: Sequence[str],
    used_evidence_ids: Sequence[str],
    used_evidence: Sequence[Dict[str, Any]],
    meta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    patch: List[Dict[str, Any]] = []
    if not isinstance(case_json.get("reasoning"), dict):
        patch.append({"op": "add", "path": "/reasoning", "value": {}})
    if normalized_output is not None:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", normalized_output))
        patch.append(_set_or_replace_op(case_json, "/reasoning/master_reasoning", normalized_output))
    else:
        patch.append(_set_or_replace_op(case_json, "/master_reasoning", None))
        patch.append(_set_or_replace_op(case_json, "/reasoning/master_reasoning", None))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_status", status))
    patch.append(_set_or_replace_op(case_json, "/reasoning/status", status))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_used_evidence_paths", list(used_paths)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence_paths", list(used_paths)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence_ids", list(used_evidence_ids)))
    patch.append(_set_or_replace_op(case_json, "/reasoning/used_evidence", list(used_evidence)))
    patch.append(_set_or_replace_op(case_json, "/master_reasoning_meta", meta))
    patch.append(_set_or_replace_op(case_json, "/reasoning/meta", meta))
    return patch


def _resolve_master_llm_params(
    reasoning_config: Dict[str, Any],
    llm_client: ResponsesLLMClient,
) -> Tuple[Optional[str], int, float, bool]:
    master_cfg = reasoning_config.get("master") if isinstance(reasoning_config.get("master"), dict) else {}
    effort: Optional[str]
    if "reasoning_effort" in master_cfg and master_cfg.get("reasoning_effort") is not None:
        effort = str(master_cfg.get("reasoning_effort"))
    elif reasoning_config.get("reasoning_effort") is not None:
        effort = str(reasoning_config.get("reasoning_effort"))
    elif llm_client.reasoning_effort is not None:
        effort = str(llm_client.reasoning_effort)
    else:
        effort = "medium"

    if effort in {"xhigh", "high"} and reasoning_config.get("reasoning_effort") is None and "reasoning_effort" not in master_cfg:
        effort = "medium"

    max_output_tokens = int(master_cfg.get("max_output_tokens") or llm_client.max_output_tokens)
    temp_raw = master_cfg.get("temperature", reasoning_config.get("temperature", llm_client.temperature))
    temperature = float(temp_raw) if isinstance(temp_raw, (int, float)) else float(MASTER_DEFAULT_TEMPERATURE)
    use_json_schema = bool(
        master_cfg.get("use_json_schema")
        if "use_json_schema" in master_cfg
        else reasoning_config.get("use_json_schema", False)
    )
    return effort, max_output_tokens, temperature, use_json_schema


def _clone_llm_client(
    llm_client: ResponsesLLMClient,
    *,
    reasoning_effort: Optional[str],
    max_output_tokens: int,
    temperature: Optional[float],
    model: Optional[str] = None,
) -> ResponsesLLMClient:
    return ResponsesLLMClient(
        base_url=llm_client.base_url,
        model=str(model or llm_client.model),
        api_key_env=llm_client.api_key_env,
        max_output_tokens=int(max_output_tokens),
        reasoning_effort=reasoning_effort,
        temperature=temperature,
    )


def _repair_json_only(
    *,
    llm_client: ResponsesLLMClient,
    raw_text: str,
    schema_name: str,
    schema: Dict[str, Any],
    reasoning_config: Dict[str, Any],
) -> Dict[str, Any]:
    _ = llm_client, schema_name, schema
    pack = reasoning_config.get("__repair_reasoning_pack") if isinstance(reasoning_config, dict) else None
    if not isinstance(pack, dict):
        pack = {}
    template_fallback = str(reasoning_config.get("__repair_template") or "mixture")
    out_obj = _tagged_text_to_master_output(
        raw_text=str(raw_text or ""),
        reasoning_pack=pack,
        reasoning_config=reasoning_config if isinstance(reasoning_config, dict) else {},
        template_fallback=template_fallback,
    )
    return {
        "parsed": out_obj,
        "request": {
            "mode": "local_tagged_repair",
            "schema_name": MASTER_OUTPUT_SCHEMA_VERSION_V3,
        },
        "response": {
            "mode": "local_tagged_repair",
            "raw_text": str(raw_text or "")[:2000],
        },
        "model": "local_tagged_repair",
        "reasoning_effort": "none",
    }


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
    output_mode = str(prompt_bundle.get("output_mode") or MASTER_OUTPUT_MODE_TAGGED_REPAIR).strip().lower()
    instructions = f"{prompt_bundle.get('system')}\n\n{prompt_bundle.get('instructions')}"
    schema_name = str(prompt_bundle.get("output_schema_name") or MASTER_OUTPUT_SCHEMA_VERSION)
    schema = prompt_bundle.get("output_schema") or master_output_schema()

    effort, max_tokens, temperature, use_json_schema = _resolve_master_llm_params(reasoning_config, llm_client)
    primary_client = _clone_llm_client(
        llm_client,
        reasoning_effort=effort,
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    llm_failure_reason: Optional[str] = None
    parsed: Dict[str, Any] = {}
    llm_request: Any = None
    llm_response_raw: Any = None

    def _failed_out(*, failure_reason: str, detail: str, request_obj: Any, response_obj: Any) -> Dict[str, Any]:
        return {
            "reasoning_pack": pack,
            "pack_hash": pack_hash,
            "prompt_bundle": prompt_bundle,
            "template_used": prompt_bundle.get("template_used"),
            "llm_request": request_obj,
            "llm_response_raw": response_obj,
            "master_output_raw": response_obj,
            "master_output_parsed": {},
            "normalized_output": None,
            "validation_errors": [
                _err("evidence", "llm_error", "$", detail),
            ],
            "used_case_paths": [],
            "used_evidence_ids": [],
            "used_evidence": [],
            "status": "failed_llm",
            "llm_failure_reason": failure_reason,
            "confidence_meta": {},
        }

    def _invoke_once(client: ResponsesLLMClient, *, call_instructions: str, out_tokens: int) -> Dict[str, Any]:
        if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA and hasattr(client, "responses_text"):
            try:
                out = client.responses_text(
                    instructions=call_instructions,
                    input_text=input_text,
                    max_output_tokens=out_tokens,
                    temperature=temperature,
                )
            except Exception as exc:
                # Backward-compatible test path: if text call cannot initialize client, allow json fallback.
                if "missing_api_key_env" in str(exc) and hasattr(client, "responses_json"):
                    return client.responses_json(
                        instructions=call_instructions,
                        input_text=input_text,
                        schema_name=schema_name,
                        schema=schema,
                        max_output_tokens=out_tokens,
                        temperature=temperature,
                        use_json_schema=use_json_schema,
                    )
                raise
            text = str(out.get("text") or "")
            if text.strip():
                parsed_text = _tagged_text_to_master_output(
                    raw_text=text,
                    reasoning_pack=pack,
                    reasoning_config=reasoning_config,
                    template_fallback=str(prompt_bundle.get("template_used") or "mixture"),
                )
                return {
                    "request": out.get("request"),
                    "response": out.get("response"),
                    "text": text,
                    "parsed": parsed_text,
                }
            raise RuntimeError(
                json.dumps(
                    {
                        "code": "no_message_output",
                        "details": {
                            "last_request": out.get("request"),
                            "last_response": out.get("response"),
                            "last_text": text,
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return client.responses_json(
            instructions=call_instructions,
            input_text=input_text,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=out_tokens,
            temperature=temperature,
            use_json_schema=use_json_schema,
        )

    def _parse_runtime_exc(exc: BaseException) -> Tuple[str, Dict[str, Any]]:
        if isinstance(exc, Exception):
            payload = _llm_error_payload(exc)
            if payload:
                return _llm_failure_reason_from_exc(exc), payload
        raw = str(exc or "")
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                code = str(obj.get("code") or "json_parse_error")
                details = obj.get("details") if isinstance(obj.get("details"), dict) else {}
                return code, details
        except Exception:
            pass
        return _llm_failure_reason_from_exc(exc), {}

    first_text = ""
    try:
        first_out = _invoke_once(
            primary_client,
            call_instructions=(
                instructions
                + (
                    "\n\nOutput mode reminder: respond in tagged natural language sections only."
                    if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
                    else "\n\nReturn JSON only."
                )
            ),
            out_tokens=max_tokens,
        )
        llm_request = first_out.get("request")
        llm_response_raw = first_out.get("response")
        first_text = str(first_out.get("text") or "")
        if first_text:
            llm_response_raw = {"primary_response": llm_response_raw, "primary_text": first_text}
        parsed = first_out.get("parsed") or {}
    except Exception as first_exc:
        llm_failure_reason, first_payload = _parse_runtime_exc(first_exc)
        first_request = first_payload.get("last_request")
        first_response = first_payload.get("last_response")
        first_text = str(first_payload.get("last_text") or "")
        if llm_failure_reason not in {"no_message_output", "json_parse_error"}:
            return _failed_out(
                failure_reason=llm_failure_reason,
                detail=str(first_exc),
                request_obj=first_request,
                response_obj=first_response,
            )

        retry_client = _clone_llm_client(
            llm_client,
            reasoning_effort=effort,
            max_output_tokens=max(max_tokens, MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS),
            temperature=temperature,
        )
        retry_instructions = (
            instructions
            + (
                "\n\nRetry instruction: respond with concise natural language only. Avoid markdown/fences."
                if output_mode != MASTER_OUTPUT_MODE_STRICT_SCHEMA
                else "\n\nRetry instruction: return JSON only; no trailing text."
            )
        )
        try:
            retry_out = _invoke_once(
                retry_client,
                call_instructions=retry_instructions,
                out_tokens=max(max_tokens, MASTER_DEFAULT_RETRY_MAX_OUTPUT_TOKENS),
            )
            llm_request = retry_out.get("request")
            llm_response_raw = retry_out.get("response")
            parsed = retry_out.get("parsed") or {}
            second_text = str(retry_out.get("text") or "")
            if second_text:
                llm_response_raw = {"retry_response": llm_response_raw, "retry_text": second_text}
        except Exception as second_exc:
            second_reason, second_payload = _parse_runtime_exc(second_exc)
            second_text = str(second_payload.get("last_text") or "")
            raw_repair_text = second_text or first_text
            if bool(reasoning_config.get("json_repair_enabled", True)) and raw_repair_text.strip():
                try:
                    repair_out = _repair_json_only(
                        llm_client=llm_client,
                        raw_text=raw_repair_text,
                        schema_name=schema_name,
                        schema=schema,
                        reasoning_config={
                            **(reasoning_config or {}),
                            "__repair_reasoning_pack": pack,
                            "__repair_template": str(prompt_bundle.get("template_used") or "mixture"),
                        },
                    )
                    parsed = repair_out.get("parsed") or {}
                    llm_request = {
                        "primary_request": first_request,
                        "retry_request": second_payload.get("last_request"),
                        "repair_request": repair_out.get("request"),
                    }
                    llm_response_raw = {
                        "primary_response": first_response,
                        "primary_text": first_text,
                        "retry_response": second_payload.get("last_response"),
                        "retry_text": second_text,
                        "repair_response": repair_out.get("response"),
                    }
                    llm_failure_reason = "json_repair_used"
                except Exception as repair_exc:
                    detail = (
                        f"retry_failed:{second_exc}; "
                        f"repair_failed:{repair_exc}"
                    )
                    return _failed_out(
                        failure_reason=second_reason,
                        detail=detail,
                        request_obj={
                            "primary_request": first_request,
                            "retry_request": second_payload.get("last_request"),
                        },
                        response_obj={
                            "primary_response": first_response,
                            "primary_text": first_text,
                            "retry_response": second_payload.get("last_response"),
                            "retry_text": second_text,
                        },
                    )
            else:
                return _failed_out(
                    failure_reason=second_reason,
                    detail=str(second_exc),
                    request_obj={
                        "primary_request": first_request,
                        "retry_request": second_payload.get("last_request"),
                    },
                    response_obj={
                        "primary_response": first_response,
                        "primary_text": first_text,
                        "retry_response": second_payload.get("last_response"),
                        "retry_text": second_text,
                    },
                )

    try:
        confidence_meta = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("__meta"), dict):
            confidence_meta = dict(parsed.get("__meta") or {})
        ok, errors, normalized_output, used_paths, used_evidence_ids, used_evidence = validate_master_output(
            parsed,
            pack,
            case_json,
            reasoning_config,
        )
    except Exception as validate_exc:  # pragma: no cover - defensive
        return {
            "reasoning_pack": pack,
            "pack_hash": pack_hash,
            "prompt_bundle": prompt_bundle,
            "template_used": prompt_bundle.get("template_used"),
            "llm_request": llm_request,
            "llm_response_raw": llm_response_raw,
            "master_output_raw": llm_response_raw,
            "master_output_parsed": parsed if isinstance(parsed, dict) else {},
            "normalized_output": None,
            "validation_errors": [
                _err("evidence", "internal_error", "$", str(validate_exc)),
            ],
            "used_case_paths": [],
            "used_evidence_ids": [],
            "used_evidence": [],
            "status": "failed_schema_validation",
            "llm_failure_reason": llm_failure_reason,
            "confidence_meta": {},
        }
    errors = errors[:5]
    return {
        "reasoning_pack": pack,
        "pack_hash": pack_hash,
        "prompt_bundle": prompt_bundle,
        "template_used": prompt_bundle.get("template_used"),
        "llm_request": llm_request,
        "llm_response_raw": llm_response_raw,
        "master_output_raw": llm_response_raw,
        "master_output_parsed": parsed,
        "normalized_output": normalized_output,
        "validation_errors": errors,
        "used_case_paths": used_paths,
        "used_evidence_ids": used_evidence_ids,
        "used_evidence": used_evidence,
        "status": "success" if ok else "failed_schema_validation",
        "llm_failure_reason": llm_failure_reason,
        "confidence_meta": confidence_meta,
    }

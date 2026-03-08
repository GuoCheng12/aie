"""
Optional LLM evaluator layer for iterative rounds.

This layer augments deterministic eval_report without owning stop policy.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from src.tools.llm_client import LLMClientError, ResponsesLLMClient


EVAL_LLM_SCHEMA_VERSION_V1 = "eval_llm_output_schema_v1"
EVAL_DEFAULT_RETRY_MAX_OUTPUT_TOKENS = 3200
EVAL_TAGGED_SECTION_ORDER = [
    "CRITIQUE_POINTS",
    "CONFLICTS",
    "VOI_RANKED_ACTIONS",
    "CONFIDENCE_DELTA_SUGGESTION",
    "NEXT_ROUND_PROFILE_SUGGESTION",
]
EVAL_TAGGED_SECTION_ALIASES = {
    "CRITIQUE": "CRITIQUE_POINTS",
    "CRITIQUE_POINT": "CRITIQUE_POINTS",
    "CONFLICT": "CONFLICTS",
    "VOI": "VOI_RANKED_ACTIONS",
    "ACTIONS": "VOI_RANKED_ACTIONS",
    "NEXT_ACTIONS": "VOI_RANKED_ACTIONS",
    "CONFIDENCE_DELTA": "CONFIDENCE_DELTA_SUGGESTION",
    "NEXT_ROUND_PROFILE": "NEXT_ROUND_PROFILE_SUGGESTION",
    "NEXT_PROFILE": "NEXT_ROUND_PROFILE_SUGGESTION",
}
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_json_candidate_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates: List[str] = [raw]
    for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE):
        b = str(block or "").strip()
        if b:
            candidates.append(b)
    l_brace = raw.find("{")
    r_brace = raw.rfind("}")
    if l_brace != -1 and r_brace > l_brace:
        candidates.append(raw[l_brace : r_brace + 1].strip())
    seen: set[str] = set()
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
    all_tags = list(EVAL_TAGGED_SECTION_ORDER) + list(EVAL_TAGGED_SECTION_ALIASES.keys())
    tag_alt = "|".join(sorted({re.escape(x) for x in all_tags}, key=len, reverse=True))
    patt = re.compile(rf"(?mi)^({tag_alt}):\s*(.*)$")
    matches = list(patt.finditer(raw))
    if not matches:
        return sections
    for i, m in enumerate(matches):
        raw_key = str(m.group(1) or "").upper()
        key = EVAL_TAGGED_SECTION_ALIASES.get(raw_key, raw_key)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        head = str(m.group(2) or "").strip()
        body = raw[start:end].strip()
        content = (head + ("\n" + body if body else "")).strip()
        sections[key] = content
    return sections


def _parse_lines(text: Any) -> List[str]:
    out: List[str] = []
    for row in str(text or "").splitlines():
        line = row.strip()
        if not line:
            continue
        line = re.sub(r"^[\-\*\d\.\)\(]+\s*", "", line).strip()
        if line:
            out.append(line)
    return out


def _extract_evidence_ids(text: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for m in re.findall(r"\bE[0-9]+\b", str(text or ""), flags=re.IGNORECASE):
        eid = str(m).upper()
        if eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    return out


def _parse_first_float(text: Any) -> Optional[float]:
    m = NUMBER_PATTERN.search(str(text or ""))
    if not m:
        return None
    return _to_float(m.group(0))


def _parse_severity(text: str) -> str:
    t = str(text or "").lower()
    if "high" in t:
        return "high"
    if "low" in t:
        return "low"
    return "medium"


def _parse_conflict_status(text: str) -> str:
    t = str(text or "").lower()
    if "resolved" in t:
        return "resolved"
    if "needs_more_data" in t or "need more" in t:
        return "needs_more_data"
    return "unresolved"


def _parse_action_name(text: str) -> str:
    line = str(text or "").strip()
    m = re.search(r"action\s*[:=]\s*([a-zA-Z0-9_/\-]+)", line, flags=re.IGNORECASE)
    if m:
        return str(m.group(1))
    m = re.match(r"([a-zA-Z0-9_/\-]+)", line)
    if m:
        return str(m.group(1))
    return "run_master_reasoner"


def _parse_next_profile(text: str, default_value: str = "R1") -> str:
    t = str(text or "").upper()
    m = re.search(r"\b(R[0-3]|NONE)\b", t)
    if m:
        return str(m.group(1))
    return default_value


def _tagged_text_to_eval_output(raw_text: str, *, default_next_profile: str = "R1") -> Dict[str, Any]:
    sections = _parse_tagged_sections(raw_text)
    critique_lines = _parse_lines(sections.get("CRITIQUE_POINTS"))
    conflict_lines = _parse_lines(sections.get("CONFLICTS"))
    voi_lines = _parse_lines(sections.get("VOI_RANKED_ACTIONS"))
    conf_delta_text = sections.get("CONFIDENCE_DELTA_SUGGESTION")
    next_profile_text = sections.get("NEXT_ROUND_PROFILE_SUGGESTION")

    if not critique_lines and not conflict_lines and not voi_lines:
        # fallback: keep one compact critique from free text to avoid parser hard-failure
        head = str(raw_text or "").strip().splitlines()
        if head:
            first = head[0].strip()
            if first:
                critique_lines = [first[:180]]

    critique_points: List[Dict[str, Any]] = []
    for i, line in enumerate(critique_lines[:6]):
        critique_points.append(
            {
                "title": f"critique_{i+1}",
                "severity": _parse_severity(line),
                "note": line[:180],
                "evidence_ids": _extract_evidence_ids(line),
            }
        )

    conflicts: List[Dict[str, Any]] = []
    for i, line in enumerate(conflict_lines[:6]):
        conflicts.append(
            {
                "conflict_id": f"C{i+1}",
                "status": _parse_conflict_status(line),
                "rationale": line[:180],
                "evidence_ids": _extract_evidence_ids(line),
            }
        )

    voi_ranked_actions: List[Dict[str, Any]] = []
    for line in voi_lines[:8]:
        voi_ranked_actions.append(
            {
                "action": _parse_action_name(line),
                "llm_priority_weight": _bounded_weight(_parse_first_float(line)),
                "rationale": line[:180],
            }
        )

    conf_delta = _to_float(_parse_first_float(conf_delta_text)) if conf_delta_text else None
    if conf_delta is None:
        conf_delta = 0.0

    next_profile = _parse_next_profile(next_profile_text or "", default_value=default_next_profile)
    return {
        "critique_points": critique_points,
        "conflicts": conflicts,
        "voi_ranked_actions": voi_ranked_actions,
        "confidence_delta_suggestion": float(conf_delta),
        "next_round_profile_suggestion": next_profile,
    }


def eval_llm_output_schema_v1() -> Dict[str, Any]:
    item_text = {"type": "string"}
    critique_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": item_text,
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "note": item_text,
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "severity", "note", "evidence_ids"],
    }
    conflict_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "conflict_id": item_text,
            "status": {"type": "string", "enum": ["resolved", "unresolved", "needs_more_data"]},
            "rationale": item_text,
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["conflict_id", "status", "rationale", "evidence_ids"],
    }
    voi_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": item_text,
            "llm_priority_weight": {"type": "number"},
            "rationale": item_text,
        },
        "required": ["action", "llm_priority_weight", "rationale"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "critique_points": {"type": "array", "items": critique_item},
            "conflicts": {"type": "array", "items": conflict_item},
            "voi_ranked_actions": {"type": "array", "items": voi_item},
            "confidence_delta_suggestion": {"type": "number"},
            "next_round_profile_suggestion": {"type": "string"},
        },
        "required": [
            "critique_points",
            "conflicts",
            "voi_ranked_actions",
            "confidence_delta_suggestion",
            "next_round_profile_suggestion",
        ],
    }


def _to_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _validate_eval_llm_output(parsed: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(parsed, dict):
        return False, ["not_object"]
    required = {
        "critique_points",
        "conflicts",
        "voi_ranked_actions",
        "confidence_delta_suggestion",
        "next_round_profile_suggestion",
    }
    missing = [k for k in required if k not in parsed]
    if missing:
        errors.extend([f"missing:{k}" for k in missing])
    if not isinstance(parsed.get("critique_points"), list):
        errors.append("invalid:critique_points")
    if not isinstance(parsed.get("conflicts"), list):
        errors.append("invalid:conflicts")
    if not isinstance(parsed.get("voi_ranked_actions"), list):
        errors.append("invalid:voi_ranked_actions")
    if _to_float(parsed.get("confidence_delta_suggestion")) is None:
        errors.append("invalid:confidence_delta_suggestion")
    if not isinstance(parsed.get("next_round_profile_suggestion"), str):
        errors.append("invalid:next_round_profile_suggestion")
    return len(errors) == 0, errors


def _llm_failure_reason(exc: BaseException) -> str:
    code = str(getattr(exc, "code", "") or "").strip().lower()
    if code in {"no_message_output", "json_parse_error", "json_repair_used"}:
        return code
    msg = str(exc).lower()
    if "responses_empty_output_text" in msg:
        return "no_message_output"
    if "responses_invalid_json" in msg or "unterminated string" in msg:
        return "json_parse_error"
    return "llm_error"


def _bounded_weight(value: Any) -> float:
    w = _to_float(value)
    if w is None:
        return 1.0
    if w < 0.1:
        return 0.1
    if w > 3.0:
        return 3.0
    return float(w)


def merge_eval_report_with_llm_layer(
    *,
    eval_report: Dict[str, Any],
    llm_output: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out = deepcopy(eval_report if isinstance(eval_report, dict) else {})
    if not isinstance(llm_output, dict):
        out["llm_layer"] = {"enabled": False, "status": "not_available"}
        return out

    out["llm_layer"] = {
        "enabled": True,
        "status": "ok",
        "schema_version": EVAL_LLM_SCHEMA_VERSION_V1,
        "output": llm_output,
    }

    weight_map: Dict[str, float] = {}
    rationale_map: Dict[str, str] = {}
    for row in llm_output.get("voi_ranked_actions") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").strip()
        if not action:
            continue
        weight_map[action] = _bounded_weight(row.get("llm_priority_weight"))
        rationale_map[action] = str(row.get("rationale") or "")

    rows: List[Dict[str, Any]] = []
    for row in out.get("voi_ranked_actions") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").strip()
        eig = _to_float(row.get("expected_information_gain")) or 0.0
        feasibility = _to_float(row.get("feasibility_score")) or 0.0
        llm_weight = weight_map.get(action, 1.0)
        row2 = deepcopy(row)
        row2["llm_priority_weight"] = round(llm_weight, 3)
        row2["priority_score"] = round(float(eig) * float(feasibility) * float(llm_weight), 3)
        if action in rationale_map and rationale_map[action]:
            row2["llm_rationale"] = rationale_map[action]
        rows.append(row2)

    rows = sorted(rows, key=lambda x: (-float(x.get("priority_score") or 0.0), str(x.get("action") or "")))
    if rows and not bool(rows[0].get("feasible")):
        for i, row in enumerate(rows):
            if bool(row.get("feasible")):
                rows.insert(0, rows.pop(i))
                break
    out["voi_ranked_actions"] = rows
    return out


class LLMEvaluator:
    def __init__(
        self,
        *,
        llm_client: Optional[ResponsesLLMClient] = None,
        base_url: Optional[str] = None,
        api_key_env: str = "OPENAI_API_KEY",
        max_output_tokens: int = 1500,
        default_model: Optional[str] = None,
        default_reasoning_effort: Optional[str] = None,
    ):
        self._injected_llm = llm_client
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.max_output_tokens = int(max_output_tokens)
        self.default_model = default_model
        self.default_reasoning_effort = default_reasoning_effort

    def _resolve_client(
        self,
        *,
        model: Optional[str],
        reasoning_effort: Optional[str],
    ) -> ResponsesLLMClient:
        # Keep testability/backward compatibility: use injected client only when no runtime overrides.
        if self._injected_llm is not None and model is None and reasoning_effort is None:
            return self._injected_llm
        resolved_model = model if model is not None else self.default_model
        resolved_effort = reasoning_effort if reasoning_effort is not None else self.default_reasoning_effort
        if not self.base_url or not resolved_model:
            raise ValueError("llm_evaluator_missing_client_settings")
        return ResponsesLLMClient(
            base_url=self.base_url,
            model=str(resolved_model),
            api_key_env=self.api_key_env,
            max_output_tokens=self.max_output_tokens,
            reasoning_effort=resolved_effort,
        )

    def _repair_json_only(
        self,
        *,
        raw_text: str,
        schema_name: str,
        schema: Dict[str, Any],
        default_next_profile: str,
    ) -> Dict[str, Any]:
        local = _tagged_text_to_eval_output(raw_text, default_next_profile=default_next_profile)
        ok, _ = _validate_eval_llm_output(local)
        if ok:
            return {
                "parsed": local,
                "request": {"mode": "local_tagged_repair", "schema_name": schema_name},
                "response": {"mode": "local_tagged_repair", "raw_text_excerpt": str(raw_text or "")[:2000]},
            }
        if not self.base_url:
            raise ValueError("llm_evaluator_missing_base_url_for_repair")
        repair_client = ResponsesLLMClient(
            base_url=self.base_url,
            model=str(self.default_model or "gpt-4o-mini"),
            api_key_env=self.api_key_env,
            max_output_tokens=max(1200, min(EVAL_DEFAULT_RETRY_MAX_OUTPUT_TOKENS, self.max_output_tokens)),
            reasoning_effort="low",
            temperature=0.0,
        )
        out = repair_client.responses_json(
            instructions=(
                "Repair RAW_TEXT into valid JSON for evaluator output keys.\n"
                "Do not add new facts/actions/conflicts.\n"
                "Only reformat existing content into valid JSON.\n"
                "Return JSON only."
            ),
            input_text=json.dumps({"raw_text": raw_text}, ensure_ascii=False),
            schema_name=schema_name,
            schema=schema,
            use_json_schema=False,
        )
        return {
            "parsed": out.get("parsed"),
            "request": out.get("request"),
            "response": out.get("response"),
        }

    def run(
        self,
        *,
        reasoning_pack: Dict[str, Any],
        master_output_parsed: Dict[str, Any],
        policy: Dict[str, Any],
        thresholds: Dict[str, Any],
        run_lane_capabilities: Dict[str, Any],
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_json_schema: bool = False,
    ) -> Dict[str, Any]:
        client = self._resolve_client(model=model, reasoning_effort=reasoning_effort)
        payload = {
            "reasoning_pack": reasoning_pack,
            "master_output_parsed": master_output_parsed,
            "policy": policy,
            "thresholds": thresholds,
            "run_lane_capabilities": run_lane_capabilities,
        }
        schema = eval_llm_output_schema_v1()
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        llm_failure_reason: Optional[str] = None
        default_next_profile = "R1"
        instructions = (
            "You are an evaluator critique layer for mechanism reasoning.\n"
            "Respond in concise natural language using tagged sections.\n"
            "Do not call tools.\n"
            "Focus on critique quality, conflict adjudication, and VoI ranking weights.\n"
            "Include explicit references to evidence_id where relevant.\n"
            "Avoid markdown tables and avoid code fences.\n"
            "Use EXACT section prefixes in this order:\n"
            "CRITIQUE_POINTS:\n"
            "CONFLICTS:\n"
            "VOI_RANKED_ACTIONS:\n"
            "CONFIDENCE_DELTA_SUGGESTION:\n"
            "NEXT_ROUND_PROFILE_SUGGESTION:\n"
            "VOI item format suggestion: action=<name>; weight=<0.1..3.0>; rationale=<text>."
        )

        def _invoke_once(*, call_instructions: str, out_tokens: int) -> Dict[str, Any]:
            if hasattr(client, "responses_text"):
                try:
                    out_text = client.responses_text(
                        instructions=call_instructions,
                        input_text=input_text,
                        max_output_tokens=out_tokens,
                    )
                except Exception as exc:
                    if "missing_api_key_env" in str(exc):
                        return client.responses_json(
                            instructions=call_instructions,
                            input_text=input_text,
                            schema_name=EVAL_LLM_SCHEMA_VERSION_V1,
                            schema=schema,
                            use_json_schema=bool(use_json_schema),
                            max_output_tokens=out_tokens,
                        )
                    raise
                text = str(out_text.get("text") or "")
                parsed_tagged = _tagged_text_to_eval_output(text, default_next_profile=default_next_profile)
                ok_tagged, _ = _validate_eval_llm_output(parsed_tagged)
                if ok_tagged:
                    return {
                        "parsed": parsed_tagged,
                        "request": out_text.get("request"),
                        "response": out_text.get("response"),
                        "text": text,
                    }
                parsed_text = _parse_json_candidate_text(text)
                if isinstance(parsed_text, dict):
                    return {
                        "parsed": parsed_text,
                        "request": out_text.get("request"),
                        "response": out_text.get("response"),
                        "text": text,
                    }
                raise LLMClientError(
                    "responses_invalid_json_from_text",
                    code="json_parse_error",
                    details={
                        "last_request": out_text.get("request"),
                        "last_response": out_text.get("response"),
                        "last_text": text,
                    },
                )
            return client.responses_json(
                instructions=call_instructions,
                input_text=input_text,
                schema_name=EVAL_LLM_SCHEMA_VERSION_V1,
                schema=schema,
                use_json_schema=bool(use_json_schema),
                max_output_tokens=out_tokens,
            )

        try:
            out = _invoke_once(
                call_instructions=instructions + "\n\nOutput mode reminder: tagged natural language sections only.",
                out_tokens=int(self.max_output_tokens),
            )
        except LLMClientError as first_exc:
            llm_failure_reason = _llm_failure_reason(first_exc)
            details = first_exc.details if isinstance(first_exc.details, dict) else {}
            retry_instructions = instructions + "\n\nRetry instruction: tagged sections only, no markdown."
            try:
                out = _invoke_once(
                    call_instructions=retry_instructions,
                    out_tokens=max(int(self.max_output_tokens), EVAL_DEFAULT_RETRY_MAX_OUTPUT_TOKENS),
                )
            except LLMClientError as second_exc:
                second_reason = _llm_failure_reason(second_exc)
                second_details = second_exc.details if isinstance(second_exc.details, dict) else {}
                raw_text = str(second_details.get("last_text") or details.get("last_text") or "")
                if not raw_text.strip():
                    raise
                repair = self._repair_json_only(
                    raw_text=raw_text,
                    schema_name=EVAL_LLM_SCHEMA_VERSION_V1,
                    schema=schema,
                    default_next_profile=default_next_profile,
                )
                out = {
                    "parsed": repair.get("parsed"),
                    "request": {
                        "primary_request": details.get("last_request"),
                        "retry_request": second_details.get("last_request"),
                        "repair_request": repair.get("request"),
                    },
                    "response": {
                        "primary_response": details.get("last_response"),
                        "primary_text": details.get("last_text"),
                        "retry_response": second_details.get("last_response"),
                        "retry_text": second_details.get("last_text"),
                        "repair_response": repair.get("response"),
                    },
                }
                llm_failure_reason = "json_repair_used" if second_reason in {"json_parse_error", "no_message_output"} else second_reason

        parsed = out.get("parsed")
        ok, errors = _validate_eval_llm_output(parsed if isinstance(parsed, dict) else {})
        if not ok:
            raise ValueError(f"invalid_eval_llm_output:{','.join(errors)}")
        return {
            "parsed": parsed,
            "request": out.get("request"),
            "response": out.get("response"),
            "llm_failure_reason": llm_failure_reason,
        }

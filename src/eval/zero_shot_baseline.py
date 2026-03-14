"""Zero-shot baseline for mechanism-label evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src.eval.label_normalizer import CANONICAL_LABELS, normalize_prediction_label
from src.tools.llm_client import LLMClientError, ResponsesLLMClient

LABEL_LINE_RE = re.compile(r"^\s*LABEL\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


ZERO_SHOT_SYSTEM_PROMPT = """You are evaluating a single molecule for AIE mechanism-label classification.

You will receive only a SMILES string and the allowed label set.
Do not assume access to any external evidence such as aTB, neighbors, literature, PDFs, experiments, or retrieved priors.
Choose exactly one label from the allowed label set.
If the SMILES alone is insufficient to support any label, output unknown.

Output contract:
- Return exactly one line.
- Format must be: LABEL: <label>
- <label> must be one of: {labels}
- Do not output any explanation or extra text.
"""


def canonical_zero_shot_labels(labels: Optional[Iterable[str]] = None) -> list[str]:
    rows = list(labels) if labels is not None else list(CANONICAL_LABELS)
    out: list[str] = []
    for row in rows:
        txt = str(row or "").strip()
        if txt and txt not in out:
            out.append(txt)
    for required in CANONICAL_LABELS:
        if required not in out:
            out.append(required)
    return out


def build_zero_shot_prompt(smiles: str, labels: Optional[Iterable[str]] = None) -> Dict[str, str]:
    allowed = canonical_zero_shot_labels(labels)
    label_text = ", ".join(allowed)
    return {
        "system": ZERO_SHOT_SYSTEM_PROMPT.format(labels=label_text),
        "user": f"SMILES: {str(smiles or '').strip()}\nAllowed labels: {label_text}",
    }


def parse_zero_shot_label(text: str | None, allowed_labels: Optional[Iterable[str]] = None) -> Optional[str]:
    raw = str(text or "").strip()
    if not raw:
        return None
    allowed = canonical_zero_shot_labels(allowed_labels)
    allowed_set = set(allowed)

    match = LABEL_LINE_RE.search(raw)
    if match:
        candidate = normalize_prediction_label(match.group(1))
        return candidate if candidate in allowed_set else None

    normalized_hits: list[str] = []
    lowered = raw.lower()
    for label in allowed:
        if re.search(rf"\b{re.escape(label.lower())}\b", lowered):
            normalized = normalize_prediction_label(label)
            if normalized in allowed_set and normalized not in normalized_hits:
                normalized_hits.append(normalized)
    if len(normalized_hits) == 1:
        return normalized_hits[0]
    return None


def write_zero_shot_trace(
    trace_path: Path,
    *,
    smiles: str,
    prompt: Dict[str, str],
    llm_result: Optional[Dict[str, Any]],
    parsed_label: Optional[str],
    error: Optional[str],
) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "smiles": str(smiles or "").strip(),
        "prompt": prompt,
        "llm_result": llm_result,
        "parsed_label": parsed_label,
        "error": error,
    }
    trace_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_zero_shot_label(
    *,
    smiles: str,
    model: str,
    base_url: str,
    reasoning_effort: str,
    temperature: float,
    api_key_env: str = "OPENAI_API_KEY",
    max_output_tokens: int = 128,
    labels: Optional[Iterable[str]] = None,
    trace_path: Optional[Path] = None,
    client_cls=ResponsesLLMClient,
) -> Dict[str, Any]:
    prompt = build_zero_shot_prompt(smiles, labels)
    client = client_cls(
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
    )
    llm_result: Optional[Dict[str, Any]] = None
    parsed_label: Optional[str] = None
    error: Optional[str] = None
    try:
        llm_result = client.responses_text(
            instructions=prompt["system"],
            input_text=prompt["user"],
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        parsed_label = parse_zero_shot_label(llm_result.get("text"), labels)
        if parsed_label is None:
            error = "missing_pred:unparseable_zero_shot_label"
    except LLMClientError as exc:
        error = str(exc)
        llm_result = {
            "request": exc.details.get("last_request") if isinstance(exc.details, dict) else None,
            "response": exc.details.get("last_response") if isinstance(exc.details, dict) else None,
            "text": exc.details.get("last_text") if isinstance(exc.details, dict) else None,
            "error_code": exc.code,
        }
    except Exception as exc:  # pragma: no cover - safety fallback
        error = str(exc)

    if trace_path is not None:
        write_zero_shot_trace(
            trace_path,
            smiles=smiles,
            prompt=prompt,
            llm_result=llm_result,
            parsed_label=parsed_label,
            error=error,
        )

    return {
        "prompt": prompt,
        "llm_result": llm_result,
        "label": parsed_label,
        "error": error,
    }

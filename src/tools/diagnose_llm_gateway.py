from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.llm_client import ResponsesLLMClient


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


@dataclass
class ProbeResult:
    name: str
    ok: bool
    latency_ms: int
    response_excerpt: Optional[str] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "ok": bool(self.ok),
            "latency_ms": int(self.latency_ms),
        }
        if self.response_excerpt is not None:
            out["response_excerpt"] = self.response_excerpt
        if self.error is not None:
            out["error"] = self.error
        return out


def _classify_error(message: str) -> str:
    text = (message or "").lower()
    if "missing_api_key_env" in text or "api key" in text and "missing" in text:
        return "missing_api_key"
    if "invalid api key" in text or "incorrect api key" in text or "authentication" in text:
        return "auth_error"
    if "insufficient_quota" in text or "billing" in text or "quota" in text:
        return "quota_or_billing"
    if "unsupported model" in text or "model_not_found" in text:
        return "model_unavailable"
    if "unsupported parameter" in text and "temperature" in text:
        return "unsupported_temperature"
    if "unable to complete inference due to an internal error" in text:
        return "model_internal_error"
    if "invalid_request_error" in text or "there was an issue with your request" in text:
        return "invalid_request"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "dns" in text or "name resolution" in text:
        return "network_error"
    return "unknown_error"


def _error_payload(exc: Exception) -> Dict[str, Any]:
    msg = str(exc)
    out: Dict[str, Any] = {
        "class": exc.__class__.__name__,
        "message": msg,
        "classification": _classify_error(msg),
    }
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        out["status_code"] = status_code
    body = getattr(exc, "body", None)
    if body is not None:
        out["body"] = _safe_json(body)
    response = getattr(exc, "response", None)
    if response is not None:
        out["response"] = _safe_json(response)
    return out


def _probe_chat(client: Any, model: str, temperature: Optional[float]) -> ProbeResult:
    start = time.perf_counter()
    try:
        req: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Reply with exactly OK."},
                {"role": "user", "content": "Ping"},
            ],
            "max_tokens": 16,
        }
        if isinstance(temperature, (int, float)):
            req["temperature"] = float(temperature)
        resp = client.chat.completions.create(**req)
        payload = resp.model_dump()
        choices = payload.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    text = content.strip()
        return ProbeResult(
            name="chat.completions",
            ok=True,
            latency_ms=int((time.perf_counter() - start) * 1000),
            response_excerpt=text[:160] if text else None,
        )
    except Exception as exc:
        return ProbeResult(
            name="chat.completions",
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=_error_payload(exc),
        )


def _probe_responses(client: Any, model: str, temperature: Optional[float], probe_name: str) -> ProbeResult:
    start = time.perf_counter()
    try:
        req: Dict[str, Any] = {
            "model": model,
            "instructions": "Reply with exactly OK.",
            "input": "Ping",
            "max_output_tokens": 32,
            "tools": [],
        }
        if isinstance(temperature, (int, float)):
            req["temperature"] = float(temperature)
        resp = client.responses.create(**req)
        payload = resp.model_dump()
        out_text = payload.get("output_text")
        excerpt = out_text.strip()[:160] if isinstance(out_text, str) else None
        return ProbeResult(
            name=probe_name,
            ok=True,
            latency_ms=int((time.perf_counter() - start) * 1000),
            response_excerpt=excerpt,
        )
    except Exception as exc:
        return ProbeResult(
            name=probe_name,
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=_error_payload(exc),
        )


def _probe_runtime_wrapper(
    *,
    base_url: str,
    model: str,
    api_key_env: str,
    max_output_tokens: int,
    reasoning_effort: Optional[str],
    temperature: Optional[float],
) -> ProbeResult:
    start = time.perf_counter()
    try:
        llm = ResponsesLLMClient(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
        )
        out = llm.responses_text(
            instructions="Reply with exactly OK.",
            input_text="Ping",
            max_output_tokens=32,
            temperature=temperature,
        )
        text = str(out.get("text") or "").strip()
        request = out.get("request")
        api = request.get("api") if isinstance(request, dict) else None
        return ProbeResult(
            name="runtime_wrapper_responses_text",
            ok=True,
            latency_ms=int((time.perf_counter() - start) * 1000),
            response_excerpt=f"api={api or 'responses'} text={text[:120]}",
        )
    except Exception as exc:
        return ProbeResult(
            name="runtime_wrapper_responses_text",
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=_error_payload(exc),
        )


def _summarize_root_cause(probes: List[ProbeResult]) -> str:
    failed = [p for p in probes if not p.ok]
    if not failed:
        return "all_probes_ok"
    classes = [((p.error or {}).get("classification") or "unknown_error") for p in failed]
    if "quota_or_billing" in classes:
        return "quota_or_billing"
    if "auth_error" in classes or "missing_api_key" in classes:
        return "auth_or_api_key"
    if "model_unavailable" in classes:
        return "model_unavailable"
    if "model_internal_error" in classes:
        return "model_internal_error"
    if "unsupported_temperature" in classes and any(p.ok for p in probes):
        return "temperature_not_supported_but_service_reachable"
    if "invalid_request" in classes and any(p.ok for p in probes):
        return "responses_path_or_request_shape_issue"
    if "network_error" in classes or "timeout" in classes:
        return "network_or_gateway_unreachable"
    return "mixed_or_unknown_gateway_issue"


def run_diagnostics(args: argparse.Namespace) -> Dict[str, Any]:
    api_key = os.getenv(args.api_key_env, "")
    report: Dict[str, Any] = {
        "generated_at": _utc_now(),
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
        "api_key_present": bool(api_key),
        "models": [],
    }
    if not api_key:
        report["fatal"] = {
            "classification": "missing_api_key",
            "message": f"Environment variable {args.api_key_env} is empty.",
        }
        return report

    try:
        from openai import OpenAI
    except Exception as exc:
        report["fatal"] = {
            "classification": "openai_import_failed",
            "message": str(exc),
        }
        return report

    base = (args.base_url or "").rstrip("/")
    if base and not base.endswith("/v1"):
        base = f"{base}/v1"
    client = OpenAI(base_url=base or None, api_key=api_key)

    for model in args.models:
        probes: List[ProbeResult] = []
        probes.append(_probe_chat(client=client, model=model, temperature=args.temperature))
        probes.append(_probe_responses(client=client, model=model, temperature=None, probe_name="responses_no_temperature"))
        probes.append(
            _probe_responses(
                client=client,
                model=model,
                temperature=args.temperature,
                probe_name="responses_with_temperature",
            )
        )
        probes.append(
            _probe_runtime_wrapper(
                base_url=args.base_url,
                model=model,
                api_key_env=args.api_key_env,
                max_output_tokens=args.max_output_tokens,
                reasoning_effort=args.reasoning_effort,
                temperature=args.temperature,
            )
        )
        report["models"].append(
            {
                "model": model,
                "root_cause_hint": _summarize_root_cause(probes),
                "probes": [p.to_dict() for p in probes],
            }
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Diagnose LLM gateway/model failures (auth, quota, model route, temperature compatibility).")
    p.add_argument("--base-url", type=str, default="http://35.220.164.252:3888/v1")
    p.add_argument("--models", type=str, nargs="+", default=["gpt-5.2", "deepseek-v3.2"])
    p.add_argument("--api-key-env", type=str, default="OPENAI_API_KEY")
    p.add_argument("--reasoning-effort", type=str, default="high")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-output-tokens", type=int, default=256)
    p.add_argument("--out", type=str, default=None, help="Optional JSON output path.")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    report = run_diagnostics(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            f.write(payload + "\n")


if __name__ == "__main__":
    main()

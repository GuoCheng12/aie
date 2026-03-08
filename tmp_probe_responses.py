#!/usr/bin/env python3
"""
Temporary probe script for gateway model capability checks:
1) Responses API without tools
2) Responses API with web_search tool
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, Optional

from openai import APIConnectionError, APITimeoutError, OpenAI

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None


def _extract_error_info(exc: Exception) -> Dict[str, Any]:
    status_code: Optional[int] = None
    err_code: Optional[str] = None
    err_type: Optional[str] = None
    err_message = str(exc)
    body: Any = None

    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        try:
            body = response.json()
        except Exception:
            body = getattr(response, "text", None)

    if isinstance(body, dict):
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            err_code = error_obj.get("code")
            err_type = error_obj.get("type")
            err_message = error_obj.get("message", err_message)

    return {
        "status_code": status_code,
        "error_code": err_code,
        "error_type": err_type,
        "message": err_message,
        "raw": body,
    }


def _safe_output_preview(resp: Any, max_chars: int = 200) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text[:max_chars]
    try:
        dumped = resp.model_dump()
    except Exception:
        try:
            dumped = resp.dict()
        except Exception:
            dumped = {"repr": repr(resp)}
    return json.dumps(dumped, ensure_ascii=False)[:max_chars]


def _run_probe(
    client: OpenAI,
    *,
    model: str,
    query: str,
    timeout_sec: int,
    tools: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {"model": model, "input": query}
    if tools is not None:
        req["tools"] = tools

    start = time.monotonic()
    try:
        resp = client.responses.create(**req)
        elapsed = round(time.monotonic() - start, 3)
        return {
            "ok": True,
            "elapsed_sec": elapsed,
            "request": req,
            "output_preview": _safe_output_preview(resp),
        }
    except (APITimeoutError, TimeoutError):
        elapsed = round(time.monotonic() - start, 3)
        return {
            "ok": False,
            "elapsed_sec": elapsed,
            "request": req,
            "error": {
                "status_code": None,
                "error_code": "timeout",
                "error_type": "timeout",
                "message": f"request timed out (>{timeout_sec}s)",
                "raw": None,
            },
        }
    except APIConnectionError as exc:
        elapsed = round(time.monotonic() - start, 3)
        return {
            "ok": False,
            "elapsed_sec": elapsed,
            "request": req,
            "error": _extract_error_info(exc),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.monotonic() - start, 3)
        return {
            "ok": False,
            "elapsed_sec": elapsed,
            "request": req,
            "error": _extract_error_info(exc),
        }


def _diagnose(p1: Dict[str, Any], p2: Dict[str, Any]) -> str:
    if p1.get("ok") and p2.get("ok"):
        return "responses_ok + web_search_tool_ok"
    if p1.get("ok") and not p2.get("ok"):
        return "responses_ok_but_web_search_tool_not_supported_for_this_model_or_gateway"
    if not p1.get("ok") and not p2.get("ok"):
        c1 = ((p1.get("error") or {}).get("error_code") or "").lower()
        c2 = ((p2.get("error") or {}).get("error_code") or "").lower()
        if c1 == "convert_request_failed" and c2 == "convert_request_failed":
            return "gateway_does_not_support_responses_conversion_for_this_model"
        return "responses_not_available_or_gateway_error_for_this_model"
    return "mixed_state_needs_manual_check"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe gateway model capability: responses vs responses+web_search"
    )
    parser.add_argument("--base_url", default="http://35.220.164.252:3888/v1")
    parser.add_argument("--api_key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--model", required=True)
    parser.add_argument("--query", default="ping")
    parser.add_argument(
        "--tool_type",
        default="web_search_preview",
        choices=["web_search_preview", "web_search"],
    )
    parser.add_argument("--timeout_sec", type=int, default=60)
    parser.add_argument(
        "--no_proxy",
        action="store_true",
        help="Ignore HTTP(S)_PROXY/ALL_PROXY env vars.",
    )
    args = parser.parse_args()

    if not args.api_key.strip():
        raise SystemExit("Missing api_key. Pass --api_key or set OPENAI_API_KEY.")

    base_url = args.base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    http_client = None
    if httpx is not None:
        timeout = httpx.Timeout(float(args.timeout_sec))
        http_client = httpx.Client(timeout=timeout, trust_env=(not args.no_proxy))

    client = OpenAI(base_url=base_url, api_key=args.api_key.strip(), http_client=http_client)

    probe_1 = _run_probe(
        client,
        model=args.model,
        query=args.query,
        timeout_sec=args.timeout_sec,
        tools=None,
    )
    probe_2 = _run_probe(
        client,
        model=args.model,
        query=args.query,
        timeout_sec=args.timeout_sec,
        tools=[{"type": args.tool_type}],
    )

    out = {
        "base_url": base_url,
        "model": args.model,
        "tool_type": args.tool_type,
        "probe_1_responses_no_tools": probe_1,
        "probe_2_responses_with_web_search_tool": probe_2,
        "diagnosis": _diagnose(probe_1, probe_2),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

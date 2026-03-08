#!/usr/bin/env python3
"""
Probe script for Grok web_search behavior via OpenAI-compatible Responses API.

Usage example:
  OPENAI_API_KEY="..." python -m src.agents.probe_grok_web_search \
    --base_url "http://35.220.164.252:3888/v1" \
    --model "grok-4-1-fast-reasoning" \
    --prompt "aggregation-induced emission triphenylmethanol"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from openai import OpenAI


def _to_dict(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    if hasattr(response, "json"):
        return json.loads(response.json())
    return {}


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{netloc}{path}"


def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    output = resp_json.get("output") or []
    chunks: List[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content") or []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks)


def _extract_web_search_calls(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for item in resp_json.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "web_search_call":
            calls.append(item)
    return calls


def _extract_sources(resp_json: Dict[str, Any]) -> List[Dict[str, str]]:
    sources: List[Dict[str, str]] = []

    def append_source(candidate: Dict[str, Any]) -> None:
        if not isinstance(candidate, dict):
            return
        url = candidate.get("url")
        if not isinstance(url, str) or not url.strip():
            return
        title = candidate.get("title")
        snippet = candidate.get("snippet")
        sources.append(
            {
                "url": url.strip(),
                "title": title.strip() if isinstance(title, str) else "",
                "snippet": snippet.strip() if isinstance(snippet, str) else "",
            }
        )

    for item in resp_json.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        action = item.get("action") if isinstance(item.get("action"), dict) else {}

        raw_sources = action.get("sources")
        if isinstance(raw_sources, list):
            for source in raw_sources:
                append_source(source)

        if item_type in {"tool_call", "tool", "web_search", "web_search_result"}:
            for source in item.get("sources") or []:
                append_source(source)
            for result in item.get("results") or []:
                append_source(result)

        if item_type == "message":
            content = item.get("content") or []
            for part in content:
                if not isinstance(part, dict):
                    continue
                for annotation in part.get("annotations") or []:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") == "url_citation":
                        append_source(annotation)

    deduped: List[Dict[str, str]] = []
    seen = set()
    for source in sources:
        norm = _normalize_url(source["url"])
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(source)
    return deduped


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Grok web_search source surfacing")
    parser.add_argument("--base_url", default="https://api.x.ai/v1")
    parser.add_argument("--model", default="grok-4-1-fast-reasoning")
    parser.add_argument(
        "--api_key",
        default=os.getenv("OPENAI_API_KEY", "") or os.getenv("XAI_API_KEY", ""),
        help="API key (default: OPENAI_API_KEY, fallback: XAI_API_KEY).",
    )
    parser.add_argument("--prompt", default="What is xAI?")
    parser.add_argument("--tool_type", default="web_search", choices=["web_search", "web_search_preview"])
    parser.add_argument("--no_tool", action="store_true", help="Probe without tool to compare behavior.")
    parser.add_argument("--max_output_tokens", type=int, default=1024)
    parser.add_argument("--timeout_sec", type=int, default=120)
    parser.add_argument("--dump_raw", default=None, help="Optional path to dump full raw response JSON.")
    parser.add_argument("--max_print_sources", type=int, default=10)
    args = parser.parse_args()

    if not args.api_key:
        print("[error] missing API key; pass --api_key or set OPENAI_API_KEY/XAI_API_KEY", file=sys.stderr)
        raise SystemExit(2)

    client = OpenAI(api_key=args.api_key, base_url=args.base_url.rstrip("/"), timeout=args.timeout_sec)
    request_body: Dict[str, Any] = {
        "model": args.model,
        "input": [{"role": "user", "content": args.prompt}],
    }
    if args.max_output_tokens > 0:
        request_body["max_output_tokens"] = args.max_output_tokens
    if not args.no_tool:
        request_body["tools"] = [{"type": args.tool_type}]

    started = time.time()
    try:
        response = client.responses.create(**request_body)
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.time() - started, 3)
        err_payload = {
            "ok": False,
            "base_url": args.base_url,
            "model": args.model,
            "elapsed_sec": elapsed,
            "request": request_body,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "status_code": getattr(exc, "status_code", None),
            "error_code": getattr(exc, "code", None),
        }
        print(json.dumps(err_payload, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    elapsed = round(time.time() - started, 3)
    resp_json = _to_dict(response)
    if args.dump_raw:
        _write_json(args.dump_raw, resp_json)

    output_types = [
        item.get("type")
        for item in (resp_json.get("output") or [])
        if isinstance(item, dict) and isinstance(item.get("type"), str)
    ]
    web_calls = _extract_web_search_calls(resp_json)
    sources = _extract_sources(resp_json)
    output_text = _extract_output_text(resp_json)

    result = {
        "ok": True,
        "base_url": args.base_url,
        "model": args.model,
        "elapsed_sec": elapsed,
        "request": request_body,
        "response_status": resp_json.get("status"),
        "incomplete_reason": (resp_json.get("incomplete_details") or {}).get("reason"),
        "output_item_types": output_types,
        "web_search_call_count": len(web_calls),
        "web_search_call_statuses": [call.get("status") for call in web_calls[:10]],
        "sources_count": len(sources),
        "sources_sample": sources[: args.max_print_sources],
        "message_text_present": bool(output_text.strip()),
        "message_text_preview": output_text[:600],
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

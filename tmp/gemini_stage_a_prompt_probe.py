#!/usr/bin/env python3
"""/tmp/gemini_stage_a_prompt_probe.py

Stage-A-only probe for the gateway's native Gemini generateContent + google_search.

Input: a single Stage A prompt (multi-line text).
Output: a small JSON summary (grounding sources + queries + text preview) and optional raw dump.

Usage examples:
  OPENAI_API_KEY="sk-..." python /tmp/gemini_stage_a_prompt_probe.py \
    --base_url "http://35.220.164.252:3888" \
    --model "gemini-2.5-flash" \
    --prompt_file /tmp/stage_a_prompt.txt \
    --dump_raw /tmp/gemini_stage_a_raw.json

  cat /tmp/stage_a_prompt.txt | OPENAI_API_KEY="sk-..." python /tmp/gemini_stage_a_prompt_probe.py \
    --base_url "http://35.220.164.252:3888" \
    --model "gemini-2.5-flash" \
    --dump_raw /tmp/gemini_stage_a_raw.json

Notes:
- This script does NOT run Stage B structuring.
- Some sources may be redirect URLs; enable --resolve_urls to print final landing URLs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _mask_key(key: str) -> str:
    s = key.strip()
    if len(s) <= 8:
        return "***"
    return s[:4] + "..." + s[-4:]


def _strip_v1_suffix(base_url: str) -> str:
    u = (base_url or "").rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u.rstrip("/")


def _extract_grounding(resp_json: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[str], str]:
    candidates = resp_json.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return [], [], ""
    cand0 = candidates[0] if isinstance(candidates[0], dict) else None
    if not isinstance(cand0, dict):
        return [], [], ""

    # Text preview (best-effort)
    text_preview = ""
    content = cand0.get("content")
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list) and parts:
            p0 = parts[0]
            if isinstance(p0, dict):
                text_preview = _norm_str(p0.get("text")) or ""

    gm = cand0.get("groundingMetadata") or cand0.get("grounding_metadata")
    if not isinstance(gm, dict):
        return [], [], text_preview

    queries: List[str] = []
    raw_queries = gm.get("webSearchQueries") or gm.get("web_search_queries")
    if isinstance(raw_queries, list):
        for q in raw_queries:
            if isinstance(q, str) and q.strip():
                queries.append(q.strip())

    sources: List[Dict[str, Any]] = []
    chunks = gm.get("groundingChunks") or gm.get("grounding_chunks") or []
    if isinstance(chunks, list):
        for ch in chunks:
            if not isinstance(ch, dict):
                continue
            web = ch.get("web")
            if not isinstance(web, dict):
                continue
            uri = _norm_str(web.get("uri") or web.get("url"))
            if uri is None:
                continue
            title = _norm_str(web.get("title"))
            sources.append({"title": title, "url": uri})

    # Dedup by URL, keep order
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for s in sources:
        u = _norm_str(s.get("url"))
        if u is None or u in seen:
            continue
        seen.add(u)
        deduped.append(s)

    return deduped, queries, text_preview


def _resolve_url_httpx(http_client: Any, url: str, timeout_sec: int) -> Optional[str]:
    raw = _norm_str(url)
    if raw is None:
        return None
    try:
        with http_client.stream("GET", raw, follow_redirects=True, timeout=float(timeout_sec)) as resp:
            final = str(resp.url)
            return final.strip() if final else None
    except Exception:
        return None


def _resolve_sources(sources: List[Dict[str, Any]], timeout_sec: int) -> tuple[List[Dict[str, Any]], int]:
    try:
        import httpx  # type: ignore

        client = httpx.Client(timeout=httpx.Timeout(float(timeout_sec)), trust_env=True)
    except Exception:
        client = None

    out: List[Dict[str, Any]] = []
    failed = 0
    for s in sources:
        raw_url = _norm_str(s.get("url"))
        if raw_url is None:
            failed += 1
            continue
        resolved = None
        if client is not None:
            resolved = _resolve_url_httpx(client, raw_url, timeout_sec=timeout_sec)
        else:
            # Fallback: no resolution
            resolved = None
        row = {"title": s.get("title"), "url": raw_url}
        if resolved:
            row["resolved_url"] = resolved
            row["resolved_host"] = (urlparse(resolved).netloc or "").lower()
        else:
            row["resolved_url"] = None
            row["resolved_host"] = None
            failed += 1
        out.append(row)

    # Dedup by resolved_url if present else raw url
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in out:
        key = _norm_str(row.get("resolved_url")) or _norm_str(row.get("url"))
        if key is None or key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Stage-A-only probe (generateContent + google_search)")
    parser.add_argument("--base_url", default="http://35.220.164.252:3888")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--api_key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--prompt_file", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max_output_tokens", type=int, default=2048)
    parser.add_argument("--timeout_sec", type=int, default=120)
    parser.add_argument("--dump_raw", default=None)
    parser.add_argument("--resolve_urls", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    api_key = _norm_str(args.api_key) or ""
    if not api_key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing_api_key",
                    "hint": "Set OPENAI_API_KEY or pass --api_key",
                },
                indent=2,
            )
        )
        raise SystemExit(2)

    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            prompt_text = f.read()
    elif args.prompt is not None:
        prompt_text = args.prompt
    else:
        prompt_text = sys.stdin.read()

    prompt_text = _norm_str(prompt_text) or ""
    if not prompt_text:
        print(json.dumps({"ok": False, "error": "empty_prompt"}, indent=2))
        raise SystemExit(2)

    gateway_root = _strip_v1_suffix(args.base_url)
    url = f"{gateway_root}/v1beta/models/{args.model}:generateContent"

    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": float(args.temperature),
            "maxOutputTokens": int(args.max_output_tokens),
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
        ],
    }

    t0 = time.monotonic()
    try:
        import httpx  # type: ignore

        client = httpx.Client(timeout=httpx.Timeout(float(args.timeout_sec)), trust_env=True)
        resp = client.post(url, params={"key": api_key}, json=payload)
        elapsed = time.monotonic() - t0

        if resp.status_code >= 400:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "url": f"{url}?key={_mask_key(api_key)}",
                        "http_status": resp.status_code,
                        "elapsed_sec": round(elapsed, 3),
                        "error": resp.text[:4000],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)

        resp_json = resp.json()
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(
            json.dumps(
                {
                    "ok": False,
                    "url": f"{url}?key={_mask_key(api_key)}",
                    "elapsed_sec": round(elapsed, 3),
                    "error": str(e),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

    if args.dump_raw:
        with open(args.dump_raw, "w", encoding="utf-8") as f:
            json.dump(resp_json, f, ensure_ascii=False, indent=2, sort_keys=True)

    sources_raw, queries, text_preview = _extract_grounding(resp_json)

    resolve_failed = 0
    sources_out: List[Dict[str, Any]]
    if args.resolve_urls:
        sources_out, resolve_failed = _resolve_sources(sources_raw, timeout_sec=int(args.timeout_sec))
    else:
        sources_out = [{"title": s.get("title"), "url": s.get("url") , "resolved_url": None, "resolved_host": None} for s in sources_raw]

    if args.debug:
        print(f"[debug] request_url={url}?key={_mask_key(api_key)}", file=sys.stderr)
        print(f"[debug] model={args.model}", file=sys.stderr)
        print(f"[debug] temperature={args.temperature}", file=sys.stderr)
        print(f"[debug] sources_raw={len(sources_raw)} resolve_failed={resolve_failed}", file=sys.stderr)

    elapsed = time.monotonic() - t0
    out = {
        "ok": True,
        "url": f"{url}?key={_mask_key(api_key)}",
        "http_status": 200,
        "elapsed_sec": round(elapsed, 3),
        "web_search_queries_sample": queries[:10],
        "grounding_sources_count": int(len(sources_out)),
        "grounding_sources_sample": sources_out[:10],
        "text_preview": (text_preview[:400] if isinstance(text_preview, str) else ""),
        "dump_raw": args.dump_raw,
    }

    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

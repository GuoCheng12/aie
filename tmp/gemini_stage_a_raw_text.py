#!/usr/bin/env python3
"""/tmp/gemini_stage_a_raw_text.py

Stage-A-only probe: Gemini native generateContent + google_search.

Behavior:
- Sends your Stage A prompt as-is.
- Prints ONLY the model's returned text to stdout (no JSON summary).
- Optional: --dump_raw writes the full upstream JSON response for debugging.

Usage:
  OPENAI_API_KEY="sk-..." python /tmp/gemini_stage_a_raw_text.py \
    --base_url "http://35.220.164.252:3888" \
    --model "gemini-2.5-flash" \
    --prompt_file /tmp/stage_a_prompt.txt \
    --dump_raw /tmp/gemini_stage_a_raw.json

Or via stdin:
  cat /tmp/stage_a_prompt.txt | OPENAI_API_KEY="sk-..." python /tmp/gemini_stage_a_raw_text.py \
    --base_url "http://35.220.164.252:3888" \
    --model "gemini-2.5-flash" \
    --dump_raw /tmp/gemini_stage_a_raw.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional


def _norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _strip_v1_suffix(base_url: str) -> str:
    u = (base_url or "").rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u.rstrip("/")


def _extract_text(resp_json: Dict[str, Any]) -> str:
    candidates = resp_json.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    cand0 = candidates[0] if isinstance(candidates[0], dict) else None
    if not isinstance(cand0, dict):
        return ""

    content = cand0.get("content")
    if not isinstance(content, dict):
        return ""

    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    texts = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = _norm_str(p.get("text"))
        if t:
            texts.append(t)

    return "\n".join(texts).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Stage-A raw text probe")
    parser.add_argument("--base_url", default="http://35.220.164.252:3888")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--api_key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--prompt_file", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max_output_tokens", type=int, default=2048)
    parser.add_argument("--timeout_sec", type=int, default=120)
    parser.add_argument("--dump_raw", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    api_key = _norm_str(args.api_key) or ""
    if not api_key:
        print("[error] missing_api_key: set OPENAI_API_KEY or pass --api_key", file=sys.stderr)
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
        print("[error] empty_prompt", file=sys.stderr)
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
        if args.debug:
            print(f"[debug] http_status={resp.status_code} elapsed_sec={elapsed:.3f}", file=sys.stderr)

        if resp.status_code >= 400:
            print(resp.text, file=sys.stderr)
            raise SystemExit(1)

        resp_json = resp.json()
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"[error] request_failed elapsed_sec={elapsed:.3f}: {e}", file=sys.stderr)
        raise SystemExit(1)

    if args.dump_raw:
        with open(args.dump_raw, "w", encoding="utf-8") as f:
            json.dump(resp_json, f, ensure_ascii=False, indent=2, sort_keys=True)

    text = _extract_text(resp_json)
    if not text:
        # Keep stdout empty; stderr explains why.
        print("[warn] empty_model_text (see --dump_raw)", file=sys.stderr)
        return

    # Print ONLY the model text to stdout.
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

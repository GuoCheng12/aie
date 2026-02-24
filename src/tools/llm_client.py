"""
OpenAI-compatible Responses API wrapper with strict JSON support.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple


class LLMClientError(RuntimeError):
    pass


def _to_json_obj(resp: Any) -> Dict[str, Any]:
    try:
        return resp.model_dump()
    except Exception:
        try:
            return resp.dict()
        except Exception:
            return json.loads(json.dumps(resp, default=str))


def _extract_text(resp_json: Dict[str, Any]) -> str:
    out_text = resp_json.get("output_text")
    if isinstance(out_text, str) and out_text.strip():
        return out_text.strip()

    chunks = []
    for item in resp_json.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                txt = part.get("text")
                if isinstance(txt, str) and txt.strip():
                    chunks.append(txt.strip())
    return "\n".join(chunks).strip()


class ResponsesLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        max_output_tokens: int = 1500,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_output_tokens = int(max_output_tokens)
        self.reasoning_effort = reasoning_effort

    def _build_client(self) -> Any:
        key = os.getenv(self.api_key_env, "")
        if not key:
            raise LLMClientError(f"missing_api_key_env:{self.api_key_env}")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise LLMClientError(f"openai_import_failed:{exc}") from exc
        base = self.base_url
        if base and not base.endswith("/v1"):
            base = f"{base}/v1"
        return OpenAI(base_url=base or None, api_key=key)

    def responses_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        client = self._build_client()
        req: Dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "tools": [],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            req["reasoning"] = {"effort": self.reasoning_effort}

        try:
            resp = client.responses.create(**req)
        except Exception as exc:
            if self.reasoning_effort is not None:
                req_no_reason = dict(req)
                req_no_reason.pop("reasoning", None)
                try:
                    resp = client.responses.create(**req_no_reason)
                    req = req_no_reason
                except Exception as exc2:
                    raise LLMClientError(f"responses_create_failed:{exc2}") from exc2
            else:
                raise LLMClientError(f"responses_create_failed:{exc}") from exc

        resp_json = _to_json_obj(resp)
        text = _extract_text(resp_json)
        if not text:
            raise LLMClientError("responses_empty_output_text")
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise LLMClientError(f"responses_invalid_json:{exc}") from exc
        return {"request": req, "response": resp_json, "text": text, "parsed": parsed}

    def responses_text(self, *, instructions: str, input_text: str) -> Dict[str, Any]:
        client = self._build_client()
        req: Dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "tools": [],
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            req["reasoning"] = {"effort": self.reasoning_effort}
        try:
            resp = client.responses.create(**req)
        except Exception as exc:
            raise LLMClientError(f"responses_create_failed:{exc}") from exc
        resp_json = _to_json_obj(resp)
        text = _extract_text(resp_json)
        if not text:
            raise LLMClientError("responses_empty_output_text")
        return {"request": req, "response": resp_json, "text": text}


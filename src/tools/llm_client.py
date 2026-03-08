"""
OpenAI-compatible Responses API wrapper with strict JSON support.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple


class LLMClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


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


def _extract_structured_json(resp_json: Dict[str, Any]) -> Optional[Any]:
    # Some gateways return structured payloads without output_text.
    top = resp_json.get("output_parsed")
    if isinstance(top, (dict, list)):
        return top

    for item in resp_json.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for key in ("parsed", "json", "object"):
            val = item.get(key)
            if isinstance(val, (dict, list)):
                return val
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            for key in ("parsed", "json", "object"):
                val = part.get(key)
                if isinstance(val, (dict, list)):
                    return val
            txt = part.get("text")
            if isinstance(txt, str) and txt.strip():
                try:
                    return json.loads(txt)
                except Exception:
                    continue
    return None


def _extract_chat_text(resp_json: Dict[str, Any]) -> str:
    choices = resp_json.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
            if parts:
                return "\n".join(parts).strip()
    return ""


def _dedupe_keep_order(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for it in items:
        key = str(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


class ResponsesLLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        max_output_tokens: int = 1500,
        reasoning_effort: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_output_tokens = int(max_output_tokens)
        self.reasoning_effort = reasoning_effort
        self.temperature = float(temperature) if isinstance(temperature, (int, float)) else None

    def _effort_attempts(self) -> list[Optional[str]]:
        eff = self.reasoning_effort
        if eff is None:
            return [None]
        chain: list[Optional[str]] = [eff]
        if eff == "xhigh":
            chain.extend(["high", "medium", "low", "none", None])
        elif eff == "high":
            chain.extend(["medium", "low", "none", None])
        elif eff == "medium":
            chain.extend(["low", "none", None])
        elif eff == "low":
            chain.extend(["none", None])
        elif eff == "none":
            chain.extend([None])
        else:
            chain.extend([None])
        return _dedupe_keep_order(chain)

    def _build_req(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        effort: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        use_json_schema: bool = True,
    ) -> Dict[str, Any]:
        req: Dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "tools": [],
            "max_output_tokens": int(max_output_tokens) if max_output_tokens is not None else self.max_output_tokens,
        }
        if use_json_schema and schema_name is not None and schema is not None:
            req["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            }
        if effort is not None:
            req["reasoning"] = {"effort": effort}
        resolved_temperature: Optional[float]
        if isinstance(temperature, (int, float)):
            resolved_temperature = float(temperature)
        else:
            resolved_temperature = self.temperature
        if resolved_temperature is not None:
            req["temperature"] = resolved_temperature
        return req

    def _build_chat_req(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        use_json_object: bool = False,
    ) -> Dict[str, Any]:
        req: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
        }
        if max_output_tokens is not None:
            req["max_tokens"] = int(max_output_tokens)
        resolved_temperature: Optional[float]
        if isinstance(temperature, (int, float)):
            resolved_temperature = float(temperature)
        else:
            resolved_temperature = self.temperature
        if resolved_temperature is not None:
            req["temperature"] = resolved_temperature
        if use_json_object:
            req["response_format"] = {"type": "json_object"}
        return req

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

    def _prefer_chat_completions(self) -> bool:
        model = (self.model or "").strip().lower()
        return model.startswith("deepseek")

    def _try_chat_json(
        self,
        *,
        client: Any,
        instructions: str,
        input_text: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        errors: list[str] = []
        last_request: Optional[Dict[str, Any]] = None
        last_response: Optional[Dict[str, Any]] = None
        last_text: Optional[str] = None
        attempts = [True, False]
        for use_json_object in attempts:
            req = self._build_chat_req(
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                use_json_object=use_json_object,
            )
            last_request = req
            try:
                resp = client.chat.completions.create(**req)
            except Exception as exc:
                errors.append(f"chat_create_failed:json_object={use_json_object}:{exc}")
                continue
            resp_json = _to_json_obj(resp)
            last_response = resp_json
            text = _extract_chat_text(resp_json)
            last_text = text
            if not text:
                errors.append(f"chat_empty_output_text:json_object={use_json_object}")
                continue
            try:
                parsed = json.loads(text)
                return {
                    "request": {"api": "chat.completions", "request": req},
                    "response": {"api": "chat.completions", "response": resp_json},
                    "text": text,
                    "parsed": parsed,
                }
            except Exception as exc:
                errors.append(f"chat_invalid_json:json_object={use_json_object}:{exc}")
                continue
        code = "json_parse_error" if any(e.startswith("chat_invalid_json:") for e in errors) else "llm_call_failed"
        if all(e.startswith("chat_empty_output_text:") for e in errors):
            code = "no_message_output"
        raise LLMClientError(
            "chat_json_failed:" + " || ".join(errors),
            code=code,
            details={
                "errors": errors,
                "last_request": last_request,
                "last_response": last_response,
                "last_text": last_text,
            },
        )

    def _try_chat_text(
        self,
        *,
        client: Any,
        instructions: str,
        input_text: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        req = self._build_chat_req(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            use_json_object=False,
        )
        try:
            resp = client.chat.completions.create(**req)
        except Exception as exc:
            raise LLMClientError(
                f"chat_text_failed:chat_create_failed:{exc}",
                code="llm_call_failed",
                details={"last_request": req, "last_response": None, "last_text": None},
            ) from exc
        resp_json = _to_json_obj(resp)
        text = _extract_chat_text(resp_json)
        if text:
            return {
                "request": {"api": "chat.completions", "request": req},
                "response": {"api": "chat.completions", "response": resp_json},
                "text": text,
            }
        raise LLMClientError(
            "chat_empty_output_text",
            code="no_message_output",
            details={"last_request": req, "last_response": resp_json, "last_text": text},
        )

    def _should_try_chat_fallback(self, exc: LLMClientError) -> bool:
        if self._prefer_chat_completions():
            return True
        details = exc.details if isinstance(exc.details, dict) else {}
        errors = details.get("errors") if isinstance(details.get("errors"), list) else []
        text = " || ".join(str(e) for e in errors)
        text = f"{exc} || {text}"
        lowered = text.lower()
        return "unsupported model" in lowered

    def responses_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: Dict[str, Any],
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        use_json_schema: bool = True,
    ) -> Dict[str, Any]:
        client = self._build_client()
        def _responses_only() -> Dict[str, Any]:
            errors: list[str] = []
            last_request: Optional[Dict[str, Any]] = None
            last_response: Optional[Dict[str, Any]] = None
            last_text: Optional[str] = None
            for effort in self._effort_attempts():
                req = self._build_req(
                    instructions=instructions,
                    input_text=input_text,
                    schema_name=schema_name,
                    schema=schema,
                    effort=effort,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    use_json_schema=use_json_schema,
                )
                last_request = req
                try:
                    resp = client.responses.create(**req)
                except Exception as exc:
                    errors.append(f"responses_create_failed:effort={effort}:{exc}")
                    continue

                resp_json = _to_json_obj(resp)
                last_response = resp_json
                text = _extract_text(resp_json)
                if text:
                    try:
                        parsed = json.loads(text)
                        return {"request": req, "response": resp_json, "text": text, "parsed": parsed}
                    except Exception as exc:
                        parsed = _extract_structured_json(resp_json)
                        if parsed is not None:
                            text = json.dumps(parsed, ensure_ascii=False)
                            return {"request": req, "response": resp_json, "text": text, "parsed": parsed}
                        last_text = text
                        errors.append(f"responses_invalid_json:effort={effort}:{exc}")
                        continue

                parsed = _extract_structured_json(resp_json)
                if parsed is not None:
                    text = json.dumps(parsed, ensure_ascii=False)
                    return {"request": req, "response": resp_json, "text": text, "parsed": parsed}

                item_types = []
                for it in resp_json.get("output", []) or []:
                    if isinstance(it, dict):
                        item_types.append(str(it.get("type") or ""))
                errors.append(
                    "responses_empty_output_text"
                    + f":effort={effort}"
                    + (f":output_types={item_types}" if item_types else "")
                )
            if errors:
                if any(e.startswith("responses_invalid_json:") for e in errors):
                    code = "json_parse_error"
                elif all(e.startswith("responses_empty_output_text:") for e in errors):
                    code = "no_message_output"
                else:
                    code = "llm_call_failed"
                raise LLMClientError(
                    "responses_json_failed:" + " || ".join(errors),
                    code=code,
                    details={
                        "errors": errors,
                        "last_request": last_request,
                        "last_response": last_response,
                        "last_text": last_text,
                    },
                )
            raise LLMClientError(
                "responses_empty_output_text",
                code="no_message_output",
                details={
                    "errors": [],
                    "last_request": last_request,
                    "last_response": last_response,
                    "last_text": last_text,
                },
            )

        if self._prefer_chat_completions():
            try:
                return self._try_chat_json(
                    client=client,
                    instructions=instructions,
                    input_text=input_text,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
            except LLMClientError as chat_exc:
                if not self._should_try_chat_fallback(chat_exc):
                    raise
                return _responses_only()

        try:
            return _responses_only()
        except LLMClientError as resp_exc:
            if not self._should_try_chat_fallback(resp_exc):
                raise
            return self._try_chat_json(
                client=client,
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )

    def responses_text(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        client = self._build_client()
        def _responses_only() -> Dict[str, Any]:
            errors: list[str] = []
            last_request: Optional[Dict[str, Any]] = None
            last_response: Optional[Dict[str, Any]] = None
            last_text: Optional[str] = None
            for effort in self._effort_attempts():
                req = self._build_req(
                    instructions=instructions,
                    input_text=input_text,
                    effort=effort,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
                last_request = req
                try:
                    resp = client.responses.create(**req)
                except Exception as exc:
                    errors.append(f"responses_create_failed:effort={effort}:{exc}")
                    continue
                resp_json = _to_json_obj(resp)
                last_response = resp_json
                text = _extract_text(resp_json)
                if text:
                    return {"request": req, "response": resp_json, "text": text}
                last_text = text
                errors.append(f"responses_empty_output_text:effort={effort}")
            if errors:
                if all(e.startswith("responses_empty_output_text:") for e in errors):
                    code = "no_message_output"
                else:
                    code = "llm_call_failed"
                raise LLMClientError(
                    "responses_text_failed:" + " || ".join(errors),
                    code=code,
                    details={
                        "errors": errors,
                        "last_request": last_request,
                        "last_response": last_response,
                        "last_text": last_text,
                    },
                )
            raise LLMClientError(
                "responses_empty_output_text",
                code="no_message_output",
                details={
                    "errors": [],
                    "last_request": last_request,
                    "last_response": last_response,
                    "last_text": last_text,
                },
            )

        if self._prefer_chat_completions():
            try:
                return self._try_chat_text(
                    client=client,
                    instructions=instructions,
                    input_text=input_text,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
            except LLMClientError as chat_exc:
                if not self._should_try_chat_fallback(chat_exc):
                    raise
                return _responses_only()

        try:
            return _responses_only()
        except LLMClientError as resp_exc:
            if not self._should_try_chat_fallback(resp_exc):
                raise
            return self._try_chat_text(
                client=client,
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )

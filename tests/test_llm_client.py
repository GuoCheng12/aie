import json

import pytest

from src.tools.llm_client import ResponsesLLMClient


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class _FakeResponses:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._payload, list):
            if not self._payload:
                raise RuntimeError("no_more_payloads")
            payload = self._payload.pop(0)
        else:
            payload = self._payload
        if isinstance(payload, BaseException):
            raise payload
        return _FakeResp(payload)


class _FakeChatCompletions:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._payload, list):
            if not self._payload:
                raise RuntimeError("no_more_payloads")
            payload = self._payload.pop(0)
        else:
            payload = self._payload
        if isinstance(payload, BaseException):
            raise payload
        return _FakeResp(payload)


class _FakeClient:
    def __init__(self, payload, holder=None, chat_payload=None):
        self.responses = _FakeResponses(payload)
        self.chat = type("ChatNS", (), {"completions": _FakeChatCompletions(chat_payload or [])})()
        if holder is not None:
            holder["responses"] = self.responses
            holder["chat"] = self.chat.completions


def test_responses_json_parses_output_text(monkeypatch):
    payload = {
        "output_text": json.dumps({"status": "ok"}),
        "output": [],
    }
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payload))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    )
    assert out["parsed"]["status"] == "ok"


def test_responses_json_falls_back_to_structured_payload(monkeypatch):
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_json",
                        "parsed": {"status": "ok", "limits": []},
                    }
                ],
            }
        ]
    }
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payload))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limits": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "limits"],
        },
    )
    assert out["parsed"]["status"] == "ok"
    assert isinstance(out["text"], str) and out["text"]


def test_responses_json_retries_lower_effort_when_empty(monkeypatch):
    payloads = [
        {"output": [{"type": "reasoning"}]},
        {"output_text": json.dumps({"status": "ok"})},
    ]
    holder = {}
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2", reasoning_effort="xhigh")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads, holder))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    )
    assert out["parsed"]["status"] == "ok"
    calls = holder["responses"].calls
    assert len(calls) >= 2
    assert calls[0]["reasoning"]["effort"] == "xhigh"
    assert calls[1]["reasoning"]["effort"] == "high"


def test_responses_json_uses_structured_fallback_when_output_text_is_invalid_json(monkeypatch):
    payload = {
        "output_text": '{"status":"ok",',  # invalid/truncated JSON
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_json", "parsed": {"status": "ok", "limits": []}}],
            }
        ],
    }
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payload))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={
            "type": "object",
            "properties": {"status": {"type": "string"}, "limits": {"type": "array", "items": {"type": "string"}}},
            "required": ["status", "limits"],
        },
    )
    assert out["parsed"]["status"] == "ok"


def test_responses_json_error_message_contains_attempt_chain(monkeypatch):
    payloads = [
        {"output_text": '{"status":"ok",', "output": []},  # invalid json
        {"output": [{"type": "reasoning"}]},  # empty output text
    ]
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2", reasoning_effort="high")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads))
    with pytest.raises(RuntimeError) as exc_info:
        client.responses_json(
            instructions="i",
            input_text="u",
            schema_name="s",
            schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
        )
    msg = str(exc_info.value)
    assert "responses_json_failed:" in msg
    assert "responses_invalid_json:effort=high" in msg
    assert "responses_empty_output_text:effort=medium" in msg


def test_responses_text_prefers_chat_for_deepseek(monkeypatch):
    holder = {}
    chat_payload = {
        "choices": [
            {
                "message": {
                    "content": "TEMPLATE_USED: mixture\nSTATUS: ok\nPRIMARY_LABEL: ICT\nPRIMARY_CONFIDENCE: 0.5"
                }
            }
        ]
    }
    client = ResponsesLLMClient(base_url="http://x", model="deepseek-v3.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient([], holder, chat_payload=chat_payload))
    out = client.responses_text(instructions="i", input_text="u")
    assert "PRIMARY_LABEL" in out["text"]
    assert holder["chat"].calls
    assert holder["responses"].calls == []


def test_responses_json_falls_back_to_chat_when_responses_api_rejects_model(monkeypatch):
    holder = {}
    chat_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"status": "ok", "limits": []})
                }
            }
        ]
    }
    client = ResponsesLLMClient(base_url="http://x", model="gpt-like-relay")

    def _build():
        return _FakeClient(
            [RuntimeError("Unsupported model: deepseek-v3.2")],
            holder,
            chat_payload=chat_payload,
        )

    monkeypatch.setattr(client, "_build_client", _build)
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={
            "type": "object",
            "properties": {"status": {"type": "string"}, "limits": {"type": "array", "items": {"type": "string"}}},
            "required": ["status", "limits"],
        },
    )
    assert out["parsed"]["status"] == "ok"
    assert holder["responses"].calls
    assert holder["chat"].calls
    assert out["request"]["api"] == "chat.completions"


def test_responses_text_retries_without_temperature_when_unsupported(monkeypatch):
    holder = {}
    payloads = [
        RuntimeError("Error code: 400 - {'error': {'message': \"Unsupported parameter: 'temperature' is not supported with this model.\"}}"),
        {"output_text": "PRIMARY_LABEL: ICT"},
    ]
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2", temperature=0.2)
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads, holder))
    out = client.responses_text(instructions="i", input_text="u")
    assert "PRIMARY_LABEL" in out["text"]
    assert len(holder["responses"].calls) == 2
    assert holder["responses"].calls[0].get("temperature") == 0.2
    assert "temperature" not in holder["responses"].calls[1]


def test_responses_json_retries_without_temperature_when_unsupported(monkeypatch):
    holder = {}
    payloads = [
        RuntimeError("Error code: 400 - {'error': {'message': \"Unsupported parameter: 'temperature' is not supported with this model.\"}}"),
        {"output_text": json.dumps({"status": "ok"})},
    ]
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2", temperature=0.2)
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads, holder))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
    )
    assert out["parsed"]["status"] == "ok"
    assert len(holder["responses"].calls) == 2
    assert holder["responses"].calls[0].get("temperature") == 0.2
    assert "temperature" not in holder["responses"].calls[1]


def test_responses_text_falls_back_to_chat_on_generic_invalid_request(monkeypatch):
    holder = {}
    chat_payload = {
        "choices": [
            {
                "message": {
                    "content": "TEMPLATE_USED: mixture\nSTATUS: ok\nPRIMARY_LABEL: ICT\nPRIMARY_CONFIDENCE: 0.5"
                }
            }
        ]
    }
    payloads = [
        RuntimeError(
            "Error code: 400 - {'error': {'message': 'There was an issue with your request. Please check your inputs and try again', 'type': 'invalid_request_error'}}"
        )
    ]
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads, holder, chat_payload=chat_payload))
    out = client.responses_text(instructions="i", input_text="u")
    assert "PRIMARY_LABEL" in out["text"]
    assert holder["responses"].calls
    assert holder["chat"].calls
    assert out["request"]["api"] == "chat.completions"


def test_responses_json_falls_back_to_chat_on_generic_invalid_request(monkeypatch):
    holder = {}
    chat_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"status": "ok", "limits": []})
                }
            }
        ]
    }
    payloads = [
        RuntimeError(
            "Error code: 400 - {'error': {'message': 'There was an issue with your request. Please check your inputs and try again', 'type': 'invalid_request_error'}}"
        )
    ]
    client = ResponsesLLMClient(base_url="http://x", model="gpt-5.2")
    monkeypatch.setattr(client, "_build_client", lambda: _FakeClient(payloads, holder, chat_payload=chat_payload))
    out = client.responses_json(
        instructions="i",
        input_text="u",
        schema_name="s",
        schema={
            "type": "object",
            "properties": {"status": {"type": "string"}, "limits": {"type": "array", "items": {"type": "string"}}},
            "required": ["status", "limits"],
        },
    )
    assert out["parsed"]["status"] == "ok"
    assert holder["responses"].calls
    assert holder["chat"].calls
    assert out["request"]["api"] == "chat.completions"

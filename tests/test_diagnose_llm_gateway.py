import json
from pathlib import Path

from src.tools.diagnose_llm_gateway import ProbeResult, _classify_error, _summarize_root_cause, main


def test_classify_error_quota():
    msg = "Error code: 429 - {'error': {'type': 'insufficient_quota', 'message': 'quota exceeded'}}"
    assert _classify_error(msg) == "quota_or_billing"


def test_classify_error_temperature():
    msg = "Unsupported parameter: 'temperature' is not supported with this model."
    assert _classify_error(msg) == "unsupported_temperature"


def test_classify_error_internal_inference():
    msg = "The model was unable to complete inference due to an internal error."
    assert _classify_error(msg) == "model_internal_error"


def test_summarize_root_cause_prefers_quota():
    probes = [
        ProbeResult(name="a", ok=True, latency_ms=1),
        ProbeResult(
            name="b",
            ok=False,
            latency_ms=1,
            error={"classification": "quota_or_billing", "message": "insufficient_quota"},
        ),
    ]
    assert _summarize_root_cause(probes) == "quota_or_billing"


def test_main_creates_output_parent_dir(monkeypatch, tmp_path):
    class _Args:
        base_url = "http://x"
        models = ["gpt-5.2"]
        api_key_env = "OPENAI_API_KEY"
        reasoning_effort = "medium"
        temperature = 0.2
        max_output_tokens = 32
        out = str(tmp_path / "nested" / "diag.json")

    monkeypatch.setattr("src.tools.diagnose_llm_gateway.build_parser", lambda: _P(_Args))
    monkeypatch.setattr(
        "src.tools.diagnose_llm_gateway.run_diagnostics",
        lambda args: {"ok": True, "models": [{"model": "gpt-5.2"}]},
    )

    main([])
    out_path = Path(_Args.out)
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True


class _P:
    def __init__(self, args_obj):
        self._args = args_obj

    def parse_args(self, _argv):
        return self._args

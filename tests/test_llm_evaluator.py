import json

import pytest

from src.agents.llm_evaluator import LLMEvaluator, merge_eval_report_with_llm_layer
from src.tools.llm_client import ResponsesLLMClient


class _MockLLM:
    def __init__(self, parsed):
        self.parsed = parsed
        self.last_input_text = None

    def responses_json(self, *, instructions, input_text, schema_name, schema, **kwargs):
        _ = instructions, schema_name, schema, kwargs
        self.last_input_text = input_text
        return {"parsed": self.parsed, "request": {"ok": True}, "response": {"ok": True}}


class _MockTextLLM:
    def __init__(self, text: str):
        self.text = text

    def responses_text(self, *, instructions, input_text, max_output_tokens):
        _ = instructions, input_text, max_output_tokens
        return {"text": self.text, "request": {"ok": True}, "response": {"ok": True}}


def test_llm_evaluator_schema_validation_and_small_input():
    parsed = {
        "critique_points": [{"title": "c1", "severity": "medium", "note": "n", "evidence_ids": ["E1"]}],
        "conflicts": [{"conflict_id": "C1", "status": "unresolved", "rationale": "r", "evidence_ids": ["E2"]}],
        "voi_ranked_actions": [{"action": "run_master_reasoner", "llm_priority_weight": 1.3, "rationale": "boost"}],
        "confidence_delta_suggestion": -0.05,
        "next_round_profile_suggestion": "R2",
    }
    mock = _MockLLM(parsed)
    ev = LLMEvaluator(llm_client=mock)  # type: ignore[arg-type]
    out = ev.run(
        reasoning_pack={"pack_version": "master_pack_v1"},
        master_output_parsed={"status": "ok"},
        policy={"neighbor_support_min_sim": 0.55},
        thresholds={"top1_sim_low": 0.5},
        run_lane_capabilities={"atb_available": True},
    )
    assert out["parsed"]["next_round_profile_suggestion"] == "R2"
    payload = json.loads(mock.last_input_text)
    assert set(payload.keys()) == {
        "reasoning_pack",
        "master_output_parsed",
        "policy",
        "thresholds",
        "run_lane_capabilities",
    }


def test_llm_evaluator_invalid_output_raises():
    parsed = {
        "critique_points": [],
        "conflicts": [],
        # missing voi_ranked_actions
        "confidence_delta_suggestion": 0.0,
        "next_round_profile_suggestion": "R1",
    }
    ev = LLMEvaluator(llm_client=_MockLLM(parsed))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ev.run(
            reasoning_pack={},
            master_output_parsed={},
            policy={},
            thresholds={},
            run_lane_capabilities={},
        )


def test_merge_llm_layer_reweights_actions_without_changing_stop():
    base = {
        "voi_ranked_actions": [
            {
                "action": "run_master_reasoner",
                "expected_information_gain": 0.4,
                "feasibility_score": 1.0,
                "priority_score": 0.4,
                "feasible": True,
            },
            {
                "action": "request_manual_pdf",
                "expected_information_gain": 0.7,
                "feasibility_score": 0.0,
                "priority_score": 0.0,
                "feasible": False,
            },
        ],
        "stop_recommendation": {"should_stop": True, "reason_code": "profile_exhausted"},
    }
    llm = {
        "critique_points": [],
        "conflicts": [],
        "voi_ranked_actions": [{"action": "run_master_reasoner", "llm_priority_weight": 1.8, "rationale": "stronger"}],
        "confidence_delta_suggestion": -0.1,
        "next_round_profile_suggestion": "R2",
    }
    merged = merge_eval_report_with_llm_layer(eval_report=base, llm_output=llm)
    assert merged["stop_recommendation"]["should_stop"] is True
    rows = merged["voi_ranked_actions"]
    assert rows[0]["action"] == "run_master_reasoner"
    assert rows[0]["priority_score"] == pytest.approx(0.72, rel=1e-6)
    assert merged["llm_layer"]["enabled"] is True


def test_llm_evaluator_can_use_different_model_and_effort(monkeypatch):
    captured = {}

    def _fake_responses_json(self, *, instructions, input_text, schema_name, schema, **kwargs):
        _ = instructions, input_text, schema_name, schema, kwargs
        captured["model"] = self.model
        captured["reasoning_effort"] = self.reasoning_effort
        return {
            "parsed": {
                "critique_points": [],
                "conflicts": [],
                "voi_ranked_actions": [],
                "confidence_delta_suggestion": 0.0,
                "next_round_profile_suggestion": "R1",
            },
            "request": {"ok": True},
            "response": {"ok": True},
        }

    monkeypatch.setattr(ResponsesLLMClient, "responses_json", _fake_responses_json)

    ev = LLMEvaluator(
        base_url="http://example/v1",
        api_key_env="OPENAI_API_KEY",
        max_output_tokens=200,
        default_model="gpt-master",
        default_reasoning_effort="xhigh",
    )
    ev.run(
        reasoning_pack={},
        master_output_parsed={},
        policy={},
        thresholds={},
        run_lane_capabilities={},
        model="gpt-evaluator",
        reasoning_effort="low",
    )
    assert captured["model"] == "gpt-evaluator"
    assert captured["reasoning_effort"] == "low"


def test_llm_evaluator_tagged_natural_language_parses():
    text = """
CRITIQUE_POINTS:
- High ambiguity remains near top competing mechanisms (E2, E6).
CONFLICTS:
- unresolved: confidence drift vs weak discriminators (E11, E22)
VOI_RANKED_ACTIONS:
- action=switch_run_lane_offline_pdf; weight=1.6; rationale=unlock external discriminators
- action=provide_offline_pdf; weight=1.4; rationale=enable extraction
CONFIDENCE_DELTA_SUGGESTION:
-0.08
NEXT_ROUND_PROFILE_SUGGESTION:
R2
"""
    ev = LLMEvaluator(llm_client=_MockTextLLM(text))  # type: ignore[arg-type]
    out = ev.run(
        reasoning_pack={},
        master_output_parsed={"status": "ok"},
        policy={},
        thresholds={},
        run_lane_capabilities={},
    )
    parsed = out["parsed"]
    assert parsed["next_round_profile_suggestion"] == "R2"
    assert len(parsed["voi_ranked_actions"]) == 2
    assert parsed["voi_ranked_actions"][0]["action"] == "switch_run_lane_offline_pdf"

import json
from copy import deepcopy

from src.reasoning.master_reasoner import build_reasoning_pack, run_master_reasoner_once
from src.tools.llm_client import LLMClientError


def _case() -> dict:
    return {
        "query": {"input_smiles": "C", "canonical_smiles": "C", "inchikey": "IK-STAB"},
        "runtime": {"run_lane": "atb_cache_only"},
        "current_gate": {
            "state": "ready_conservative",
            "ready_for_reasoning": True,
            "reasoning_mode": "conservative",
            "reason": "atb_success_without_emission",
        },
        "neighbors": [{"rank": 1, "sim": 0.82, "neighbor_inchikey": "N-1", "neighbor_mechanism_label": "other"}],
        "risk_scores": {
            "top1_sim": 0.82,
            "mean_topk_sim": 0.71,
            "novelty_struct": 0.21,
            "mechanism_entropy": 0.42,
            "atb_neighbor_consistency": {"flag": "insufficient_neighbors", "reliability": "low"},
        },
        "evidence_readiness": {
            "atb": {
                "cache_status": "success",
                "features_summary": {
                    "delta_gap": -0.2,
                    "delta_dihedral": -11.2,
                    "delta_volume": 0.14,
                },
            },
            "literature": {"status": "not_started", "notes": "lane_disabled"},
            "experiment": {"status": "not_requested", "notes": "lane_disabled"},
        },
        "target_fields": {},
        "target_fields_provenance": {},
    }


def _eid(pack: dict, case_path: str) -> str:
    for row in pack.get("evidence_registry") or []:
        if isinstance(row, dict) and row.get("case_path") == case_path:
            evidence_id = row.get("evidence_id")
            if isinstance(evidence_id, str):
                return evidence_id
    raise AssertionError(f"missing evidence id for {case_path}")


def _ev(pack: dict, case_path: str, note: str, role: str) -> dict:
    return {"evidence_id": _eid(pack, case_path), "note": note, "role": role}


def _valid_master_output(pack: dict) -> dict:
    ev_sim = [_ev(pack, "/risk_scores/top1_sim", "similarity context.", "context")]
    ev_dihedral = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_dihedral", "torsional access cue.", "support")]
    ev_gap = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_gap", "ct-family context cue.", "context")]
    ev_vol = [_ev(pack, "/evidence_readiness/atb/features_summary/delta_volume", "rigidification proxy cue.", "context")]
    return {
        "status": "ok",
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "other",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Conservative mechanism summary based on aTB and structural context.",
                "atb_support_level": "weak",
            },
            "confidence": 0.55,
            "reasoning_mode_used": "conservative",
        },
        "supporting_chain": [
            {
                "step_id": "A",
                "step_name": "torsion_access",
                "claim": "Excited-state structural access is supported by aTB dihedral behavior.",
                "evidence_used": ev_dihedral,
            },
            {
                "step_id": "B",
                "step_name": "ct_family",
                "claim": "A nonradiative channel is plausible under torsion-sensitive CT behavior.",
                "evidence_used": ev_gap,
            },
            {
                "step_id": "C",
                "step_name": "aIE_bridge",
                "claim": "Aggregation rigidification can suppress this channel and improve radiative yield.",
                "evidence_used": ev_vol,
            },
            {
                "step_id": "D",
                "step_name": "discriminators",
                "claim": "Compare and measure time-resolved and polarity-dependent tests to separate top hypotheses.",
                "evidence_used": ev_dihedral,
            },
        ],
        "competing_hypotheses": [
            {"name": "alt_hyp_1", "confidence": 0.3, "atb_support_level": "weak", "evidence_used": ev_sim}
        ],
        "predictions": [
            {"prediction": "Time-resolved PL test", "expected_signal": "lifetime trend", "evidence_used": ev_dihedral},
            {"prediction": "Polarity compare", "expected_signal": "band-shift trend", "evidence_used": ev_gap},
            {"prediction": "Temperature compare", "expected_signal": "nonradiative trend", "evidence_used": ev_vol},
        ],
        "limits": [
            "Conservative mode: mechanism assignment is tentative and should be interpreted with uncertainty.",
            "No emission evidence is currently available.",
        ],
        "evidence_used": ev_sim + ev_dihedral + ev_gap,
        "recommended_next_actions": ["switch_run_lane_offline_pdf"],
    }


class _FakeMasterLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.base_url = "http://fake/v1"
        self.model = "fake-master"
        self.api_key_env = "OPENAI_API_KEY"
        self.max_output_tokens = 1500
        self.reasoning_effort = "medium"
        self.temperature = 0.2

    def responses_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no fake response left")
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        parsed = deepcopy(nxt)
        return {
            "request": {"mock": True, **{k: kwargs.get(k) for k in ["max_output_tokens", "temperature"]}},
            "response": {"output_text": json.dumps(parsed, ensure_ascii=False)},
            "text": json.dumps(parsed, ensure_ascii=False),
            "parsed": parsed,
        }


def _reasoning_config() -> dict:
    return {
        "run_lane": "atb_cache_only",
        "master_output_schema_version": "v3",
        "conservative_confidence_cap": 0.65,
        "master": {
            "reasoning_effort": "medium",
            "temperature": 0.2,
        },
    }


def test_master_reasoner_retries_once_on_no_output(monkeypatch):
    case = _case()
    pack = build_reasoning_pack(case, _reasoning_config())
    output = _valid_master_output(pack)
    fake = _FakeMasterLLM(
        responses=[
            LLMClientError(
                "responses_json_failed:responses_empty_output_text:effort=medium",
                code="no_message_output",
                details={"last_request": {"mock": "r0"}, "last_response": {"output": [{"type": "reasoning"}]}, "last_text": ""},
            ),
            output,
        ]
    )
    monkeypatch.setattr("src.reasoning.master_reasoner._clone_llm_client", lambda *args, **kwargs: fake)
    out = run_master_reasoner_once(case_json=case, reasoning_config=_reasoning_config(), llm_client=fake, reasoning_pack=pack)
    assert out["status"] == "success"
    assert out["llm_failure_reason"] == "no_message_output"
    assert len(fake.calls) == 2
    assert fake.calls[1]["max_output_tokens"] >= 3200
    assert "Retry instruction:" in fake.calls[1]["instructions"]


def test_master_reasoner_uses_json_repair_on_parse_failure(monkeypatch):
    case = _case()
    pack = build_reasoning_pack(case, _reasoning_config())
    output = _valid_master_output(pack)

    def _fake_repair_json_only(*, llm_client, raw_text, schema_name, schema, reasoning_config):  # noqa: ARG001
        return {"parsed": deepcopy(output), "request": {"repair": True}, "response": {"repair": True}}

    monkeypatch.setattr("src.reasoning.master_reasoner._repair_json_only", _fake_repair_json_only)

    fake = _FakeMasterLLM(
        responses=[
            LLMClientError(
                "responses_json_failed:responses_invalid_json:effort=medium",
                code="json_parse_error",
                details={"last_request": {"mock": "r0"}, "last_response": {"output_text": "{\"status\":\"ok\","}, "last_text": "{\"status\":\"ok\","},
            ),
            LLMClientError(
                "responses_json_failed:responses_invalid_json:effort=medium",
                code="json_parse_error",
                details={"last_request": {"mock": "r1"}, "last_response": {"output_text": "{\"status\":\"ok\","}, "last_text": "{\"status\":\"ok\","},
            ),
        ]
    )
    monkeypatch.setattr("src.reasoning.master_reasoner._clone_llm_client", lambda *args, **kwargs: fake)
    out = run_master_reasoner_once(case_json=case, reasoning_config=_reasoning_config(), llm_client=fake, reasoning_pack=pack)
    assert out["status"] == "success"
    assert out["llm_failure_reason"] == "json_repair_used"
    assert out["validation_errors"] == []


def test_master_reasoner_success_path_still_validates_schema(monkeypatch):
    case = _case()
    pack = build_reasoning_pack(case, _reasoning_config())
    output = _valid_master_output(pack)
    fake = _FakeMasterLLM(responses=[output])
    monkeypatch.setattr("src.reasoning.master_reasoner._clone_llm_client", lambda *args, **kwargs: fake)
    out = run_master_reasoner_once(case_json=case, reasoning_config=_reasoning_config(), llm_client=fake, reasoning_pack=pack)
    assert out["status"] == "success"
    assert out["llm_failure_reason"] is None
    assert out["validation_errors"] == []

from src.tools.llm_trace_store import build_reasoning_five_signals


def test_build_reasoning_five_signals_extracts_expected_sections():
    parsed = {
        "status": "ok",
        "template_used": "mixture",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "TICT",
                "aie_rationale_type": "mixture",
                "natural_language_mechanism": "demo",
            },
            "confidence": 0.42,
            "reasoning_mode_used": "conservative",
        },
        "competing_hypotheses": [{"name": "ICT", "confidence": 0.33, "evidence_used": []}],
        "evidence_used": [{"evidence_id": "E1", "role": "support", "note": "demo"}],
        "predictions": [
            {"prediction": "time-resolved PL", "expected_signal": "lifetime trend", "evidence_used": []},
            {"prediction": "polarity perturbation", "expected_signal": "spectral shift", "evidence_used": []},
            {"prediction": "aggregation perturbation", "expected_signal": "intensity trend", "evidence_used": []},
        ],
        "limits": ["L1"],
        "recommended_next_actions": ["A1"],
    }
    out = build_reasoning_five_signals(
        run_id="r1",
        case_id="c1",
        status="completed",
        model="gpt-5.2",
        reasoning_effort="xhigh",
        parsed=parsed,
    )
    sig = out["five_signals"]
    assert sig["conclusion"]["mechanism_label"] == "TICT"
    assert sig["confidence"]["value"] == 0.42
    assert sig["competing_hypotheses"][0]["name"] == "ICT"
    assert 8 <= len(sig["evidence_chain"]) <= 12
    ids = [x.get("evidence_id") for x in sig["evidence_chain"]]
    assert "E2" in ids and "E4" in ids and "E6" in ids
    assert "E11" in ids and "E12" in ids
    assert "E19" in ids and "E20" in ids
    assert "case_path" not in sig["evidence_chain"][0]
    narrative = sig["conclusion"]["natural_language_mechanism"]
    assert isinstance(narrative, str)
    assert len([p for p in narrative.split("\n\n") if p.strip()]) >= 3
    assert sig["limits_and_next_actions"]["recommended_next_actions"] == ["A1"]

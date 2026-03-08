from src.agents.judge_agent import apply_evaluator_confidence_adjustment


def _base_eval_report() -> dict:
    return {
        "confidence_update": {"prev": 0.50, "delta": 0.0, "new": 0.50, "basis": "master_confidence"},
        "evidence_scorecard": [{"dimension": "d1", "score": 0.4}],
        "feasibility": {"overall_score": 0.3},
    }


def test_evaluator_confidence_adjustment_disabled_no_change():
    out = apply_evaluator_confidence_adjustment(
        eval_report=_base_eval_report(),
        config={"enabled": False, "max_abs_delta": 0.05, "require_new_evidence": True, "high_weight_evidence_ids": ["E21"]},
        master_confidence=0.50,
        cap=0.65,
        added_ids=["E21"],
        count_added=1,
        resolved_conflicts=[],
        scorecard_improved=True,
        feasibility_improved=False,
        conflicts_increased=False,
    )
    assert out["confidence_update"]["new"] == 0.50
    assert out["confidence_adjustment"]["applied"] is False
    assert out["confidence_adjustment"]["reason"] == "disabled"


def test_evaluator_confidence_adjustment_guard_requires_trigger():
    out = apply_evaluator_confidence_adjustment(
        eval_report=_base_eval_report(),
        config={"enabled": True, "max_abs_delta": 0.05, "require_new_evidence": True, "high_weight_evidence_ids": ["E21"]},
        master_confidence=0.50,
        cap=0.65,
        added_ids=[],
        count_added=0,
        resolved_conflicts=[],
        scorecard_improved=True,
        feasibility_improved=True,
        conflicts_increased=False,
    )
    assert out["confidence_update"]["new"] == 0.50
    assert out["confidence_adjustment"]["applied"] is False
    assert out["confidence_adjustment"]["reason"] == "trigger_not_met"


def test_evaluator_confidence_adjustment_applies_for_high_weight_evidence():
    out = apply_evaluator_confidence_adjustment(
        eval_report=_base_eval_report(),
        config={"enabled": True, "max_abs_delta": 0.05, "require_new_evidence": True, "high_weight_evidence_ids": ["E21", "E22"]},
        master_confidence=0.50,
        cap=0.65,
        added_ids=["E21"],
        count_added=1,
        resolved_conflicts=[],
        scorecard_improved=True,
        feasibility_improved=False,
        conflicts_increased=False,
    )
    adj = out["confidence_adjustment"]
    assert adj["applied"] is True
    assert 0.0 <= float(adj["delta"]) <= 0.05
    assert out["confidence_update"]["new"] <= 0.65
    assert "E21" in (adj.get("triggered_by") or {}).get("added_ids", [])

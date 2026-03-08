from src.agents.judge_agent import build_eval_report


def _case_fixture() -> dict:
    return {
        "case_id": "CASE-EVAL",
        "inputs": {"offline_pdfs": []},
        "evidence_readiness": {
            "atb": {"cache_status": "success"},
            "literature": {"status": "not_started"},
            "experiment": {"status": "not_requested"},
        },
        "master_reasoning": {"status": "ok", "mechanism_claim": {"confidence": 0.5}},
    }


def test_eval_report_feasibility_contract_fields():
    report = build_eval_report(
        case_json=_case_fixture(),
        judged={"status": "needs_followup", "confidence": 0.4, "contradictions": [], "missing_evidence": [], "recommended_actions": []},
        round_index=0,
        active_profile="R0",
        run_lane="atb_cache_only",
        prev_confidence=0.35,
    )

    feasibility = report.get("feasibility") or {}
    assert "lane_capabilities" in feasibility
    assert "constraints" in feasibility
    assert "overall_score" in feasibility
    assert isinstance(report.get("voi_ranked_actions"), list)
    assert report["voi_ranked_actions"], "expected non-empty action list"
    row = report["voi_ranked_actions"][0]
    assert "feasible" in row
    assert "feasibility_score" in row
    assert "blocked_by" in row
    assert "unblock_actions" in row


def test_eval_action_ranking_with_feasibility_prefers_feasible_action():
    case = _case_fixture()
    report = build_eval_report(
        case_json=case,
        judged={"status": "needs_followup", "confidence": 0.2, "contradictions": [], "missing_evidence": [], "recommended_actions": []},
        round_index=1,
        active_profile="R1",
        run_lane="atb_cache_only",
        prev_confidence=0.2,
    )
    actions = report.get("voi_ranked_actions") or []
    assert actions
    if any(bool(x.get("feasible")) for x in actions):
        assert bool(actions[0].get("feasible")) is True


def test_eval_report_atb_lane_prefers_lane_unblock_action():
    report = build_eval_report(
        case_json=_case_fixture(),
        judged={"status": "needs_followup", "confidence": 0.2, "contradictions": [], "missing_evidence": [], "recommended_actions": []},
        round_index=0,
        active_profile="R0",
        run_lane="atb_cache_only",
        prev_confidence=0.2,
        info_gain={"count_added": 0, "hypothesis_changed": False},
    )
    actions = report.get("voi_ranked_actions") or []
    assert actions
    assert actions[0]["action"] in {"switch_run_lane_offline_pdf", "provide_offline_pdf"}
    assert actions[0]["action"] != "request_manual_pdf"

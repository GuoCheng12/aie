import pytest


def _make_er(timestamp: str):
    from src.cases.case_schema import create_empty_evidence_readiness

    return create_empty_evidence_readiness(timestamp)


class TestCaseActionPlanSemantics:
    def test_atb_success_inlier_mode_normal_ready_true(self):
        from src.cases.case_schema import now_iso, evaluate_gate, AtbCacheStatus, KEY_ATB_FIELDS
        from src.cases.create_case_from_smiles import build_llm_action_plan_v07

        ts = now_iso()
        er = _make_er(ts)
        er["atb"]["cache_status"] = AtbCacheStatus.SUCCESS.value
        er["atb"]["features_summary"] = {k: 1.0 for k in KEY_ATB_FIELDS}

        ready, _ = evaluate_gate(er)
        assert ready is True

        plan, rationale, mode = build_llm_action_plan_v07(
            inchikey="TEST-INCHIKEY",
            canonical_smiles="c1ccccc1",
            cache_status=AtbCacheStatus.SUCCESS.value,
            atb_missing_fields=[],
            atb_neighbor_flag="inlier",
            has_emission=False,
            has_solvent=False,
            retry_failed_atb=True,
            aliases=[],
        )

        assert mode == "normal"
        assert plan[0]["action"] == "run_master_reasoner"
        assert plan[0]["priority"] == 1

    def test_atb_success_outlier_mode_conservative_includes_escalations(self):
        from src.cases.case_schema import now_iso, evaluate_gate, AtbCacheStatus, KEY_ATB_FIELDS
        from src.cases.create_case_from_smiles import build_llm_action_plan_v07

        ts = now_iso()
        er = _make_er(ts)
        er["atb"]["cache_status"] = AtbCacheStatus.SUCCESS.value
        er["atb"]["features_summary"] = {k: 1.0 for k in KEY_ATB_FIELDS}

        ready, _ = evaluate_gate(er)
        assert ready is True

        plan, rationale, mode = build_llm_action_plan_v07(
            inchikey="TEST-INCHIKEY",
            canonical_smiles="c1ccccc1",
            cache_status=AtbCacheStatus.SUCCESS.value,
            atb_missing_fields=[],
            atb_neighbor_flag="outlier",
            has_emission=False,
            has_solvent=False,
            retry_failed_atb=True,
            aliases=["alias1"],
        )

        assert mode == "conservative"
        actions = [a["action"] for a in plan]
        assert actions[0] == "run_master_reasoner"
        assert "literature_search_web" in actions
        assert "expand_structure_neighbors" in actions
        assert any("out-of-distribution" in x for x in rationale)

    def test_atb_failed_no_emission_mode_blocked_first_action_retry_blocking(self):
        from src.cases.case_schema import now_iso, evaluate_gate, AtbCacheStatus
        from src.cases.create_case_from_smiles import build_llm_action_plan_v07

        ts = now_iso()
        er = _make_er(ts)
        er["atb"]["cache_status"] = AtbCacheStatus.FAILED.value
        er["minimal_experiment_available"]["has_emission"] = False

        ready, _ = evaluate_gate(er)
        assert ready is False

        plan, _, mode = build_llm_action_plan_v07(
            inchikey="TEST-INCHIKEY",
            canonical_smiles="c1ccccc1",
            cache_status=AtbCacheStatus.FAILED.value,
            atb_missing_fields=[],
            atb_neighbor_flag="target_missing",
            has_emission=False,
            has_solvent=False,
            retry_failed_atb=True,
            aliases=[],
        )

        assert mode == "blocked"
        assert plan[0]["action"] == "retry_target_atb_alt_settings"
        assert plan[0]["blocking"] is True

    def test_atb_absent_mode_blocked_first_action_compute_blocking(self):
        from src.cases.case_schema import now_iso, evaluate_gate, AtbCacheStatus
        from src.cases.create_case_from_smiles import build_llm_action_plan_v07

        ts = now_iso()
        er = _make_er(ts)
        er["atb"]["cache_status"] = AtbCacheStatus.ABSENT.value
        er["minimal_experiment_available"]["has_emission"] = False

        ready, _ = evaluate_gate(er)
        assert ready is False

        plan, _, mode = build_llm_action_plan_v07(
            inchikey="TEST-INCHIKEY",
            canonical_smiles="c1ccccc1",
            cache_status=AtbCacheStatus.ABSENT.value,
            atb_missing_fields=[],
            atb_neighbor_flag="target_missing",
            has_emission=False,
            has_solvent=False,
            retry_failed_atb=True,
            aliases=[],
        )

        assert mode == "blocked"
        assert plan[0]["action"] == "compute_target_atb"
        assert plan[0]["blocking"] is True

    def test_action_objects_have_required_keys_and_priorities_increasing(self):
        from src.cases.case_schema import AtbCacheStatus
        from src.cases.create_case_from_smiles import build_llm_action_plan_v07

        plan, _, _ = build_llm_action_plan_v07(
            inchikey="TEST-INCHIKEY",
            canonical_smiles="c1ccccc1",
            cache_status=AtbCacheStatus.SUCCESS.value,
            atb_missing_fields=[],
            atb_neighbor_flag="outlier",
            has_emission=False,
            has_solvent=False,
            retry_failed_atb=True,
            aliases=[],
        )

        required = {"action", "priority", "status", "inputs", "expected_outputs", "blocking", "notes"}
        priorities = []
        for a in plan:
            assert required.issubset(set(a.keys()))
            priorities.append(a["priority"])

        assert priorities == sorted(priorities)
        assert len(set(priorities)) == len(priorities)

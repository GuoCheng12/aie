from src.orchestration.round_runner import _enforce_comparative_only_adjustment


def test_comparative_only_adjustment_cannot_flip_primary_label():
    parsed_master = {
        "template_used": "stable",
        "mechanism_claim": {
            "primary_hypothesis": {
                "mechanism_label": "ICT",
                "aie_rationale_type": "stable",
                "natural_language_mechanism": "Comparative evidence suggests a different label.",
                "atb_support_level": "weak",
            },
            "confidence": 0.55,
            "reasoning_mode_used": "conservative",
        },
        "limits": [],
        "__meta": {},
    }
    prev_master_report = {
        "hypothesis": {"mechanism_label": "neutral aromatic"},
        "confidence": 0.40,
    }
    adjusted, warning = _enforce_comparative_only_adjustment(
        active_profile="R2",
        effective_added_ids=["E21", "E22"],
        prev_master_report=prev_master_report,
        parsed_master=parsed_master,
        normalized_output=None,
        max_abs_conf_delta=0.04,
    )
    assert adjusted is True
    assert warning == "comparative_only_cannot_flip_primary_label"
    assert parsed_master["mechanism_claim"]["primary_hypothesis"]["mechanism_label"] == "neutral aromatic"
    assert parsed_master["template_used"] == "mixture"
    confidence = float(parsed_master["mechanism_claim"]["confidence"])
    assert 0.36 <= confidence <= 0.44
    limits = [str(x) for x in parsed_master.get("limits") or []]
    assert any("cannot flip the primary mechanism label" in x for x in limits)
    assert bool((parsed_master.get("__meta") or {}).get("comparative_only_adjust_applied")) is True

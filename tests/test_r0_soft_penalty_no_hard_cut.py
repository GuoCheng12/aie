from src.reasoning.master_reasoner import _soft_confidence


def _pack(profile: str) -> dict:
    return {
        "risk_scores": {
            "top1_sim": 0.90,
            "mechanism_entropy": 0.20,
        },
        "gate": {"reasoning_mode": "normal"},
        "evidence_profile": {"active_profile": profile},
    }


def test_r0_soft_penalty_no_hard_cut():
    cfg_r0 = {
        "policy": {
            "global_confidence_cap": 0.95,
            "r0_penalty_factor": 0.90,
            "top1_sim_low": 0.50,
            "entropy_high": 0.75,
            "penalty_sim_strength": 0.25,
            "penalty_entropy_strength": 0.25,
        },
        "round_index": 0,
        "conservative_confidence_cap": 0.65,
    }
    cfg_r1 = dict(cfg_r0)
    cfg_r1["round_index"] = 1

    conf_r0, meta_r0 = _soft_confidence(
        raw_confidence=0.80,
        template_used="stable",
        reasoning_pack=_pack("R0"),
        reasoning_config=cfg_r0,
    )
    conf_r1, meta_r1 = _soft_confidence(
        raw_confidence=0.80,
        template_used="stable",
        reasoning_pack=_pack("R1"),
        reasoning_config=cfg_r1,
    )

    assert conf_r0 < conf_r1
    assert conf_r0 > 0.45
    assert meta_r0.get("r0_penalty_applied") is True
    assert meta_r1.get("r0_penalty_applied") is False

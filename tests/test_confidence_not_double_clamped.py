from src.reasoning.master_reasoner import _soft_confidence


def _pack(*, top1: float, entropy: float, active_profile: str = "R1", gate_mode: str = "normal") -> dict:
    return {
        "risk_scores": {
            "top1_sim": top1,
            "mechanism_entropy": entropy,
        },
        "gate": {"reasoning_mode": gate_mode},
        "evidence_profile": {"active_profile": active_profile},
    }


def test_confidence_not_double_clamped():
    cfg = {
        "policy": {
            "top1_sim_low": 0.50,
            "entropy_high": 0.75,
            "penalty_sim_strength": 0.25,
            "penalty_entropy_strength": 0.25,
            "penalty_mode_conservative": 0.86,
            "global_confidence_cap": 0.95,
            "r0_penalty_factor": 0.90,
        },
        "conservative_confidence_cap": 0.65,
        "round_index": 1,
    }

    c1, _ = _soft_confidence(
        raw_confidence=0.8,
        template_used="stable",
        reasoning_pack=_pack(top1=0.90, entropy=0.20),
        reasoning_config=cfg,
    )
    c2, _ = _soft_confidence(
        raw_confidence=0.8,
        template_used="stable",
        reasoning_pack=_pack(top1=0.40, entropy=0.20),
        reasoning_config=cfg,
    )
    c3, meta3 = _soft_confidence(
        raw_confidence=0.8,
        template_used="stable",
        reasoning_pack=_pack(top1=0.90, entropy=0.90),
        reasoning_config=cfg,
    )

    vals = {round(c1, 4), round(c2, 4), round(c3, 4)}
    assert len(vals) >= 3
    assert all(abs(v - 0.42) > 1.0e-6 for v in vals)
    assert "final_conf_pre_cap" in meta3
    assert "final_conf_post_cap" in meta3
    assert "cap_value" in meta3

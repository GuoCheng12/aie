from src.reasoning.atb_trends_self import compute_atb_trends_self


def test_atb_trends_self_bucketing_and_direction_defaults():
    out = compute_atb_trends_self(
        {
            "delta_dihedral": 12.0,
            "delta_gap": -0.7,
            "delta_volume": 0.04,
            "excitation_energy": 1.9,
        },
        {
            "atb_dihedral_thresh_none": 8.0,
            "atb_dihedral_thresh_strong": 15.0,
            "atb_gap_flat_eps": 0.05,
            "atb_gap_weak": 0.2,
            "atb_gap_strong": 0.6,
            "atb_vol_flat_eps": 0.1,
            "atb_vol_weak": 0.5,
            "atb_vol_strong": 2.0,
        },
    )
    assert out["delta_dihedral_bucket"] == "weak"
    assert out["delta_dihedral_direction"] == "increase"
    assert out["delta_gap_direction"] == "decrease"
    assert out["delta_gap_bucket"] == "strong"
    assert out["delta_volume_direction"] == "flat"
    assert out["delta_volume_bucket"] == "weak"
    assert out["reliability"] == "high"
    assert out["enabled"] is True


def test_atb_trends_self_missing_fields_degrades_reliability():
    out = compute_atb_trends_self(
        {
            "delta_dihedral": None,
            "delta_gap": -0.1,
        },
        {},
    )
    assert out["reliability"] == "low"
    assert out["enabled"] is False
    assert out["delta_dihedral_bucket"] == "unknown"
    assert out["overall_motion_proxy"] in {"low", "medium", "high", "unknown"}


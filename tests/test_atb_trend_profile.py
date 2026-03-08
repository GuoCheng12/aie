import json

from src.reasoning.atb_trend_profile import compute_atb_trend_profile


def test_atb_trend_profile_buckets_and_directions():
    profile = compute_atb_trend_profile(
        {
            "delta_dihedral": -0.04,
            "delta_gap": -0.5,
            "delta_volume": 4.2,
            "excitation_energy": 2.3,
        }
    )

    assert profile["version"] == "atb_trend_v1"
    assert profile["abs_values"]["delta_dihedral"] == 0.04
    assert profile["buckets"]["delta_dihedral"] == "low"
    assert profile["buckets"]["delta_gap"] == "mid"
    assert profile["buckets"]["delta_volume"] == "high"
    assert profile["direction"]["delta_dihedral"] == "flat"
    assert profile["direction"]["delta_gap"] == "decrease"
    assert profile["direction"]["delta_volume"] == "increase"
    assert profile["overall_motion_proxy"] == "high"
    assert profile["reliability"] == "high"


def test_atb_trend_profile_reliability_degrades_with_missing_fields():
    profile = compute_atb_trend_profile(
        {
            "delta_dihedral": None,
            "delta_gap": 0.0,
            "delta_volume": "bad",
            "excitation_energy": 2.1,
        }
    )

    assert profile["reliability"] == "low"
    assert profile["buckets"]["delta_dihedral"] == "unknown"
    assert profile["direction"]["delta_dihedral"] == "unknown"
    assert profile["direction"]["delta_gap"] == "flat"
    assert profile["direction"]["delta_volume"] == "unknown"


def test_atb_trend_profile_size_budget_under_2kb():
    profile = compute_atb_trend_profile(
        {
            "delta_dihedral": 2.2,
            "delta_gap": 1.5,
            "delta_volume": 8.7,
            "excitation_energy": 3.1,
        }
    )
    blob = json.dumps(profile, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert len(blob.encode("utf-8")) < 2 * 1024

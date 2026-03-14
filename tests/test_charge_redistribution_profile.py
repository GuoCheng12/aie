from src.reasoning.atb_ct_proxy_profile import compute_atb_ct_proxy_profile
from src.reasoning.charge_redistribution_profile import compute_charge_redistribution_profile


def test_charge_redistribution_profile_prefers_atomwise_summary():
    profile = compute_charge_redistribution_profile(
        {
            "charge_redis_total_abs": 0.55,
            "charge_redis_max_abs_atom": 0.04,
            "charge_redis_top3_abs_share": 0.35,
            "charge_redis_heteroatom_abs_share": 0.28,
            "charge_redis_n_atoms_ge_0p01": 8,
            "charge_redis_n_atoms_ge_0p02": 4,
            "delta_gap": -0.41,
        }
    )
    alias = compute_atb_ct_proxy_profile(
        {
            "charge_redis_total_abs": 0.55,
            "charge_redis_max_abs_atom": 0.04,
            "charge_redis_top3_abs_share": 0.35,
            "charge_redis_heteroatom_abs_share": 0.28,
            "charge_redis_n_atoms_ge_0p01": 8,
            "charge_redis_n_atoms_ge_0p02": 4,
            "delta_gap": -0.41,
        }
    )
    assert profile["version"] == "charge_redistribution_v2"
    assert profile["source"] == "atomwise_charge_variation"
    assert profile["redistribution_magnitude_bucket"] == "high"
    assert profile["redistribution_localization"] == "localized"
    assert profile["heteroatom_involvement"] == "high"
    assert profile["redistribution_score"] == "high"
    assert profile["reliability"] == "high"
    assert alias["version"] == "atb_ct_proxy_v1"
    assert alias["ct_proxy_score"] == profile["redistribution_score"]
    assert alias["delta_dipole_abs"] == profile["total_abs_charge_variation"]
    assert alias["delta_dipole_bucket"] == profile["redistribution_magnitude_bucket"]


def test_charge_redistribution_profile_scalar_fallback_still_works():
    profile = compute_charge_redistribution_profile(
        {
            "delta_dipole": 0.82,
            "delta_gap": -0.41,
        }
    )
    assert profile["source"] == "scalar_delta_dipole"
    assert profile["redistribution_magnitude_bucket"] == "high"
    assert profile["delta_gap_bucket"] in {"mid", "high"}
    assert profile["redistribution_score"] in {"medium", "high"}
    assert profile["reliability"] == "medium"


def test_charge_redistribution_profile_gap_only_is_low_reliability():
    profile = compute_charge_redistribution_profile(
        {
            "delta_gap": -0.75,
        }
    )
    assert profile["source"] == "gap_only"
    assert profile["redistribution_magnitude_bucket"] == "unknown"
    assert profile["reliability"] == "low"
    assert profile["delta_gap_bucket"] == "high"

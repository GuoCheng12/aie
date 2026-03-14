import json

from src.reasoning.atb_ct_proxy_profile import compute_atb_ct_proxy_profile
from src.reasoning.atb_shape_rigidity_profile import compute_atb_shape_rigidity_profile
from src.reasoning.atb_structural_relaxation_profile import compute_atb_structural_relaxation_profile


def test_atb_ct_proxy_profile_compact_and_interpretable():
    profile = compute_atb_ct_proxy_profile(
        {
            "delta_dipole": 0.82,
            "delta_gap": -0.41,
        }
    )
    assert profile["version"] == "atb_ct_proxy_v1"
    assert profile["delta_dipole_bucket"] == "high"
    assert profile["delta_gap_bucket"] in {"mid", "high"}
    assert profile["ct_proxy_score"] in {"medium", "high"}
    assert profile["reliability"] == "medium"
    assert len(json.dumps(profile, ensure_ascii=False).encode("utf-8")) < 2048


def test_atb_structural_relaxation_profile_uses_more_than_dihedral():
    profile = compute_atb_structural_relaxation_profile(
        {
            "delta_dihedral": 0.2,
            "delta_bonds": 0.09,
            "delta_angles": 0.9,
            "delta_volume": 4.2,
        }
    )
    assert profile["version"] == "atb_structural_relaxation_v1"
    assert profile["buckets"]["delta_bonds"] == "high"
    assert profile["buckets"]["delta_angles"] == "high"
    assert profile["buckets"]["delta_volume"] == "high"
    assert profile["relaxation_proxy_score"] in {"medium", "high"}
    assert profile["reliability"] == "high"


def test_atb_shape_rigidity_profile_prefers_small_changes_as_high_rigidity():
    profile = compute_atb_shape_rigidity_profile(
        {
            "s0_rays_asymmetry_parameter": 0.10,
            "s1_rays_asymmetry_parameter": 0.11,
            "s0_rotational_constant_a": 1.0,
            "s1_rotational_constant_a": 1.01,
            "s0_rotational_constant_b": 0.5,
            "s1_rotational_constant_b": 0.49,
            "s0_rotational_constant_c": 0.25,
            "s1_rotational_constant_c": 0.251,
        }
    )
    assert profile["version"] == "atb_shape_rigidity_v1"
    assert profile["rigidity_proxy"] == "high"
    assert profile["reliability"] == "high"
    assert len(json.dumps(profile, ensure_ascii=False).encode("utf-8")) < 2048

from src.reasoning.emission_observation_profile import compute_emission_observation_profile


def _prov(condition: str) -> dict:
    return {
        "source_type": "dataset_row",
        "source_ref": "/tmp/demo.csv",
        "source_locator": "row_index=1; code=DEMO",
        "confidence": 1.0,
        "identity_match": "exact",
        "identity_match_confidence": 1.0,
        "condition": condition,
        "condition_bucket": condition,
    }


def test_emission_observation_profile_handles_partial_and_full_coverage():
    aggr_only = compute_emission_observation_profile(
        {"emission_aggr_nm": 520.0},
        {"emission_aggr_nm": _prov("aggregation")},
    )
    assert aggr_only["coverage"] == "aggr_only"
    assert aggr_only["shift_direction"] == "unknown"
    assert aggr_only["shift_magnitude_bucket"] == "unknown"
    assert aggr_only["reliability"] == "medium"

    solid_only = compute_emission_observation_profile(
        {"emission_solid_or_film_nm": 560.0},
        {"emission_solid_or_film_nm": _prov("solid_or_film")},
    )
    assert solid_only["coverage"] == "solid_only"
    assert solid_only["reliability"] == "medium"

    both = compute_emission_observation_profile(
        {"emission_aggr_nm": 520.0, "emission_solid_or_film_nm": 565.0},
        {
            "emission_aggr_nm": _prov("aggregation"),
            "emission_solid_or_film_nm": _prov("solid_or_film"),
        },
    )
    assert both["coverage"] == "both"
    assert both["shift_nm"] == 45.0
    assert both["shift_direction"] == "red_shift"
    assert both["shift_magnitude_bucket"] == "large"
    assert both["reliability"] == "high"


def test_emission_observation_profile_handles_flat_and_blue_shift():
    flat = compute_emission_observation_profile(
        {"emission_aggr_nm": 520.0, "emission_solid_or_film_nm": 527.5},
        {
            "emission_aggr_nm": _prov("aggregation"),
            "emission_solid_or_film_nm": _prov("solid_or_film"),
        },
    )
    assert flat["shift_direction"] == "flat"
    assert flat["shift_magnitude_bucket"] == "small"

    blue = compute_emission_observation_profile(
        {"emission_aggr_nm": 540.0, "emission_solid_or_film_nm": 500.0},
        {
            "emission_aggr_nm": _prov("aggregation"),
            "emission_solid_or_film_nm": _prov("solid_or_film"),
        },
    )
    assert blue["shift_nm"] == -40.0
    assert blue["shift_direction"] == "blue_shift"
    assert blue["shift_magnitude_bucket"] == "large"


def test_emission_observation_profile_missing_values_are_low_reliability():
    missing = compute_emission_observation_profile({}, {})
    assert missing["coverage"] == "none"
    assert missing["shift_direction"] == "unknown"
    assert missing["shift_magnitude_bucket"] == "unknown"
    assert missing["reliability"] == "low"

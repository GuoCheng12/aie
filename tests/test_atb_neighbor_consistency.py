from src.chem.atb_neighbor_consistency import compute_atb_neighbor_consistency


def _neighbor(status, gap=None, dih=None, vol=None):
    return {
        "cache_status": status,
        "delta_gap": gap,
        "delta_dihedral": dih,
        "delta_volume": vol,
    }


def test_target_missing_when_target_vector_absent():
    out = compute_atb_neighbor_consistency(
        target_features=None,
        neighbor_features=[_neighbor("success", 1.0, 10.0, 100.0)],
    )
    assert out["flag"] == "target_missing"
    assert out["sample_size"] == 0
    assert out["outlier_score_max"] is None
    assert out["reliability"] == "low"


def test_insufficient_neighbors_ignores_failed_or_incomplete_rows():
    target = {"delta_gap": 2.0, "delta_dihedral": 20.0, "delta_volume": 200.0}
    neighbors = [
        _neighbor("success", 1.0, 10.0, 100.0),
        _neighbor("success", 2.0, 20.0, 200.0),
        _neighbor("success", 3.0, 30.0, 300.0),
        _neighbor("failed", 4.0, 40.0, 400.0),
        _neighbor("success", 5.0, 50.0, None),
    ]
    out = compute_atb_neighbor_consistency(
        target_features=target,
        neighbor_features=neighbors,
        min_sample_size=5,
    )
    assert out["sample_size"] == 3
    assert out["flag"] == "insufficient_neighbors"
    assert out["outlier_score_max"] is None


def test_outlier_flag_with_robust_z_score():
    neighbors = [
        _neighbor("success", 1.0, 10.0, 100.0),
        _neighbor("success", 2.0, 20.0, 200.0),
        _neighbor("success", 3.0, 30.0, 300.0),
        _neighbor("success", 2.5, 25.0, 250.0),
        _neighbor("success", 1.5, 15.0, 150.0),
    ]
    target = {
        # median(gap)=2.0, mad(gap)=0.5 -> denom=0.7413; z~=4.0
        "delta_gap": 2.0 + 4.0 * (1.4826 * 0.5),
        "delta_dihedral": 20.0,
        "delta_volume": 200.0,
    }
    out = compute_atb_neighbor_consistency(
        target_features=target,
        neighbor_features=neighbors,
        z_max_threshold=3.5,
    )
    assert out["sample_size"] == 5
    assert out["flag"] == "outlier"
    assert out["outlier_score_max"] is not None and out["outlier_score_max"] >= 3.5
    assert "delta_gap" in out["outlier_dims"]


def test_mad_zero_handling_sets_null_z_and_warning():
    neighbors = [
        _neighbor("success", 1.0, 10.0, 100.0),
        _neighbor("success", 1.0, 20.0, 200.0),
        _neighbor("success", 1.0, 30.0, 300.0),
        _neighbor("success", 1.0, 40.0, 400.0),
        _neighbor("success", 1.0, 50.0, 500.0),
    ]
    target = {
        "delta_gap": 2.0,  # mad=0 and target!=median
        "delta_dihedral": 30.0,
        "delta_volume": 300.0,
    }
    out = compute_atb_neighbor_consistency(
        target_features=target,
        neighbor_features=neighbors,
    )
    assert out["mad"]["delta_gap"] == 0.0
    assert out["z_scores"]["delta_gap"] is None
    assert "mad_zero:delta_gap" in out["warnings"]
    assert out["reliability"] == "low"


def test_reliability_high_with_large_stable_sample():
    neighbors = []
    for i in range(1, 16):
        neighbors.append(_neighbor("success", float(i), float(i * 2), float(i * 3)))
    target = {"delta_gap": 8.0, "delta_dihedral": 16.0, "delta_volume": 24.0}
    out = compute_atb_neighbor_consistency(
        target_features=target,
        neighbor_features=neighbors,
    )
    assert out["sample_size"] == 15
    assert out["reliability"] == "high"
    assert out["flag"] == "inlier"

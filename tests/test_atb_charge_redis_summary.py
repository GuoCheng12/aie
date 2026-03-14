from src.chem.atb_cache import extract_features_summary, extract_numeric_features


def test_extract_features_summary_adds_atomwise_charge_redistribution_fields():
    features = {
        "delta_volume": 1.2,
        "delta_gap": -0.7,
        "delta_dihedral": 3.4,
        "excitation_energy": 2.1,
        "delta_dipole": {
            "element": ["C", "N", "O", "H"],
            "charge_variation": [0.03, -0.02, 0.01, -0.005],
        },
    }
    summary, missing = extract_features_summary(features)
    assert summary is not None
    assert summary["charge_redis_total_abs"] == 0.065
    assert summary["charge_redis_max_abs_atom"] == 0.03
    assert round(summary["charge_redis_top3_abs_share"], 6) == round(0.06 / 0.065, 6)
    assert round(summary["charge_redis_heteroatom_abs_share"], 6) == round(0.03 / 0.065, 6)
    assert summary["charge_redis_n_atoms_ge_0p01"] == 3.0
    assert summary["charge_redis_n_atoms_ge_0p02"] == 2.0
    assert "delta_dipole" not in summary
    assert "delta_dipole" not in missing


def test_extract_numeric_features_adds_charge_redistribution_fields_without_scalar_delta_dipole():
    features = {
        "delta_gap": -0.2,
        "delta_dipole": {
            "element": ["C", "Cl", "H"],
            "charge_variation": [0.005, -0.025, 0.01],
        },
    }
    row = extract_numeric_features(features)
    assert row["delta_dipole"] is None
    assert row["charge_redis_total_abs"] == 0.04
    assert row["charge_redis_max_abs_atom"] == 0.025
    assert row["charge_redis_n_atoms_ge_0p01"] == 2.0
    assert row["charge_redis_n_atoms_ge_0p02"] == 1.0


def test_scalar_delta_dipole_path_remains_available():
    features = {
        "delta_volume": 1.2,
        "delta_gap": -0.3,
        "delta_dihedral": 1.4,
        "excitation_energy": 2.2,
        "delta_dipole": 0.42,
    }
    summary, missing = extract_features_summary(features)
    assert summary is not None
    assert summary["delta_dipole"] == 0.42
    assert "delta_dipole" not in missing

"""
tests/test_atb_parser.py

Tests for AIE-aTB result.json parsing.
"""

import pytest
import json
import tempfile
from pathlib import Path

from src.chem.atb_parser import (
    parse_result_json,
    detect_fail_stage,
    extract_features,
    compute_charge_dipole,
    save_features_json,
)


class TestATBParser:
    """Test suite for ATB parser."""

    @pytest.fixture
    def sample_result(self):
        """Sample result.json content from AIE-aTB."""
        return {
            "ground_state": {
                "HOMO-LUMO": "1.8318795",
                "charge": {
                    "element": ["C", "C", "N"],
                    "charge": [-0.078, 0.264, -0.570]
                },
                "structure": {
                    "bonds": 1.233,
                    "angles": 115.39,
                    "DA": 5.88
                },
                "volume": 513.01528
            },
            "excited_state": {
                "HOMO-LUMO": "1.5424777",
                "charge": {
                    "element": ["C", "C", "N"],
                    "charge": [-0.079, 0.265, -0.583]
                },
                "structure": {
                    "bonds": 1.252,
                    "angles": 115.20,
                    "DA": -2.81
                },
                "volume": 512.97471
            },
            "NEB": 512.81177
        }

    @pytest.fixture
    def temp_result_file(self, sample_result):
        """Create temporary result.json file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(sample_result, f)
            return Path(f.name)

    def test_parse_result_json_success(self, temp_result_file):
        """Test successful parsing of result.json."""
        features, fail_stage = parse_result_json(temp_result_file)

        assert fail_stage is None
        assert features is not None
        assert "s0_volume" in features
        assert features["s0_volume"] == pytest.approx(513.01528)
        assert features["s1_volume"] == pytest.approx(512.97471)

    def test_parse_result_json_file_not_found(self):
        """Test parsing when file doesn't exist."""
        features, fail_stage = parse_result_json(Path("/nonexistent/path.json"))

        assert features is None
        assert fail_stage == "feature_parse"

    def test_parse_result_json_invalid_json(self):
        """Test parsing of invalid JSON."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json }")
            path = Path(f.name)

        features, fail_stage = parse_result_json(path)

        assert features is None
        assert fail_stage == "feature_parse"

    def test_detect_fail_stage_success(self, sample_result):
        """Test fail stage detection for complete result."""
        fail_stage = detect_fail_stage(sample_result)
        assert fail_stage is None

    def test_detect_fail_stage_missing_ground_state(self, sample_result):
        """Test fail stage detection when ground_state missing."""
        del sample_result["ground_state"]
        fail_stage = detect_fail_stage(sample_result)
        assert fail_stage == "opt"

    def test_detect_fail_stage_missing_excited_state(self, sample_result):
        """Test fail stage detection when excited_state missing."""
        del sample_result["excited_state"]
        fail_stage = detect_fail_stage(sample_result)
        assert fail_stage == "excit"

    def test_detect_fail_stage_missing_neb(self, sample_result):
        """Test fail stage detection when NEB missing."""
        del sample_result["NEB"]
        fail_stage = detect_fail_stage(sample_result)
        assert fail_stage == "neb"

    def test_detect_fail_stage_missing_volume(self, sample_result):
        """Test fail stage detection when volume missing."""
        del sample_result["ground_state"]["volume"]
        fail_stage = detect_fail_stage(sample_result)
        assert fail_stage == "volume"

    def test_extract_features(self, sample_result):
        """Test feature extraction from result.json."""
        features = extract_features(sample_result)

        # Volume
        assert features["s0_volume"] == pytest.approx(513.01528)
        assert features["s1_volume"] == pytest.approx(512.97471)
        assert features["delta_volume"] == pytest.approx(-0.04057, abs=0.001)

        # HOMO-LUMO gap
        assert features["s0_homo_lumo_gap"] == pytest.approx(1.8318795)
        assert features["s1_homo_lumo_gap"] == pytest.approx(1.5424777)
        assert features["delta_gap"] == pytest.approx(-0.2894018, abs=0.001)

        # Dihedral
        assert features["s0_dihedral_avg"] == pytest.approx(5.88)
        assert features["s1_dihedral_avg"] == pytest.approx(-2.81)
        assert features["delta_dihedral"] == pytest.approx(-8.69)

        # NEB
        assert features["neb_mean_volume"] == pytest.approx(512.81177)

    def test_compute_charge_dipole(self):
        """Test charge dipole computation."""
        charge_data = {
            "element": ["C", "C", "N"],
            "charge": [-0.5, 0.3, 0.2]
        }
        dipole = compute_charge_dipole(charge_data)

        # Sum of absolute charges
        assert dipole == pytest.approx(1.0)

    def test_compute_charge_dipole_none(self):
        """Test charge dipole with None input."""
        assert compute_charge_dipole(None) is None
        assert compute_charge_dipole({}) is None
        assert compute_charge_dipole({"element": []}) is None

    def test_save_features_json(self, sample_result):
        """Test saving features.json."""
        features = extract_features(sample_result)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir)
            features_path = save_features_json(features, cache_path)

            assert features_path.exists()
            with open(features_path, "r") as f:
                loaded = json.load(f)

            assert loaded["s0_volume"] == features["s0_volume"]
            assert loaded["delta_gap"] == features["delta_gap"]


class TestATBParserSchemaCompliance:
    """Test that parser output matches doc/schemas.md."""

    @pytest.fixture
    def sample_result(self):
        """Sample result.json content."""
        return {
            "ground_state": {
                "HOMO-LUMO": "1.83",
                "structure": {"DA": 5.0},
                "volume": 500.0,
                "charge": {"element": ["C"], "charge": [0.1]}
            },
            "excited_state": {
                "HOMO-LUMO": "1.50",
                "structure": {"DA": -3.0},
                "volume": 510.0,
                "charge": {"element": ["C"], "charge": [0.2]}
            },
            "NEB": 505.0
        }

    def test_features_schema_columns(self, sample_result):
        """Test that extracted features match expected schema columns."""
        features = extract_features(sample_result)

        # Required columns from doc/schemas.md atb_features.parquet
        required_columns = [
            "s0_volume",
            "s1_volume",
            "delta_volume",
            "s0_homo_lumo_gap",
            "s1_homo_lumo_gap",
            "delta_gap",
            "s0_dihedral_avg",
            "s1_dihedral_avg",
            "delta_dihedral",
            "s0_charge_dipole",
            "s1_charge_dipole",
            "delta_dipole",
            "excitation_energy",
        ]

        for col in required_columns:
            assert col in features, f"Missing column: {col}"

    def test_delta_calculations(self, sample_result):
        """Test that delta values are computed as S1 - S0."""
        features = extract_features(sample_result)

        # delta = S1 - S0
        assert features["delta_volume"] == features["s1_volume"] - features["s0_volume"]
        assert features["delta_gap"] == features["s1_homo_lumo_gap"] - features["s0_homo_lumo_gap"]
        assert features["delta_dihedral"] == features["s1_dihedral_avg"] - features["s0_dihedral_avg"]

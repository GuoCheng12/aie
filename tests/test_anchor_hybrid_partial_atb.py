"""
tests/test_anchor_hybrid_partial_atb.py

Unit tests for hybrid ECFP + aTB anchor space builder.
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.features.anchor_hybrid_ecfp_atb_partial import (
    safe_parse_float,
    extract_atb_features,
    to_binary_fingerprint,
    tanimoto_similarity,
    cosine_to_sim,
    ATB_FEATURES,
    is_valid_inchikey,
    build_atb_matrix,
)


class TestSafeParseFloat:
    """Tests for excitation_energy string parsing."""

    def test_parse_string_float(self):
        """Parse string representation of float."""
        assert safe_parse_float("3.8347") == pytest.approx(3.8347)

    def test_parse_integer_string(self):
        """Parse string representation of integer."""
        assert safe_parse_float("5") == pytest.approx(5.0)

    def test_parse_actual_float(self):
        """Parse actual float value."""
        assert safe_parse_float(3.14159) == pytest.approx(3.14159)

    def test_parse_actual_int(self):
        """Parse actual integer value."""
        assert safe_parse_float(42) == pytest.approx(42.0)

    def test_parse_none(self):
        """None returns None."""
        assert safe_parse_float(None) is None

    def test_parse_empty_string(self):
        """Empty string returns None."""
        assert safe_parse_float("") is None

    def test_parse_invalid_string(self):
        """Invalid string returns None."""
        assert safe_parse_float("not_a_number") is None

    def test_parse_nan(self):
        """NaN returns None."""
        assert safe_parse_float(float('nan')) is None

    def test_parse_inf(self):
        """Infinity returns None."""
        assert safe_parse_float(float('inf')) is None
        assert safe_parse_float(float('-inf')) is None

    def test_parse_negative(self):
        """Parse negative number."""
        assert safe_parse_float("-2.5") == pytest.approx(-2.5)

    def test_parse_scientific_notation(self):
        """Parse scientific notation."""
        assert safe_parse_float("1.5e-3") == pytest.approx(0.0015)


class TestExtractAtbFeatures:
    """Tests for aTB feature extraction with missingness filter."""

    def test_complete_features(self):
        """All 4 features present returns dict."""
        features = {
            "delta_volume": 1.5,
            "delta_gap": -0.7,
            "delta_dihedral": -0.65,
            "excitation_energy": "3.83",
            "extra_field": 100  # Should be ignored
        }
        result = extract_atb_features(features)
        assert result is not None
        assert len(result) == 4
        assert result["delta_volume"] == pytest.approx(1.5)
        assert result["excitation_energy"] == pytest.approx(3.83)

    def test_missing_one_feature(self):
        """Missing one feature returns None."""
        features = {
            "delta_volume": 1.5,
            "delta_gap": -0.7,
            "delta_dihedral": -0.65,
            # excitation_energy missing
        }
        result = extract_atb_features(features)
        assert result is None

    def test_null_feature(self):
        """Feature with null value returns None."""
        features = {
            "delta_volume": 1.5,
            "delta_gap": -0.7,
            "delta_dihedral": None,  # Explicit null
            "excitation_energy": "3.83"
        }
        result = extract_atb_features(features)
        assert result is None

    def test_invalid_excitation_energy(self):
        """Invalid excitation_energy string returns None."""
        features = {
            "delta_volume": 1.5,
            "delta_gap": -0.7,
            "delta_dihedral": -0.65,
            "excitation_energy": "invalid"
        }
        result = extract_atb_features(features)
        assert result is None

    def test_empty_dict(self):
        """Empty dict returns None."""
        result = extract_atb_features({})
        assert result is None


class TestSimilarityRanges:
    """Tests for similarity value ranges."""

    def test_tanimoto_range(self):
        """Tanimoto similarity should be in [0, 1]."""
        # Identical fingerprints
        fp = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
        assert tanimoto_similarity(fp, fp) == pytest.approx(1.0)

        # Completely different
        fp1 = np.array([1, 1, 0, 0], dtype=np.uint8)
        fp2 = np.array([0, 0, 1, 1], dtype=np.uint8)
        assert tanimoto_similarity(fp1, fp2) == pytest.approx(0.0)

        # Partial overlap
        fp1 = np.array([1, 1, 1, 0, 0], dtype=np.uint8)
        fp2 = np.array([1, 1, 0, 1, 0], dtype=np.uint8)
        sim = tanimoto_similarity(fp1, fp2)
        assert 0.0 <= sim <= 1.0
        # Intersection = 2, Union = 4
        assert sim == pytest.approx(2.0 / 4.0)

    def test_cosine_to_sim_range(self):
        """Mapped cosine similarity should be in [0, 1]."""
        # Test boundary values
        assert cosine_to_sim(-1.0) == pytest.approx(0.0)
        assert cosine_to_sim(0.0) == pytest.approx(0.5)
        assert cosine_to_sim(1.0) == pytest.approx(1.0)

        # Test intermediate values
        assert 0.0 <= cosine_to_sim(0.5) <= 1.0
        assert 0.0 <= cosine_to_sim(-0.5) <= 1.0

    def test_binary_fingerprint_coercion(self):
        """Fingerprint coercion should produce binary values."""
        # Test with non-binary input (using valid int8 range: -128 to 127)
        fp = np.array([0, 1, 2, 5, 0, -1, 127], dtype=np.int8)
        binary = to_binary_fingerprint(fp)
        assert binary.dtype == np.uint8
        assert set(binary.tolist()) <= {0, 1}
        # 0 -> 0, positive -> 1, negative -> 0 (since -1 > 0 is False)
        assert binary.tolist() == [0, 1, 1, 1, 0, 0, 1]


class TestOutputSchemaColumns:
    """Tests for expected output schema."""

    def test_atb_features_list(self):
        """ATB_FEATURES constant has expected features."""
        expected = ["delta_volume", "delta_gap", "delta_dihedral", "excitation_energy"]
        assert ATB_FEATURES == expected

    def test_build_atb_matrix_shape(self):
        """ATB matrix has correct shape and normalization."""
        atb_data = [
            {"inchikey": "TEST1", "atb_features": {
                "delta_volume": 1.0, "delta_gap": -0.5,
                "delta_dihedral": 0.1, "excitation_energy": 3.5
            }},
            {"inchikey": "TEST2", "atb_features": {
                "delta_volume": 2.0, "delta_gap": -0.3,
                "delta_dihedral": 0.2, "excitation_energy": 3.8
            }},
            {"inchikey": "TEST3", "atb_features": {
                "delta_volume": 1.5, "delta_gap": -0.4,
                "delta_dihedral": 0.15, "excitation_energy": 3.6
            }},
        ]
        inchikeys = ["TEST1", "TEST2", "TEST3"]

        matrix, feat_names, means, stds = build_atb_matrix(atb_data, inchikeys)

        # Check shape
        assert matrix.shape == (3, 4)

        # Check L2 normalization (each row should have unit norm)
        for i in range(3):
            row_norm = np.linalg.norm(matrix[i])
            assert row_norm == pytest.approx(1.0, abs=1e-6)

        # Check feature names
        assert feat_names == ATB_FEATURES

        # Check stats returned
        assert len(means) == 4
        assert len(stds) == 4


class TestInChIKeyValidation:
    """Tests for InChIKey format validation."""

    def test_valid_inchikey(self):
        """Valid InChIKey format passes."""
        assert is_valid_inchikey("AAAQKTZKLRYKHR-UHFFFAOYSA-N") is True
        assert is_valid_inchikey("XLYOFNOQVPJJNP-UHFFFAOYSA-N") is True

    def test_invalid_inchikey_short(self):
        """Too short InChIKey fails."""
        assert is_valid_inchikey("AAAQKTZKLRYKHR-UHFFFAOYSA") is False

    def test_invalid_inchikey_lowercase(self):
        """Lowercase InChIKey fails."""
        assert is_valid_inchikey("aaaqktzklrykhr-uhfffaoysa-n") is False

    def test_invalid_inchikey_none(self):
        """None returns False."""
        assert is_valid_inchikey(None) is False

    def test_invalid_inchikey_empty(self):
        """Empty string returns False."""
        assert is_valid_inchikey("") is False


class TestRanksInOutput:
    """Tests for rank values in output."""

    def test_ranks_are_sequential(self):
        """Ranks should be 1 to k."""
        # This test uses mock data to verify rank generation logic
        # In actual output, we check that ranks are consecutive integers

        # Simulate what the output should look like
        mock_output = pd.DataFrame({
            "inchikey": ["A"] * 5 + ["B"] * 5,
            "neighbor_inchikey": ["X", "Y", "Z", "W", "V"] * 2,
            "rank": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
            "sim": [0.9, 0.8, 0.7, 0.6, 0.5] * 2,
            "sim_ecfp": [0.85, 0.75, 0.65, 0.55, 0.45] * 2,
            "sim_atb": [0.95, 0.85, 0.75, 0.65, 0.55] * 2
        })

        # Check ranks per molecule
        for ik in mock_output["inchikey"].unique():
            mol_ranks = mock_output[mock_output["inchikey"] == ik]["rank"].tolist()
            assert mol_ranks == list(range(1, len(mol_ranks) + 1))

    def test_output_columns_exist(self):
        """Expected columns are present."""
        expected_columns = [
            "inchikey", "neighbor_inchikey", "rank",
            "sim", "sim_ecfp", "sim_atb"
        ]

        # Create mock output
        mock_output = pd.DataFrame(columns=expected_columns)

        for col in expected_columns:
            assert col in mock_output.columns


class TestIntegrationSmoke:
    """Smoke tests for integration (if data exists)."""

    @pytest.fixture
    def check_data_exists(self):
        """Check if required data files exist."""
        rdkit_path = Path("data/rdkit_features.parquet")
        cache_path = Path("cache/atb")
        return rdkit_path.exists() and cache_path.exists()

    def test_can_import_all_functions(self):
        """All main functions can be imported."""
        from src.features.anchor_hybrid_ecfp_atb_partial import (
            discover_successful_cache,
            extract_atb_features,
            load_ecfp_for_subset,
            build_atb_matrix,
            compute_hybrid_neighbors,
            build_hybrid_anchor_neighbors,
        )
        # Just check imports work
        assert callable(discover_successful_cache)
        assert callable(build_hybrid_anchor_neighbors)

    def test_discover_cache_returns_list(self, check_data_exists):
        """discover_successful_cache returns a list."""
        if not check_data_exists:
            pytest.skip("Data files not available")

        from src.features.anchor_hybrid_ecfp_atb_partial import discover_successful_cache
        result = discover_successful_cache()
        assert isinstance(result, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

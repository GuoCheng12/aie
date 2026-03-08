"""
tests/test_anchor_ecfp.py

Unit tests for ECFP anchor neighbor computation.
"""

import numpy as np
import pandas as pd
import pytest
import tempfile
from pathlib import Path

from src.features.anchor_ecfp import (
    is_valid_inchikey,
    to_binary_fingerprint,
    tanimoto_similarity,
    compute_all_neighbors,
    load_ecfp_data,
)


class TestInChIKeyFiltering:
    """Tests for InChIKey validation."""

    def test_valid_inchikey(self):
        """Valid InChIKey should pass."""
        assert is_valid_inchikey("AAAQKTZKLRYKHR-UHFFFAOYSA-N") is True
        assert is_valid_inchikey("CVWRQIXEYCUPJM-UHFFFAOYSA-N") is True

    def test_invalid_inchikey_empty(self):
        """Empty string should fail."""
        assert is_valid_inchikey("") is False

    def test_invalid_inchikey_none(self):
        """None should fail."""
        assert is_valid_inchikey(None) is False

    def test_invalid_inchikey_short(self):
        """Too short InChIKey should fail."""
        assert is_valid_inchikey("AAAQ-UHFF-N") is False

    def test_invalid_inchikey_lowercase(self):
        """Lowercase InChIKey should fail."""
        assert is_valid_inchikey("aaaqktzklrykhr-uhfffaoysa-n") is False

    def test_invalid_inchikey_wrong_format(self):
        """Wrong format should fail."""
        assert is_valid_inchikey("NOTANINCHIKEY") is False
        assert is_valid_inchikey("12345678901234-1234567890-A") is False


class TestTanimotoComputation:
    """Tests for Tanimoto similarity computation."""

    def test_identical_fingerprints(self):
        """Identical fingerprints should have similarity 1.0."""
        fp = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
        assert tanimoto_similarity(fp, fp) == pytest.approx(1.0)

    def test_disjoint_fingerprints(self):
        """Completely different fingerprints should have similarity 0.0."""
        fp1 = np.array([1, 1, 0, 0], dtype=np.uint8)
        fp2 = np.array([0, 0, 1, 1], dtype=np.uint8)
        assert tanimoto_similarity(fp1, fp2) == pytest.approx(0.0)

    def test_partial_overlap(self):
        """Partial overlap should give intermediate similarity."""
        fp1 = np.array([1, 1, 1, 0], dtype=np.uint8)  # 3 bits set
        fp2 = np.array([1, 1, 0, 0], dtype=np.uint8)  # 2 bits set, both overlap
        # Intersection = 2, Union = 3
        # Tanimoto = 2/3 = 0.666...
        assert tanimoto_similarity(fp1, fp2) == pytest.approx(2.0 / 3.0)

    def test_zero_fingerprints(self):
        """Two all-zero fingerprints should return 0 (undefined case)."""
        fp1 = np.zeros(8, dtype=np.uint8)
        fp2 = np.zeros(8, dtype=np.uint8)
        assert tanimoto_similarity(fp1, fp2) == 0.0

    def test_one_zero_one_nonzero(self):
        """One zero, one non-zero should return 0."""
        fp1 = np.zeros(8, dtype=np.uint8)
        fp2 = np.array([1, 1, 0, 0, 0, 0, 0, 0], dtype=np.uint8)
        assert tanimoto_similarity(fp1, fp2) == 0.0

    def test_similarity_range(self):
        """Tanimoto should always be in [0, 1]."""
        np.random.seed(42)
        for _ in range(100):
            fp1 = np.random.randint(0, 2, size=2048).astype(np.uint8)
            fp2 = np.random.randint(0, 2, size=2048).astype(np.uint8)
            sim = tanimoto_similarity(fp1, fp2)
            assert 0.0 <= sim <= 1.0

    def test_symmetry(self):
        """Tanimoto(A, B) should equal Tanimoto(B, A)."""
        np.random.seed(123)
        fp1 = np.random.randint(0, 2, size=64).astype(np.uint8)
        fp2 = np.random.randint(0, 2, size=64).astype(np.uint8)
        assert tanimoto_similarity(fp1, fp2) == pytest.approx(tanimoto_similarity(fp2, fp1))


class TestBinaryFingerprint:
    """Tests for fingerprint coercion to binary."""

    def test_already_binary(self):
        """Already binary (0/1) should stay the same."""
        fp = np.array([0, 1, 0, 1, 1], dtype=np.int8)
        result = to_binary_fingerprint(fp)
        expected = np.array([0, 1, 0, 1, 1], dtype=np.uint8)
        np.testing.assert_array_equal(result, expected)

    def test_non_binary_coercion(self):
        """Values > 0 should become 1, values <= 0 should become 0."""
        fp = np.array([0, 1, 2, 5, -1, 0], dtype=np.int8)
        result = to_binary_fingerprint(fp)
        expected = np.array([0, 1, 1, 1, 0, 0], dtype=np.uint8)
        np.testing.assert_array_equal(result, expected)

    def test_none_input(self):
        """None input should return None."""
        assert to_binary_fingerprint(None) is None


class TestNeighborOutput:
    """Tests for neighbor computation output format."""

    def test_neighbor_schema(self):
        """Output should have correct columns."""
        # Create small test dataset
        df = pd.DataFrame({
            "inchikey": ["AAAA-BBBB-C", "DDDD-EEEE-F", "GGGG-HHHH-I"],
            "ecfp_2048": [
                np.array([1, 0, 1, 0], dtype=np.int8),
                np.array([1, 1, 0, 0], dtype=np.int8),
                np.array([0, 1, 1, 0], dtype=np.int8),
            ],
        })

        result = compute_all_neighbors(df, k=2)

        # Check columns exist
        assert "inchikey" in result.columns
        assert "neighbor_inchikey" in result.columns
        assert "rank" in result.columns
        assert "tanimoto_sim" in result.columns

    def test_neighbor_count(self):
        """Each molecule should have exactly k neighbors (excluding self)."""
        df = pd.DataFrame({
            "inchikey": ["A", "B", "C", "D"],
            "ecfp_2048": [
                np.array([1, 0, 0, 0], dtype=np.int8),
                np.array([0, 1, 0, 0], dtype=np.int8),
                np.array([0, 0, 1, 0], dtype=np.int8),
                np.array([0, 0, 0, 1], dtype=np.int8),
            ],
        })

        result = compute_all_neighbors(df, k=2)

        # 4 molecules, each with 2 neighbors = 8 records
        assert len(result) == 8

        # Each molecule should have exactly 2 neighbors
        for ik in ["A", "B", "C", "D"]:
            assert len(result[result["inchikey"] == ik]) == 2

    def test_self_excluded(self):
        """Self should not appear as a neighbor."""
        df = pd.DataFrame({
            "inchikey": ["A", "B", "C"],
            "ecfp_2048": [
                np.array([1, 1, 1, 1], dtype=np.int8),  # Same fingerprint
                np.array([1, 1, 1, 1], dtype=np.int8),  # Same fingerprint
                np.array([1, 1, 1, 1], dtype=np.int8),  # Same fingerprint
            ],
        })

        result = compute_all_neighbors(df, k=2)

        # No molecule should have itself as neighbor
        for _, row in result.iterrows():
            assert row["inchikey"] != row["neighbor_inchikey"]

    def test_rank_ordering(self):
        """Rank 1 should have highest similarity."""
        df = pd.DataFrame({
            "inchikey": ["A", "B", "C"],
            "ecfp_2048": [
                np.array([1, 1, 0, 0], dtype=np.int8),
                np.array([1, 1, 1, 0], dtype=np.int8),  # More similar to A
                np.array([1, 0, 0, 0], dtype=np.int8),  # Less similar to A
            ],
        })

        result = compute_all_neighbors(df, k=2)

        # For molecule A, rank 1 should have higher sim than rank 2
        a_neighbors = result[result["inchikey"] == "A"].sort_values("rank")
        sims = a_neighbors["tanimoto_sim"].tolist()
        assert sims[0] >= sims[1], "Rank 1 should have highest similarity"

    def test_similarity_values_in_range(self):
        """All similarity values should be in [0, 1]."""
        np.random.seed(42)
        df = pd.DataFrame({
            "inchikey": [f"IK{i}" for i in range(10)],
            "ecfp_2048": [np.random.randint(0, 2, size=64).astype(np.int8) for _ in range(10)],
        })

        result = compute_all_neighbors(df, k=5)

        assert result["tanimoto_sim"].min() >= 0.0
        assert result["tanimoto_sim"].max() <= 1.0


class TestLoadECFPData:
    """Tests for data loading with InChIKey filtering."""

    def test_load_filters_invalid_inchikeys(self):
        """Invalid InChIKeys should be filtered out."""
        # Create temp parquet with mix of valid/invalid
        df = pd.DataFrame({
            "inchikey": [
                "AAAQKTZKLRYKHR-UHFFFAOYSA-N",  # valid
                "",  # invalid (empty)
                "INVALID",  # invalid (wrong format)
                "CVWRQIXEYCUPJM-UHFFFAOYSA-N",  # valid
            ],
            "ecfp_2048": [
                np.zeros(8, dtype=np.int8),
                np.zeros(8, dtype=np.int8),
                np.zeros(8, dtype=np.int8),
                np.zeros(8, dtype=np.int8),
            ],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.parquet"
            df.to_parquet(path)

            result = load_ecfp_data(str(path))

            # Should have only 2 valid InChIKeys
            assert len(result) == 2
            assert "AAAQKTZKLRYKHR-UHFFFAOYSA-N" in result["inchikey"].values
            assert "CVWRQIXEYCUPJM-UHFFFAOYSA-N" in result["inchikey"].values


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

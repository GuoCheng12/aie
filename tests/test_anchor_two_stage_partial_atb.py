"""
tests/test_anchor_two_stage_partial_atb.py

Unit tests for two-stage retrieval implementation.
"""

import numpy as np
import pandas as pd
import pytest

from src.features.anchor_two_stage_partial_atb import (
    safe_parse_float,
    extract_atb_features,
    to_binary_fingerprint,
    tanimoto_similarity,
    cosine_to_sim,
    is_valid_inchikey,
    build_atb_matrix,
    compute_two_stage_neighbors,
    ATB_FEATURES,
)


class TestTwoStageRetrievalLogic:
    """Tests for two-stage retrieval logic."""

    def test_stage1_candidate_restriction(self):
        """Stage 1 should restrict to top-M candidates by ECFP."""
        # Create mock data
        n = 10
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        # Mock ECFP matrix (random binary)
        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)

        # Mock aTB matrix (random L2-normalized)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        # Test with M=3 (should only consider top-3 ECFP candidates)
        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=3,
            k=2,
            w_ecfp=0.7,
            w_atb=0.3
        )

        # Check stage1_rank values
        stage1_ranks = result["stage1_rank"].values
        assert np.max(stage1_ranks) <= 3, "Stage 1 rank should not exceed M=3"
        assert np.min(stage1_ranks) >= 1, "Stage 1 rank should be 1-indexed"

    def test_output_schema(self):
        """Output DataFrame should have required columns."""
        n = 5
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=4,
            k=2,
            w_ecfp=0.7,
            w_atb=0.3
        )

        required_cols = [
            "inchikey", "neighbor_inchikey", "rank",
            "sim", "sim_ecfp", "sim_atb", "stage1_rank"
        ]
        for col in required_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_ranks_sequential(self):
        """Ranks should be 1 to k for each query."""
        n = 5
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        k = 3
        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=4,
            k=k,
            w_ecfp=0.7,
            w_atb=0.3
        )

        # Check ranks for each query
        for ik in inchikeys:
            query_ranks = result[result["inchikey"] == ik]["rank"].tolist()
            assert query_ranks == list(range(1, k + 1)), f"Ranks not sequential for {ik}"

    def test_no_self_neighbors(self):
        """Neighbors should not include query itself."""
        n = 5
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=4,
            k=2,
            w_ecfp=0.7,
            w_atb=0.3
        )

        # Check no self-neighbors
        for _, row in result.iterrows():
            assert row["inchikey"] != row["neighbor_inchikey"], "Self-neighbor detected"

    def test_similarity_in_range(self):
        """All similarity values should be in [0, 1]."""
        n = 5
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=4,
            k=2,
            w_ecfp=0.7,
            w_atb=0.3
        )

        # Check similarity ranges
        assert result["sim"].min() >= 0.0, "sim < 0 detected"
        assert result["sim"].max() <= 1.0, "sim > 1 detected"
        assert result["sim_ecfp"].min() >= 0.0, "sim_ecfp < 0 detected"
        assert result["sim_ecfp"].max() <= 1.0, "sim_ecfp > 1 detected"
        assert result["sim_atb"].min() >= 0.0, "sim_atb < 0 detected"
        assert result["sim_atb"].max() <= 1.0, "sim_atb > 1 detected"

    def test_fused_similarity_calculation(self):
        """Fused similarity should match weighted combination."""
        n = 5
        inchikeys = [f"IK{i:03d}" for i in range(n)]

        np.random.seed(42)
        ecfp_matrix = np.random.randint(0, 2, size=(n, 2048), dtype=np.uint8)
        atb_matrix = np.random.randn(n, 4)
        atb_matrix = atb_matrix / np.linalg.norm(atb_matrix, axis=1, keepdims=True)

        w_ecfp = 0.7
        w_atb = 0.3

        result = compute_two_stage_neighbors(
            ecfp_matrix=ecfp_matrix,
            atb_matrix=atb_matrix,
            inchikeys=inchikeys,
            M=4,
            k=2,
            w_ecfp=w_ecfp,
            w_atb=w_atb
        )

        # Check fused similarity calculation
        for _, row in result.iterrows():
            expected_sim = w_ecfp * row["sim_ecfp"] + w_atb * row["sim_atb"]
            assert abs(row["sim"] - expected_sim) < 1e-6, "Fused similarity mismatch"


class TestHelperFunctions:
    """Tests for helper functions (reused from hybrid tests)."""

    def test_safe_parse_float(self):
        """Parse float from various inputs."""
        assert safe_parse_float("3.14") == pytest.approx(3.14)
        assert safe_parse_float(42) == pytest.approx(42.0)
        assert safe_parse_float(None) is None
        assert safe_parse_float("") is None
        assert safe_parse_float("invalid") is None
        assert safe_parse_float(float('nan')) is None

    def test_extract_atb_features(self):
        """Extract aTB features with missingness check."""
        complete = {
            "delta_volume": 1.5,
            "delta_gap": -0.7,
            "delta_dihedral": -0.65,
            "excitation_energy": "3.83"
        }
        result = extract_atb_features(complete)
        assert result is not None
        assert len(result) == 4

        # Missing one feature
        incomplete = complete.copy()
        del incomplete["delta_gap"]
        assert extract_atb_features(incomplete) is None

    def test_tanimoto_similarity(self):
        """Tanimoto similarity in [0, 1]."""
        fp1 = np.array([1, 0, 1, 1, 0], dtype=np.uint8)
        fp2 = np.array([1, 1, 0, 1, 0], dtype=np.uint8)

        sim = tanimoto_similarity(fp1, fp2)
        assert 0.0 <= sim <= 1.0

        # Identical
        assert tanimoto_similarity(fp1, fp1) == pytest.approx(1.0)

    def test_cosine_to_sim(self):
        """Map cosine [-1, 1] to [0, 1]."""
        assert cosine_to_sim(-1.0) == pytest.approx(0.0)
        assert cosine_to_sim(0.0) == pytest.approx(0.5)
        assert cosine_to_sim(1.0) == pytest.approx(1.0)

    def test_is_valid_inchikey(self):
        """Validate InChIKey format."""
        assert is_valid_inchikey("AAAQKTZKLRYKHR-UHFFFAOYSA-N") is True
        assert is_valid_inchikey("invalid") is False
        assert is_valid_inchikey(None) is False


class TestAtbMatrixBuilder:
    """Tests for aTB matrix building."""

    def test_build_atb_matrix_shape(self):
        """aTB matrix should have correct shape."""
        atb_data = [
            {"inchikey": "TEST1", "atb_features": {
                "delta_volume": 1.0, "delta_gap": -0.5,
                "delta_dihedral": 0.1, "excitation_energy": 3.5
            }},
            {"inchikey": "TEST2", "atb_features": {
                "delta_volume": 2.0, "delta_gap": -0.3,
                "delta_dihedral": 0.2, "excitation_energy": 3.8
            }},
        ]
        inchikeys = ["TEST1", "TEST2"]

        matrix, feat_names, means, stds = build_atb_matrix(atb_data, inchikeys)

        assert matrix.shape == (2, 4)
        assert feat_names == ATB_FEATURES

        # Check L2 normalization
        for i in range(2):
            row_norm = np.linalg.norm(matrix[i])
            assert row_norm == pytest.approx(1.0, abs=1e-6)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

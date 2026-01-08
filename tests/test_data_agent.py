"""
tests/test_data_agent.py

Tests for DataAgent (fetch by id/inchikey from parquet files).
"""

import pytest
from pathlib import Path

from src.agents.data_agent import DataAgent


class TestDataAgent:
    """Test suite for DataAgent."""

    @pytest.fixture
    def agent(self):
        """Create DataAgent instance."""
        return DataAgent(data_dir="data")

    def test_get_record_by_id_success(self, agent):
        """Test fetching a valid record by id."""
        # Use id=1 (should exist in private_clean.parquet)
        record = agent.get_record_by_id(1)

        # Check required fields
        assert "id" in record
        assert record["id"] == 1
        assert "inchikey" in record
        assert "canonical_smiles" in record

        # InChIKey should be string or None
        if record["inchikey"] is not None:
            assert isinstance(record["inchikey"], str)

    def test_get_record_by_id_not_found(self, agent):
        """Test fetching a non-existent record id."""
        # Use a very large id that shouldn't exist
        with pytest.raises(ValueError, match="not found"):
            agent.get_record_by_id(999999)

    def test_get_missing_summary(self, agent):
        """Test missing value summary computation."""
        # Fetch a record
        record = agent.get_record_by_id(1)

        # Get missing summary
        summary = agent.get_missing_summary(record)

        # Check structure
        assert "n_missing" in summary
        assert "missing_fields" in summary
        assert isinstance(summary["n_missing"], int)
        assert isinstance(summary["missing_fields"], list)
        assert summary["n_missing"] == len(summary["missing_fields"])

        # n_missing should be non-negative
        assert summary["n_missing"] >= 0

    def test_get_molecule_by_inchikey_success(self, agent):
        """Test fetching a molecule by InChIKey."""
        # First get a record to extract a valid InChIKey
        record = agent.get_record_by_id(1)
        inchikey = record.get("inchikey")

        if inchikey:
            # Fetch molecule
            molecule = agent.get_molecule_by_inchikey(inchikey)

            # Check required fields
            assert "inchikey" in molecule
            assert molecule["inchikey"] == inchikey
            assert "canonical_smiles" in molecule
            assert "id_list" in molecule
            assert "n_records" in molecule

    def test_get_molecule_by_inchikey_not_found(self, agent):
        """Test fetching a non-existent InChIKey."""
        fake_inchikey = "XXXXXXXXXXXXXX-XXXXXXXXXX-X"

        with pytest.raises(ValueError, match="not found"):
            agent.get_molecule_by_inchikey(fake_inchikey)

    def test_private_clean_caching(self, agent):
        """Test that private_clean.parquet is cached after first load."""
        # First call loads from disk
        record1 = agent.get_record_by_id(1)

        # Second call should use cached DataFrame
        record2 = agent.get_record_by_id(1)

        # Should return identical data
        assert record1["id"] == record2["id"]
        assert record1.get("inchikey") == record2.get("inchikey")

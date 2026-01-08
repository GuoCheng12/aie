"""
tests/test_atb_agent.py

Tests for ATBAgent (cache management for aTB computations).
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path

from src.agents.atb_agent import ATBAgent


class TestATBAgent:
    """Test suite for ATBAgent."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory for testing."""
        temp_dir = tempfile.mkdtemp(prefix="test_atb_cache_")
        yield temp_dir
        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def agent(self, temp_cache_dir):
        """Create ATBAgent instance with temp cache dir."""
        return ATBAgent(cache_dir=temp_cache_dir)

    def test_get_cache_path(self, agent):
        """Test cache path generation with 2-char prefix."""
        inchikey = "ABCDEFGHIJ-KLMNOPQRST-U"
        cache_path = agent.get_cache_path(inchikey)

        # Check structure: cache_dir/AB/ABCDEFGHIJ-KLMNOPQRST-U/
        assert cache_path.name == inchikey
        assert cache_path.parent.name == "AB"

    def test_get_cache_path_invalid_inchikey(self, agent):
        """Test cache path generation with invalid InChIKey."""
        with pytest.raises(ValueError, match="Invalid InChIKey"):
            agent.get_cache_path("X")

        with pytest.raises(ValueError, match="Invalid InChIKey"):
            agent.get_cache_path("")

    def test_check_cache_miss(self, agent):
        """Test cache check when cache doesn't exist."""
        inchikey = "TESTINCHIK-EYDOESNTEXI-S"
        exists = agent.check_cache(inchikey)

        assert exists is False

    def test_mark_pending(self, agent):
        """Test creating pending status.json."""
        inchikey = "TESTINCHIK-EYPENDINGTES-T"
        smiles = "C1=CC=CC=C1"

        # Mark as pending
        status_file = agent.mark_pending(inchikey, smiles)

        # Check file was created
        assert status_file.exists()

        # Load and verify content
        with open(status_file, "r") as f:
            status = json.load(f)

        assert status["inchikey"] == inchikey
        assert status["run_status"] == "pending"
        assert status["fail_stage"] is None
        assert status["error_msg"] is None
        assert status["atb_version"] is None
        assert status["runtime_sec"] is None
        assert "timestamp" in status

    def test_mark_pending_strict_schema(self, agent):
        """Test that status.json adheres to strict schema (no extra fields)."""
        inchikey = "TESTINCHIK-EYSTRICTSCHEM-A"
        smiles = "CCO"

        # Mark as pending
        status_file = agent.mark_pending(inchikey, smiles)

        # Load status.json
        with open(status_file, "r") as f:
            status = json.load(f)

        # Verify EXACT schema compliance (doc/process.md P2)
        required_fields = {
            "inchikey", "run_status", "fail_stage",
            "error_msg", "timestamp", "atb_version", "runtime_sec"
        }

        # Check all required fields present
        for field in required_fields:
            assert field in status, f"Required field '{field}' missing from status.json"

        # Check no extra fields (strict schema)
        extra_fields = set(status.keys()) - required_fields
        assert len(extra_fields) == 0, f"Extra fields in status.json: {extra_fields}"

        # SMILES should be stored separately, NOT in status.json
        assert "canonical_smiles" not in status
        assert "note" not in status

        # Verify SMILES stored separately
        cache_path = agent.get_cache_path(inchikey)
        smiles_file = cache_path / "canonical_smiles.txt"
        assert smiles_file.exists()
        with open(smiles_file, "r") as f:
            stored_smiles = f.read().strip()
        assert stored_smiles == smiles

    def test_check_cache_hit_after_mark_pending(self, agent):
        """Test cache check after marking as pending."""
        inchikey = "TESTINCHIK-EYCACHETESTH-I"

        # Initially no cache
        assert agent.check_cache(inchikey) is False

        # Mark as pending
        agent.mark_pending(inchikey)

        # Now cache should exist
        assert agent.check_cache(inchikey) is True

    def test_load_status(self, agent):
        """Test loading status.json."""
        inchikey = "TESTINCHIK-EYLOADSTATUS-T"
        smiles = "CCO"

        # Create pending status
        agent.mark_pending(inchikey, smiles)

        # Load it back
        status = agent.load_status(inchikey)

        assert status["inchikey"] == inchikey
        assert status["run_status"] == "pending"

    def test_load_status_not_found(self, agent):
        """Test loading status for non-existent cache."""
        inchikey = "TESTINCHIK-EYNOTFOUNDST-A"

        with pytest.raises(FileNotFoundError, match="status.json not found"):
            agent.load_status(inchikey)

    def test_get_cache_summary(self, agent):
        """Test cache summary generation."""
        inchikey = "TESTINCHIK-EYSUMMARYTEST"

        # Before creating cache
        summary = agent.get_cache_summary(inchikey)
        assert summary["cache_exists"] is False
        assert summary["run_status"] is None

        # After marking pending
        agent.mark_pending(inchikey)

        summary = agent.get_cache_summary(inchikey)
        assert summary["cache_exists"] is True
        assert summary["run_status"] == "pending"
        assert summary["fail_stage"] is None
        assert summary["features_available"] is False

    def test_load_features_not_found(self, agent):
        """Test loading features when features.json doesn't exist."""
        inchikey = "TESTINCHIK-EYNOFEATUREST"

        # Mark as pending (creates status.json but not features.json)
        agent.mark_pending(inchikey)

        # Try to load features
        features = agent.load_features(inchikey)

        assert features is None

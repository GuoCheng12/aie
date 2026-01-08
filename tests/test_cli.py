"""
tests/test_cli.py

Tests for CLI report field filtering and schema compliance.
"""

import pytest
from src.cli import filter_record_fields, REPORT_FIELD_ALLOWLIST, REPORT_FIELD_BLOCKLIST


class TestCLIFieldFiltering:
    """Test suite for CLI field filtering."""

    def test_filter_record_fields_allowlist(self):
        """Test that only allowlisted fields are included."""
        record = {
            "id": 1,
            "inchikey": "TEST-INCHIKEY-X",
            "emission_sol": 450.0,
            "qy_sol": 0.65,
            "comment": "This is sensitive information that should be excluded",
            "random_field": "Should also be excluded",
        }

        filtered = filter_record_fields(record)

        # Allowed fields should be present
        assert "id" in filtered
        assert "inchikey" in filtered
        assert "emission_sol" in filtered
        assert "qy_sol" in filtered

        # Blocked/unlisted fields should NOT be present
        assert "comment" not in filtered
        assert "random_field" not in filtered

    def test_filter_record_fields_blocklist(self):
        """Test that blocklisted fields are explicitly excluded."""
        record = {
            "id": 1,
            "comment": "Sensitive researcher notes",
        }

        filtered = filter_record_fields(record)

        # ID should be present (allowlisted)
        assert "id" in filtered

        # comment should NOT be present (blocklisted)
        assert "comment" not in filtered

    def test_filter_record_fields_all_critical(self):
        """Test filtering includes all critical photophysical fields."""
        record = {
            "emission_sol": 450.0,
            "emission_solid": 500.0,
            "emission_aggr": 520.0,
            "emission_crys": 540.0,
            "qy_sol": 0.65,
            "qy_solid": 0.75,
            "qy_aggr": 0.85,
            "qy_crys": 0.95,
            "tau_sol": 3.2,
            "tau_solid": 4.5,
            "tau_aggr": 5.1,
            "tau_crys": 6.8,
            "absorption": 430.0,
            "tested_solvent": "THF",
        }

        filtered = filter_record_fields(record)

        # All critical fields should be present
        assert len(filtered) == 14
        for field in record.keys():
            assert field in filtered

    def test_filter_record_fields_missing_indicators(self):
        """Test that missing indicators are included."""
        record = {
            "id": 1,
            "emission_sol_missing": True,
            "qy_crys_missing": True,
            "tau_solid_missing": False,
        }

        filtered = filter_record_fields(record)

        assert "emission_sol_missing" in filtered
        assert "qy_crys_missing" in filtered
        assert "tau_solid_missing" in filtered

    def test_filter_record_fields_normalized_fields(self):
        """Test that normalized/raw fields are included."""
        record = {
            "qy_sol": 0.65,
            "qy_sol_raw": 65.0,
            "tau_sol_log": 0.505,
            "tau_sol_outlier": False,
            "qy_unit_inferred": "percent",
        }

        filtered = filter_record_fields(record)

        assert "qy_sol" in filtered
        assert "qy_sol_raw" in filtered
        assert "tau_sol_log" in filtered
        assert "tau_sol_outlier" in filtered
        assert "qy_unit_inferred" in filtered

    def test_blocklist_not_in_allowlist(self):
        """Verify blocklist items are not in allowlist (schema integrity check)."""
        allowlist_set = set(REPORT_FIELD_ALLOWLIST)
        blocklist_set = set(REPORT_FIELD_BLOCKLIST)

        # No overlap between allowlist and blocklist
        overlap = allowlist_set.intersection(blocklist_set)
        assert len(overlap) == 0, f"Blocklist items found in allowlist: {overlap}"

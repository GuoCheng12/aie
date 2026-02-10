"""
tests/test_uq_pre_atb.py

Unit tests for P5a pre-aTB UQ computation.

Tests:
- Score ranges in [0,1] for valid rows
- Invalid inchikey routes to Evidence-insufficient
- Router determinism given fixed thresholds
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Import the module under test
from src.uq.compute_uq_pre_atb import (
    compute_c_sim,
    compute_c_meta,
    compute_novelty,
    compute_aleatoric,
    compute_thresholds,
    compute_router_action,
    compute_recommended_next_steps,
    CRITICAL_FIELDS,
    MISSING_COLUMNS,
)


class TestCSim:
    """Tests for C_sim computation."""
    
    def test_c_sim_basic(self):
        """C_sim should be mean of top-k similarities."""
        neighbors = pd.DataFrame({
            'inchikey': ['AAA'] * 3,
            'neighbor_inchikey': ['BBB', 'CCC', 'DDD'],
            'rank': [1, 2, 3],
            'tanimoto_sim': [0.8, 0.6, 0.4]
        })
        
        result = compute_c_sim(neighbors)
        
        assert len(result) == 1
        assert result.iloc[0]['inchikey'] == 'AAA'
        assert np.isclose(result.iloc[0]['C_sim'], 0.6)  # mean(0.8, 0.6, 0.4)
        assert np.isclose(result.iloc[0]['top1_sim'], 0.8)
    
    def test_c_sim_multiple_molecules(self):
        """C_sim for multiple molecules."""
        neighbors = pd.DataFrame({
            'inchikey': ['AAA', 'AAA', 'BBB', 'BBB'],
            'neighbor_inchikey': ['X1', 'X2', 'Y1', 'Y2'],
            'rank': [1, 2, 1, 2],
            'tanimoto_sim': [0.9, 0.8, 0.5, 0.4]
        })
        
        result = compute_c_sim(neighbors)
        
        assert len(result) == 2
        aaa = result[result['inchikey'] == 'AAA'].iloc[0]
        bbb = result[result['inchikey'] == 'BBB'].iloc[0]
        
        assert np.isclose(aaa['C_sim'], 0.85)  # mean(0.9, 0.8)
        assert np.isclose(bbb['C_sim'], 0.45)  # mean(0.5, 0.4)
    
    def test_c_sim_in_range(self):
        """C_sim should be in [0, 1] for valid inputs."""
        neighbors = pd.DataFrame({
            'inchikey': ['AAA'] * 10,
            'neighbor_inchikey': [f'N{i}' for i in range(10)],
            'rank': list(range(1, 11)),
            'tanimoto_sim': [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05]
        })
        
        result = compute_c_sim(neighbors)
        
        assert result.iloc[0]['C_sim'] >= 0
        assert result.iloc[0]['C_sim'] <= 1


class TestCMeta:
    """Tests for C_meta computation."""
    
    def test_c_meta_no_missing(self):
        """C_meta should be 1.0 when no fields are missing."""
        record = pd.DataFrame([{
            'id': 1,
            'inchikey': 'AAA',
            **{col: False for col in MISSING_COLUMNS}
        }])
        
        result = compute_c_meta(record)
        
        assert len(result) == 1
        assert result.iloc[0]['C_meta'] == 1.0
        assert result.iloc[0]['missing_count'] == 0
    
    def test_c_meta_all_missing(self):
        """C_meta should be 0.0 when all fields are missing."""
        record = pd.DataFrame([{
            'id': 1,
            'inchikey': 'AAA',
            **{col: True for col in MISSING_COLUMNS}
        }])
        
        result = compute_c_meta(record)

        assert len(result) == 1
        assert result.iloc[0]['C_meta'] == 0.0
        assert result.iloc[0]['missing_count'] == len(MISSING_COLUMNS)
    
    def test_c_meta_half_missing(self):
        """C_meta should be 0.5 when half fields are missing."""
        record_data = {
            'id': 1,
            'inchikey': 'AAA',
        }
        # First half of fields missing, rest not
        half = len(MISSING_COLUMNS) // 2
        for i, col in enumerate(MISSING_COLUMNS):
            record_data[col] = (i < half)

        result = compute_c_meta(pd.DataFrame([record_data]))

        assert len(result) == 1
        expected_c_meta = 1 - (half / len(MISSING_COLUMNS))
        assert result.iloc[0]['C_meta'] == expected_c_meta
        assert result.iloc[0]['missing_count'] == half
    
    def test_c_meta_in_range(self):
        """C_meta should be in [0, 1]."""
        # Create various records with different missing counts
        records = []
        for i in range(15):
            record = {'id': i, 'inchikey': f'IK{i}'}
            for j, col in enumerate(MISSING_COLUMNS):
                record[col] = (j < i)  # i fields missing
            records.append(record)
        
        result = compute_c_meta(pd.DataFrame(records))
        
        assert all(result['C_meta'] >= 0)
        assert all(result['C_meta'] <= 1)


class TestNovelty:
    """Tests for novelty computation."""
    
    def test_novelty_raw_formula(self):
        """novelty_raw should be 1 - top1_sim."""
        c_sim_df = pd.DataFrame({
            'inchikey': ['AAA', 'BBB', 'CCC'],
            'C_sim': [0.5, 0.7, 0.3],
            'top1_sim': [0.8, 0.6, 1.0],
            'similarities': [[0.8, 0.4], [0.6, 0.7], [1.0, 0.3]]
        })
        
        novelty_df, _ = compute_novelty(c_sim_df)
        
        # novelty_raw = 1 - top1_sim
        aaa = novelty_df[novelty_df['inchikey'] == 'AAA'].iloc[0]
        bbb = novelty_df[novelty_df['inchikey'] == 'BBB'].iloc[0]
        ccc = novelty_df[novelty_df['inchikey'] == 'CCC'].iloc[0]
        
        assert np.isclose(aaa['novelty_raw'], 0.2)  # 1 - 0.8
        assert np.isclose(bbb['novelty_raw'], 0.4)  # 1 - 0.6
        assert np.isclose(ccc['novelty_raw'], 0.0)  # 1 - 1.0
    
    def test_novelty_in_range(self):
        """novelty should be in [0, 1] after normalization."""
        c_sim_df = pd.DataFrame({
            'inchikey': [f'IK{i}' for i in range(100)],
            'C_sim': np.random.uniform(0.2, 0.8, 100),
            'top1_sim': np.random.uniform(0.3, 0.9, 100),
            'similarities': [[0.5, 0.4, 0.3]] * 100
        })
        
        novelty_df, _ = compute_novelty(c_sim_df)
        
        assert all(novelty_df['novelty'] >= 0)
        assert all(novelty_df['novelty'] <= 1)


class TestAleatoric:
    """Tests for aleatoric computation."""
    
    def test_aleatoric_uniform_distribution(self):
        """Uniform similarities should give aleatoric close to 1.0 (max entropy)."""
        c_sim_df = pd.DataFrame({
            'inchikey': ['AAA'],
            'C_sim': [0.5],
            'top1_sim': [0.5],
            'similarities': [[0.5, 0.5, 0.5, 0.5, 0.5]]  # Uniform
        })
        
        result = compute_aleatoric(c_sim_df)
        
        # Uniform distribution -> max entropy -> aleatoric close to 1
        assert result.iloc[0]['aleatoric'] > 0.99
    
    def test_aleatoric_concentrated_distribution(self):
        """One dominant similarity should give low aleatoric."""
        c_sim_df = pd.DataFrame({
            'inchikey': ['AAA'],
            'C_sim': [0.5],
            'top1_sim': [0.99],
            'similarities': [[0.99, 0.001, 0.001, 0.001, 0.001]]  # Concentrated
        })
        
        result = compute_aleatoric(c_sim_df)
        
        # Concentrated distribution -> low entropy -> low aleatoric
        assert result.iloc[0]['aleatoric'] < 0.3
    
    def test_aleatoric_in_range(self):
        """aleatoric should be in [0, 1]."""
        c_sim_df = pd.DataFrame({
            'inchikey': [f'IK{i}' for i in range(50)],
            'C_sim': [0.5] * 50,
            'top1_sim': [0.5] * 50,
            'similarities': [np.random.uniform(0.1, 0.9, 10).tolist() for _ in range(50)]
        })
        
        result = compute_aleatoric(c_sim_df)
        
        assert all(result['aleatoric'] >= 0)
        assert all(result['aleatoric'] <= 1)
    
    def test_aleatoric_zero_sum(self):
        """When all similarities are 0, aleatoric should be 1.0."""
        c_sim_df = pd.DataFrame({
            'inchikey': ['AAA'],
            'C_sim': [0.0],
            'top1_sim': [0.0],
            'similarities': [[0.0, 0.0, 0.0]]
        })
        
        result = compute_aleatoric(c_sim_df)
        
        assert result.iloc[0]['aleatoric'] == 1.0


class TestRouterAction:
    """Tests for router action logic."""
    
    def create_thresholds(self):
        """Create standard thresholds for testing."""
        return {
            'cov_low': 0.4,
            'cov_high': 0.7,
            'nov_high': 0.6,
            'ale_high': 0.6
        }
    
    def test_evidence_insufficient_nan_c_sim(self):
        """NaN C_sim should route to Evidence-insufficient."""
        row = pd.Series({
            'C_sim': np.nan,
            'coverage': np.nan,
            'novelty': 0.5,
            'aleatoric': 0.5
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "Evidence-insufficient"
    
    def test_evidence_insufficient_low_coverage(self):
        """Low coverage should route to Evidence-insufficient."""
        row = pd.Series({
            'C_sim': 0.3,
            'coverage': 0.3,  # Below cov_low (0.4)
            'novelty': 0.5,
            'aleatoric': 0.5
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "Evidence-insufficient"
    
    def test_novelty_candidate(self):
        """High novelty + medium coverage should route to Novelty-candidate."""
        row = pd.Series({
            'C_sim': 0.5,
            'coverage': 0.5,  # Between cov_low and cov_high
            'novelty': 0.7,   # Above nov_high
            'aleatoric': 0.5
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "Novelty-candidate"
    
    def test_novelty_candidate_high_aleatoric(self):
        """High novelty + high aleatoric should route to Novelty-candidate."""
        row = pd.Series({
            'C_sim': 0.8,
            'coverage': 0.8,  # Above cov_high
            'novelty': 0.7,   # Above nov_high
            'aleatoric': 0.7  # Above ale_high
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "Novelty-candidate"
    
    def test_in_domain_ambiguous(self):
        """High aleatoric but low novelty should route to In-domain ambiguous."""
        row = pd.Series({
            'C_sim': 0.6,
            'coverage': 0.5,
            'novelty': 0.4,   # Below nov_high
            'aleatoric': 0.7  # Above ale_high
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "In-domain ambiguous"
    
    def test_known_stable(self):
        """Normal scores should route to Known/Stable."""
        row = pd.Series({
            'C_sim': 0.7,
            'coverage': 0.6,
            'novelty': 0.4,   # Below nov_high
            'aleatoric': 0.4  # Below ale_high
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        assert action == "Known/Stable"
    
    def test_known_stable_high_coverage_blocks_novelty(self):
        """High coverage alone should block novelty claims (conservative gate)."""
        row = pd.Series({
            'C_sim': 0.9,
            'coverage': 0.85,  # Above cov_high
            'novelty': 0.7,    # Above nov_high
            'aleatoric': 0.4   # Below ale_high
        })
        
        action = compute_router_action(row, self.create_thresholds())
        
        # High coverage + low aleatoric blocks novelty claim
        assert action == "Known/Stable"
    
    def test_router_determinism(self):
        """Same inputs should always produce same outputs."""
        row = pd.Series({
            'C_sim': 0.5,
            'coverage': 0.5,
            'novelty': 0.5,
            'aleatoric': 0.5
        })
        thresholds = self.create_thresholds()
        
        # Run multiple times
        actions = [compute_router_action(row, thresholds) for _ in range(10)]
        
        # All should be the same
        assert len(set(actions)) == 1


class TestRecommendedNextSteps:
    """Tests for recommended next steps generation."""
    
    def test_evidence_insufficient_steps(self):
        """Evidence-insufficient should include check_smiles_validity."""
        row = pd.Series({
            'router_action': 'Evidence-insufficient',
            'missing_fields': ['emission_sol', 'qy_sol']
        })
        
        steps = compute_recommended_next_steps(row)
        
        assert 'check_smiles_validity' in steps
        assert 'verify_inchikey' in steps
    
    def test_novelty_candidate_steps(self):
        """Novelty-candidate should include manual_review and atb request."""
        row = pd.Series({
            'router_action': 'Novelty-candidate',
            'missing_fields': []
        })
        
        steps = compute_recommended_next_steps(row)
        
        assert 'manual_review' in steps
        assert 'request_atb_compute_on_linux' in steps
    
    def test_in_domain_ambiguous_steps(self):
        """In-domain ambiguous should include compare_with_neighbors."""
        row = pd.Series({
            'router_action': 'In-domain ambiguous',
            'missing_fields': []
        })
        
        steps = compute_recommended_next_steps(row)
        
        assert 'compare_with_neighbors' in steps
    
    def test_known_stable_no_missing(self):
        """Known/Stable with no missing fields should have empty steps."""
        row = pd.Series({
            'router_action': 'Known/Stable',
            'missing_fields': []
        })
        
        steps = compute_recommended_next_steps(row)
        
        assert len(steps) == 0


class TestIntegration:
    """Integration tests with real-like data."""
    
    def test_full_pipeline_smoke(self):
        """Smoke test for the full pipeline with synthetic data."""
        # Create synthetic neighbors
        neighbors = pd.DataFrame({
            'inchikey': ['AAA'] * 10 + ['BBB'] * 10,
            'neighbor_inchikey': [f'N{i}' for i in range(10)] + [f'M{i}' for i in range(10)],
            'rank': list(range(1, 11)) * 2,
            'tanimoto_sim': np.random.uniform(0.2, 0.8, 20)
        })
        
        # Compute C_sim
        c_sim_df = compute_c_sim(neighbors)
        assert len(c_sim_df) == 2
        
        # Compute novelty
        novelty_df, _ = compute_novelty(c_sim_df)
        assert len(novelty_df) == 2
        
        # Compute aleatoric
        aleatoric_df = compute_aleatoric(c_sim_df)
        assert len(aleatoric_df) == 2
        
        # All scores should be in [0, 1]
        assert all(c_sim_df['C_sim'] >= 0) and all(c_sim_df['C_sim'] <= 1)
        assert all(novelty_df['novelty'] >= 0) and all(novelty_df['novelty'] <= 1)
        assert all(aleatoric_df['aleatoric'] >= 0) and all(aleatoric_df['aleatoric'] <= 1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
